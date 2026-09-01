// 渲染层与主进程之间唯一的门。
//
// contextIsolation 开着、nodeIntegration 关着，所以渲染层拿不到 require ——
// 这是有意的：页面里那些字符串拼出来的 HTML 一旦能碰到 fs，
// 一个转义漏洞就是任意文件读写。这里只开五个具体动作，不开通用能力。

'use strict';

const { contextBridge, ipcRenderer, webUtils } = require('electron');

contextBridge.exposeInMainWorld('api', {
  getPort: () => ipcRenderer.invoke('get-port'),
  pickFiles: () => ipcRenderer.invoke('pick-files'),
  pickDir: () => ipcRenderer.invoke('pick-dir'),
  pickOutDir: () => ipcRenderer.invoke('pick-out-dir'),
  openPath: (p) => ipcRenderer.invoke('open-path', p),
  openFile: (p) => ipcRenderer.invoke('open-file', p),

  // 更新装好之后重启软件。**必须重启才生效** —— 覆盖的是 .py 和 .js，
  // 当前进程跑的还是加载时的那份旧代码。
  restart: () => ipcRenderer.invoke('restart-app'),

  // 引导用户去装 Office 时要能打开官网。主进程那边卡了白名单 ——
  // 页面是字符串拼出来的 HTML，万一哪天有个转义漏洞，
  // 能被诱导打开任意 URL 就是钓鱼入口。
  openUrl: (u) => ipcRenderer.invoke('open-url', u),

  // 拖进来的文件要拿真实路径。Electron 32 之后 File.path 被移除了，
  // 得走 webUtils.getPathForFile —— 不处理这个，拖放功能会静默失灵。
  pathForFile: (file) => {
    try {
      return webUtils.getPathForFile(file);
    } catch (e) {
      return file && file.path ? file.path : '';
    }
  },
});
