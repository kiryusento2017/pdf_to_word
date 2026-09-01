// PDF 转 Word · Electron 外壳
//
// 外壳只做三件事：起 Python 服务、开窗、把系统对话框（选文件、打开文件夹）
// 转给渲染层。**业务逻辑一行都不放这里** —— 放这儿就没法用 Python 那套测试测了。
//
// 起服务的约定：Python 把 `PDF2WORD_PORT=<端口>` 打到 stdout，这边等那一行。
// 端口由系统分配，不写死 —— 写死会在用户同时开着别的软件时撞车，那种失败极难查。

'use strict';

const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

// 安装目录。发行版和开发环境的层级不一样：
//
//   发行版    <安装目录>/resources/app/main.js   → 上两级
//   开发环境  <项目>/app/main.js                 → 上一级
//
// 发行版把代码放进 resources/app 是 Electron 的标准形态（VS Code、Discord
// 都是），这样 electron.exe 改个名就能双击直接开 —— 不用再弹个 cmd 黑框
// 去调它。判断依据是父目录叫不叫 resources，不依赖 app.isPackaged
// （我们没打 asar，那个标志不可靠）。
const ROOT = path.basename(path.dirname(__dirname)) === 'resources'
  ? path.join(__dirname, '..', '..')
  : path.join(__dirname, '..');
const SERVER = path.join(ROOT, 'server', 'main.py');

// 🔴 Python 的位置：发行版和开发环境不一样，按顺序找。
//    发行版用官方 embeddable 包（自带 stdlib，自包含）放在 runtime/python/；
//    .venv **不能打包分发** —— 它的 Lib 下只有 site-packages，没有 stdlib，
//    os.__file__ 指向开发机的 Python 安装目录，换台机器第一句 import 就死。
//    跟 Python 那边的 paths.find_exe 是同一套思路，别再各写各的。
const PYTHON = (() => {
  const cands = [
    path.join(ROOT, 'runtime', 'python', 'python.exe'),   // 发行版
    path.join(ROOT, '.venv', 'Scripts', 'python.exe'),    // 开发环境
  ];
  const fs = require('fs');
  for (const p of cands) {
    if (fs.existsSync(p)) return p;
  }
  return cands[cands.length - 1];   // 都没有：让它报错，错误信息里能看见路径
})();

let win = null;
let py = null;
let apiPort = 0;

function startServer() {
  return new Promise((resolve, reject) => {
    py = spawn(PYTHON, [SERVER], { cwd: ROOT });
    let buf = '';
    // 超时给到两分钟：首次启动要 import torch / mineru，机械盘或者
    // 网吧那种机器上二十秒根本不够 —— 超时了软件就直接打不开，
    // 而它其实只是还在加载。
    // 等这么久不会掩盖真故障：进程要是崩了，下面的 'exit' 会立刻
    // reject，不用等到超时。
    const timer = setTimeout(() => {
      reject(new Error('后台服务两分钟没起来。最后的输出：\n' + buf.slice(-600)));
    }, 120000);

    py.stdout.on('data', (d) => {
      buf += d.toString();
      const m = buf.match(/PDF2WORD_PORT=(\d+)/);
      if (m) {
        clearTimeout(timer);
        apiPort = parseInt(m[1], 10);
        resolve(apiPort);
      }
    });
    // stderr 也留着：服务起不来时，原因几乎总在这里
    py.stderr.on('data', (d) => { buf += d.toString(); });
    py.on('error', (e) => { clearTimeout(timer); reject(e); });
    py.on('exit', (code) => {
      if (!apiPort) {
        clearTimeout(timer);
        reject(new Error('后台服务退出了（code ' + code + '）：\n' + buf.slice(-600)));
      }
    });
  });
}

// 🔴 把 Electron 自己的缓存挪进安装目录。
//    小蔡定的规矩：运行中产生的一切都留在安装文件夹内，只有导出的 Word
//    例外 —— 删掉文件夹 = 卸载干净。Electron 默认往
//    %APPDATA%\\pdf2word 放 4.6 MB（GPU 缓存、字典、Code Cache…），
//    是最后一处还落在外面的东西。
//    **必须在 app ready 之前设**，ready 之后再设就来不及了。
function relocateUserData() {
  const dir = path.join(ROOT, 'appdata');
  try {
    require('fs').mkdirSync(dir, { recursive: true });
    app.setPath('userData', dir);
    app.setPath('sessionData', dir);
  } catch (e) {
    // 目录建不出来（比如装进了 Program Files）就维持默认位置 ——
    // 这种情况下后端的 writable 自检会拦住用户并说明原因，
    // 不必在这里再弹一次窗。
  }
}

relocateUserData();

// 🔴 只允许开一份。开两份的后果，按严重度排：
//
//   1. 两个 MinerU 同时吃 GPU → 8 GB 的卡直接 OOM，两边都失败
//   2. 工作目录是写死的 _tmp/extract，两边转同名 PDF 时产物互相覆盖，
//      find_output 可能读到另一份正在写的半成品 → **静默拿到错内容**
//   3. 同时下模型 → 两个下载器写同一个 models/，互相破坏那 4.6 GB
//   4. 两份 Electron + 两份 Python + 两份 torch 常驻内存
//
// 而触发它只需要手快双击两下。原来没有任何提示，就是安静地又开一个窗口。
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  // 第二次点开时，把已经开着的那个窗口提到前面 —— 用户要的是「打开软件」，
  // 给他看到窗口就是对的响应，静悄悄什么都不发生反而像是点坏了。
  app.on('second-instance', () => {
    if (win) {
      if (win.isMinimized()) win.restore();
      win.show();
      win.focus();
    }
  });
}

function createWindow() {
  // 小工具的尺寸，参照 Geek Uninstaller 那一类。620 宽刚好放得下
  // 「文件名 + 无文字层提示 + 页数」三列，440 高能露出 15 行左右。
  // 拉大窗口列表会跟着长 —— 主区是 flex:1，不是写死的高度。
  win = new BrowserWindow({
    width: 620,
    height: 440,
    minWidth: 460,
    minHeight: 300,
    backgroundColor: '#ffffff',   // 跟页面底色一致，开窗时不白闪
    title: 'PDF 转 Word',
    // 图标：终末诗篇的手写落款。ico 里分档放了不同内容 ——
    // 16/24/32 是单字「终」，48 以上才是四个字：四个字缩到 16px
    // 每字只剩 8x8 像素，糊成一团灰，认不出来。
    // 底色是白色圆角：源图是全透明底 + 黑墨迹，在深色任务栏上等于隐形。
    icon: path.join(__dirname, 'icon.ico'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.setMenuBarVisibility(false);
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
}

app.whenReady().then(async () => {
  try {
    await startServer();
  } catch (e) {
    // 服务起不来就没法干活了。**把原因原样给人看**，别只说「启动失败」——
    // 那四个字谁也查不了。
    dialog.showErrorBox('启动失败', String(e.message || e));
    app.quit();
    return;
  }
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

// 🔴 kill(py) 只杀 Python 本身，**MinerU 是它的子进程** —— Python 死了
//    MinerU 变成孤儿继续跑，用户关掉软件之后它还在后台吃着 GPU 直到转完。
//    Windows 上要 taskkill /T（连整棵进程树）才能真正收干净。
//    /F 是强杀：这时候窗口已经关了，没有「优雅退出」可谈，
//    留着一个吃 4 GB 显存的孤儿进程比丢掉半个转换产物糟得多。
function killTree(proc) {
  if (!proc || proc.killed) return;
  try {
    if (process.platform === 'win32' && proc.pid) {
      require('child_process').spawnSync(
        'taskkill', ['/PID', String(proc.pid), '/T', '/F'],
        { stdio: 'ignore', windowsHide: true });
    } else {
      proc.kill();
    }
  } catch (e) { /* 已经没了 */ }
  try { proc.kill(); } catch (e) { /* 同上 */ }
}

app.on('window-all-closed', () => {
  killTree(py);
  app.quit();
});

// 进程被外面强制结束时也收一次 —— 任务管理器结束进程、Ctrl+C 之类。
app.on('before-quit', () => { killTree(py); });

// ── 渲染层要的系统能力 ────────────────────────────────────────────────
ipcMain.handle('get-port', () => apiPort);

ipcMain.handle('pick-files', async () => {
  const r = await dialog.showOpenDialog(win, {
    title: '选 PDF',
    properties: ['openFile', 'multiSelections'],
    filters: [{ name: 'PDF', extensions: ['pdf'] }],
  });
  return r.canceled ? [] : r.filePaths;
});

ipcMain.handle('pick-dir', async () => {
  const r = await dialog.showOpenDialog(win, {
    title: '选文件夹',
    properties: ['openDirectory'],
  });
  return r.canceled ? [] : r.filePaths;
});

ipcMain.handle('pick-out-dir', async () => {
  const r = await dialog.showOpenDialog(win, {
    title: '转好的 Word 放哪',
    properties: ['openDirectory', 'createDirectory'],
  });
  return r.canceled ? '' : r.filePaths[0];
});

ipcMain.handle('open-path', async (_e, p) => {
  // 打开文件用默认程序；打开目录并选中文件，比只开目录省用户一次找
  if (!p) return;
  try {
    const fs = require('fs');
    if (fs.existsSync(p) && fs.statSync(p).isDirectory()) {
      await shell.openPath(p);
    } else {
      shell.showItemInFolder(p);
    }
  } catch (e) { /* 文件被人挪走了，不值得为此弹窗 */ }
});

ipcMain.handle('open-file', async (_e, p) => {
  if (p) await shell.openPath(p);
});

// 只放行这几个域名。页面的 HTML 是字符串拼出来的，万一哪天有个转义
// 漏洞，「能打开任意 URL」就成了钓鱼入口 —— 用户看到是我们的软件
// 弹出的浏览器，戒心是最低的。宁可写死几条也不开通用能力。
const URL_WHITELIST = [
  'https://www.microsoft.com/',
  'https://www.microsoftstore.com.cn/',
  'https://nodejs.org/',
  // Visual C++ 运行库。torch 的 c10.dll 依赖它，缺了整个 torch 都
  // import 不了（Windows 只报一句「动态链接库初始化例程失败」）。
  // aka.ms 是微软自己的短链域名，会跳到 download.visualstudio.microsoft.com。
  'https://aka.ms/',
  'https://www.nvidia.com/',        // 驱动太旧时引导去更新
  'https://www.nvidia.cn/',
  // 跨大版本时要让用户去 Release 页面下完整安装包。
  // 只放行本仓库那一条路径，不是整个 github.com。
  'https://github.com/kiryusento2017/pdf_to_word/releases',
];

// 更新装好之后重启。relaunch 排一个新实例，quit 关掉当前这个 ——
// window-all-closed 里会顺手 kill 掉 Python 后端，新实例会重新起一个。
ipcMain.handle('restart-app', () => {
  app.relaunch();
  app.quit();
  return true;
});

ipcMain.handle('open-url', async (_e, u) => {
  if (typeof u !== 'string') return false;
  if (!URL_WHITELIST.some((prefix) => u.startsWith(prefix))) return false;
  await shell.openExternal(u);
  return true;
});
