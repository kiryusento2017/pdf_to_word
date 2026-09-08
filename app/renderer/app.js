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
  // ── 待办队列 ──────────────────────────────────────────────────────
  // 软件永远只有两个队列：正在跑的（task）和攒着的（pending）。
  // 转换中拖进来 / 选进来的文件不塞进正在跑的那批 —— 那批的倒计时和
  // 进度条是开工时估死的，中途改会让它们当场失真。攒在这儿，等这批
  // 全部转完，整批当成一个全新任务接上（见 actions 的 promotePending）。
  pending: [],          // 待办清单，元素结构跟 items 一样（体检结果）
  pendingBusy: false,   // 待办正在体检（/api/scan 往返那一两秒）
  // 上一批的逐份结果。待办晋升那一刻留一份 —— 界面立刻换成新一批的进度，
  // 而上一批的报告还得看得到（那是一张校对清单，指着哪几页最该核对）。
  //
  // 只留 results 这一个数组，不留整个 task：报告只用得上它，而 task 里
  // 还挂着最多 120 行日志。也不改成「只记 task_id 回头查后端」——
  // 报告按钮该不该显示要当场判断（worthReport），而那个函数在 pages.js
  // 内部，actions.js 够不到它（front_check 只认 app.js 定义过的全局）。
  lastResults: null,
  // 在看上一批的报告。**不跟 showReport 共用** —— 那个是「当前这批」的，
  // 一个开关管两批的话，两边会互相顶掉。
  showLastReport: false,
  starting: false,
  err: '',
  port: 0,
  // JS 报错。**这是以前完全空白的一块** —— 后端出事有 logs\convert.log
  // 兜着，前端出事一个字都不留：界面就那么卡死，除了让用户重启没别的办法
  // （actions.js 里记着 2026-09-02 那次，写了个不存在的函数，点下去抛异常、
  // 轮询没启动，界面永远停在「正在装」）。这些进诊断文件。
  jsErrors: [],
  diagBusy: false,      // 正在生成诊断文件
  // 升级备份列表（进环境检测页时拉）。以前 /api/upgrade/backups 做好了
  // 没人调，界面上看不见也退不回去。
  backups: [],
  rollbackAsk: '',      // 哪一份正在问「确定退回？」，空 = 没在问
  rollbackBusy: false,  // 正在退回（拷 4 GB 要一会儿）
  rollbackDone: false,  // 退完了，等重启 —— 这时列表换成「立即重启」
  // 首次使用那一屏：源清单、选中的源、下载进度
  runs: [],             // 转换历史，进「历史」那一屏时拉
  upgPending: null,     // 有没有下好等着装的升级（开机问一次）
  upgIns: null,         // 正在装 / 装完了
  showReport: false,    // 转完之后在看报告
  reportText: '',       // 报告正文，只在内存里，不落盘
  openStage: null,      // 展开了哪一行的步骤清单，null = 都收着
  progMax: 0,           // 总进度条到过的最大值，**只涨不退**（见 pages.js 的 convProgress）
  upd: null,            // 检查更新的结果：null=没查过
  updBusy: false,       // 正在查 / 正在下
  updLinesOpen: false,  // 线路表展开了没（默认折叠，只占一行）
  updNotesOpen: false,  // 更新说明展开了没（默认只显示摘要那几行）
  updLeft: 0,           // 查更新倒计时还剩几秒（0 = 没在倒计时）

  // 关于 / 环境检测。about = null 表示没打开；打开时是
  // 'about' 或 'env' 两种视图之一。
  about: null,
  diag: null,           // 诊断报告的原始数据（进环境检测时拉）
  maint: null,          // 各项占用（同上）
  maintBusy: false,
  maintPick: {},        // 勾了哪几类要清理：{key: true}
  cacheOpen: false,     // pip 缓存的明细展开了没
  deps: null,           // 上游版本检查的结果。**默认 null =
                        // 没查过，界面上显示破折号，不显示「已是最新」**
  depsBusy: false,
  copied: false,        // 诊断信息刚复制过（给个短暂反馈）
  diagText: '',         // 拼好的诊断文本。放 state 不放 window ——
                        // 隐式全局栽过（2026-09-02 那次点下去直接崩）

  // 依赖升级
  upgPick: {},          // 勾了哪几个包要升
  upgPlan: null,        // 预演结果：会动哪些包
  upgBusy: false,
  upgDl: null,          // 下载进度
  upgDetail: false,     // 「会动哪些包」的完整清单展开了没 ——
                        // 一次升级动十几个包很正常，全摊开会吓着人
  updPick: '',          // 手动指定的线路 id，空 = 自动挑最快的
  updProbing: false,    // 正在实测下载速度（用户主动点的）
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
  // C++ 运行库排在 GPU 运行库前面 —— 它是那 2.8 GB 的前提，
  // 顺序反了用户就得白下一趟（2026-09-02 小蔡真踩了）。
  if (!(e.vcredist || {}).ok) return true;
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

  // 🔴 重绘是把整个 DOM 推倒重来（innerHTML 整体赋值），**每一个**滚动容器
  //    都跟着被换成新元素、scrollTop 归零 —— 表现就是：列表拉到下面，随便
  //    点个勾就弹回最顶上，转换中每秒一次的轮询更是一秒弹一次。
  //    所以画之前把位置记下来，画完放回去。
  //
  //    **认 data-keep-scroll，不写死选择器。** 原来是一个容器写一段
  //    （.main 一段、缓存明细一段），结果 2026-09-07 连着漏了两回：先是
  //    环境检测页的缓存明细（小蔡报「展开往下滑自己跳回顶部」），修完当天
  //    又在新做的历史屏里造了个一模一样的。只要新加滚动区的人不记得回来
  //    改这里，就会有下一个。现在标记跟容器写在一起，加的时候顺手就带上了。
  //
  //    日志区**不走这套**：它要的是「贴底就跟着滚」，不是「留在原处」，
  //    见下面那段。
  var keep = {};
  var olds = el.querySelectorAll('[data-keep-scroll]');
  for (var i = 0; i < olds.length; i++) {
    var k = olds[i].getAttribute('data-keep-scroll');
    if (k) keep[k] = olds[i].scrollTop;
  }

  // 🔴 日志区**只在用户本来就贴着底部时**才跟着滚。
  //    原来是无条件 scrollTop = scrollHeight，而转换和下载期间每秒重绘
  //    一次 —— 用户想往上翻看历史，一秒之内就被拽回最底下，等于翻不动。
  //    3px 的容差是为了吃掉亚像素和缩放带来的误差。
  //    拿不到 scrollHeight/clientHeight（测试用的假 DOM）时按贴底处理，
  //    保持改造前的行为。
  var prevLog = el.querySelector ? el.querySelector('#dllog') : null;
  var logStick = true, logTop = 0;
  if (prevLog) {
    logTop = prevLog.scrollTop || 0;
    var sh = prevLog.scrollHeight, ch = prevLog.clientHeight;
    logStick = !(typeof sh === 'number' && typeof ch === 'number')
      || (sh - logTop - ch) <= 3;
  }

  var page = window.P2W_PAGES[state.page] || window.P2W_PAGES.main;
  el.innerHTML = page(state);

  // 下载日志要自动滚到底 —— 不滚的话新行出现在看不见的地方，
  // 用户盯着一屏不动的旧输出，跟没有日志一样。
  // 但只在他没有主动翻上去的时候滚（见上面 logStick）。
  // （这跟下面保住 .main 滚动位置是两回事：那边是别把用户翻到的位置
  //   弄丢，这边是新内容必须自己露出来。）
  if (el.querySelector) {
    var lg = el.querySelector('#dllog');
    if (lg) lg.scrollTop = logStick ? lg.scrollHeight : logTop;
  }

  // 位置放回去。内容变短时浏览器自己会截断到最大值，不用管。
  var news = el.querySelectorAll('[data-keep-scroll]');
  for (var j = 0; j < news.length; j++) {
    var k2 = news[j].getAttribute('data-keep-scroll');
    if (k2 && keep[k2]) news[j].scrollTop = keep[k2];
  }
}

// ── JS 报错留痕 ────────────────────────────────────────────────────────
// 🔴 **不改变任何行为，只记一笔。** 不弹窗、不打断、不 render ——
//    出错的时候界面往往已经不对劲了，再弹个框只会让老师更慌。
//    悄悄记下来，等他点「生成诊断文件」时一起交出去。
//
//    只留最近 20 条：真出问题时同一个错会疯狂重复（每秒轮询一次就报一次），
//    留太多的话最早那条真正的病根反而被挤出去了 —— 所以**留最早的 20 条**，
//    不是最近的。第一条通常就是病根，后面全是它的回声。
function noteJsError(kind, msg, extra) {
  try {
    if (state.jsErrors.length >= 20) return;
    state.jsErrors.push(new Date().toLocaleTimeString() + ' [' + kind + '] '
      + String(msg).slice(0, 240) + (extra ? '  @' + extra : ''));
  } catch (e) { /* 记录出错本身绝不能再抛 */ }
}
window.addEventListener('error', function (e) {
  // 🔴 **资源加载失败（img/script 404）也走这个事件，但它没有 message**，
  //    `String(e)` 会落成一句没用的 `[object Event]`。20 个格子被这种噪音
  //    占满之后，真正的崩溃一条也记不下 —— 而那才是要留的东西。
  if (!e || !e.message) return;
  noteJsError('error', e.message,
    e.filename ? (e.filename.split('/').pop() + ':' + e.lineno) : '');
});
window.addEventListener('unhandledrejection', function (e) {
  // Promise 里抛的错不会触发上面那个 —— 而这个软件几乎所有网络请求
  // 都是 Promise，漏了它等于漏掉一大半。
  var r = e && e.reason;
  noteJsError('promise', (r && (r.stack || r.message)) || r, '');
});

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
// 🔴 **转换中不再拦拖放。** 原来这里是 `|| isRunning(state)`，转换中拖进来
//    的文件连个提示都没有就被丢掉。现在改成照收，只是收进待办队列而不是
//    正在跑的那批（见下面 drop 里的分流）。
//
//    ⚠️ `state.page !== 'main'` 实际只拦住**首次下模型那一屏**
//    （page === 'model'）。环境检测、历史、关于、更新面板走的都是
//    `st.about` / `st.upd` 分支，`state.page` 仍然是 `'main'` —— 它们
//    一个都没拦，v0.2.6 也没拦，这里没有改变任何既有行为。
//
//    ⚠️ 只动这个函数，**不动 isRunning 本身**。它还被另外 5 处共用
//    （底栏的「关于」按钮、环境检测页的三个按钮、主界面形态判断），
//    改它等于顺手放开了「转换中禁用清理 / 检查更新 / 升级」那几道闸。
function dropBusy() {
  return state.page !== 'main';
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
  // 转换中收进待办，其余照旧进待转清单。分流放在这儿而不是 addPaths 里面：
  // addPaths 还被「选择 PDF 文件」「从这里挑」两个按钮共用，那两个在转换中
  // 本来就摸不到（主界面那时是进度屏），口径混在一起反而说不清。
  // 🔴 `|| state.starting` 不能少。isRunning 只认「st.task 已经是 running」，
  //    而从点下「开始转换」到第一次轮询回来之间（以及待办晋升时 start()
  //    的请求还在飞的那段），st.task 还是 null 或上一批的旧快照 ——
  //    那时拖进来的文件会走 addPaths 塞进待转清单，而这一批的 paths
  //    早就算完发出去了，它们会一直显示「未处理」，永远不转。
  if (paths.length) {
    if (isRunning(state) || state.starting) window.P2W_ACTS.addPending(paths);
    else window.P2W_ACTS.addPaths(paths);
  } else render();
});

// ── 启动 ───────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', function () {
  render();
  window.api.getPort().then(function (port) {
    state.port = port;
    // 🔴 **拉历史必须在拿到 port 之后。** 2026-09-07 栽过：这句原本放在
    //    getPort 前面，而 apiUrl 是 'http://127.0.0.1:' + state.port + p ——
    //    开机那次请求发的是 `.../127.0.0.1:null/api/runs`，必然失败，又被
    //    loadRuns 自己的 catch 静默吞掉，于是「开机看不到历史」而且一声不吭。
    //    调用位置错 + catch 吃掉证据，两个错叠一起才成了哑巴 bug。
    try { window.P2W_ACTS.loadRuns(); } catch (e) { /* 历史拉不到不挡主流程 */ }
    // 有没有上次下好、还没装的升级。**这一句以前没有** —— 后端
    // install() 和 /api/upgrade/pending 都是好的，就是没人问，于是
    // 用户下了 2.5 GB、界面说「重启后生效」，重启之后什么都没发生。
    try { window.P2W_ACTS.loadUpgPending(); } catch (e) { /* 同上，不挡主流程 */ }
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
