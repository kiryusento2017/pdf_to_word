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
