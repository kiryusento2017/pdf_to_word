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

const ROOT = path.join(__dirname, '..');
const PYTHON = path.join(ROOT, '.venv', 'Scripts', 'python.exe');
const SERVER = path.join(ROOT, 'server', 'main.py');

let win = null;
let py = null;
let apiPort = 0;

function startServer() {
  return new Promise((resolve, reject) => {
    py = spawn(PYTHON, [SERVER], { cwd: ROOT });
    let buf = '';
    const timer = setTimeout(() => {
      reject(new Error('后台服务 20 秒没起来。最后的输出：\n' + buf.slice(-600)));
    }, 20000);

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

app.on('window-all-closed', () => {
  if (py) { try { py.kill(); } catch (e) { /* 已经没了 */ } }
  app.quit();
});

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
