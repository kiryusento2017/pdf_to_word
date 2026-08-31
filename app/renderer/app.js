// PDF 转 Word · 渲染层
//
// 无框架，纯 JS 拼字符串 + 事件委托 —— 跟金石工作台同一套路子。
// 理由不是复古：这软件只有三屏，装个框架的收益抵不过多一层构建的代价，
// 而且拼字符串这套在工作台那边已经被一套「假 window 里真渲染」的测试盯住了。
//
// 三屏：
//   check   环境自检（首次打开必看：显卡够不够、缺什么）
//   pick    选书 + 开始转换
//   run     转换中 / 结果
'use strict';

var BS = {
  card: 'background:var(--card);border-radius:var(--radius);padding:20px;'
      + 'border:1px solid var(--line)',
  h1: 'margin:0 0 6px;font-size:20px;font-weight:600;color:var(--ink)',
  h2: 'margin:0;font-size:15px;font-weight:600;color:var(--ink)',
  body: 'font-size:14px;line-height:1.6;color:var(--ink2)',
  faint: 'font-size:12px;color:var(--ink3)',
  mono: 'font-family:Consolas,"JetBrains Mono",monospace',
};

var state = {
  page: 'check',
  env: null,
  envLoading: true,
  envError: '',
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
function render() {
  var el = document.getElementById('app');
  if (!el) return;
  var page = window.P2W_PAGES[state.page] || window.P2W_PAGES.check;
  el.innerHTML =
    '<div style="max-width:900px;margin:0 auto;padding:28px 24px 40px">'
    + page(state) + '</div>';
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
window.addEventListener('dragover', function (e) {
  e.preventDefault();
  if (state.page === 'run') return;
  if (!state.dragging) { state.dragging = true; render(); }
});
window.addEventListener('dragleave', function (e) {
  if (e.relatedTarget) return;      // 只在真正离开窗口时收起提示
  if (state.dragging) { state.dragging = false; render(); }
});
window.addEventListener('drop', function (e) {
  e.preventDefault();
  state.dragging = false;
  if (state.page === 'run') { render(); return; }
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
window.P2W_BS = BS;
window.P2W_FMT = { sec: fmtSec, base: baseName };
window.P2W_HTTP = { get: get, post: post };
