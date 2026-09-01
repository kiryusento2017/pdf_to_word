// PDF 转 Word · 渲染层
//
// 无框架，纯 JS 拼字符串 + 事件委托 —— 跟终末诗篇工作台同一套路子。
// 理由不是复古：这软件只有两屏，装个框架的收益抵不过多一层构建的代价，
// 而且拼字符串这套已经被一套「假 window 里真渲染」的测试盯住了。
//
// 两屏：
//   main    主屏。工具条 + 文件表 + 状态栏。**任何时候主体都是那张表** ——
//           待转换时是待转清单，转换中原地变进度，转完变结果。不跳屏。
//   model   首次没模型时的选源屏，一次性的，选完就再也见不到
//
// 环境自检不占屏：结果压进状态栏左侧；只有「显卡不够」「引擎缺失」这种
// 必须让用户拿主意的，才在主区拦一下（gate）。
'use strict';

var state = {
  page: 'main',
  env: null,
  envLoading: true,
  envError: '',
  gateAck: false,       // 用户看过「显卡不够」并选了「仍然继续」
  items: [],            // 选进来的 PDF（体检结果）
  picked: {},           // path -> 勾没勾
  scanning: false,
  outDir: '',           // 空 = 跟原 PDF 放一起
  dragging: false,
  taskId: '',
  task: null,
  starting: false,
  err: '',
  port: 0,
  // 首次使用那一屏：源清单、选中的源、下载进度
  upd: null,            // 检查更新的结果：null=没查过
  updBusy: false,       // 正在查 / 正在下
  sources: null,
  srcLoading: false,
  srcError: '',
  srcPick: '',
  srcTotalGb: '',
  dl: null,
};

// HTML 转义。**必须做**：文件名是用户给的，出现 < > & 是常事。
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function fmtSec(s) {
  s = Math.round(s || 0);
  if (s < 60) return s + ' 秒';
  return Math.floor(s / 60) + ' 分 ' + (s % 60) + ' 秒';
}

function baseName(p) {
  var m = String(p || '').split(/[\\/]/);
  return m[m.length - 1] || p;
}

// 字节转人看的单位。下载那屏显示「已下 2.3 GB / 约 4.6 GB」——
// 总量 4.6 GB 是估的，百分比可能冲到 103% 或停在 97%，
// 但「已下多少」永远是真的。
function fmtGB(n) {
  n = Number(n) || 0;
  if (n < 1024 * 1024) return Math.round(n / 1024) + ' KB';
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(0) + ' MB';
  return (n / 1024 / 1024 / 1024).toFixed(1) + ' GB';
}

// 转换正在进行 —— 好几处要判断（拖放要不要拦、状态栏显示什么、
// 表格是待转清单还是进度），抽出来免得各写各的判断口径不一。
function isRunning(st) {
  return !!(st.task && st.task.state === 'running');
}

// 首启该不该先去下模型。**任何一道拦截没过就先别下** ——
// 4.6 GB 下完之后再告诉人家「你显卡不满足，要不要退出」，
// 顺序是反的：该问的话要问在花时间之前。
// 判据跟 pages.js 的 gateKind 一致，**包括显卡那道软拦**：
// 用户点过「仍然继续」（gateAck）之后才轮到下模型。
function isBlocked(st) {
  var e = st.env || {};
  if (!(e.writable || {}).ok) return true;
  if (!(e.formula || {}).ok) return true;
  if (!(e.mineru || {}).ok) return true;
  // GPU 运行库（CUDA 版 PyTorch）没装或装成了 CPU 版 —— 硬拦。
  // 这台机器有没有显卡是另一回事（下面那条），两个都得过。
  if (!(e.cuda_torch || {}).ok) return true;
  if (!(e.gpu || {}).ok && !st.gateAck) return true;
  return false;
}

// ── 跟后端说话 ─────────────────────────────────────────────────────────
function apiUrl(p) { return 'http://127.0.0.1:' + state.port + p; }

function get(p) {
  return fetch(apiUrl(p)).then(function (r) { return r.json(); });
}

function post(p, body) {
  return fetch(apiUrl(p), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  }).then(function (r) {
    return r.json().then(function (d) {
      if (!r.ok) throw new Error(d.detail || ('HTTP ' + r.status));
      return d;
    });
  });
}

// ── 渲染 ───────────────────────────────────────────────────────────────
// 页面函数自己吐完整的三段结构（工具条 / 主区 / 状态栏），这里不再包
// 任何居中限宽的容器 —— 那是网页排版，工具软件的内容要顶到窗口边。
function render() {
  var el = document.getElementById('app');
  if (!el) return;

  // 🔴 重绘是把整个 DOM 推倒重来（innerHTML 整体赋值），滚动容器跟着被换掉，
  //    scrollTop 归零 —— 表现就是：列表拉到下面，随便点个勾就弹回最顶上，
  //    转换中每秒一次的轮询更是一秒弹一次。
  //    所以画之前记下滚动位置，画完放回去。
  //    querySelector 做了容错：测试用的假 window 没有它。
  var prev = el.querySelector ? el.querySelector('.main') : null;
  var top = prev ? prev.scrollTop : 0;

  var page = window.P2W_PAGES[state.page] || window.P2W_PAGES.main;
  el.innerHTML = page(state);

  if (top && el.querySelector) {
    var now = el.querySelector('.main');
    // 列表变短时浏览器自己会截断到最大值，不用管
    if (now) now.scrollTop = top;
  }
}

// 事件委托：所有按钮走 data-act，页面重绘也不用重新绑
document.addEventListener('click', function (e) {
  var t = e.target.closest('[data-act]');
  if (!t) return;
  var act = t.getAttribute('data-act');
  var arg = t.getAttribute('data-arg') || '';
  var fn = window.P2W_ACTS[act];
  if (fn) fn(arg, t);
});

// ── 拖放 ───────────────────────────────────────────────────────────────
// 阻止默认是必须的：不拦的话 Electron 会用当前窗口打开那个 PDF，页面直接没了。
function dropBusy() {
  return state.page !== 'main' || isRunning(state);
}
window.addEventListener('dragover', function (e) {
  e.preventDefault();
  if (dropBusy()) return;
  if (!state.dragging) { state.dragging = true; render(); }
});
window.addEventListener('dragleave', function (e) {
  if (e.relatedTarget) return;      // 只在真正离开窗口时收起提示
  if (state.dragging) { state.dragging = false; render(); }
});
window.addEventListener('drop', function (e) {
  e.preventDefault();
  state.dragging = false;
  if (dropBusy()) { render(); return; }
  var paths = [];
  var files = (e.dataTransfer && e.dataTransfer.files) || [];
  for (var i = 0; i < files.length; i++) {
    var p = window.api.pathForFile(files[i]);
    if (p) paths.push(p);
  }
  if (paths.length) window.P2W_ACTS.addPaths(paths);
  else render();
});

// ── 启动 ───────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', function () {
  render();
  window.api.getPort().then(function (port) {
    state.port = port;
    return get('/api/env');
  }).then(function (d) {
    state.env = d;
    state.envLoading = false;
    // 🔴 没有模型就先去选源下载。
    //    这一句之前**根本不存在** —— 选源屏和整套并发测速写好了却没有
    //    任何入口，用户第一次用只会看到主界面，然后在第一次转换时
    //    等 MinerU 在后台默默下 4.6 GB，界面上什么都不显示。
    //    只在环境本身没问题时才跳：连 Office 都没有的话，先解决那个，
    //    下了模型也用不了。
    if (!isBlocked(state) && !((d.models || {}).ok)) {
      state.page = 'model';
    }
    render();
  }).catch(function (e) {
    state.envLoading = false;
    state.envError = String(e && e.message || e);
    render();
  });
});

window.P2W_STATE = state;
window.P2W_RENDER = render;
window.P2W_ESC = esc;
window.P2W_FMT = { sec: fmtSec, base: baseName, gb: fmtGB };
window.P2W_RUNNING = isRunning;
window.P2W_BLOCKED = isBlocked;
window.P2W_HTTP = { get: get, post: post };
