// 前端检查：在假 window 里**真渲染**两个页面。
//
// 存在的理由是工作台那边的教训：题库页从头到尾没写渲染选项的代码，
// 而所有测试都绿着 —— 因为它们只测空态，空态根本走不到那段分支。
// 所以这里每一屏都喂真数据，并断言关键内容真的出现在 HTML 里。
//
// 2026-08-31 UI 改版：四屏合成「主屏 + 首次选源屏」，主体永远是那张
// 文件表。原来的 35 条行为一条没删，只是换了呈现位置；另加 5 条钉住
// 这次改版的目标（三段结构、空态铺满、不跳屏、没有死按钮）。
//
// 跑法：node tests\front_check.js
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const R = path.join(__dirname, '..', 'app', 'renderer');
let bad = 0;

// 假 DOM 里 textContent 存的是**原文**，而全量渲染吐出来的 HTML 是转义过的。
// 要把两条路径的产物摆在一起比，就得把后者还原回原文。
// 顺序跟 app.js 的 esc() 反着来：&amp; 必须最后还原，否则 &amp;lt; 会被
// 多还原一层，比出来是假的不一致。
function unesc(s) {
  return String(s == null ? '' : s)
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&amp;/g, '&');
}

function ck(name, fn) {
  try {
    fn();
    console.log('  \u2713 ' + name);
  } catch (e) {
    bad++;
    console.log('  \u2717 ' + name + '  ' + (e && e.message));
  }
}

function mkSandbox() {
  const listeners = {};
  // 假的 #app。关键是模拟出真实 DOM 的一个语义：innerHTML 整体赋值之后，
  // 里面的 .main 是**一个新元素**，scrollTop 从 0 开始 —— 滚动位置丢失
  // 那个 bug 就出在这里，不模拟这一点就测不出来。
  const appEl = (function () {
    let logEl = null;
    let keepEls = {};
    // 带 id 的节点。转换中的增量刷新（renderConv）靠 querySelector('#cv-…')
    // 拿到这几个节点直接改文字 / 改宽度 —— 不模拟出它们，那条路径在这里
    // 根本跑不起来，只会静默降级回全量重绘，于是测试绿的是降级路径，
    // 真正每秒在跑的那条一行没测到（坑 4 的老形状）。
    let idEls = {};
    return {
      set innerHTML(v) {
        this._html = v;
        // 日志区 innerHTML 换掉之后是**新元素**。给出 scrollHeight /
        // clientHeight，粘底判据才算得出来。它不走通用那套 —— 它要的是
        // 「贴底就跟着滚」，不是「留在原处」。
        logEl = v.indexOf('id="dllog"') >= 0
          ? { scrollTop: 0, scrollHeight: 1000, clientHeight: 100 } : null;
        // 🔴 **凡是带 data-keep-scroll 的都是新元素、scrollTop 从 0 开始。**
        //    不模拟这一点就测不出「一滑就跳回顶部」那一类 bug。
        keepEls = {};
        const re = /data-keep-scroll="([^"]+)"/g;
        let m;
        while ((m = re.exec(v)) !== null) {
          const key = m[1];
          keepEls[key] = {
            scrollTop: 0, scrollHeight: 600, clientHeight: 96,
            getAttribute: (a) => (a === 'data-keep-scroll' ? key : null),
          };
        }
        // 🔴 带 id 的节点。只认两种形状，因为增量刷新要动的就这两样：
        //    文字类 <span id="…">文本</span> 取标签里的文本；
        //    进度条那种空的 <i id="…" style="width:N%"></i> 取宽度。
        //    取内容用的是「往后找第一个同名闭合标签」，所以**这些节点里
        //    不能再套同名标签** —— 套了就取错。真要套，改这里的解析。
        idEls = {};
        const reTag = /<(\w+)\s+id="([^"]+)"([^>]*?)>/g;
        let g;
        while ((g = reTag.exec(v)) !== null) {
          const tag = g[1], id = g[2], attrs = g[3];
          const close = v.indexOf('</' + tag + '>', reTag.lastIndex);
          const inner = close < 0 ? '' : v.slice(reTag.lastIndex, close);
          const w = /width:\s*([^;"]+)/.exec(attrs);
          idEls[id] = {
            id,
            textContent: unesc(inner),
            style: { width: w ? w[1].trim() : '' },
          };
        }
      },
      get innerHTML() { return this._html || ''; },
      querySelector: (s) => {
        if (s === '#dllog') return logEl;
        if (s && s.charAt(0) === '#') return idEls[s.slice(1)] || null;
        return null;
      },
      querySelectorAll: (s) => (s === '[data-keep-scroll]'
        ? Object.keys(keepEls).map((k) => keepEls[k]) : []),
      get _log() { return logEl; },
      get _keep() { return keepEls; },
      get _ids() { return idEls; },
    };
  }());
  const sb = {
    console,
    document: {
      getElementById: () => appEl,
      addEventListener: (k, f) => { listeners[k] = f; },
    },
    fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
    setInterval: () => 0,
    clearInterval: () => {},
    // 真实页面在 window 上挂了拖放和 DOMContentLoaded 的监听，
    // 假 window 得认这些调用，否则脚本一加载就炸
    addEventListener: (k, f) => { listeners[k] = f; },
    location: { reload: () => {} },
    close: () => {},
    api: {
      getPort: () => Promise.resolve(1234),
      pickFiles: () => Promise.resolve([]),
      pickDir: () => Promise.resolve([]),
      pickOutDir: () => Promise.resolve(''),
      openPath: () => {}, openFile: () => {},
      pathForFile: (f) => (f && f.path) || '',
    },
  };
  // 把监听器暴露出去 —— 测「开机那一刻做了什么」要能手动触发
  // DOMContentLoaded。
  sb._on = listeners;
  sb.window = sb;
  sb.globalThis = sb;
  vm.createContext(sb);
  for (const f of ['app.js', 'pages.js', 'actions.js']) {
    vm.runInContext(fs.readFileSync(path.join(R, f), 'utf8'), sb, { filename: f });
  }
  return sb;
}

function baseState(sb) {
  return JSON.parse(JSON.stringify(sb.window.P2W_STATE));
}

// 一切正常的环境，绝大多数用例从它出发。
// writable / formula / models 是 2026-09-01 加的：小蔡定下「文件全留
// 安装目录」和「必须有微软 XSL」之后，这三个字段决定拦不拦人。
// 故意不给默认值 —— 缺字段就该被判成不可用，那说明前后端版本对不上。
function goodEnv() {
  return {
    gpu: { ok: true, why: '显卡「RTX 4060」，显存 8.0 GB，满足要求。' },
    mineru: { ok: true }, pandoc: { ok: true }, office: { ok: true },
    node: { ok: true },
    formula: { ok: true, why: '公式会转成 Word 原生公式对象，可编辑可搜索。' },
    models: { ok: true, dir: 'D:\\app\\models', bytes: 4.6e9 },
    writable: { ok: true, dir: 'D:\\app' },
    // C++ 运行库（msvcp140 那一套）。它是 GPU 运行库的**前提** ——
    // 缺了的话那 2.8 GB 装上也加载不了，所以拦截顺序排在前面。
    vcredist: { ok: true },
    // GPU 运行库（CUDA 版 PyTorch）。跟「有没有显卡」是两件事：
    // 这一项管的是装的 torch 能不能调用显卡，gpu 那项管的是机器上有没有卡。
    cuda_torch: { ok: true, why: 'GPU 运行库就绪（PyTorch 2.11.0+cu128，CUDA 12.8）。',
                  version: '2.11.0+cu128' },
  };
}

function ready(sb) {
  const st = baseState(sb);
  st.envLoading = false;
  st.env = goodEnv();
  return st;
}

// 三段结构齐不齐 —— 这次改版的地基。少任何一段「铺满」就无从谈起：
// 主区靠 flex:1 撑开，没有外壳它会退化成普通文档流。
function segs(h) {
  return h.includes('class="chrome-top"')
      && h.includes('class="main"')
      && h.includes('class="chrome-bot"');
}

console.log('\u52a0\u8f7d\u4e0e\u7ed3\u6784\uff1a');
{
  const sb = mkSandbox();
  ck('两个渲染函数都注册了', () => {
    const p = sb.window.P2W_PAGES;
    for (const k of ['main', 'model']) {
      if (typeof p[k] !== 'function') throw new Error('缺 ' + k);
    }
  });

  ck('动作表齐全', () => {
    const a = sb.window.P2W_ACTS;
    for (const k of ['reload', 'quit', 'ackGate', 'newBatch', 'addPaths',
                     'pickFiles', 'pickDir', 'pickOut', 'outDefault', 'toggle',
                     'selAll', 'selNone', 'clear', 'start', 'cancel',
                     'probeSources', 'pickSource', 'startDownload', 'pickLocal',
                     // openOffice 2026-09-09 删了：XSL 随包分发之后，
                     // 拦截屏不再劝人去装 Office，那个按钮没了调用方。
                     'openFile', 'openPath', 'openNode',
                     'cancelDownload', 'checkUpdate', 'closeUpdate',
                     'downloadUpdate', 'restartApp',
                     'openAbout', 'closeAbout', 'openEnvCheck',
                     'checkDeps', 'toggleMaint', 'toggleCache',
                     'doClean', 'exportDiag', 'toggleUpdNotes',
                     'toggleUpg', 'planUpgrade', 'toggleUpgDetail',
                     'startUpgrade', 'updateModels']) {
      if (typeof a[k] !== 'function') throw new Error('缺 ' + k);
    }
  });

  ck('页面上每个按钮都有处理器，没有死按钮', () => {
    const acts = sb.window.P2W_ACTS;
    const states = [ready(sb)];
    states.push(Object.assign(ready(sb), { envError: '炸了' }));
    states.push(Object.assign(ready(sb), {
      env: Object.assign(goodEnv(), { gpu: { ok: false, why: '慢约 2 倍，8 分钟' } }) }));
    states.push(Object.assign(ready(sb), {
      env: Object.assign(goodEnv(), { mineru: { ok: false } }) }));
    const withItems = ready(sb);
    withItems.items = [{ path: 'C:\\a.pdf', ok: true, pages: 10, scan_pages: [] }];
    states.push(withItems);
    const runSt = ready(sb);
    runSt.items = withItems.items;
    runSt.task = { state: 'running', total: 1, current: 0, current_name: 'a.pdf',
                   stage: '识别公式', stage_cur: 1, stage_total: 10,
                   results: [], elapsed: 5, remain: 100 };
    states.push(runSt);
    const doneSt = ready(sb);
    doneSt.items = withItems.items;
    doneSt.task = { state: 'done', total: 1, current: 1, elapsed: 60, remain: 0,
      results: [{ ok: true, pdf: 'C:\\a.pdf', docx: 'C:\\a.docx', line: '公式 3' }] };
    states.push(doneSt);

    let html = states.map((s) => sb.window.P2W_PAGES.main(s)).join('');
    const mSt = ready(sb);
    mSt.sources = [{ id: 'ms', name: 'ModelScope', ok: true, eta: '约 6 分钟' }];
    mSt.srcPick = 'ms';
    html += sb.window.P2W_PAGES.model(mSt);

    const want = new Set();
    for (const m of html.matchAll(/data-act="([a-zA-Z]+)"/g)) want.add(m[1]);
    for (const k of want) {
      if (typeof acts[k] !== 'function') throw new Error('死按钮：' + k);
    }
    if (want.size < 10) throw new Error('只采到 ' + want.size + ' 个按钮，覆盖不够');
  });

  ck('重绘时保住滚动位置，不弹回顶上', () => {
    // 真实症状：列表拉到下面，点个勾就弹回最顶上；转换中每秒轮询一次，
    // 就一秒弹一次。根因是 innerHTML 整体赋值把滚动容器换成了新元素。
    const sb2 = mkSandbox();
    const el = sb2.document.getElementById('app');
    const st = sb2.window.P2W_STATE;
    st.envLoading = false;
    st.env = goodEnv();
    st.items = Array.from({ length: 40 }, (_, i) => (
      { path: 'C:\\a\\' + i + '.pdf', ok: true, pages: 10, scan_pages: [] }));
    sb2.window.P2W_RENDER();
    el._keep.main.scrollTop = 500;     // 用户滚到中间
    sb2.window.P2W_RENDER();           // 勾一下 / 轮询一次都会走到这
    if (el._keep.main.scrollTop !== 500) {
      throw new Error('滚动位置丢了，弹回了 ' + el._keep.main.scrollTop);
    }
  });

  ck('在顶部时不做多余的滚动设置', () => {
    const sb2 = mkSandbox();
    const el = sb2.document.getElementById('app');
    sb2.window.P2W_RENDER();
    if (el._keep.main.scrollTop !== 0) throw new Error('本来在顶部，却被挪到了别处');
  });

  // 🔴 小蔡 2026-09-08 原话「页面上下卡顿我受不了了」，问清楚是**转换中**卡。
  //    根因不是保不住滚动位置（那个 2026-09-07 已经修了），而是**每秒把整页
  //    推倒重来**：用户滚轮的惯性还没停，DOM 就被换掉、scrollTop 被按上一秒
  //    的值写回去，一秒拽一次。
  //    所以这里断的不是「位置对不对」，是「**整页有没有被重建**」——
  //    位置对但每秒重建，照样卡。
  function runningSt(sb2) {
    const st = sb2.window.P2W_STATE;
    st.envLoading = false;
    st.env = goodEnv();
    st.items = Array.from({ length: 40 }, (_, i) => (
      { path: 'C:\\a\\' + i + '.pdf', ok: true, pages: 10, scan_pages: [] }));
    // lines / progress_line 是后端每秒原样带回来的（poll 返回整个 task），
    // 真实数据里一定有，测试也得带上 —— 少了它们就测不出「日志一动就整页
    // 重绘」这个洞。
    st.task = { state: 'running', total: 40, current: 0,
                stage: '逐页识别', stage_cur: 5, stage_total: 56,
                results: [], elapsed: 10, remain: 1800,
                lines: [], progress_line: '', stages: ['逐页识别'] };
    return st;
  }

  ck('转换中每秒刷新不重建整页，滚动不被打断', () => {
    const sb2 = mkSandbox();
    const el = sb2.document.getElementById('app');
    const st = runningSt(sb2);

    sb2.window.P2W_RENDER_CONV();       // 第一次：整页画出来，理所应当
    const before = el._keep.main;       // 记住这一次的滚动容器
    before.scrollTop = 500;             // 用户滚到中间正看着

    st.task.elapsed = 11;               // 一秒后，变的只有这几个数
    st.task.remain = 1799;
    st.task.stage_cur = 6;
    sb2.window.P2W_RENDER_CONV();       // 第二次：该走增量，不该重建

    if (el._keep.main !== before) {
      throw new Error('整页被重建了 —— 滚动容器换成了新元素，用户正滚着就被拽一下');
    }
    if (before.scrollTop !== 500) {
      throw new Error('滚动位置被动过，停在 ' + before.scrollTop);
    }
    // 🔴 光「不重建」不算数：不刷新也满足这一条。数字必须真的跟着变，
    //    否则就是拿「界面冻住」换来的假流畅。
    const used = el._ids['cv-used'] && el._ids['cv-used'].textContent;
    const eta = el._ids['cv-eta'] && el._ids['cv-eta'].textContent;
    if (!used || used.indexOf('11 秒') < 0) {
      throw new Error('底部已用时没刷新，还是「' + used + '」');
    }
    if (!eta || eta.indexOf('29 分') < 0) {
      throw new Error('顶部剩余时间没刷新，还是「' + eta + '」');
    }
  });

  // 🔴 **这条是整个改动的命根子。**
  //    增量刷新是第二条渲染路径，跟整页重绘并存 —— 两条路各画各的，早晚
  //    分叉，而分叉的表现是「同一份数据在两条路下显示不同」，肉眼极难发现
  //    （坑 4 的形状：测试和实现一起错，于是一起绿）。
  //    所以这里不测「增量有没有更新」，测的是**增量的结果跟整页重绘逐字
  //    相同**。谁哪天在 pageMain 里改了文案却忘了改 patchConv，这条就红。
  const convSnap = (el) => {
    const g = (id) => {
      const n = el._ids[id];
      return n ? (n.textContent + ' | ' + n.style.width) : '(这个节点不见了)';
    };
    return { 'cv-eta': g('cv-eta'), 'cv-used': g('cv-used'),
             'cv-tbar': g('cv-tbar'), 'cv-stg': g('cv-stg'),
             'cv-sbar': g('cv-sbar') };
  };

  ck('增量刷新的结果跟整页重绘逐字相同', () => {
    const sb2 = mkSandbox();
    const el = sb2.document.getElementById('app');
    const st = runningSt(sb2);

    sb2.window.P2W_RENDER_CONV();      // 第一次：整页画出来
    st.task.elapsed = 137;             // 跳到一个不好凑巧蒙对的数
    st.task.remain = 642;
    st.task.stage_cur = 31;
    sb2.window.P2W_RENDER_CONV();      // 第二次：走增量
    const patched = convSnap(el);

    sb2.window.P2W_RENDER();           // 同一份数据，整页重绘一遍
    const full = convSnap(el);

    for (const k of Object.keys(full)) {
      if (patched[k] !== full[k]) {
        throw new Error(k + ' 对不上 —— 增量「' + patched[k]
          + '」，整页「' + full[k] + '」');
      }
    }
  });

  // 🔴 结构一变就必须退回整页重绘，否则「该出现的东西没出现」。
  //    最容易踩的是行内那条小进度条：stage_cur 从 0 变成正数时它**才被
  //    渲染出来**，这时候只改数字是改不到一个还不存在的节点的。
  ck('结构变了要退回整页重绘，不能硬打补丁', () => {
    const sb2 = mkSandbox();
    const el = sb2.document.getElementById('app');
    const st = runningSt(sb2);
    st.task.stage_cur = 0;             // 小条这会儿根本没渲染

    sb2.window.P2W_RENDER_CONV();
    if (el._ids['cv-sbar']) throw new Error('stage_cur=0 时小进度条就不该存在');
    const before = el._keep.main;

    st.task.stage_cur = 1;             // 从无到有 —— 这是结构变化
    sb2.window.P2W_RENDER_CONV();

    if (el._keep.main === before) {
      throw new Error('该整页重绘却走了增量，小进度条永远出不来');
    }
    if (!el._ids['cv-sbar']) throw new Error('重绘了但小进度条还是没出来');
  });

  // 🔴 小蔡 2026-09-08 验收：「还是偶尔会卡一下」。
  //    原因：后端 poll 把**整个 task 原样返回**，里头的 `lines`（转换日志）
  //    和 `progress_line`（MinerU 那条 tqdm，几乎每秒在刷）也跟着回来。
  //    它们只在日志展开时才上屏（pages.js 的 `if (st.showLog)`），可日志
  //    收着的时候它们一变照样让签名变、照样整页重绘 ——
  //    **界面上什么都没变，白卡一下。** MinerU 吐日志的节奏不匀，
  //    表现就是「偶尔卡一下」。
  ck('日志收着时，日志变了不该整页重绘', () => {
    const sb2 = mkSandbox();
    const el = sb2.document.getElementById('app');
    const st = runningSt(sb2);
    st.showLog = false;                     // 默认就是收着的

    sb2.window.P2W_RENDER_CONV();
    const before = el._keep.main;
    before.scrollTop = 300;

    st.task.lines = ['正在处理第 3 页'];       // MinerU 吐了一行
    st.task.progress_line = 'Loading: 30%';  // tqdm 在原地刷
    st.task.elapsed = 11;
    sb2.window.P2W_RENDER_CONV();

    if (el._keep.main !== before) {
      throw new Error('日志一动就整页重绘 —— 而日志根本没显示，纯白卡一下');
    }
    if (before.scrollTop !== 300) {
      throw new Error('滚动位置被动过，停在 ' + before.scrollTop);
    }
  });

  // 反过来也得成立：日志开着的时候它们要上屏，那就必须重绘，
  // 否则新日志永远出不来 —— 别为了不卡把功能弄丢了。
  ck('日志展开时，日志变了必须重绘', () => {
    const sb2 = mkSandbox();
    const el = sb2.document.getElementById('app');
    const st = runningSt(sb2);
    st.showLog = true;

    sb2.window.P2W_RENDER_CONV();
    const before = el._keep.main;

    st.task.lines = ['新的一行'];
    sb2.window.P2W_RENDER_CONV();

    if (el._keep.main === before) {
      throw new Error('日志开着却没重绘，新日志出不来');
    }
  });

  // 🔴 同一个洞的第二个实例：**换阶段**。
  //    MinerU 一份文件要跑六七个阶段，每换一次 `stage` / `stage_total` /
  //    `stages` 都变。但这三样只喂三个地方：
  //      stage       -> convProgress（cv-tbar 宽度）、stageText（cv-stg 文字）
  //      stage_total -> 同上，外加 cv-sbar 宽度
  //      stages      -> **只在展开了当前这行的步骤详情时**才上屏
  //    前两个 patch 全改得到，第三个收着的时候根本不显示 ——
  //    让它们进签名等于每换一次阶段白重绘一次。一份文件卡六七下。
  ck('换阶段不该整页重绘（步骤详情收着时）', () => {
    const sb2 = mkSandbox();
    const el = sb2.document.getElementById('app');
    const st = runningSt(sb2);
    st.openStage = null;                    // 步骤详情收着，默认状态

    sb2.window.P2W_RENDER_CONV();
    const before = el._keep.main;
    before.scrollTop = 420;

    st.task.stage = '识别公式和文字';          // 换到下一个阶段
    st.task.stage_total = 1100;             // 单位也跟着换了
    st.task.stage_cur = 3;                  // 仍然 > 0，小条该在的还在
    st.task.stages = ['逐页识别', '识别公式和文字'];
    sb2.window.P2W_RENDER_CONV();

    if (el._keep.main !== before) {
      throw new Error('换个阶段就整页重绘 —— 一份文件要换六七次，卡六七下');
    }
    if (before.scrollTop !== 420) {
      throw new Error('滚动位置被动过，停在 ' + before.scrollTop);
    }
    const stg = el._ids['cv-stg'] && el._ids['cv-stg'].textContent;
    if (!stg || stg.indexOf('识别公式和文字') < 0) {
      throw new Error('阶段名没跟着变，还是「' + stg + '」');
    }
  });

  // 反过来：步骤详情展开着的时候，stages 要上屏，那就必须重绘。
  ck('步骤详情展开时，换阶段必须重绘', () => {
    const sb2 = mkSandbox();
    const el = sb2.document.getElementById('app');
    const st = runningSt(sb2);
    st.openStage = 0;                       // 展开的正是当前这行（current=0）

    sb2.window.P2W_RENDER_CONV();
    const before = el._keep.main;

    st.task.stages = ['逐页识别', '识别公式和文字'];
    sb2.window.P2W_RENDER_CONV();

    if (el._keep.main === before) {
      throw new Error('步骤详情开着却没重绘，新走过的那一步永远出不来');
    }
  });

  // 🔴 小蔡 2026-09-08 第三次验收：「历史里面也会卡顿」。
  //    这是增量刷新的**盲区**，不是历史屏自己的毛病：
  //    转换还在跑的时候切到历史 / 关于 / 环境检测 / 更新面板，轮询照旧每秒
  //    跑，可这些屏上根本没有 cv-eta 那几个抓手 —— patchConv 拿不到就报失败，
  //    于是每秒退回整页重绘，200 条历史重拼一遍，滚动照样被拽。
  //
  //    抓手不在 = **这一屏根本不显示转换进度** = elapsed 那几个字段变了
  //    界面上什么都不会变 = **什么都不用做**，而不是重绘。
  ck('转换中切到历史屏，不该每秒整页重绘', () => {
    const sb2 = mkSandbox();
    const el = sb2.document.getElementById('app');
    const st = runningSt(sb2);
    st.about = 'history';
    st.runs = Array.from({ length: 200 }, (_, i) => ({
      ok: true, time: '2026-09-08 12:00', pdf: 'C:\\a\\' + i + '.pdf',
      docx: 'C:\\a\\' + i + '.docx', took_sec: 120,
    }));

    sb2.window.P2W_RENDER_CONV();
    const before = el._keep.history;      // 历史屏自己的滚动容器
    if (!before) throw new Error('历史屏没渲染出来，测试前提就不成立');
    before.scrollTop = 500;               // 用户翻到中间在看

    st.task.elapsed = 11;                 // 后台转换照常在跑
    st.task.remain = 1799;
    st.task.stage_cur = 6;
    sb2.window.P2W_RENDER_CONV();

    if (el._keep.history !== before) {
      throw new Error('每秒把 200 条历史整个重拼一遍 —— 滚动照样被拽');
    }
    if (before.scrollTop !== 500) {
      throw new Error('滚动位置被动过，停在 ' + before.scrollTop);
    }
  });

  // 🔴 **这条护栏守的是整个增量刷新方案里唯一能产生「界面该变没变」的路径。**
  //
  //    convSig 把这几个字段排除在签名之外，前提是「它们只喂那几个 patch
  //    改得到的地方」。这个前提是 2026-09-08 逐处查证过的，但**它会随着
  //    pages.js 的改动失效**：哪天有人拿 t.stage 去渲染一个新东西，而它
  //    还被排除在签名外，那个新东西就永远不刷新 —— 而且测试全绿，因为
  //    没人知道该去测它。
  //
  //    所以这里盯着「用途数量」。数字一变就红，红了不代表你错，是提醒你
  //    回去看 app.js 的 convSig：新增的那处用途，patch 改得到吗？
  //      改得到 -> 把数字更新到下面表里，完事
  //      改不到 -> 必须把那个字段从 convSig 的 drop 列表里拿掉
  ck('被排除出签名的字段，用途没变多（变多了要回去改 convSig）', () => {
    const src = fs.readFileSync(path.join(R, 'pages.js'), 'utf8');
    const live = src.split('\n')
      .filter((l) => !l.trim().startsWith('//')).join('\n');
    // 2026-09-08 查证：stage 3 处全在 convProgress / stageText；
    // stage_total 6 处全在 convProgress / stageText / cv-sbar 宽度；
    // stages 1 处只在展开步骤详情时；lines 1 处、progress_line 2 处只在
    // 日志展开时。全都是 patch 改得到、或者收着时根本不上屏的。
    const want = {
      't.stage': [/t\.stage\b/g, 3],
      't.stage_total': [/t\.stage_total\b/g, 6],
      't.stages': [/t\.stages\b/g, 1],
      't.lines': [/t\.lines\b/g, 1],
      't.progress_line': [/t\.progress_line\b/g, 2],
    };
    for (const name of Object.keys(want)) {
      const [re, n] = want[name];
      const got = (live.match(re) || []).length;
      if (got !== n) {
        throw new Error(name + ' 的用途从 ' + n + ' 处变成了 ' + got
          + ' 处 —— 回去看 app.js 的 convSig：新增那处 patch 改得到吗？'
          + '改不到的话必须把它从 drop 列表里拿掉，否则界面该变没变');
      }
    }
  });

  // 护栏：转换轮询必须走 renderConv。有人哪天顺手改回 render()，卡顿就
  // 原样回来了，而且**不会有任何测试红** —— 上面那几条测的是 renderConv
  // 自己的行为，管不着调用方用的是哪个。
  //
  // 🔴 这是源码字符串匹配，**先把注释行剔掉再匹配**：CLAUDE.md 记着
  //    「`// loadUpgPending();` 里的字符串跟活代码一模一样，源码匹配分不清
  //    活代码和注释掉的代码」—— 而这段代码的注释里正好写着 renderConv，
  //    不剔注释的话，把活代码删了这条照样绿。
  ck('转换轮询走的是增量刷新，不是整页重绘', () => {
    const src = fs.readFileSync(path.join(R, 'actions.js'), 'utf8');
    const live = src.split('\n')
      .filter((l) => !l.trim().startsWith('//')).join('\n');
    const at = live.indexOf('function poll()');
    if (at < 0) throw new Error('找不到转换轮询 poll()');
    // 只截 poll 这一个函数。**不能按固定字符数截**，也不能找「下一个顶层
    // function」—— poll 后面跟的全是 `xxx: function` 这种对象方法，没有
    // 顶层函数声明，一路截到文件尾会扫进别人正常的 render()，误报。
    // poll 缩进两格，它的收尾就是行首两个空格的 `}`；内部所有闭合都比这更深。
    const end = live.indexOf('\n  }', at);
    const seg = live.slice(at, end < 0 ? live.length : end);
    if (!/renderConv\(\)/.test(seg)) {
      throw new Error('转换轮询没走 renderConv —— 每秒重建整页，滚动又要被拽');
    }
    if (/(^|[^a-zA-Z])render\(\)/m.test(seg.replace(/renderConv\(\)/g, ''))) {
      throw new Error('转换轮询里还留着整页重绘的 render()');
    }
  });

  ck('每种状态都吐完整三段，主区才能铺满', () => {
    const cases = {
      '正常空态': ready(sb),
      '后端连不上': Object.assign(ready(sb), { envError: '连接被拒绝' }),
      '还在自检': baseState(sb),
      '显卡拦截': Object.assign(ready(sb), {
        env: Object.assign(goodEnv(), { gpu: { ok: false, why: '慢约 2 倍' } }) }),
      '引擎缺失': Object.assign(ready(sb), {
        env: Object.assign(goodEnv(), { mineru: { ok: false } }) }),
    };
    for (const [name, st] of Object.entries(cases)) {
      if (!segs(sb.window.P2W_PAGES.main(st))) throw new Error(name + ' 少了段');
    }
  });
}

console.log('\n\u73af\u5883\u81ea\u68c0\uff08\u72b6\u6001\u680f + \u62e6\u622a\uff09\uff1a');
{
  const sb = mkSandbox();
  const fn = sb.window.P2W_PAGES.main;

  ck('还在检查时状态栏有话说', () => {
    const h = fn(baseState(sb));
    if (!h.includes('正在检查这台电脑')) throw new Error('自检中一声不吭');
  });

  ck('连不上后端时把原因原样显示', () => {
    const st = ready(sb);
    st.envError = 'ECONNREFUSED 127.0.0.1:8731';
    const h = fn(st);
    if (!h.includes('ECONNREFUSED 127.0.0.1:8731')) throw new Error('没把原因显示出来');
    if (!h.includes('data-act="reload"')) throw new Error('没给重试的路');
  });

  ck('显卡够用时不拦，直接就是主界面', () => {
    const h = fn(ready(sb));
    if (h.includes('data-act="ackGate"')) throw new Error('显卡没问题却拦了');
    if (!h.includes('把 PDF 拖进来')) throw new Error('没进到主界面');
  });

  ck('显卡不够时拦住，让用户自己选退出还是继续', () => {
    const st = ready(sb);
    st.env.gpu = { ok: false,
      why: '这台电脑没有独立显卡，会用 CPU 转换 —— 实测比显卡慢约 2 倍：10 页的讲义约 8 分钟。' };
    const h = fn(st);
    if (!h.includes('data-act="ackGate"')) throw new Error('没给「仍然继续」');
    if (!h.includes('data-act="quit"')) throw new Error('没给「退出」');
    // 话术必须带具体分钟数 ——「会慢很多」等于没说，gpu.py 那边有同样的钉子
    if (!h.includes('8 分钟')) throw new Error('没把具体耗时告诉用户');
  });

  ck('选了「仍然继续」之后不再拦第二次', () => {
    const st = ready(sb);
    st.env.gpu = { ok: false, why: '慢约 2 倍' };
    st.gateAck = true;
    const h = fn(st);
    if (h.includes('data-act="ackGate"')) throw new Error('确认过还拦');
    if (!h.includes('把 PDF 拖进来')) throw new Error('放行了却没进主界面');
    if (!h.includes('显卡 ✗')) throw new Error('状态栏没留记号，人会忘了自己在硬来');
  });

  ck('公式引擎缺失时拦住，并说清楚该怎么办', () => {
    // 2026-09-01 小蔡改定：XSL 是硬性要求，不再降级到 Pandoc。
    // 🔴 2026-09-09 大改：XSL 随包分发（runtime/xsl/），node 本来就随包，
    //    所以走到这一屏**只可能是安装包缺东西**（多半被杀软删了），
    //    不再是「你没装 Office」。这一屏是被拦下来的老师唯一能看到的解释。
    const st = ready(sb);
    st.env.office = { ok: false };
    st.env.formula = { ok: false,
      why: '公式转换要用的文件不见了：安装目录下的 runtime/xsl/MML2OMML.XSL。'
         + '通常是被杀毒软件删掉了。把安装包重新解压一次即可；'
         + '如果反复被删，把安装目录加进杀软白名单。' };
    const h = fn(st);
    if (h.includes('把 PDF 拖进来')) throw new Error('公式引擎缺了却放行了');
    if (!h.includes('安装包不完整')) throw new Error('没说清楚这是什么问题');
    if (!h.includes('MML2OMML')) throw new Error('没说缺的是哪个文件');
    if (!h.includes('杀毒软件')) throw new Error('没说最可能的原因和解法');
    if (!h.includes('data-act="reload"')) throw new Error('修好之后没法重新检查');
    // 🔴 反向断言：**不许再劝人去装 Office**。文件已经随软件分发了，
    //    装 Office 解决不了「安装目录下的文件被删」，那是错的引导。
    if (h.includes('需要先安装微软 Office')) throw new Error('还在劝人装 Office');
    if (h.includes('data-act="openOffice"')) throw new Error('还留着去微软官网的按钮');
  });

  ck('显卡不够的人，先问显卡再谈下模型', () => {
    // 顺序反了的话：启动 → 直接进选源屏 → 让他张罗下 4.6 GB →
    // 回主界面 → 「显卡不满足，要不要退出」。该问的话要问在花时间之前。
    const sb2 = mkSandbox();
    const st = sb2.window.P2W_STATE;
    st.envLoading = false;
    st.env = Object.assign(goodEnv(), {
      gpu: { ok: false, why: '没有独立显卡，慢约 2 倍：10 页约 8 分钟。' },
      models: { ok: false, dir: 'D:\\app\\models', bytes: 0 },
    });
    if (!sb2.window.P2W_BLOCKED(st)) {
      throw new Error('显卡没过却不算 blocked —— 会先让人去下 4.6 GB');
    }
    // 点过「仍然继续」之后，才轮到去下模型
    sb2.window.P2W_ACTS.ackGate();
    if (st.page !== 'model') throw new Error('放行之后没接着去下模型，用户会停在没模型的主界面');
  });

  ck('缺 node 时给的是 nodejs.org，不是「重装一次」', () => {
    // 「重新安装一次应该能解决」是句错话：setup_env.py 根本不装 node，
    // 重装我们的软件不会带来它。说一句解决不了问题的话比不说更糟。
    const st = ready(sb);
    st.env.node = { ok: false };
    st.env.formula = { ok: false,
      why: '缺少 Node.js —— 公式的第一步转换要用到它，而这台电脑上没有。'
         + '到 nodejs.org 装一个「LTS」版本（一路下一步即可），装完回来点「重新检查」。' };
    const h = fn(st);
    if (!h.includes('nodejs.org')) throw new Error('没告诉用户去哪装');
    if (!h.includes('data-act="openNode"')) throw new Error('没给下载入口');
    if (h.includes('重新安装一次')) throw new Error('还在说那句解决不了问题的错话');
  });

  ck('缺 node 时说是安装包的问题，别让用户去装 Office', () => {
    // Office 有、node 没有 —— 这是我们打包漏了东西，不该让用户背锅
    const st = ready(sb);
    st.env.node = { ok: false };
    st.env.formula = { ok: false, why: '缺少 Node.js 运行环境 —— 公式的第一步转换要用到它。' };
    const h = fn(st);
    if (h.includes('data-act="openOffice"')) throw new Error('Office 明明有，还让人去装');
    if (!h.includes('Node')) throw new Error('没说清楚缺的是什么');
  });

  ck('安装目录不可写时拦住，并告诉用户挪到哪', () => {
    // 「所有文件留在安装文件夹」的代价：装进 Program Files 就没法用。
    // 必须开门就说，不能等用户拖完 PDF 点了转换才报权限错。
    const st = ready(sb);
    st.env.writable = { ok: false, dir: 'C:\\Program Files\\pdf2word' };
    const h = fn(st);
    if (h.includes('把 PDF 拖进来')) throw new Error('写不了盘却放行了');
    if (!h.includes('不能写文件')) throw new Error('没说清楚问题');
    if (!h.includes('Program Files')) throw new Error('没说清楚不能装在哪');
    if (!h.includes('C:\\Program Files\\pdf2word')) throw new Error('没显示当前位置');
  });

  ck('转换引擎缺失时拦死，且没有「仍然继续」', () => {
    const st = ready(sb);
    st.env.mineru = { ok: false };
    const h = fn(st);
    if (h.includes('data-act="ackGate"')) throw new Error('引擎都没有还能继续？');
    if (!h.includes('转换引擎还没装好')) throw new Error('没说清楚缺什么');
  });

  ck('actions 里没有编造的名字（不存在的全局函数）', () => {
    // 🔴 2026-09-02 栽过：installVcRedist 里写了 window.P2W_RELOAD_ENV()
    //    和裸的 post()/get()，三个都不存在。点下去 JS 直接抛异常，
    //    轮询没启动，界面永远停在「正在装」。
    //
    //    前端检查只看 mainArea() 吐的 HTML，从不执行 action 里的代码，
    // actions.js 里读到的每个 window.P2W_xxx，app.js 都得真的定义过。
    //
    // 🔴 这条为什么存在：2026-09-02 我在 installVcRedist 里写了
    //    window.P2W_RELOAD_ENV()，那个东西根本不存在。点下去 JS 抛异常，
    //    轮询没启动，界面永远停在「正在装」—— 小蔡看到的空进度条就是它。
    //    前端检查只看 mainArea() 吐的 HTML，从不执行 action 里的代码。
    //
    //    第一版这条检查自己写崩了两次（正则回溯、转义写出个退格符），
    //    比被测的代码还容易错。现在改成最笨的写法：按 'window.P2W_'
    //    切开，每段取开头那串大写字母，不玩花的。
    const rd = (f) => require('fs').readFileSync(
      require('path').join(__dirname, '..', 'app', 'renderer', f), 'utf8');
    const pick = (txt) => txt.split('window.P2W_').slice(1)
      .map(part => 'P2W_' + (part.match(/^[A-Z_]+/) || [''])[0]);
    // 注释行先剔掉 —— 上面那段说明里就写着 window.P2W_RELOAD_ENV
    // 当反面教材，扫到它是误报。
    const nocomment = (t) => t.split('\n').filter(
      (l) => !l.trim().startsWith('//')).join('\n');
    const src = nocomment(rd('actions.js'));
    const known = pick(nocomment(rd('app.js')));
    for (const nm of pick(src)) {
      // actions.js 末尾有 window.P2W_ACTS = ...，那是它自己往外给的
      if (src.includes('window.' + nm + ' =')) continue;
      if (!known.includes(nm)) throw new Error('用了不存在的全局 ' + nm);
    }
    // 裸的 post( / get( —— 这个文件里正确的是 HTTP.post / HTTP.get
    if (/[^.\w]post\(/.test(src.replace(/HTTP\.post\(/g, 'X(')))
      throw new Error('有裸的 post()，应该是 HTTP.post()');
    if (/[^.\w]get\(/.test(src.replace(/HTTP\.get\(/g, 'X(')))
      throw new Error('有裸的 get()，应该是 HTTP.get()');
  });

  ck('装 C++ 运行库时不摆没意义的进度条', () => {
    // vc_redist 有自己的进度界面，我们这边看不见它的进度。
    const st = ready(sb);
    st.env.vcredist = { ok: false };
    st.vcBusy = true;
    st.vcDl = { installing: true, cmd: 'vc_redist.x64.exe /install' };
    const h = fn(st);
    if (/class="bar"|width:\s*\d+%/.test(h))
      throw new Error('安装阶段还摆着进度条');
    if (!h.includes('点「是」')) throw new Error('没告诉用户要点权限确认');
  });

  ck('缺 C++ 运行库时拦住，而且排在 GPU 运行库前面', () => {
    // 小蔡 2026-09-02 真机上踩的：自检显示「显卡 ✓ Office ✓」看着一切
    // 正常，点「现在就装」下完 2.8 GB 才发现装不上，卸掉、退回同一屏。
    // 他的原话：「不是一整个必须第一个装！不要排在 gpu 库后面好吗！」
    const st = ready(sb);
    st.env.vcredist = { ok: false };
    st.env.cuda_torch = { ok: false, why: '还没装 GPU 运行库（PyTorch）。' };
    const h = fn(st);
    if (!h.includes('Visual C++')) throw new Error('没拦在 C++ 运行库这一屏');
    if (h.includes('data-act="installGpuLib"'))
      throw new Error('拦成了 GPU 运行库那屏，顺序反了');
    if (!h.includes('25 MB')) throw new Error('没说要下多大');
    if (!h.includes('data-act="installVcRedist"'))
      throw new Error('没给一键安装的按钮');
  });

  ck('C++ 运行库齐了才轮到 GPU 运行库那一屏', () => {
    const st = ready(sb);
    st.env.vcredist = { ok: true };
    st.env.cuda_torch = { ok: false, why: '还没装 GPU 运行库（PyTorch）。' };
    const h = fn(st);
    if (!h.includes('2.8 GB')) throw new Error('该显示 GPU 运行库那一屏了');
  });

  ck('缺 C++ 运行库那屏不给「现在就装」，免得又白下一趟', () => {
    const st = ready(sb);
    st.env.vcredist = { ok: false };
    const h = fn(st);
    if (h.includes('data-act="installGpuLib"'))
      throw new Error('这屏不该给装 GPU 运行库的按钮');
  });

  ck('缺 GPU 运行库时拦住，并给一键安装', () => {
    // 小蔡 2026-09-02 定「只用 GPU」。发行版里不带 CUDA 版 torch
    //（解压后 4.2 GB，打进安装包会顶到 GitHub 单文件 2 GiB 上限），
    // 所以首启要下。这一屏是那条路的入口。
    const st = ready(sb);
    st.env.cuda_torch = { ok: false,
      why: '装的是 CPU 版 PyTorch（2.13.0+cpu），用不了显卡。' };
    const h = fn(st);
    if (!h.includes('GPU 运行库')) throw new Error('没说缺什么');
    if (!h.includes('CPU 版 PyTorch')) throw new Error('没把后端给的原因显示出来');
    if (!h.includes('data-act="installGpuLib"')) throw new Error('没有安装按钮');
    if (!h.includes('2.8 GB')) throw new Error('没说要下多大，用户没法决定现在装还是等会儿');
  });

  ck('装 GPU 运行库时显示进度条、命令和滚动日志', () => {
    // 小蔡 2026-09-02：「下载任何文件都应该显示一个进度条，并且要弹出
    // 背后的命令，这样下载的人才可以知道完整的进度，而不是黑盒。」
    // 他那次就是靠界面给的日志路径去翻文件才找到原因的 —— 说明光给
    // 路径不够，日志得直接摆在界面上。
    const st = ready(sb);
    st.env.cuda_torch = { ok: false, why: '还没装 GPU 运行库（PyTorch）。' };
    st.gpuLibBusy = true;
    st.gpuLib = {
      got: 1.4 * 1024 * 1024 * 1024, total: 2.8 * 1024 * 1024 * 1024,
      cmd: 'python.exe -m pip install --upgrade torch torchvision '
         + '--index-url https://download.pytorch.org/whl/cu128',
      lines: ['Collecting torch',
              'Downloading torch-2.11.0+cu128-win_amd64.whl (2753.2 MB)'],
      log: 'D:\\PDF2Word\\logs\\torch_install.log',
    };
    const h = fn(st);
    if (!h.includes('正在装')) throw new Error('没说在装');
    if (!h.includes('50%')) throw new Error('没显示百分比');
    if (!h.includes('Downloading torch')) throw new Error('没显示 pip 的输出，看着像卡死了');
    if (!h.includes('pip install')) throw new Error('没显示跑的是哪条命令');
    if (!h.includes('torch_install.log')) throw new Error('没给完整日志的路径');
    if (h.includes('data-act="installGpuLib"')) throw new Error('装着还能再点一次');
  });

  ck('装失败时按错误原因给对应的出路按钮', () => {
    // 「陌生人的电脑」上没人会去翻日志，所以错误里说缺什么，
    // 就得同时把那个东西的下载入口摆出来 —— 不能让老师自己去搜
    //「vc运行库」，搜出来前几条常常是第三方打包站。
    const st = ready(sb);
    st.env.cuda_torch = { ok: false, why: '还没装 GPU 运行库（PyTorch）。' };
    st.gpuLibError = '缺少 Visual C++ 运行库（少了 vcruntime140.dll），'
                   + 'GPU 运行库加载不了。';
    let h = fn(st);
    if (!h.includes('data-act="openVcRedist"')) throw new Error('说缺运行库却不给下载入口');
    if (h.includes('data-act="openDriver"')) throw new Error('跟驱动无关却给了驱动按钮');

    st.gpuLibError = '显卡驱动是 470.05，撑不起这版 GPU 运行库（需要 570 以上）。';
    h = fn(st);
    if (!h.includes('data-act="openDriver"')) throw new Error('说驱动旧却不给更新入口');
  });

  ck('缺 GPU 运行库排在显卡不达标前面', () => {
    // 两个都不满足时先说运行库 —— 那是用户自己能解决的，
    // 显卡不行是没办法的事。先让人做能做的那件。
    const st = ready(sb);
    st.env.cuda_torch = { ok: false, why: '还没装 GPU 运行库（PyTorch）。' };
    st.env.gpu = { ok: false, why: '这台电脑没有 NVIDIA 独立显卡。' };
    const h = fn(st);
    if (!h.includes('GPU 运行库')) throw new Error('先弹的是显卡那屏');
  });

  ck('引擎缺失屏不许指向发行版里不存在的文件', () => {
    // 🔴 这条测试原来反着写：它**要求**出现 setup_env，而发行版根目录里
    //    既没有 README.md 也没有 tools/（build_release.py 的 CODE 清单里
    //    就没打包它们）。老师撞上这屏 —— 还是新用户最容易撞的一屏 ——
    //    看到的是一句指向不存在文件的说明，彻底卡死。
    //    测试绿着，因为它断言的是开发者眼里的项目，不是用户拿到的包。
    const st = ready(sb);
    st.env.mineru = { ok: false };
    const h = fn(st);
    if (/setup_env/.test(h)) throw new Error('指向了发行版里没有的 tools\setup_env.py');
    if (/README/.test(h)) throw new Error('指向了发行版里没有的 README');
    if (!h.includes('重新解压')) throw new Error('没告诉人具体该干什么');
  });
}

console.log('\n\u9009\u4e66\uff08\u4e3b\u5c4f\u5f85\u8f6c\u6001\uff09\uff1a');
{
  const sb = mkSandbox();
  const fn = sb.window.P2W_PAGES.main;

  ck('空态给拖放区和两个入口', () => {
    const h = fn(ready(sb));
    if (!h.includes('把 PDF 拖进来')) throw new Error('没提示拖放');
    if (!h.includes('data-act="pickFiles"')) throw new Error('没有选文件');
    if (!h.includes('data-act="pickDir"')) throw new Error('没有选文件夹');
  });

  ck('正在读文件夹时必须说话', () => {
    // 实测 456 份的讲义库要 16 秒。这期间界面一个字不变的话，
    // 用户只会以为软件卡死了 ——「反应很慢」的抱怨多半来自这里。
    const st = ready(sb);
    st.scanning = true;
    const h = fn(st);
    if (!h.includes('正在读取')) throw new Error('扫描时一声不吭');
    if (!segs(h)) throw new Error('结构不全');
  });

  ck('追加文件时说明已有的不受影响', () => {
    const st = ready(sb);
    st.items = [{ path: 'C:\\a\\x.pdf', ok: true, pages: 5, scan_pages: [] }];
    st.scanning = true;
    const h = fn(st);
    if (!h.includes('1 份不受影响')) throw new Error('没说清楚已有文件的去向');
  });

  ck('空态也铺满窗口，不是居中的小方框', () => {
    const h = fn(ready(sb));
    if (!h.includes('class="fill')) throw new Error('空态没铺满');
  });

  ck('拖动时落点有反馈', () => {
    const st = ready(sb);
    st.dragging = true;
    const h = fn(st);
    if (!h.includes('松手就行')) throw new Error('拖动时没反馈');
    if (!h.includes('fill drop')) throw new Error('没有落点高亮');
  });

  ck('列表把每份的页数显示出来', () => {
    const st = ready(sb);
    st.items = [
      { path: 'C:\\a\\解不等式.pdf', ok: true, pages: 10, scan_pages: [] },
      { path: 'C:\\a\\电场.pdf', ok: true, pages: 23, scan_pages: [] },
    ];
    const h = fn(st);
    if (!h.includes('解不等式.pdf')) throw new Error('没显示文件名');
    if (!h.includes('10 页')) throw new Error('没显示页数');
    if (!h.includes('23 页')) throw new Error('第二份的页数没显示');
    if (!h.includes('选中 2 份')) throw new Error('状态栏没统计份数');
    if (!h.includes('33 页')) throw new Error('状态栏没合计页数');
  });

  ck('无文字层的页要标出来', () => {
    const st = ready(sb);
    st.items = [{ path: 'C:\\a\\x.pdf', ok: true, pages: 20, scan_pages: [3, 7, 9] }];
    const h = fn(st);
    if (!h.includes('第 3、7、9 页没有文字层')) throw new Error('没标出扫描页');
  });

  ck('整份没文字层时说人话', () => {
    const st = ready(sb);
    st.items = [{ path: 'C:\\a\\x.pdf', ok: true, pages: 3, scan_pages: [1, 2, 3] }];
    const h = fn(st);
    if (!h.includes('整份没有文字层')) throw new Error('没说人话');
  });

  ck('坏文件显示原因而不是消失', () => {
    const st = ready(sb);
    st.items = [
      { path: 'C:\\a\\good.pdf', ok: true, pages: 5, scan_pages: [] },
      { path: 'C:\\a\\bad.pdf', ok: false, error: '这个 PDF 加密了，打不开' },
    ];
    const h = fn(st);
    if (!h.includes('bad.pdf')) throw new Error('坏文件消失了');
    if (!h.includes('这个 PDF 加密了，打不开')) throw new Error('没说为什么不行');
    if (!h.includes('选中 1 份')) throw new Error('坏文件不该算进选中数');
  });

  ck('没选中任何一份时不能开始', () => {
    const st = ready(sb);
    st.items = [{ path: 'C:\\a\\x.pdf', ok: true, pages: 5, scan_pages: [] }];
    st.picked['C:\\a\\x.pdf'] = false;
    const h = fn(st);
    const i = h.indexOf('data-act="start"');
    if (i < 0) throw new Error('没有开始按钮');
    if (!h.slice(i, i + 90).includes('disabled')) throw new Error('一份没选还能点开始');
  });

  ck('默认输出位置说得明白', () => {
    const st = ready(sb);
    st.items = [{ path: 'C:\\a\\x.pdf', ok: true, pages: 5, scan_pages: [] }];
    const h = fn(st);
    if (!h.includes('跟原 PDF 放在一起')) throw new Error('没说清楚放哪');
    if (!h.includes('data-act="pickOut"')) throw new Error('不能改位置');
  });

  ck('文件名里的尖括号被转义', () => {
    const st = ready(sb);
    st.items = [{ path: 'C:\\a\\<script>.pdf', ok: true, pages: 1, scan_pages: [] }];
    const h = fn(st);
    if (h.includes('<script>.pdf')) throw new Error('没转义，能注入');
    if (!h.includes('&lt;script&gt;.pdf')) throw new Error('转义结果不对');
  });
}

console.log('\n\u8f6c\u6362\uff08\u4e3b\u5c4f\u8fdb\u5ea6\u6001\uff09\uff1a');
{
  const sb = mkSandbox();
  const fn = sb.window.P2W_PAGES.main;

  // 三份书、正在转第二份的典型现场
  function runState() {
    const st = ready(sb);
    st.items = [
      { path: 'C:\\a\\一.pdf', ok: true, pages: 10, scan_pages: [] },
      { path: 'C:\\a\\二.pdf', ok: true, pages: 20, scan_pages: [] },
      { path: 'C:\\a\\三.pdf', ok: true, pages: 30, scan_pages: [] },
    ];
    st.taskId = 'abc';
    st.task = {
      state: 'running', total: 3, current: 1, current_name: '二.pdf',
      stage: '识别公式', stage_cur: 4, stage_total: 10,
      results: [{ ok: true, pdf: 'C:\\a\\一.pdf', docx: 'C:\\a\\一.docx', line: '公式 12' }],
      elapsed: 65, remain: 320,
    };
    return st;
  }

  ck('最显眼的位置是「还要多久」', () => {
    // 用户唯一关心的就是这个数。MinerU 的阶段进度答不了它：
    // 各阶段耗时差 100 倍，跑满一整条也可能只花 1 秒。
    const h = fn(runState());
    if (!h.includes('还要约 5 分 20 秒')) throw new Error('没显示剩余时间');
    const iEta = h.indexOf('还要约');
    const iStage = h.indexOf('识别公式');
    if (iEta < 0 || iStage < 0) throw new Error('内容不全');
    if (iEta > iStage) throw new Error('剩余时间排在阶段名后面，不够显眼');
    if (iEta > h.indexOf('class="main"')) throw new Error('剩余时间没在顶部条里');
  });

  ck('转换中同一张表原地变，不跳屏', () => {
    const st = runState();
    const h = fn(st);
    if (st.page !== 'main') throw new Error('转换中切走了页面');
    for (const n of ['一.docx', '二.pdf', '三.pdf']) {
      if (!h.includes(n)) throw new Error('表里少了 ' + n);
    }
    if (!h.includes('等待')) throw new Error('排队的没标出来');
  });

  ck('当前文件和阶段仍然看得到，只是降级成小字', () => {
    const h = fn(runState());
    if (!h.includes('二.pdf')) throw new Error('没显示当前文件');
    if (!h.includes('识别公式')) throw new Error('没显示当前阶段');
  });

  ck('不显示阶段内的 x/y 数字', () => {
    // 🔴 这条测试原来是反的：它**要求**显示「4/10」。
    //
    //    2026-09-02 真机把这个要求推翻了。MinerU 换阶段时总数会换单位 ——
    //    先按页（0→11），下一个阶段按检测到的文本块（0→247）。
    //    小蔡原话：「刚刚文件本来是 5/11，现在是 5/247，我无语了」。
    //    在这之前他还问过「准备版面一直是 0，这阶段真的有用吗」——
    //    同一个东西绊了他两次。
    //
    //    数字本身没算错，是它压根不该给用户看：单位在变、有些阶段不吐
    //    中间值。用户真正要的「还要多久」在顶上单独显示。
    const h = fn(runState());
    if (/\b4\/10\b/.test(h)) throw new Error('又把阶段内的 x/y 显示出来了');
    // 阶段名和那条比例进度条还得留着 —— 得让人看出「在动」
    if (!h.includes('识别公式')) throw new Error('阶段名也被删了');
  });

  ck('转换时有「日志」按钮，点开能看实时输出', () => {
    // 小蔡 2026-09-02：「你要考虑到，转换区万一一堆文件呢，提供一个
    // 日志按钮吧，可以点击看真实的实时日志。」
    // 固定占一块会挤掉文件列表 —— 转一批书的时候列表本来就长。
    const st = runState();
    st.task.lines = ['正在加载模型…', 'MFR model loaded in 12.3s'];
    st.task.log = 'D:\\PDF2Word\\logs\\convert.log';

    let h = fn(st);
    if (!h.includes('data-act="toggleLog"')) throw new Error('没有日志按钮');
    if (h.includes('MFR model loaded')) throw new Error('没点就把日志铺出来了');

    st.showLog = true;
    h = fn(st);
    if (!h.includes('MFR model loaded')) throw new Error('点开了却看不到日志');
    if (!h.includes('convert.log')) throw new Error('没给完整日志的路径');
    // 顶部的剩余时间要留着 —— 看日志时也得知道整体跑到哪了
    if (!h.includes('还要')) throw new Error('看日志时把剩余时间弄丢了');
  });

  ck('转换的状态栏不许再说「停止只在这份转完后生效」', () => {
    // 2026-09-02 起停止是当场生效的（extract._spawn 里的 watch 线程
    // 杀进程树）。留着一句过时的免责声明，比什么都不写更坏 ——
    // 用户会因此不去点那个其实管用的按钮。
    const h = fn(runState());
    if (h.includes('转完之后生效')) throw new Error('还留着过时的说明');
    if (!h.includes('data-act="cancel"')) throw new Error('停止按钮没了');
  });

  ck('只转一份时不显示「第几份」', () => {
    const st = runState();
    st.items = st.items.slice(0, 1);
    st.task.total = 1;
    st.task.current = 0;
    st.task.results = [];
    const h = fn(st);
    if (h.includes('第 1 / 1 份')) throw new Error('一份也显示了「第几份」');
  });

  ck('估不出来时说正在估算，不瞎猜一个数', () => {
    const st = runState();
    st.task.remain = null;
    const h = fn(st);
    if (!h.includes('正在估算')) throw new Error('估不出来却没说');
    if (h.includes('还要约')) throw new Error('估不出来还是给了个数');
  });

  ck('转换中能停止', () => {
    // 这条测试原来还要求显示「当前这份转完之后生效」—— 那是 2026-09-02
    // 之前的行为：取消只在两份 PDF 之间检查，只转一份的话根本不生效。
    // 小蔡真机原话：「点击停止还没用，程序一共有几个停止，都有用吗？」
    // 现在 extract._spawn 里有 watch 线程，点了当场杀进程树，
    // 那句免责声明也就跟着删了（上面有条测试专门钉它不许回来）。
    const h = fn(runState());
    if (!h.includes('data-act="cancel"')) throw new Error('没有停止按钮');
    if (!h.includes('已用 1 分 5 秒')) throw new Error('没显示已用时');
  });

  ck('任务还没回来时不空白', () => {
    const st = ready(sb);
    st.taskId = 'abc';
    st.task = null;
    st.items = [{ path: 'C:\\a\\x.pdf', ok: true, pages: 5, scan_pages: [] }];
    const h = fn(st);
    if (!segs(h)) throw new Error('结构不全');
    if (!h.includes('x.pdf')) throw new Error('白屏了');
  });

  ck('完成后每份都能打开', () => {
    const st = runState();
    st.items = st.items.slice(0, 1);
    st.task = {
      state: 'done', total: 1, current: 1, elapsed: 240, remain: 0,
      results: [{ ok: true, pdf: 'C:\\a\\一.pdf', docx: 'C:\\a\\一.docx',
                  line: '公式 213 · 表格 2' }],
    };
    const h = fn(st);
    if (!h.includes('转换完成')) throw new Error('没说完成');
    if (!h.includes('data-act="openFile"')) throw new Error('不能打开文件');
    if (!h.includes('data-act="openPath"')) throw new Error('不能打开文件夹');
    if (!h.includes('公式 213')) throw new Error('没显示转换结果摘要');
    if (!h.includes('data-act="newBatch"')) throw new Error('没有再转一批');
  });

  ck('失败的那份显示原因，不是干瞪眼', () => {
    const st = runState();
    st.items = st.items.slice(0, 1);
    st.task = {
      state: 'done', total: 1, current: 1, elapsed: 30, remain: 0,
      results: [{ ok: false, pdf: 'C:\\a\\一.pdf', error: 'MinerU 提取失败：显存不够' }],
    };
    const h = fn(st);
    if (!h.includes('MinerU 提取失败：显存不够')) throw new Error('没说失败原因');
  });

  ck('一份失败一份成功要分别显示', () => {
    const st = runState();
    st.items = st.items.slice(0, 2);
    st.task = {
      state: 'done', total: 2, current: 2, elapsed: 90, remain: 0,
      results: [
        { ok: true, pdf: 'C:\\a\\一.pdf', docx: 'C:\\a\\一.docx', line: '公式 12' },
        { ok: false, pdf: 'C:\\a\\二.pdf', error: '这份加密了' },
      ],
    };
    const h = fn(st);
    if (!h.includes('一.docx')) throw new Error('成功的没显示');
    if (!h.includes('这份加密了')) throw new Error('失败的没显示');
    if (!h.includes('成功 1 份')) throw new Error('状态栏没分开统计');
    if (!h.includes('失败 1 份')) throw new Error('状态栏没报失败数');
  });

  ck('停止之后说的是已停止而不是已完成', () => {
    const st = runState();
    st.items = st.items.slice(0, 1);
    st.task = {
      state: 'cancelled', total: 1, current: 1, elapsed: 50, remain: 0,
      results: [{ ok: true, pdf: 'C:\\a\\一.pdf', docx: 'C:\\a\\一.docx', line: '' }],
    };
    const h = fn(st);
    if (!h.includes('已停止')) throw new Error('没说已停止');
    if (h.includes('转换完成')) throw new Error('停止了却说完成');
  });

  ck('「再转一批」把成功的移出列表、失败的自动勾上', () => {
    // 🔴 2026-09-09 改的：成功的**移出去**，不再是留着取消勾选。
    //    留着的话它跟没转过的长得一模一样（都是没勾的白底行），用户
    //    看到的是「已经转完的还堵在队列里」。Word 都出来了，留着没用。
    //    失败的留下并勾好，点一下开始就是重试 —— 这才是这个按钮的价值。
    const sb2 = mkSandbox();
    const real = sb2.window.P2W_STATE;
    real.items = [
      { path: 'C:\\a\\好.pdf', ok: true, pages: 5, scan_pages: [] },
      { path: 'C:\\a\\坏.pdf', ok: true, pages: 5, scan_pages: [] },
    ];
    real.taskId = 'abc';
    real.task = { state: 'done', results: [
      { ok: true, pdf: 'C:\\a\\好.pdf', docx: 'C:\\a\\好.docx' },
      { ok: false, pdf: 'C:\\a\\坏.pdf', error: '炸了' },
    ] };
    sb2.window.P2W_ACTS.newBatch();
    if (real.task !== null) throw new Error('任务没清掉');
    if (real.taskId !== '') throw new Error('taskId 没清掉');
    if (real.items.some((x) => x.path === 'C:\\a\\好.pdf')) {
      throw new Error('转成功的没移走，还堵在待转清单里');
    }
    if (!real.items.some((x) => x.path === 'C:\\a\\坏.pdf')) {
      throw new Error('转失败的被移走了，没法一键重试');
    }
    if (real.picked['C:\\a\\坏.pdf'] !== true) throw new Error('失败的没勾上');
    // 🔴 移走的同时必须删掉 picked 的键。picked 是三态，**键不存在才
    //    等于选中**，而本项目的 addPaths 不给新文件设 picked（靠的就是
    //    这一条）。留着那个 false 的话，同一份 PDF 再拖进来会默认不勾。
    if ('C:\\a\\好.pdf' in real.picked) {
      throw new Error('移走了却把 picked 的键留着，再拖进来会默认不勾');
    }
  });

  ck('「再转一批」不许碰这批根本没转过的文件', () => {
    // 🔴 有了待办队列之后，items 里会混着**没转过**的文件：摁停止时并回来
    //    的待办、晋升时 start() 失败留下的那批。原来一律按 results 重设
    //    勾选，它们不在 results 里 → 全被取消勾选 —— 用户看到的是
    //    「软件把我刚拖进来的东西吃了」。
    const sb2 = mkSandbox();
    const real = sb2.window.P2W_STATE;
    real.items = [
      { path: 'C:\\a\\转过的.pdf', ok: true, pages: 5, scan_pages: [] },
      { path: 'C:\\a\\刚拖进来的.pdf', ok: true, pages: 5, scan_pages: [] },
    ];
    real.picked = { 'C:\\a\\转过的.pdf': true, 'C:\\a\\刚拖进来的.pdf': true };
    real.taskId = 'abc';
    real.task = { state: 'done', results: [
      { ok: true, pdf: 'C:\\a\\转过的.pdf', docx: 'C:\\a\\转过的.docx' },
    ] };
    sb2.window.P2W_ACTS.newBatch();
    // 转过且成功的**移出列表**（2026-09-09 起），没转过的原样不动。
    if (real.items.some((x) => x.path === 'C:\\a\\转过的.pdf')) {
      throw new Error('转过且成功的该移出列表');
    }
    if (!real.items.some((x) => x.path === 'C:\\a\\刚拖进来的.pdf')) {
      throw new Error('没转过的文件被移走了 —— 用户会以为文件被吃了');
    }
    if (real.picked['C:\\a\\刚拖进来的.pdf'] !== true)
      throw new Error('没转过的文件被取消勾选了 —— 用户会以为文件被吃了');
  });

  ck('「再转一批」要把上一批的报告清掉，否则会隔着一批串味', () => {
    // A 晋升 B（lastResults=A）→ B 转完 → 手动转 C → C 运行中点
    // 「上一批的报告」看到的是 A，B 整个被跳过。报告是拿去核对 Word 的，
    // 指错批次等于指错文件。
    const sb2 = mkSandbox();
    const real = sb2.window.P2W_STATE;
    real.items = [];
    real.taskId = 'abc';
    real.task = { state: 'done', results: [] };
    real.lastResults = [{ ok: false, pdf: 'C:\\a\\上上批.pdf', error: 'x' }];
    real.showLastReport = true;
    sb2.window.P2W_ACTS.newBatch();
    if (real.lastResults !== null) throw new Error('上一批的报告没清掉，会串味');
    if (real.showLastReport !== false) throw new Error('报告开关没关');
  });
}

console.log('\n\u68c0\u67e5\u66f4\u65b0\uff1a');
{
  const sb = mkSandbox();
  const fn = sb.window.P2W_PAGES.main;

  // 2026-09-05 底栏入口从「检查更新」改成「关于」（检查更新挪进那一屏）。
  // 守护的规矩没变：**每一屏都要有自救入口**。
  ck('状态栏有关于的入口，空态和有文件时都有', () => {
    const empty = fn(ready(sb));
    if (!empty.includes('data-act="openAbout"')) throw new Error('空态没有入口');
    const st = ready(sb);
    st.items = [{ path: 'C:\\a\\x.pdf', ok: true, pages: 5, scan_pages: [] }];
    if (!fn(st).includes('data-act="openAbout"')) throw new Error('有文件时没有入口');
  });

  ck('关于页里第一个按钮就是检查更新', () => {
    // 🔴 这条是上面那条改动的**代价补偿**：入口多了一层，那么
    //    进去之后必须一眼看到自救手段，不能再藏。
    const st = ready(sb);
    st.about = 'about';
    const h = fn(st);
    if (!h.includes('data-act="checkUpdate"')) throw new Error('关于页里没有检查更新');
    if (!h.includes('data-act="openEnvCheck"')) throw new Error('关于页里没有环境检测');
  });

  ck('环境检测页：没查过上游就是破折号，不能写「已是最新」', () => {
    // 🔴 查不到和已最新是两回事，混了就是假绿灯。
    const st = ready(sb);
    st.about = 'env';
    st.diag = { versions: { torch: '2.11.0', mineru: '3.4.5' }, root: 'D:/x' };
    const h = fn(st);
    if (h.includes('已最新')) throw new Error('没查过却说已最新');
    if (!h.includes('data-act="checkDeps"')) throw new Error('没有检查上游的按钮');
  });

  ck('环境检测页有「生成诊断文件」，而且不再是复制到剪贴板', () => {
    // 2026-09-08 小蔡定：剪贴板那条路砍掉，任何情况都生成文件 ——
    // 十行粘微信还行，三百行没人看，而真出问题时要的就是那三百行。
    const st = ready(sb);
    st.about = 'env';
    st.diag = { versions: { mineru: '3.4.5' }, root: 'D:/x' };
    const h = fn(st);
    if (!h.includes('data-act="exportDiag"')) throw new Error('没有生成诊断文件的按钮');
    if (h.includes('data-act="copyDiag"')) throw new Error('剪贴板那条路还在');
  });

  ck('正在生成诊断文件时按钮禁用，防连点', () => {
    const st = ready(sb);
    st.about = 'env';
    st.diag = { versions: { mineru: '3.4.5' }, root: 'D:/x' };
    st.diagBusy = true;
    const h = fn(st);
    if (!h.includes('正在生成')) throw new Error('没给「正在生成」的反馈');
  });

  ck('有升级备份时列出来，每份都能退回', () => {
    // 🔴 回滚这条链以前整个是断的：后端 rollback 和 /api/upgrade/backups
    //    都写好了，前端一个字都没引用 —— 界面上既看不见有哪几份备份，
    //    也没有任何地方能退回去。跟「2.5 GB 下好了没人装」同形。
    const st = ready(sb);
    st.about = 'env';
    st.diag = { versions: { mineru: '3.4.5' }, root: 'D:/x' };
    st.backups = [{ name: '20260907_145754', size: 4447397185,
                    picked: ['torch'], versions: { torch: '2.11.0+cu128' } }];
    const h = fn(st);
    if (!h.includes('20260907_145754')) throw new Error('没列出备份');
    if (!h.includes('2.11.0+cu128')) throw new Error('没说退回去会变成哪个版本');
    if (!h.includes('data-act="askRollback"')) throw new Error('没有退回入口');
  });

  ck('退回要问第二遍才真退', () => {
    // 拷 4 GB 要好几分钟，误触的代价不小，值得多问一次。
    const st = ready(sb);
    st.about = 'env';
    st.diag = { versions: { mineru: '3.4.5' }, root: 'D:/x' };
    st.backups = [{ name: '20260907_145754', size: 100, versions: {} }];
    st.rollbackAsk = '20260907_145754';
    const h = fn(st);
    if (!h.includes('data-act="doRollback"')) throw new Error('确认态没出现');
    if (!h.includes('确定退回')) throw new Error('没给确认文案');
  });

  ck('转换进行中不许退回', () => {
    // 退回要删掉 site-packages 里的 torch 再拷回来，转换中做这个当场炸。
    const st = ready(sb);
    st.about = 'env';
    st.diag = { versions: { mineru: '3.4.5' }, root: 'D:/x' };
    st.backups = [{ name: '20260907_145754', size: 100, versions: {} }];
    st.task = { state: 'running' };
    const h = fn(st);
    const i = h.indexOf('data-act="askRollback"');
    if (i < 0) throw new Error('按钮不该消失，只该变灰');
    const tag = h.slice(i, h.indexOf('>', i));
    if (!tag.includes('disabled')) throw new Error('转换中还能点退回');
  });

  ck('退完了要给一个真的「立即重启」按钮', () => {
    // 🔴 以前退完只弹一句「点『立即重启』或者关掉软件重开才生效」——
    //    **而那个按钮根本不存在**，文案在指挥用户去点一个没有的东西。
    //    2026-09-08 小蔡点名要这个按钮。
    const st = ready(sb);
    st.about = 'env';
    st.diag = { versions: { mineru: '3.4.5' }, root: 'D:/x' };
    st.backups = [{ name: '20260907_145754', size: 100, versions: {} }];
    st.rollbackDone = true;
    const h = fn(st);
    if (!h.includes('data-act="restartApp"')) throw new Error('没有重启按钮');
    if (!h.includes('已经退回')) throw new Error('没说退回成功了');
  });

  ck('退完之后不再摆那张过时的备份表', () => {
    // 那份备份重启后就会被 drop_backups_of_current 收掉，还摆在那儿
    // 等于让用户对着一个即将消失的东西再点一次。
    const st = ready(sb);
    st.about = 'env';
    st.diag = { versions: { mineru: '3.4.5' }, root: 'D:/x' };
    st.backups = [{ name: '20260907_145754', size: 100, versions: {} }];
    st.rollbackDone = true;
    const h = fn(st);
    if (h.includes('data-act="askRollback"')) {
      throw new Error('退完还摆着退回按钮');
    }
  });

  ck('生成诊断文件失败时，环境检测页得把原因说出来', () => {
    // 🔴 st.err 以前全项目只在待转屏渲染，这一屏通篇没有 —— 于是三个位置
    //    都写不进去时，按钮只是从「正在生成…」闪回原样，一个字的解释都没有。
    //    一个出问题时才用的功能，自己失败还静默，性质最差。
    const st = ready(sb);
    st.about = 'env';
    st.diag = { versions: { mineru: '3.4.5' }, root: 'D:/x' };
    st.err = 'logs、安装目录、临时目录都写不进去';
    const h = fn(st);
    if (!h.includes('都写不进去')) throw new Error('失败原因没显示，用户只能干瞪眼');
  });

  ck('转换进行中，「关于」是能点的', () => {
    // 🔴 2026-09-08 改：以前转换中它是灰的，理由是进去第一个按钮就是
    //    「检查更新」。但那是**为了拦一个按钮把整间屋子锁了** —— 屋里
    //    还有版本号、环境检测、磁盘占用、生成诊断文件，转换中看全都安全，
    //    而诊断恰恰是转换卡住时最需要的那一个，以前正好进不去。
    //    保护点下移到「检查更新」自己身上（见下一条）。
    const st = ready(sb);
    st.task = { state: 'running', items: [] };
    const h = fn(st);
    if (!h.includes('data-act="openAbout"')) throw new Error('按钮被拿掉了');
    const i = h.indexOf('data-act="openAbout"');
    const tag = h.slice(i, h.indexOf('>', i));
    if (tag.includes('disabled')) throw new Error('转换中进不去关于，诊断就摸不到');
  });

  ck('转换进行中，关于页里的「检查更新」必须是灰的', () => {
    // 🔴 门开了之后，这个按钮就直接暴露在外 —— 而它是屋里最危险的一个：
    //    更新包覆盖的正是 pipeline/*.py，转到一半换掉代码，后面几份读到的
    //    是新代码；装完还要重启，一重启这批全废，而老师可能已经等了十几
    //    分钟。**以前根本没有测试盯着它**，全靠外面那道门。
    const st = ready(sb);
    st.task = { state: 'running', items: [] };
    st.about = 'about';
    const h = fn(st);
    const i = h.indexOf('data-act="checkUpdate"');
    if (i < 0) throw new Error('关于页里没有检查更新按钮');
    const tag = h.slice(i, h.indexOf('>', i));
    if (!tag.includes('disabled'))
      throw new Error('转换中还能点检查更新 —— 换掉代码会让这批全废');
  });

  ck('没在转换时关于是能点的', () => {
    const st = ready(sb);
    const h = fn(st);
    const i = h.indexOf('data-act="openAbout"');
    const tag = h.slice(i, h.indexOf('>', i));
    if (tag.includes('disabled')) throw new Error('空闲时反而点不了');
  });

  ck('已是最新时说清楚，别让人以为没查', () => {
    const st = ready(sb);
    st.upd = { ok: true, has_update: false, local: 'v1.0.0', latest: 'v1.0.0',
               error: '' };
    const h = fn(st);
    if (!h.includes('已经是最新版本')) throw new Error('没说结果');
    if (!h.includes('v1.0.0')) throw new Error('没显示当前版本');
    if (!h.includes('data-act="closeUpdate"')) throw new Error('关不掉');
  });

  ck('有新版本时显示版本号、发布日期和更新说明', () => {
    const st = ready(sb);
    st.upd = { ok: true, has_update: true, local: 'v1.0.0', latest: 'v1.1.0',
               published: '2026-09-05', error: '',
               notes: '修了剩余时间倒涨\n公式改走 XSL',
               asset: { name: 'u.zip', url: 'https://x/u.zip', size: 900000 } };
    const h = fn(st);
    if (!h.includes('有新版本 v1.1.0')) throw new Error('没显示新版本号');
    if (!h.includes('v1.0.0')) throw new Error('没显示当前版本');
    if (!h.includes('2026-09-05')) throw new Error('没显示发布日期');
    if (!h.includes('修了剩余时间倒涨')) throw new Error('没显示更新说明');
    if (!h.includes('data-act="downloadUpdate"')) throw new Error('没有下载按钮');
  });

  ck('更新说明里的尖括号要转义', () => {
    const st = ready(sb);
    st.upd = { ok: true, has_update: true, local: 'v1', latest: 'v2',
               notes: '<img src=x onerror=alert(1)>', error: '',
               asset: { name: 'u.zip', url: 'https://x/u.zip', size: 1 } };
    const h = fn(st);
    if (h.includes('<img src=x')) throw new Error('release notes 没转义，能注入');
  });

  ck('检查失败时给原因和重试，不是干瞪眼', () => {
    const st = ready(sb);
    st.upd = { ok: false, has_update: false, local: '', latest: '',
               error: '连不上 GitHub：timeout' };
    const h = fn(st);
    if (!h.includes('连不上 GitHub')) throw new Error('没显示原因');
    if (!h.includes('data-act="checkUpdate"')) throw new Error('没法重试');
  });

  // ── 线路表（2026-09-03）。检查更新原来是个黑盒：前端发一个请求然后干等，
  //    看不到试了哪些镜像、谁通谁不通。下载面板早就把命令和日志摆出来了，
  //    检查更新这条路一直是例外。
  const LINES = [
    { id: 'direct', name: 'GitHub 官方', ok: true, ms: 895, error: '', used: true },
    { id: 'gh-proxy', name: 'gh-proxy.com', ok: true, ms: 1080, error: '', used: false },
    { id: 'ghfast', name: 'ghfast.top', ok: false, ms: 1224,
      error: '403 不代理 API', used: false }
  ];

  ck('线路表默认折叠，只占一行', () => {
    const st = ready(sb);
    st.upd = { ok: true, has_update: false, local: 'v1.0.0', latest: 'v1.0.0',
               error: '', lines: LINES };
    const h = fn(st);
    if (!h.includes('经 GitHub 官方')) throw new Error('没说这次走的哪条');
    if (!h.includes('2/3 条可用')) throw new Error('没说几条通');
    if (h.includes('data-act="pickUpdLine"')) {
      throw new Error('默认就展开了，440 的高度装不下');
    }
  });

  // 🔴 pending 不等于不可用（2026-09-05）。查版本改成并发赛跑之后，
  //    第一条成功就返回，剩下几条根本没跑完，后端把它们标成 pending。
  //    前端原来只数 ok，于是六条里五条没测的被算成「不通」，界面显示
  //    「1/6 条可用」—— 小蔡升到 v0.2.1 后报「怎么只剩一条线路能用」，
  //    实际网络没变，是显示在骗人。
  //    后端当时的注释写着「界面上显示检测中」，而前端一直没读这个字段：
  //    典型的「后端加了字段、前端没接」，跟 note_run 那个 bug 同一类。
  const LINES_PENDING = [
    { id: 'direct', name: 'GitHub 官方', ok: true, ms: 1011, error: '',
      used: true, pending: false },
    { id: 'gh-proxy', name: 'gh-proxy.com', ok: false, ms: 0, error: '',
      used: false, pending: true },
    { id: 'ghfast', name: 'ghfast.top', ok: false, ms: 0, error: '',
      used: false, pending: true }
  ];

  ck('没测完的线路不算成不可用', () => {
    const st = ready(sb);
    st.upd = { ok: true, has_update: false, local: 'v1.0.0', latest: 'v1.0.0',
               error: '', lines: LINES_PENDING };
    const h = fn(st);
    if (h.includes('1/3 条可用')) {
      throw new Error('把没测完的当成了不可用 —— 用户会以为网络坏了');
    }
    if (!h.includes('2 条未测')) throw new Error('没说清楚那两条只是还没测');
  });

  ck('展开后没测完的写「未测」，不写「连不上」', () => {
    const st = ready(sb);
    st.updLinesOpen = true;
    st.upd = { ok: true, has_update: false, local: 'v1.0.0', latest: 'v1.0.0',
               error: '', lines: LINES_PENDING };
    const h = fn(st);
    if (h.includes('连不上')) {
      throw new Error('把没测完的写成「连不上」—— 那是没验证过的结论');
    }
    if (!h.includes('未测')) throw new Error('没标出「未测」');
  });

  ck('点更新之后真开始下之前，每一步都要说明在干什么', () => {
    // 🔴 这一段以前是纯黑盒：点完更新界面毫无反应，而底下可能正在等一条
    //    连不上的线路超时（urlopen timeout=30，六条最坏 180 秒）。
    //    用户只能理解成卡死 —— 小蔡从 v0.1.1 升级时就是这个体验。
    //
    //    后端把 running 细分成 checking / probing / running（_UPD['step']），
    //    这里逐个钉住：**没有哪一步是顶着「正在下载 0%」装死的**。
    const base = { ok: true, has_update: true, local: 'v1.0.0', latest: 'v1.1.0',
                   error: '', lines: LINES_PENDING,
                   asset: { name: 'x-update.zip', size: 500000, url: 'https://x/y' } };

    const st1 = ready(sb);
    st1.updBusy = true;
    st1.upd = Object.assign({}, base, { phase: 'running', step: 'checking' });
    const h1 = fn(st1);
    if (!h1.includes('正在确认最新版本')) {
      throw new Error('确认版本那几秒界面什么都不说');
    }
    if (h1.includes('正在下载')) {
      throw new Error('还没开始下就说在下载，是假状态');
    }

    const st2 = ready(sb);
    st2.updBusy = true;
    st2.upd = Object.assign({}, base, { phase: 'running', step: 'probing' });
    const h2 = fn(st2);
    if (!h2.includes('正在挑最快的线路')) {
      throw new Error('挑线路那几秒界面什么都不说，跟卡死没区别');
    }
    if (h2.includes('正在下载')) {
      throw new Error('还在测速就说在下载，是假状态');
    }

    const st3 = ready(sb);
    st3.updBusy = true;
    st3.upd = Object.assign({}, base, { phase: 'running', step: 'running',
                                        dlGot: 250000, dlTotal: 500000 });
    const h3 = fn(st3);
    if (!h3.includes('正在下载') || !h3.includes('50%')) {
      throw new Error('真在下的时候反而没有进度');
    }
  });

  ck('没测速就不许显示任何速度数字', () => {
    // 🔴 小蔡 2026-09-03 定的：**数据必须真实，没有就留空**。
    //    原来这一列默认填的是查版本的响应延迟 —— 那是另一件事，
    //    填进来等于暗示一个我们根本没测过的结论（延迟低 ≠ 下得快）。
    const st = ready(sb);
    st.updLinesOpen = true;
    st.upd = { ok: true, has_update: false, local: 'v1.0.0', latest: 'v1.0.0',
               error: '', lines: LINES };   // LINES 里只有 ms，没有 bps
    const h = fn(st);
    if (/\d\.\d 秒/.test(h)) throw new Error('把响应延迟当速度显示了');
    if (/KB\/s|MB\/s|GB\/s/.test(h)) throw new Error('没测速却有速度数字');
    if (!h.includes('—')) throw new Error('空值没画出来');
    if (!h.includes('没测过，所以是空的')) throw new Error('没解释这列为什么空');
  });

  ck('测速之后显示实测字节率', () => {
    const st = ready(sb);
    st.updLinesOpen = true;
    st.upd = { ok: true, has_update: false, local: 'v1.0.0', latest: 'v1.0.0',
               error: '', lines: [
                 { id: 'gh-proxy', name: 'gh-proxy.com', ok: true, ms: 0,
                   bps: 249907, error: '', used: true },
                 { id: 'direct', name: 'GitHub 官方', ok: true, ms: 0,
                   bps: 190946, error: '', used: false }] };
    const h = fn(st);
    if (!h.includes('KB/s')) throw new Error('测了却不显示速度');
    if (!h.includes('数字是刚才实测的')) throw new Error('没说明数字的来源');
  });

  ck('展开就能测速，不用等到有新版本', () => {
    const st = ready(sb);
    st.updLinesOpen = true;
    st.upd = { ok: true, has_update: false, local: 'v1.0.0', latest: 'v1.0.0',
               error: '', lines: LINES };
    const h = fn(st);
    if (!h.includes('data-act="probeUpdSpeed"')) throw new Error('没有测速按钮');
  });

  ck('默认状态下有且只有一个选中项', () => {
    // 🔴 2026-09-03：渲染出来才看见「自动」和「本次采用的那条」两个
    //    圆点同时亮着 —— 「你选了谁」和「这次用了谁」被混成了一个状态。
    //    上面那几条断言全绿也没抓到，因为它们查的是「有没有」不是「对不对」。
    const st = ready(sb);
    st.updLinesOpen = true;
    st.upd = { ok: true, has_update: false, local: 'v1.0.0', latest: 'v1.0.0',
               error: '', lines: LINES };
    const h = fn(st);
    const n = (h.match(/ checked/g) || []).length;
    if (n !== 1) throw new Error('选中了 ' + n + ' 个，radio 只该亮一个');
    // 没手动选时，亮的那个必须是「自动」
    if (h.split('自动（用最快的）')[0].lastIndexOf('checked') < 0) {
      throw new Error('默认亮的不是「自动」');
    }
  });

  ck('展开后每条线路都在，挂了的写明原因', () => {
    const st = ready(sb);
    st.updLinesOpen = true;
    st.upd = { ok: true, has_update: false, local: 'v1.0.0', latest: 'v1.0.0',
               error: '', lines: LINES };
    const h = fn(st);
    if (!h.includes('gh-proxy.com')) throw new Error('少了线路');
    if (!h.includes('403 不代理 API')) throw new Error('挂了却不说为什么');
    if (!h.includes('自动（用最快的）')) throw new Error('没有回到自动的入口');
  });

  ck('查更新失败那屏更要有线路表', () => {
    // 「连不上 GitHub」这句没有任何可操作性；「三条里两条超时、一条 403」
    // 才能让人判断到底是断网还是被墙。
    const st = ready(sb);
    st.updLinesOpen = true;
    st.upd = { ok: false, has_update: false, local: 'v1', latest: '',
               error: '连不上 GitHub（直连和几个镜像都试过了）',
               lines: LINES.map(function (x) {
                 return { id: x.id, name: x.name, ok: false, ms: x.ms,
                          error: x.error || '超时', used: false };
               }) };
    const h = fn(st);
    if (!h.includes('3 条线路全部失败')) throw new Error('没汇总失败情况');
    if (!h.includes('超时')) throw new Error('没逐条说原因');
  });

  ck('手动指定线路后选中状态跟着走', () => {
    const st = ready(sb);
    st.updLinesOpen = true;
    st.updPick = 'gh-proxy';
    st.upd = { ok: true, has_update: false, local: 'v1.0.0', latest: 'v1.0.0',
               error: '', lines: LINES };
    const h = fn(st);
    const seg = h.split('gh-proxy.com')[0];
    if (seg.lastIndexOf('class="it on"') < seg.lastIndexOf('class="it"')) {
      throw new Error('选了 gh-proxy 却没高亮它');
    }
    if (h.split('自动（用最快的）')[0].indexOf('class="it on"') >
        h.indexOf('gh-proxy.com')) {
      throw new Error('「自动」还占着选中态');
    }
  });

  ck('线路名要转义，别被镜像名注入', () => {
    const st = ready(sb);
    st.updLinesOpen = true;
    st.upd = { ok: true, has_update: false, local: 'v1', latest: 'v1', error: '',
               lines: [{ id: 'x', name: '<img src=x onerror=alert(1)>', ok: true,
                         ms: 100, error: '', used: true }] };
    const h = fn(st);
    if (h.includes('<img src=x')) throw new Error('线路名没转义，能注入');
  });

  ck('没有线路数据时不画空表', () => {
    // 老前端拿到的旧结构里没有 lines —— 更新包只覆盖 .py 和 .js，
    // 用户手上那份 index.html 是旧是新取决于他更新过几次。
    const st = ready(sb);
    st.upd = { ok: true, has_update: false, local: 'v1.0.0', latest: 'v1.0.0',
               error: '' };
    const h = fn(st);
    if (h.includes('线路 ')) throw new Error('没数据却画了折叠行');
    if (!h.includes('已经是最新版本')) throw new Error('把正文也搞没了');
  });

  ck('仓库还没发布过版本时说人话', () => {
    const st = ready(sb);
    st.upd = { ok: true, has_update: false, local: '(未知)', latest: '',
               error: '仓库里还没有发布任何版本' };
    const h = fn(st);
    if (!h.includes('仓库里还没有发布任何版本')) throw new Error('没照实说');
    if (h.includes('暂时没法检查更新')) throw new Error('查成功了却说成检查失败');
  });

  ck('本地比远端新时不许说成「没法检查更新」', () => {
    // check() 有三种 ok=true 却带 error 的**正常**结果（本地更新、
    // 仓库没发过版本、version.json 缺失）。原来的分支写成
    // `if (!u.ok || u.error)`，这三种全被塞进错误分支 ——
    // 标题「暂时没法检查更新」和正文「本地版本比仓库里的还新」自相矛盾。
    const st = ready(sb);
    st.upd = { ok: true, has_update: false, local: 'v0.0.3', latest: 'v0.0.1',
               error: '本地版本（v0.0.3）比仓库里的（v0.0.1）还新，不用更新' };
    const h = fn(st);
    if (h.includes('暂时没法检查更新')) throw new Error('查成功了却说成检查失败');
    if (!h.includes('比仓库里的')) throw new Error('没把原因照实说出来');
  });

  ck('真连不上时才说「没法检查更新」，并给重试', () => {
    const st = ready(sb);
    st.upd = { ok: false, has_update: false, local: 'v1', latest: '',
               error: '连不上 GitHub：timed out' };
    const h = fn(st);
    if (!h.includes('暂时没法检查更新')) throw new Error('真失败了却没说');
    if (!h.includes('data-act="checkUpdate"')) throw new Error('没法重试');
  });

  ck('依赖不满足时让人去下完整包，不是硬更新', () => {
    // 小蔡 2026-09-02：「一个人手里有旧版本，github 上比他快 30 个版本，
    // 难道要一个一个更新上去吗？」—— 跨修订号是一步到位的（更新包是
    // 全量替换）；但跨次版本意味着依赖变了，更新包只有 .py 和 .js，
    // 补不上，硬更新会让人拿到「新代码 + 旧依赖」，下次启动直接崩。
    const st = ready(sb);
    st.upd = { ok: true, has_update: false, need_full: true,
               local: 'v1.0.5', latest: 'v1.1.0',
               error: '有新版本 v1.1.0，但它需要的东西你这儿还没有'
                    + '（某个包（没装）），需要重新下载完整安装包。' };
    const h = fn(st);
    if (!h.includes('重新下载安装包')) throw new Error('没说清楚这次要换个方式');
    if (!h.includes('data-act="openReleases"')) throw new Error('没给下载页入口');
    if (h.includes('data-act="downloadUpdate"')) throw new Error('还让人走自动更新');
    if (!h.includes('不受影响')) throw new Error('没说模型和运行库不用重下');
  });

  ck('拿不到校验值时问用户，不是把路堵死', () => {
    // 🔴 原来这里是硬拒绝：「出于安全没有下载」，然后就没有然后了。
    //    小蔡 2026-09-02：「不能这样吧，那更新按钮是干嘛的」——
    //    更新按钮的全部意义就是点一下自动搞定，而那条安全规则挡住的
    //    是正常更新、不是攻击。
    //    改成跟显卡那条一个道理：报警，但不替用户做主。
    const st = ready(sb);
    st.upd = { ok: true, has_update: true, local: 'v1', latest: 'v2',
               needConfirm: true,
               confirmWhy: '拿不到 GitHub 给的校验值，没法确认下回来的是不是原件。',
               asset: { name: 'u.zip', url: 'https://x/u.zip', size: 1 } };
    const h = fn(st);
    if (!h.includes('没法验证')) throw new Error('没说清楚是什么情况');
    if (!h.includes('拿不到 GitHub 给的校验值')) throw new Error('没显示后端给的原因');
    if (!h.includes('data-act="installAnyway"')) throw new Error('把路堵死了，没有「仍然安装」');
    if (!h.includes('风险')) throw new Error('没提示风险就让人装');
    if (!h.includes('data-act="closeUpdate"')) throw new Error('退不出去');
  });

  ck('装好之后只剩重启，不让用户自己去覆盖文件', () => {
    // 小蔡定的体验：点「更新」→ 自动下载 → 自动装好 → 提示重启。
    // 「没有人会去开 github」，也没有人愿意自己解压覆盖。
    const st = ready(sb);
    st.upd = { ok: true, has_update: true, local: 'v1', latest: 'v2', error: '',
               installed: true, files: 45, via: 'ghfast.top',
               asset: { name: 'u.zip', url: 'https://x/u.zip', size: 1 } };
    const h = fn(st);
    if (!h.includes('更新完成')) throw new Error('没说装好了');
    if (!h.includes('45 个文件')) throw new Error('没说更新了多少');
    if (!h.includes('data-act="restartApp"')) throw new Error('没有重启按钮');
    if (h.includes('解压')) throw new Error('还在让用户自己解压');
    if (h.includes('覆盖到')) throw new Error('还在让用户自己覆盖');
    if (!h.includes('不受影响')) throw new Error('没说模型和已转文件安全');
  });

  ck('查更新等待屏显示倒计时，归零后不显示 0', () => {
    // 小蔡 2026-09-05 定的：倒计时归零和结果出现必须是同一时刻。
    // 这里只钉住「数字真的渲染出来了」和「归零不显示 0」——
    // 「归零那一刻出结果」是 actions 里的时序，渲染层测不到。
    const st = ready(sb);
    st.updBusy = true; st.upd = null; st.updLeft = 4;
    if (!fn(st).includes('正在查看有没有新版本… 4'))
      throw new Error('等待屏没显示倒计时数字');
    st.updLeft = 0;
    const h0 = fn(st);
    if (h0.includes('… 0')) throw new Error('归零了还在显示 0');
    if (!h0.includes('就快好了')) throw new Error('归零后没换文案');
  });

  ck('下载中说明下完会自动装，不用再动手', () => {
    const st = ready(sb);
    st.updBusy = true;
    st.upd = { ok: true, has_update: true, local: 'v1', latest: 'v2', error: '',
               dlGot: 200000, dlTotal: 400000, phase: 'running',
               asset: { name: 'u.zip', url: 'https://x/u.zip', size: 400000 } };
    const h = fn(st);
    if (!h.includes('正在下载')) throw new Error('没说在下');
    if (!h.includes('50%')) throw new Error('没显示进度');
    if (!h.includes('自动装好')) throw new Error('没说下完会自动装');
  });

  ck('安装阶段有单独的提示', () => {
    const st = ready(sb);
    st.updBusy = true;
    st.upd = { ok: true, has_update: true, local: 'v1', latest: 'v2', error: '',
               phase: 'installing',
               asset: { name: 'u.zip', url: 'https://x/u.zip', size: 1 } };
    const h = fn(st);
    if (!h.includes('正在安装')) throw new Error('安装阶段没提示，用户以为卡住了');
  });

  ck('按钮是「更新」和「暂不更新」', () => {
    const st = ready(sb);
    st.upd = { ok: true, has_update: true, local: 'v1', latest: 'v2',
               published: '2026-09-05', notes: 'x', error: '',
               asset: { name: 'u.zip', url: 'https://x/u.zip', size: 400000 } };
    const h = fn(st);
    if (!h.includes('>更新<')) throw new Error('没有「更新」按钮');
    if (!h.includes('暂不更新')) throw new Error('没有「暂不更新」');
    if (h.includes('下载更新包')) throw new Error('还是旧文案');
  });

  ck('环境有硬伤时，更新面板不能盖住拦截屏', () => {
    // 公式引擎都缺了的话，先解决那个 —— 更新了也用不了
    const st = ready(sb);
    st.env.formula = { ok: false, why: '公式转换要用的文件不见了' };
    st.upd = { ok: true, has_update: true, local: 'v1', latest: 'v2', error: '' };
    const h = fn(st);
    if (h.includes('有新版本 v2')) throw new Error('更新面板盖住了拦截屏');
    if (!h.includes('安装包不完整')) throw new Error('该拦的没拦');
  });
}

console.log('\n\u4e0b\u8f7d\u6a21\u578b\uff08\u9996\u6b21\u4f7f\u7528\uff09\uff1a');
{
  const sb = mkSandbox();
  const fn = sb.window.P2W_PAGES.model;

  ck('测速中有话说', () => {
    const st = ready(sb);
    st.srcLoading = true;
    const h = fn(st);
    if (!h.includes('正在测试各个下载源的速度')) throw new Error('测速时一声不吭');
    if (!segs(h)) throw new Error('结构不全');
  });

  ck('显示的是预计几分钟，不是 MB/s', () => {
    const st = ready(sb);
    st.sources = [
      { id: 'ms', name: 'ModelScope（国内）', ok: true, eta: '约 6 分钟' },
      { id: 'hf', name: 'HuggingFace 官方', ok: true, eta: '约 40 分钟' },
    ];
    st.srcPick = 'ms';
    const h = fn(st);
    if (!h.includes('约 6 分钟')) throw new Error('没显示预计耗时');
    if (h.includes('MB/s')) throw new Error('显示了 MB/s，电脑盲要做换算题');
  });

  ck('最快的默认选中，电脑盲直接点开始即可', () => {
    const st = ready(sb);
    st.sources = [
      { id: 'ms', name: 'ModelScope', ok: true, eta: '约 6 分钟' },
      { id: 'hf', name: 'HuggingFace', ok: true, eta: '约 40 分钟' },
    ];
    st.srcPick = 'ms';
    const h = fn(st);
    const i = h.indexOf('data-arg="ms"');
    if (i < 0) throw new Error('没渲染出源');
    if (!h.slice(i, i + 200).includes('checked')) throw new Error('最快的没默认选中');
    const j = h.indexOf('data-act="startDownload"');
    if (h.slice(j, j + 90).includes('disabled')) throw new Error('有可用源却不让下载');
  });

  ck('连不上的源变灰且不能选', () => {
    const st = ready(sb);
    st.sources = [
      { id: 'ms', name: 'ModelScope', ok: true, eta: '约 6 分钟' },
      { id: 'hf', name: 'HuggingFace', ok: false, error: 'timeout' },
    ];
    st.srcPick = 'ms';
    const h = fn(st);
    if (h.includes('data-arg="hf"')) throw new Error('连不上的源还能点');
    if (!h.includes('连不上')) throw new Error('没说明连不上');
  });

  ck('全都连不上时不让点开始并说明原因', () => {
    const st = ready(sb);
    st.sources = [{ id: 'ms', name: 'ModelScope', ok: false, error: 'timeout' }];
    const h = fn(st);
    const i = h.indexOf('data-act="startDownload"');
    if (!h.slice(i, i + 90).includes('disabled')) throw new Error('全连不上还能下载');
    if (!h.includes('检查一下网络')) throw new Error('没告诉用户怎么办');
  });

  ck('给已有模型的人一条路', () => {
    const st = ready(sb);
    st.sources = [{ id: 'ms', name: 'ModelScope', ok: true, eta: '约 6 分钟' }];
    const h = fn(st);
    if (!h.includes('data-act="pickLocal"')) throw new Error('已有模型的人没路走');
  });

  ck('下载中显示真实字节数，不只是百分比', () => {
    // 总量 4.6 GB 是估的，百分比可能冲到 103% 或停在 97%，
    // 但「已下 1.0 GB」永远是真的。
    const st = ready(sb);
    st.dl = { running: true, got: 1.0 * 1024 * 1024 * 1024,
              total: 4.6 * 1024 * 1024 * 1024, line: 'Downloading layout.pth' };
    const h = fn(st);
    if (!h.includes('正在下载')) throw new Error('没显示下载中');
    if (!h.includes('接着上次的位置继续')) throw new Error('没说明能续传');
    if (!h.includes('1.0 GB')) throw new Error('没显示已下多少');
    if (!h.includes('4.6 GB')) throw new Error('没显示总量');
    if (!h.includes('22%')) throw new Error('没显示百分比');
    if (!h.includes('data-act="cancelDownload"')) throw new Error('下到一半没法停');
  });

  ck('百分比不会超过 100', () => {
    // 总量是估的，实际下的可能更多 —— 显示 108% 会让人以为出错了
    const st = ready(sb);
    st.dl = { running: true, got: 5.0e9, total: 4.6e9 };
    const h = fn(st);
    // 正则要排除 100 本身 —— bar() 生成的 width:100% 是合法的
    if (/1(?:0[1-9]|[1-9][0-9])%/.test(h)) throw new Error('百分比超过 100 了');
    if (!h.includes('100%')) throw new Error('没封顶到 100%');
  });

  ck('下载没完不许跳回主界面', () => {
    // 🔴 2026-09-02 真机：「点完下载，模型进度条跑了一点点就跳到了拖文件
    //    的界面，然后后台在下载。」
    //
    //    原因是轮询里写了 `d.state === 'done' || d.ready`，而 d.ready 来自
    //    models.ready()，判据是「模型目录里有没有 >1 MB 的文件」——
    //    下载才开始、第一个文件刚落盘它就成立了。
    //
    //    后果比「界面跳了」严重：用户以为下完了直接去转换，而模型只有
    //    一小部分，转换失败，他会以为是转换功能坏了。
    //
    //    这条用扫源码的方式钉：轮询逻辑不在渲染路径上，测不到，
    //    而它又是那种「顺手加个兜底」就会被改回去的地方。
    const src = fs.readFileSync(path.join(R, 'actions.js'), 'utf8');
    if (/'done'\s*\|\|\s*d\.ready/.test(src)) {
      throw new Error('又拿 d.ready 当下载完成的判据了');
    }
    if (!/d\.state === 'done'/.test(src)) {
      throw new Error('没有按后端 state 判断完成');
    }
  });

  ck('下载失败时日志留在眼前，不跳去「测速失败」', () => {
    // 🔴 2026-09-02 真机：模型下载失败，界面跳到「测速失败」那一屏 ——
    //    而真正的原因是 torch 的 c10.dll 加载不了，跟网络和下载源
    //    毫无关系。用户被指去怀疑错的东西。
    //    小蔡是靠界面给出的**日志文件路径**自己去翻文件才找到的，
    //    说明光给路径不够，日志本身就该摆在界面上。
    const st = ready(sb);
    st.page = 'model';
    st.dl = {
      running: false,
      error: 'GPU 运行库装上了，但这台电脑加载不了它（Windows 报'
           + '「动态链接库初始化失败」）。',
      got: 0, total: 2.8e9,
      cmd: 'python.exe -m pip install --upgrade torch torchvision',
      lines: ['Collecting torch',
              'OSError: [WinError 1114] Error loading c10.dll'],
      log: 'logs/torch_install.log', phase: 'gpulib',
    };
    const h = fn(st);
    if (h.includes('测速失败')) throw new Error('下载失败被显示成测速失败');
    if (!h.includes('c10.dll')) throw new Error('日志没留在眼前');
    if (!h.includes('动态链接库')) throw new Error('没显示失败原因');
    if (!h.includes('data-act="startDownload"')) throw new Error('没法重试');
  });

  ck('测速失败时把原因显示出来', () => {
    const st = ready(sb);
    st.srcError = 'getaddrinfo ENOTFOUND modelscope.cn';
    const h = fn(st);
    if (!h.includes('getaddrinfo ENOTFOUND modelscope.cn')) throw new Error('没显示原因');
    if (!h.includes('data-act="probeSources"')) throw new Error('没给重试的路');
  });
}

console.log('底部常驻：');
{
  const sb = mkSandbox();
  const M = sb.window.P2W_PAGES.main;
  const D = sb.window.P2W_PAGES.model;

  // 卡在安装任何一步的人，唯一的自救手段就是更新到修好的版本。
  // 按钮不在那一屏，人就只能重下安装包 —— v0.0.1 那次正是如此。
  function runSt2() {
    const st = ready(sb);
    st.items = [{ path: 'C:/a/x.pdf', ok: true, pages: 10, scan_pages: [] }];
    st.task = { state: 'running', total: 1, current: 0, current_name: 'x.pdf',
                stage: '识别中', stage_cur: 4, stage_total: 10,
                results: [], elapsed: 65, remain: 320 };
    return st;
  }
  const screens = [
    ['后台连不上', M, Object.assign(ready(sb), { envError: '炸了' })],
    ['环境拦截·显卡不达标', M, Object.assign(ready(sb), {
      env: Object.assign(goodEnv(), { gpu: { ok: false, why: '不达标' } }) })],
    ['环境拦截·引擎缺失', M, Object.assign(ready(sb), {
      env: Object.assign(goodEnv(), { mineru: { ok: false } }) })],
    ['主屏·空态', M, ready(sb)],
    ['主屏·读取中', M, Object.assign(ready(sb), { scanning: true })],
    ['主屏·有文件', M, Object.assign(ready(sb), {
      items: [{ path: 'C:/a/x.pdf', ok: true, pages: 10, scan_pages: [] }] })],
    ['转换中', M, runSt2()],
    ['转换完成', M, (function () {
      const st = runSt2();
      st.task = { state: 'done', total: 1, current: 1, elapsed: 240, remain: 0,
                  results: [{ ok: true, pdf: 'C:/a/x.pdf', docx: 'C:/a/x.docx',
                              line: '公式 213' }] };
      return st;
    }())],
    ['测速中', D, Object.assign(ready(sb), { srcLoading: true })],
    ['测速失败', D, Object.assign(ready(sb), { srcError: '全都连不上' })],
    ['还没测速', D, Object.assign(ready(sb), { sources: [] })],
    ['选源列表', D, Object.assign(ready(sb), {
      sources: [{ id: 'ms', name: 'ModelScope', ok: true, eta: '约 6 分钟' }] })],
    ['下载中', D, Object.assign(ready(sb), {
      dl: { phase: 'models', got: 1e9, total: 4.6e9, cmd: 'mineru-models-download',
            lines: ['开始下载'], log: 'D:/logs/model_download.log' } })],
    ['下载失败', D, Object.assign(ready(sb), {
      dl: { phase: 'gpulib', got: 0, total: 2.8e9, cmd: 'pip install torch',
            lines: ['连不上'], error: '下载中断' } })],
  ];
  for (const [name, fn, st] of screens) {
    ck('「' + name + '」屏底部有关于（自救入口）', () => {
      const h = fn(st);
      if (!h.includes('data-act="openAbout"')) throw new Error('没有关于按钮');
      if (!segs(h)) throw new Error('三段结构不全');
    });
  }

  ck('更新屏自己不放检查更新（那是死按钮）', () => {
    const st = Object.assign(ready(sb), { upd: { has: false, local: 'v0.0.4' } });
    const h = M(st);
    if (!segs(h)) throw new Error('三段结构不全');
  });

  ck('底部挤的时候环境状态缩成圆点，不挤时显示全文', () => {
    const tight = sb.window.P2W_PAGES.model(Object.assign(ready(sb), {
      sources: [{ id: 'ms', name: 'ModelScope', ok: true, eta: '约 6 分钟' }] }));
    // compact 把文字塞进 title，所以判据是「标签外有没有可见文字」，
    // 不能只 includes('显卡 ✓') —— title 里也有。
    if (tight.includes('>显卡 ✓')) throw new Error('挤的屏没缩成圆点');
    if (!tight.includes('title="显卡 ✓')) throw new Error('缩了但没给悬停详情');
    const roomy = M(ready(sb));
    if (!roomy.includes('>显卡 ✓')) throw new Error('不挤的屏该显示全文');
  });
}

console.log('日志粘底：');
{
  const sb = mkSandbox();
  const st = sb.window.P2W_STATE;
  Object.assign(st, ready(sb));
  st.showLog = true;
  st.items = [{ path: 'C:/a/x.pdf', ok: true, pages: 10, scan_pages: [] }];
  st.task = { state: 'running', total: 1, current: 0, current_name: 'x.pdf',
              stage: '识别中', stage_cur: 4, stage_total: 10, results: [],
              elapsed: 65, remain: 320, lines: ['第一行', '第二行'] };
  const el = sb.document.getElementById('app');

  ck('新内容自己露出来（用户贴着底部时跟随）', () => {
    sb.window.P2W_RENDER();
    if (el._log.scrollTop !== el._log.scrollHeight) throw new Error('没滚到底');
  });

  ck('用户翻上去看历史，不许被拽回底部', () => {
    sb.window.P2W_RENDER();
    el._log.scrollTop = 200;          // 用户手动往上滚
    sb.window.P2W_RENDER();           // 轮询又重绘了一次
    if (el._log.scrollTop !== 200) {
      throw new Error('被拽回去了，停在 ' + el._log.scrollTop);
    }
  });

  ck('用户自己滚回底部后，恢复跟随', () => {
    sb.window.P2W_RENDER();
    el._log.scrollTop = 200;
    sb.window.P2W_RENDER();
    el._log.scrollTop = 900;          // 1000 - 100，正好贴底
    sb.window.P2W_RENDER();
    if (el._log.scrollTop !== 1000) throw new Error('没恢复跟随');
  });
}

console.log('缓存与兜底：');
{
  const sb = mkSandbox();
  const M = sb.window.P2W_PAGES.main;
  function withTask(task) {
    const st = ready(sb);
    st.items = [{ path: 'C:/a/x.pdf', ok: true, pages: 23, scan_pages: [] }];
    st.task = task;
    return st;
  }
  const okRes = (cached) => [{ ok: true, pdf: 'C:/a/x.pdf', docx: 'C:/a/x.docx',
                               line: '公式 613', cached: cached }];

  ck('缓存命中的份要标出来', () => {
    const h = M(withTask({ state: 'done', total: 1, current: 1, elapsed: 2,
      remain: 0, pages: [23], sec_per_page: 26, results: okRes(true) }));
    if (!h.includes('>缓存<')) throw new Error('没标出来，用户会以为根本没转');
  });

  ck('倒计时归零还没转完就认账', () => {
    const h = M(withTask({ state: 'running', total: 1, current: 0,
      current_name: 'x.pdf', stage: '识别中', stage_cur: 9, stage_total: 10,
      results: [], elapsed: 900, remain: 0, pages: [23], sec_per_page: 26 }));
    if (!h.includes('你的 GPU 真垃圾')) throw new Error('还挂着「还要约 0 秒」');
  });

  ck('比预估快得多就夸一句', () => {
    // 出厂估 23 x 26 = 598 秒，实际 100 秒
    const h = M(withTask({ state: 'done', total: 1, current: 1, elapsed: 100,
      remain: 0, pages: [23], sec_per_page: 26, results: okRes(false) }));
    if (!h.includes('你的 GPU 真牛逼')) throw new Error('该夸没夸');
  });

  ck('哪几个公式没转成，悬停要看得到', () => {
    // math_note 以前没有任何地方读 —— 点名了也到不了用户眼前。
    const h = M(withTask({ state: 'done', total: 1, current: 1, elapsed: 240,
      remain: 0, pages: [23], sec_per_page: 26,
      results: [{ ok: true, pdf: 'C:/a/x.pdf', docx: 'C:/a/x.docx',
                  line: '公式 613 ｜ 1 个公式没转成',
                  math_note: '第 543 个（a=gtan alpha）没转成' }] }));
    if (!h.includes('第 543 个')) throw new Error('点名的信息没到用户眼前');
  });

  ck('失败的那份也要给出次品的入口', () => {
    // 转一份四分钟。因为一个公式没转成就让人两手空空，代价太大 ——
    // 正文、表格、图片都在，名字里带着【公式未完全转换】不会被认错。
    const h = M(withTask({ state: 'done', total: 1, current: 1, elapsed: 240,
      remain: 0, pages: [23], sec_per_page: 26,
      results: [{ ok: false, pdf: 'C:/a/x.pdf', error: '公式没转成',
                  degraded: 'C:/a/x【公式未完全转换】.docx' }] }));
    if (!h.includes('打开次品')) throw new Error('四分钟换来的东西没给用户');
    if (!h.includes('公式未完全转换')) throw new Error('没带上次品路径');
  });

  ck('没有次品时不显示那个入口（避免死按钮）', () => {
    const h = M(withTask({ state: 'done', total: 1, current: 1, elapsed: 240,
      remain: 0, pages: [23], sec_per_page: 26,
      results: [{ ok: false, pdf: 'C:/a/x.pdf', error: '连 PDF 都打不开',
                  degraded: '' }] }));
    if (h.includes('打开次品')) throw new Error('没有次品却给了入口');
  });

  ck('全靠缓存的秒回不算 GPU 快', () => {
    const h = M(withTask({ state: 'done', total: 1, current: 1, elapsed: 2,
      remain: 0, pages: [23], sec_per_page: 26, results: okRes(true) }));
    if (h.includes('真牛逼')) throw new Error('夸错了对象 —— 秒回是没跑 GPU');
    if (!h.includes('转换完成')) throw new Error('该显示转换完成');
  });
}


console.log('转换时永远有数字在跳：');
{
  const sb = mkSandbox();
  const M = sb.window.P2W_PAGES.main;
  function running(extra) {
    const st = ready(sb);
    st.items = [{ path: 'C:/a/x.pdf', ok: true, pages: 23, scan_pages: [] }];
    st.task = Object.assign({
      state: 'running', total: 1, current: 0, current_name: 'x.pdf',
      stage: '识别中', stage_cur: 4, stage_total: 10, results: [],
      elapsed: 132, remain: 320, pages: [23], sec_per_page: 26, lines: [],
    }, extra || {});
    return st;
  }

  ck('进度行钉在日志区，往上翻也翻不走', () => {
    const st = running({ progress_line: '识别中: 57%|#####| 142/247' });
    st.showLog = true;
    const h = M(st);
    if (!h.includes('142/247')) throw new Error('进度行没显示，屏幕上没有动的东西');
  });

  ck('跑的那条命令要摆出来', () => {
    const st = running({ lines: ['$ python -m mineru.cli.client -p x.pdf',
                                 'MFR model loaded'] });
    st.showLog = true;
    const h = M(st);
    if (!h.includes('mineru.cli.client')) throw new Error('命令没显示');
    if (!h.includes('class="l cmd"')) throw new Error('命令没跟输出区分开');
  });

  ck('估不出剩余时间也要有数字在跳', () => {
    // remain=null 是 MinerU 还没吐第一条进度的那几十秒。
    // 以前这里只有「正在估算…」五个字，一动不动，看着像卡死。
    const h = M(running({ remain: null, elapsed: 47 }));
    if (!h.includes('正在估算')) throw new Error('该说估不出来');
    if (!h.includes('已用')) throw new Error('屏幕上一个动的数字都没有');
  });

  ck('加载模型那几十秒也有阶段名', () => {
    const h = M(running({ stage: '正在加载识别模型', stage_cur: 0,
                          stage_total: 0, remain: null }));
    if (!h.includes('正在加载识别模型')) throw new Error('阶段名没显示');
  });
}

console.log('');

console.log('\n升级区：');
{
  const sb = mkSandbox();
  const fn = sb.window.P2W_PAGES.main;
  const box = sb.window.P2W_PAGES.upgradeBox;
  const env = (e) => Object.assign(ready(sb), {
    about: 'env', diag: { versions: { mineru: '3.4.5' }, root: 'D:/x' },
    maint: { ok: true, items: [] },
  }, e || {});
  const HAS_NEW = {
    ok: true, mineru: { local: '3.4.5', latest: '3.6.0', error: '' },
    torch: { local: '2.11.0', latest: '2.11.0', error: '' }, models: {},
  };

  ck('没查过上游时不显示升级区', () => {
    const h = fn(env({}));
    if (h.includes('可以升级的')) throw new Error('没数据却摆出勾选框');
  });

  ck('已是最新时不显示升级区', () => {
    const h = fn(env({ deps: {
      ok: true, mineru: { local: '3.4.5', latest: '3.4.5', error: '' },
      torch: { local: '2.11.0', latest: '2.11.0', error: '' }, models: {} } }));
    if (h.includes('可以升级的')) throw new Error('没新版本却显示升级区');
  });

  ck('有新版本且策略没写时，写明「我们没测过」', () => {
    // 🔴 默认是 null（没测过）—— 不能当成可以升，也不能当成不能升。
    const h = fn(env({ deps: HAS_NEW }));
    if (!h.includes('可以升级的')) throw new Error('有新版本却不显示');
    if (!h.includes('我们没测过')) throw new Error('没说清楚我们没测过');
    if (!h.includes('data-act="toggleUpg"')) throw new Error('没给勾选框');
  });

  ck('策略说不能升时只给理由，不给勾选框', () => {
    // 🔴 2026-09-05 抓到过一次断线：read_upgrade 定义了但没人调用，
    //    前端永远拿到空对象，理由显示不出来。这条守住整条链路。
    const st = env({ deps: HAS_NEW,
      upd: { upgrade: { mineru: { ok: false, note: '3.6 的表格识别退步了' } } } });
    const h = box(st);
    if (!h.includes('表格识别退步')) throw new Error('策略的理由没显示出来');
    if (h.indexOf('data-arg="mineru"') >= 0) throw new Error('说了不能升却还给勾选框');
  });

  ck('预演结果默认折叠，展开才看完整清单', () => {
    // 一次升级动十几个包很正常，全摊开会吓着人。
    const plan = { ok: true, changes: [
      { name: 'mineru', from: '3.4.5', to: '3.6.0' },
      { name: 'transformers', from: '4.57.6', to: '4.58.0' } ] };
    const a = fn(env({ deps: HAS_NEW, upgPick: { mineru: true }, upgPlan: plan }));
    if (!a.includes('会动 2 个包')) throw new Error('没说会动几个包');
    if (a.includes('transformers')) throw new Error('默认就摊开了');
    const b = fn(env({ deps: HAS_NEW, upgPick: { mineru: true },
                       upgPlan: plan, upgDetail: true }));
    if (!b.includes('transformers')) throw new Error('展开了还看不到');
  });

  ck('pip 解不出来时说清楚装不了，且不让下载', () => {
    // 🔴 这正是约束文件要的效果：显式暴露冲突，而不是偷偷装出坏组合。
    const h = fn(env({ deps: HAS_NEW, upgPick: { mineru: true },
      upgPlan: { ok: false, error: 'ResolutionImpossible: 需要 torch>=2.12' } }));
    if (!h.includes('装不了')) throw new Error('没说为什么装不了');
    const i = h.indexOf('data-act="startUpgrade"');
    if (i >= 0) {
      const tag = h.slice(i, h.indexOf('>', i));
      if (!tag.includes('disabled')) throw new Error('装不了却还能点下载');
    }
  });
}


console.log('\n模型更新入口：');
{
  const sb = mkSandbox();
  const fn = sb.window.P2W_PAGES.main;
  const env = (deps) => Object.assign(ready(sb), {
    about: 'env',
    diag: { versions: { mineru: '3.4.5' }, root: 'D:/x', models_ready: true },
    maint: { ok: true, items: [] },
    deps: deps,
  });

  ck('没查过上游时不出现「更新模型」', () => {
    // 🔴 没查过 ≠ 已最新，也 ≠ 该更新。什么都不知道时不该摆按钮。
    const h = fn(env(undefined));
    if (h.includes('data-act="updateModels"')) throw new Error('没查过却让人更新');
  });

  ck('模型已是最新时不出现「更新模型」', () => {
    const h = fn(env({ ok: true, models: {
      ready: true, local_time: '2026-09-05', upstream_time: '2026-06-16',
      error: '' } }));
    if (h.includes('data-act="updateModels"')) throw new Error('已最新却让人更新');
  });

  ck('模型旧了才出现「更新模型」', () => {
    const h = fn(env({ ok: true, models: {
      ready: true, local_time: '2026-05-01', upstream_time: '2026-06-16',
      error: '' } }));
    if (!h.includes('data-act="updateModels"')) throw new Error('旧了却没入口');
  });

  ck('查不到上游时间时不出现（不猜）', () => {
    const h = fn(env({ ok: false, models: {
      ready: true, local_time: '2026-05-01', upstream_time: '', error: '断网' } }));
    if (h.includes('data-act="updateModels"')) throw new Error('查不到却让人更新');
  });

  ck('转换进行中，更新模型按钮禁用但不消失', () => {
    const st = env({ ok: true, models: {
      ready: true, local_time: '2026-05-01', upstream_time: '2026-06-16',
      error: '' } });
    st.task = { state: 'running', items: [] };
    const h = fn(st);
    const i = h.indexOf('data-act="updateModels"');
    if (i < 0) throw new Error('按钮被拿掉了');
    const tag = h.slice(i, h.indexOf('>', i));
    if (!tag.includes('disabled')) throw new Error('转换中却还能点');
  });
}

console.log('\n清理按钮的防护：');
{
  const sb = mkSandbox();
  const fn = sb.window.P2W_PAGES.main;
  // 勾上一项 —— 不勾的话按钮本来就是灰的，测不出是被谁拦的
  const base = () => Object.assign(ready(sb), {
    about: 'env',
    diag: { versions: {}, root: 'D:/x', models_ready: true },
    maint: { ok: true, items: [] },
    maintPick: { temp_pip: true },
  });
  const cleanTag = (st) => {
    const h = fn(st);
    const i = h.indexOf('data-act="doClean"');
    if (i < 0) throw new Error('清理按钮不见了');
    return h.slice(i, h.indexOf('>', i));
  };

  ck('没在装东西的时候能点清理', () => {
    // 🔴 拦过头比不拦更烦人：按钮永远点不了，而用户不知道为什么
    if (cleanTag(base()).includes('disabled')) throw new Error('没在装却点不了');
  });

  ck('装东西时清理按钮变灰并说明原因', () => {
    // 🔴 只有这两样够得着这一屏。软件自更新（后端 _UPD）不在其中 ——
    //    st.upd 一有值 pageMain 就切去「检查更新」那一屏，清理按钮
    //    根本不渲染。后端照样拦它，两边不对称是有意的。
    const cases = [
      ['下模型或装运行库', (st) => { st.dl = { running: true }; }],
      ['依赖升级', (st) => { st.upgDl = { state: 'running' }; }],
    ];
    for (const [name, set] of cases) {
      const st = base();
      set(st);
      const tag = cleanTag(st);
      if (!tag.includes('disabled')) throw new Error(name + ' 时还能点清理');
      if (!tag.includes('正在安装')) throw new Error(name + ' 时没说为什么点不了');
    }
  });

  ck('转换进行中也不让清', () => {
    const st = base();
    st.task = { state: 'running', items: [] };
    const tag = cleanTag(st);
    if (!tag.includes('disabled')) throw new Error('转换中还能点清理');
    if (!tag.includes('正在转换')) throw new Error('转换中没说为什么');
  });
}


console.log('\n总进度条：');
{
  const sb = mkSandbox();
  const fn = sb.window.P2W_PAGES.main;
  const W = { pass1: 0.44, pass2: 0.51, other: 0.05 };
  const mk = (over) => {
    const st = ready(sb);
    st.items = [{ path: 'C:\a.pdf', ok: true, pages: 56, scan_pages: [] }];
    st.task = Object.assign({
      state: 'running', total: 1, current: 0, current_name: 'a.pdf',
      stage: '逐页识别', stage_cur: 0, stage_total: 56,
      results: [], elapsed: 10, remain: 1800, weights: W,
    }, over || {});
    return st;
  };
  // 顶上那条是渲染出的第一个 <i style="width:N%">。
  // id 是可选的：增量刷新给顶上那条挂了 id="cv-tbar"，行内的小条没挂，
  // 两种形状都得认 —— 写死成「紧跟 style」的话，加个属性就全红。
  const pct = (st) => {
    const h = fn(st);
    const m = h.match(/<div class="bar"><i(?: id="[^"]*")? style="width:(\d+)%/);
    if (!m) throw new Error('没找到进度条');
    return parseInt(m[1], 10);
  };

  ck('逐页识别到一半，总进度约等于第一轮权重的一半', () => {
    const got = pct(mk({ stage_cur: 28 }));
    if (Math.abs(got - 22) > 3) throw new Error('期望约 22%，实际 ' + got + '%');
  });

  ck('第二轮刚开始，总进度约等于第一轮权重', () => {
    const got = pct(mk({ stage: '识别公式和文字', stage_cur: 0, stage_total: 1100 }));
    if (Math.abs(got - 44) > 3) throw new Error('期望约 44%，实际 ' + got + '%');
  });

  ck('换阶段时不会清零重来', () => {
    // 🔴 这条钉的是原来那个 bug：进度条算的是「当前阶段」的比例，
    //    一份文件跑六七个阶段，于是涨满又清零六次。
    const st = mk({ stage_cur: 56 });        // 第一轮跑满
    const a = pct(st);
    st.task = Object.assign({}, st.task,
      { stage: '识别公式和文字', stage_cur: 1, stage_total: 1100 });
    const b = pct(st);
    if (b < a) throw new Error('换阶段后倒退了：' + a + '% → ' + b + '%');
  });

  ck('只准涨不准退', () => {
    const st = mk({ stage_cur: 40 });
    const a = pct(st);
    st.task = Object.assign({}, st.task, { stage_cur: 5 });   // 源头报了个更小的
    const b = pct(st);
    if (b < a) throw new Error('倒退了：' + a + '% → ' + b + '%');
  });

  ck('转完之前最多 99%', () => {
    const st = mk({ stage: '识别公式和文字', stage_cur: 1100, stage_total: 1100 });
    const got = pct(st);
    if (got > 99) throw new Error('还没转完就 ' + got + '%');
  });

  ck('拿不到权重时用实测的兜底', () => {
    const st = mk({ stage_cur: 28 });
    delete st.task.weights;
    const got = pct(st);
    if (Math.abs(got - 22) > 3) throw new Error('兜底不对，实际 ' + got + '%');
  });
}



console.log('\n步骤名和步骤清单：');
{
  const sb = mkSandbox();
  const fn = sb.window.P2W_PAGES.main;
  const mk = (over, openStage) => {
    const st = ready(sb);
    st.items = [{ path: 'C:\a.pdf', ok: true, pages: 56, scan_pages: [] },
                { path: 'C:\b.pdf', ok: true, pages: 20, scan_pages: [] }];
    if (openStage !== undefined) st.openStage = openStage;
    st.task = Object.assign({
      state: 'running', total: 2, current: 0, current_name: 'a.pdf',
      stage: '逐页识别', stage_cur: 23, stage_total: 56,
      stages: ['分析版面', '准备版面', '逐页识别'],
      results: [], elapsed: 60, remain: 900,
      weights: { pass1: 0.44, pass2: 0.51, other: 0.05 },
    }, over || {});
    return st;
  };

  ck('逐页识别带「页」这个单位', () => {
    const h = fn(mk());
    if (!h.includes('逐页识别 23/56 页')) throw new Error('没写成「23/56 页」');
  });

  ck('识别公式和文字带「项」这个单位', () => {
    const h = fn(mk({ stage: '识别公式和文字', stage_cur: 544, stage_total: 1100 }));
    if (!h.includes('识别公式和文字 544/1100 项')) throw new Error('没写成「544/1100 项」');
  });

  ck('其余阶段仍然不显示数字', () => {
    // 🔴 2026-09-02 的教训：MinerU 换阶段时单位会变（5/11 页 → 5/247 块），
    //    用户看到数字跳变以为出 bug。只有说得清单位的那两轮才放开。
    const h = fn(mk({ stage: '分析版面', stage_cur: 5, stage_total: 11 }));
    if (h.includes('5/11')) throw new Error('把 5/11 显示出来了');
    if (!h.includes('分析版面')) throw new Error('阶段名也没了');
  });

  ck('默认不展开步骤清单', () => {
    const h = fn(mk());
    if (h.includes('✓ 分析版面')) throw new Error('没点就展开了');
  });

  ck('展开后列出真的走过的那几步', () => {
    const h = fn(mk({}, 0));
    if (!h.includes('✓ 分析版面')) throw new Error('做完的没打勾');
    if (!h.includes('▶ 逐页识别')) throw new Error('正在做的没给箭头');
    if (h.includes('识别表格')) throw new Error('把没走过的步骤也摆出来了');
  });

  ck('正在转的那行能点开', () => {
    const h = fn(mk());
    if (!h.includes('data-act="toggleStages"')) throw new Error('点不开');
  });

  ck('还没轮到的那行点不开', () => {
    const h = fn(mk());
    const n = (h.match(/data-act="toggleStages"/g) || []).length;
    if (n !== 1) throw new Error('可点开的行有 ' + n + ' 个，只该有正在转的那一个');
  });

  ck('转完的那行也能点开', () => {
    const st = mk({
      state: 'done', current: 2,
      results: [{ ok: true, pdf: 'C:\a.pdf', docx: 'C:\a.docx', line: '公式 3',
                  stages: ['分析版面', '逐页识别', '识别公式和文字'] }],
    }, 0);
    const h = fn(st);
    if (!h.includes('data-act="toggleStages"')) throw new Error('转完就点不开了');
    if (!h.includes('✓ 识别公式和文字')) throw new Error('展开后看不到走过的步骤');
  });
}



console.log('\n转换报告：');
{
  const sb = mkSandbox();
  const fn = sb.window.P2W_PAGES.main;
  const mk = (results, over) => {
    const st = ready(sb);
    st.items = [{ path: 'C:\a.pdf', ok: true, pages: 56, scan_pages: [] }];
    Object.assign(st, over || {});
    st.task = { state: 'done', total: results.length, current: results.length,
                elapsed: 1814, remain: 0, results: results };
    return st;
  };
  const OK = { ok: true, pdf: 'C:\a.pdf', docx: 'C:\a.docx', pages: 56,
               formulas: 613, formulas_xsl: 613, tables: 18, images: 132,
               scan_pages: [], details_dropped: 0, line: '公式 613' };

  ck('全都干净就不出现「看报告」', () => {
    // 🔴 小蔡：「要是全都转换成功，为什么要报告呢」
    const h = fn(mk([OK]));
    if (h.includes('看报告')) throw new Error('没毛病也摆了个报告按钮');
  });

  ck('有公式没转成就出现', () => {
    const h = fn(mk([Object.assign({}, OK, { formulas_xsl: 610 })]));
    if (!h.includes('看报告')) throw new Error('有公式没转成却不给报告');
  });

  ck('有页没文字层就出现', () => {
    const h = fn(mk([Object.assign({}, OK, { scan_pages: [3, 7] })]));
    if (!h.includes('看报告')) throw new Error('有扫描页却不给报告');
  });

  ck('图里的文字被拦下也要出现', () => {
    const h = fn(mk([Object.assign({}, OK, { details_dropped: 54 })]));
    if (!h.includes('看报告')) throw new Error('删了 54 处却不提');
  });

  ck('转失败了当然要出现', () => {
    const h = fn(mk([{ ok: false, pdf: 'C:\a.pdf', error: '显卡不够' }]));
    if (!h.includes('看报告')) throw new Error('失败了不给报告');
  });

  ck('报告里该说的都说了', () => {
    const st = mk([Object.assign({}, OK, {
      formulas_xsl: 610, scan_pages: [3, 7], details_dropped: 54,
      math_note: '第 42 个没转成' })], { showReport: true });
    const h = fn(st);
    if (!h.includes('3 个公式没转成')) throw new Error('没说公式');
    if (!h.includes('第 3、7 页没有文字层')) throw new Error('没说扫描页');
    if (!h.includes('54 处没有放进正文')) throw new Error('没说图里的文字');
    if (!h.includes('没列出来的不代表一定对')) throw new Error('少了那句免责');
    if (!h.includes('不会存成文件')) throw new Error('没说清不落盘');
  });

  ck('报告能复制', () => {
    const st = mk([Object.assign({}, OK, { scan_pages: [3] })], { showReport: true });
    const h = fn(st);
    if (!h.includes('data-act="copyReport"')) throw new Error('没有复制按钮');
  });
}



console.log('\n界面状态不许串到下一批：');
{
  const sb = mkSandbox();
  const fn = sb.window.P2W_PAGES.main;
  const clean = { ok: true, pdf: 'C:\a.pdf', docx: 'C:\a.docx', pages: 10,
                  formulas: 5, formulas_xsl: 5, tables: 0, images: 0,
                  scan_pages: [], details_dropped: 0, line: '公式 5' };

  ck('上一批看过报告，这一批全干净时不许自己冒出报告页', () => {
    // 🔴 2026-09-07 实测复现过：只判 st.showReport 的话，这一屏进得来，
    //    而顶上那个「返回列表」按钮由 worthReport 控制、此时不渲染 ——
    //    用户被丢进一个没有退出口的报告页。
    const st = ready(sb);
    st.items = [{ path: 'C:\a.pdf', ok: true, pages: 10, scan_pages: [] }];
    st.showReport = true;                       // 上一批留下的
    st.task = { state: 'done', total: 1, current: 1, elapsed: 60, remain: 0,
                results: [clean] };
    const h = fn(st);
    if (h.includes('不会存成文件')) throw new Error('自己进了报告页');
  });

  ck('真有东西可报时，报告页还是进得去', () => {
    const st = ready(sb);
    st.items = [{ path: 'C:\a.pdf', ok: true, pages: 10, scan_pages: [] }];
    st.showReport = true;
    st.task = { state: 'done', total: 1, current: 1, elapsed: 60, remain: 0,
                results: [Object.assign({}, clean, { scan_pages: [3] })] };
    const h = fn(st);
    if (!h.includes('不会存成文件')) throw new Error('该给报告的时候没给');
    if (!h.includes('data-act="copyReport"')) throw new Error('复制按钮没了');
  });

  // ── 待办队列（转换中加进来的文件，这批转完自动接上）────────────────
  const runTask = { state: 'running', total: 1, current: 0,
                    elapsed: 10, remain: 60, results: [],
                    stage: '逐页识别', stage_cur: 1, stage_total: 3 };
  const oneItem = [{ path: 'C:\\a.pdf', ok: true, pages: 10, scan_pages: [] }];

  ck('转换中有待办时列出来，每份都能移除', () => {
    const st = ready(sb);
    st.items = oneItem;
    st.task = Object.assign({}, runTask);
    st.pending = [{ path: 'C:\\b.pdf', ok: true, pages: 7, scan_pages: [] }];
    const h = fn(st);
    if (!h.includes('待办 1 份')) throw new Error('没显示待办份数');
    if (!h.includes('b.pdf')) throw new Error('没列出待办的文件名');
    if (!h.includes('data-act="delPending"')) throw new Error('没有移除按钮');
  });

  ck('转换中没待办时，也得让人知道还能加', () => {
    const st = ready(sb);
    st.items = oneItem;
    st.task = Object.assign({}, runTask);
    st.pending = [];
    const h = fn(st);
    if (!h.includes('data-act="pickMore"')) throw new Error('没有「再加几份」入口');
    if (!h.includes('拖进来')) throw new Error('没告诉用户可以拖');
  });

  ck('上一批有值得看的东西，才给「上一批的报告」', () => {
    const st = ready(sb);
    st.items = oneItem;
    st.task = Object.assign({}, runTask);
    st.lastResults = [Object.assign({}, clean, { scan_pages: [3] })];
    const h = fn(st);
    if (!h.includes('data-act="toggleLastReport"'))
      throw new Error('上一批有扫描页却没给报告入口');
  });

  ck('上一批的报告页，返回按钮必须排在报告前面', () => {
    // 🔴 2026-09-08 小蔡真机实测：「点了上一批的报告之后，怎么没有返回
    //    按钮，我被困在了报告页面。」
    //
    //    按钮其实渲染了 —— 但报告那块是 .fill，CSS 写着 min-height:100%，
    //    它自己就把主区撑满；拼在它后面的东西被顶到第一屏之外，要往下滚
    //    才看得见。620x440 的窗口里那等于不存在。
    //
    //    所以光判断「按钮在不在」是不够的，**得判断它在不在前面**。
    const st = ready(sb);
    st.items = oneItem;
    st.task = Object.assign({}, runTask);
    st.lastResults = [Object.assign({}, clean, { scan_pages: [3] })];
    st.showLastReport = true;
    const h = fn(st);
    const btnAt = h.indexOf('data-act="toggleLastReport"');
    const repAt = h.indexOf('没列出来的不代表一定对');
    if (btnAt < 0) throw new Error('报告页没有返回按钮');
    if (repAt < 0) throw new Error('报告内容没渲染出来');
    if (btnAt > repAt)
      throw new Error('返回按钮排在报告后面，会被顶出屏幕 —— 等于没有');
  });

  ck('上一批的报告页要保住滚动位置', () => {
    // 🔴 它跟当前批的报告页不一样：**能在新一批转换进行中打开**（入口就长在
    //    pendingBox 的 !done 分支里），而转换中每秒 render 一次、整个 DOM
    //    推倒重来。不挂 data-keep-scroll 的话，往下滑一秒弹回顶部一次，
    //    报告根本读不下去 —— 跟 2026-09-07 缓存明细那个 bug 同形。
    const st = ready(sb);
    st.items = oneItem;
    st.task = Object.assign({}, runTask);
    st.lastResults = [Object.assign({}, clean, { scan_pages: [3] })];
    st.showLastReport = true;
    const h = fn(st);
    if (!h.includes('data-keep-scroll="lastreport"'))
      throw new Error('没挂滚动保持，转换中看报告会一秒弹回顶部一次');
  });

  ck('上一批全干净时，报告页进不去（否则是个没有出口的页面）', () => {
    // 🔴 主区那个分支以前只判 lastResults.length，不判 worthReport，而退出
    //    按钮由 pendingBox 里的 worthReport 控制 —— 两个条件不一致时，
    //    页面进得来、按钮不渲染，用户被困在里面。跟 1513 行那次事故同形。
    const st = ready(sb);
    st.items = oneItem;
    st.task = Object.assign({}, runTask);
    st.lastResults = [clean];          // 全都干干净净
    st.showLastReport = true;          // 开关却开着
    const h = fn(st);
    if (h.includes('没列出来的不代表一定对'))
      throw new Error('进了一个没有退出口的报告页');
  });

  ck('上一批全干净时不给「上一批的报告」—— 那是个死按钮', () => {
    // 🔴 判据跟主报告同一个 worthReport。先摆按钮、点开才发现没什么可报，
    //    就是死按钮 —— 这一屏为此栽过一次（见上面「没有退出口的报告页」）。
    const st = ready(sb);
    st.items = oneItem;
    st.task = Object.assign({}, runTask);
    st.lastResults = [clean];
    const h = fn(st);
    if (h.includes('data-act="toggleLastReport"'))
      throw new Error('上一批干干净净还给了报告按钮');
  });

  ck('开新一批时把报告和展开状态都归位', () => {
    const src = require('fs').readFileSync(
      'D:/claude_code_workspace/pdf_to_word/app/renderer/actions.js', 'utf8');
    const i = src.indexOf('poller = setInterval(poll, 1000)');
    if (i < 0) throw new Error('找不到开转那一段');
    const seg = src.slice(Math.max(0, i - 600), i);
    for (const k of ['st.progMax = 0', 'st.showReport = false', 'st.openStage = null']) {
      if (!seg.includes(k)) throw new Error('开新一批没归位：' + k);
    }
  });

  ck('清理完，占用数字和备份列表两个都得刷', () => {
    // 🔴 2026-09-08 小蔡实测撞上：勾「升级备份」清掉三份之后，上面的
    //    占用数字变了，下面那张表还端着三行已经不存在的备份。
    //    原因是清理完只重新拉了 /api/maint/scan，而备份列表来自另一个
    //    接口 —— 新数据源接进了 openEnvCheck，却漏了这条刷新链。
    const src = require('fs').readFileSync(
      'D:/claude_code_workspace/pdf_to_word/app/renderer/actions.js', 'utf8');
    const i = src.indexOf("HTTP.post('/api/maint/clean'");
    if (i < 0) throw new Error('找不到清理那一段');
    const seg = src.slice(i, i + 900);
    if (!seg.includes('/api/maint/scan')) throw new Error('清理完没刷占用数字');
    if (!seg.includes('/api/upgrade/backups')) {
      throw new Error('清理完没刷备份列表');
    }
  });
}



console.log('\n转换历史（专门一屏）：');
{
  const sb = mkSandbox();
  const fn = sb.window.P2W_PAGES.main;
  const OK = { time: '2026-09-06 10:30:42', file: '讲义.pdf', ok: true,
               pages: 56, took_sec: 1814, pdf: 'D:\源\讲义.pdf',
               docx: 'D:\出\讲义.docx', error: '', error_full: '' };
  const BAD = { time: '2026-09-05 19:12:03', file: '作业.pdf', ok: false,
                pages: 20, took_sec: 3600, pdf: 'D:\源\作业.pdf', docx: '',
                error: '超时被掐断', error_full: '超时被掐断，完整的一大段原因写在这里' };
  const hist = (runs) => Object.assign(ready(sb), { items: [], runs: runs, about: 'history' });

  ck('主屏不再顺带展示历史（改成专门一屏了）', () => {
    const st = Object.assign(ready(sb), { items: [], runs: [OK, BAD] });
    const h = fn(st);
    if (h.includes('之前转过的')) throw new Error('主屏还在顺带展示历史');
    if (!h.includes('把 PDF 拖进来')) throw new Error('把拖放提示弄丢了');
  });

  ck('底部有「历史」入口，而且排在「关于」前面', () => {
    const h = fn(Object.assign(ready(sb), { items: [] }));
    const a = h.indexOf('data-act="openHistory"');
    const b = h.indexOf('data-act="openAbout"');
    if (a < 0) throw new Error('底部没有「历史」按钮');
    if (!(a < b)) throw new Error('「历史」该排在「关于」前面');
  });

  ck('转换中「关于」变灰，但「历史」照样能点', () => {
    const st = ready(sb);
    st.items = [{ path: 'C:\a.pdf', ok: true, pages: 10, scan_pages: [] }];
    st.task = { state: 'running', total: 1, current: 0, elapsed: 5,
                remain: 60, results: [], stage: '识别公式和文字' };
    const h = fn(st);
    const at = h.indexOf('data-act="openHistory"');
    const seg = h.slice(Math.max(0, at - 120), at + 60);
    if (/disabled/.test(seg)) throw new Error('转换中把「历史」也禁用了');
    if (!h.includes('data-act="openAbout"')) throw new Error('「关于」不见了');
  });

  ck('历史屏把记录列出来', () => {
    const h = fn(hist([OK, BAD]));
    if (!h.includes('转换历史')) throw new Error('标题不对');
    if (!h.includes('讲义.pdf') || !h.includes('作业.pdf')) throw new Error('记录没列全');
    if (!h.includes('data-act="closeAbout"')) throw new Error('没有「返回」');
  });

  ck('成功那行能打开 Word，也能开它所在的目录', () => {
    const h = fn(hist([OK]));
    const f = h.match(/data-act="openFile" data-arg="([^"]*)"/);
    const p = h.match(/data-act="openPath" data-arg="([^"]*)"/);
    if (!f) throw new Error('没有「打开」');
    if (!p) throw new Error('没有「文件夹」');
    if (!/讲义\.docx$/.test(f[1])) throw new Error('「打开」没指向产物 Word：' + f[1]);
    if (!/讲义\.docx$/.test(p[1])) throw new Error('「文件夹」该指向产物所在目录：' + p[1]);
  });

  ck('失败那行不给「打开」，「文件夹」指向源 PDF', () => {
    // 🔴 **必须验按钮的 data-arg，不能只验页面上有没有那个文件名** ——
    //    文件名在行首那一列本来就显示着，光 includes('作业.pdf') 的话，
    //    把 folder 改成永远指向空的 docx 这条也照样绿（2026-09-07 变异抓到）。
    const h = fn(hist([BAD]));
    if (h.includes('data-act="openFile"')) throw new Error('失败的还给「打开」');
    const m = h.match(/data-act="openPath" data-arg="([^"]*)"/);
    if (!m) throw new Error('失败那行没有「文件夹」按钮');
    if (!/作业\.pdf$/.test(m[1])) {
      throw new Error('「文件夹」没指向源 PDF，指向的是：' + (m[1] || '(空)'));
    }
    if (!h.includes('完整的一大段原因')) throw new Error('完整报错没挂上去（截断版没用）');
  });

  ck('每次进历史屏都重新拉一次，不吃开机那份缓存', () => {
    // 🔴 用户常是**刚转完一批就想看**，用开机时拉的那份会正好少掉最关心的
    //    那几条。2026-09-07 变异发现这条承诺没人盯着：把 openHistory 里的
    //    loadRuns 删掉，所有测试照样绿。
    const sb2 = mkSandbox();
    const urls = [];
    sb2.window.P2W_STATE.port = 1234;
    sb2.fetch = (u) => {
      urls.push(String(u));
      return { then: () => ({ then: () => ({ catch: () => {} }) }) };
    };
    sb2.window.P2W_ACTS.openHistory();
    if (!urls.some((u) => u.indexOf('/api/runs') >= 0)) {
      throw new Error('进历史屏没去拉数据，发出去的请求：' + JSON.stringify(urls));
    }
  });

  ck('没有「一键重转」', () => {
    const h = fn(hist([OK, BAD]));
    if (h.includes('reconvert')) throw new Error('「重转」又回来了');
  });

  ck('一条记录都没有时给句人话，不是空白', () => {
    const h = fn(hist([]));
    if (!h.includes('还没有转换记录')) throw new Error('空态没提示');
    if (!h.includes('关掉软件也还在')) throw new Error('没说清楚记录是持久的');
  });
}


console.log('\n开机那一刻的顺序：');
{
  ck('拉转换历史必须在拿到端口之后', () => {
    // 🔴 2026-09-07 的哑巴 bug：这句原本排在 getPort 前面，而
    //    apiUrl 是 'http://127.0.0.1:' + state.port + p —— 开机那次请求
    //    发的是 `.../127.0.0.1:null/api/runs`，必然失败，又被 loadRuns
    //    自己的 catch 静默吞掉。**调用位置错 + catch 吃掉证据**，
    //    于是「开机看不到历史」而且一声不吭。
    //
    //    渲染测试全是纯函数，测不到「什么时候调」，所以这条专门盯时序：
    //    把 getPort 换成同步 thenable，启动链第一环当场跑完，然后看
    //    每次 fetch 发生时 state.port 是什么。
    const sb = mkSandbox();
    const seen = [];
    sb.fetch = (u) => {
      seen.push({ url: String(u), port: sb.window.P2W_STATE.port });
      return { then: () => ({ then: () => ({ catch: () => {} }) }) };
    };
    sb.api.getPort = () => ({
      then: (f) => { f(1234); return { then: () => ({ catch: () => {} }) }; },
    });
    const boot = sb._on['DOMContentLoaded'];
    if (!boot) throw new Error('没注册 DOMContentLoaded，启动流程没法测');
    boot();

    const runs = seen.filter((x) => x.url.indexOf('/api/runs') >= 0);
    if (!runs.length) throw new Error('开机压根没去拉历史');
    // 🔴 判据就一条：请求发出去那一刻，port 必须已经是 getPort 给的值。
    //    别写花哨的条件 —— 第一版写成 `!r.port || r.port === 1234 ? false : true`，
    //    括号优先级让它**永远不抛**，变异测试当场抓出这是条假绿测试。
    for (const r of runs) {
      if (r.port !== 1234) {
        throw new Error('端口还没就位就去拉历史了，当时 port = '
                        + JSON.stringify(r.port) + '，URL：' + r.url);
      }
    }
  });

  // 🔴 本来还想加一条「历史拉不到不许把启动流程带崩」，写完发现**测不到
  //    它声称要测的东西**：真实的 fetch 失败返回的是 rejected Promise，
  //    不会同步抛；为了在同步的 ck 里测而让 fetch 直接 throw，崩的其实是
  //    后面那句 get('/api/env')，跟历史这条路无关。造个不真实的场景凑一条
  //    绿测试没有意义 —— 删掉，记在这儿。
}


console.log('\n重绘不许把任何滚动区弹回顶部：');
{
  // 🔴 2026-09-07 一天之内在同一件事上栽了两回：
  //    先是小蔡报「环境检测页展开缓存明细往下滑，自己跳回最顶上，只在
  //    更新组件时」—— 那个框是 max-height + overflow:auto 的独立滚动容器，
  //    而 render() 当时只按写死的选择器保住 .main 和 #dllog 两个；
  //    修完当天又在新做的历史屏里造了个一模一样的。
  //
  //    所以改成认 data-keep-scroll：标记跟容器写在一起，加滚动区的时候
  //    顺手就带上了，不用记得回来改 render()。下面这组按标记逐个盯。
  const sb = mkSandbox();
  const el = sb.document.getElementById('app');
  const st = sb.window.P2W_STATE;

  // 每一项：名字 → 把状态摆成「那个滚动区出现」的样子
  const spots = {
    cachelist: () => {
      Object.assign(st, ready(sb));
      st.about = 'env';
      st.cacheOpen = true;
      st.maint = { ok: true, pip: { items: [
        { name: 'torch', size: 2600000000, ours: true },
        { name: 'numpy', size: 40000000, ours: false }] } };
    },
    history: () => {
      Object.assign(st, ready(sb));
      st.about = 'history';
      st.runs = [{ time: '2026-09-06 10:30:42', file: 'a.pdf', ok: true,
                   pages: 10, took_sec: 60, pdf: 'D:\a.pdf', docx: 'D:\a.docx' }];
    },
    upgchanges: () => {
      Object.assign(st, ready(sb));
      st.about = 'env';
      st.upgDetail = true;
      st.deps = { torch: { local: '2.9.1', latest: '2.14.0' } };
      st.upd = null;
      st.upgPlan = { ok: true, changes: [{ name: 'torch', from: '2.9.1', to: '2.14.0' }] };
    },
    updnotes: () => {
      Object.assign(st, ready(sb));
      st.upd = { ok: true, has_update: true, local: '0.2.6', latest: '0.2.7',
                 notes: '一些说明\n第二行',
                 asset: { url: 'http://x/y.zip', size: 1000 } };
    },
  };

  for (const name of Object.keys(spots)) {
    ck('「' + name + '」滚到一半，重绘之后还在原处', () => {
      spots[name]();
      sb.window.P2W_RENDER();
      if (!el._keep[name]) {
        throw new Error('这个滚动区没渲染出来。页面开头：'
          + el.innerHTML.slice(0, 200).replace(/\s+/g, ' '));
      }
      el._keep[name].scrollTop = 42;     // 用户往下滑
      sb.window.P2W_RENDER();            // 轮询期间每秒来这么一次
      // 🔴 **重绘之后必须重新取一次**，不能拿着重绘前的那个对象断言 ——
      //    重绘会把容器换成新元素，旧对象的 scrollTop 永远是刚才自己设的
      //    42，跟恢复逻辑跑没跑完全无关。第一版就是这么写的，四条全是
      //    假绿的，靠变异（把恢复改成不按名字配对）才抓出来。
      const after = el._keep[name];
      if (!after) throw new Error('重绘之后这个滚动区没了');
      if (after.scrollTop !== 42) {
        throw new Error('被滚回了 ' + after.scrollTop + '，该留在 42');
      }
    });
  }

  ck('滚动区不在时不因为找不到它而出错', () => {
    Object.assign(st, ready(sb));
    st.about = 'env';
    st.cacheOpen = false;
    sb.window.P2W_RENDER();              // 不抛就算过
  });

  ck('日志区不走这套 —— 它要的是贴底跟着滚，不是留在原处', () => {
    // 🔴 别顺手把日志区也改成 data-keep-scroll：它有自己的规矩
    //    （用户翻上去看历史就别拽他，贴着底就跟着新行走）。
    const src = require('fs').readFileSync(
      'D:/claude_code_workspace/pdf_to_word/app/renderer/pages.js', 'utf8');
    const at = src.indexOf('id="dllog"');
    if (at < 0) throw new Error('日志区不见了');
    const seg = src.slice(at - 200, at + 200);
    if (seg.includes('data-keep-scroll')) {
      throw new Error('日志区被并进通用那套了，它的贴底行为会丢');
    }
  });
}


console.log('\n下好的升级要能被发现、能装上：');
{
  // 🔴 2026-09-07 小蔡实测：torch 2.14 下好了、界面说「重启后生效」，
  //    重启之后什么都没发生，版本还是 2.11，再点检查又要重下一遍。
  //    后端 install() 和 /api/upgrade/pending 都是好的 —— **前端从来
  //    没调过它们**。整条链在「谁按下那个装」这一环断了。
  const sb = mkSandbox();
  const fn = sb.window.P2W_PAGES.main;
  const envSt = (pend) => {
    const st = Object.assign(ready(sb), { about: 'env' });
    st.upgPending = pend;
    st.deps = { torch: { local: '2.11.0+cu128', latest: '2.14.0+cu126' } };
    return st;
  };

  ck('开机时会去问「有没有下好等着装的」', () => {
    const sb2 = mkSandbox();
    const seen = [];
    sb2.fetch = (u) => {
      seen.push({ url: String(u), port: sb2.window.P2W_STATE.port });
      return { then: () => ({ then: () => ({ catch: () => {} }) }) };
    };
    sb2.api.getPort = () => ({
      then: (f) => { f(1234); return { then: () => ({ catch: () => {} }) }; },
    });
    sb2._on['DOMContentLoaded']();
    const hit = seen.filter((x) => x.url.indexOf('/api/upgrade/pending') >= 0);
    if (!hit.length) throw new Error('开机压根没问，发出去的：' + JSON.stringify(seen.map(x => x.url)));
    // 🔴 跟拉历史一样，必须在拿到 port 之后 —— 否则 URL 里是 127.0.0.1:0
    for (const h of hit) {
      if (h.port !== 1234) throw new Error('端口还没就位就问了：' + h.url);
    }
  });

  ck('下载一完成就重新问一次，别让「立即重启」等到下次开机才出现', () => {
    // 🔴 2026-09-07 小蔡实测：「我点了，他显示下载完成，要重启，
    //    但是没有重启按钮。」
    //
    //    按钮显示什么取决于 st.upgPending，而那是**开机时**问的那一次 ——
    //    当时还没下载，结果自然是「没有待装的」。下载完成后没有任何人
    //    再问一次，界面就一直停在原来那两个按钮上，得关掉软件重开才冒出来。
    //
    // ⚠️ **这条必须是行为测试，不能查源码字符串**：第一版写的是
    //    「函数体里有没有 loadUpgPending」，而变异把那行改成注释
    //    `// loadUpgPending();` 之后，字符串照样在，测试照样绿 ——
    //    源码匹配分不清活代码和注释掉的代码。
    const sb2 = mkSandbox();
    const asked = [];
    // HTTP 是 actions.js 加载时捕获的同一个对象，换它的方法有效。
    sb2.window.P2W_HTTP.get = function (p) {
      asked.push(String(p));
      const body = String(p).indexOf('/api/upgrade/download') >= 0
        ? { state: 'done', ok: true } : { action: 'install', picked: ['torch'] };
      return { then: function (f) { f(body); return { catch: function () {} }; },
               catch: function () {} };
    };
    sb2.window.P2W_HTTP.post = function () {
      return { then: function (f) { f({}); return { catch: function () {} }; },
               catch: function () {} };
    };
    sb2.window.P2W_STATE.port = 1234;
    sb2.window.P2W_STATE.upgPick = { torch: true };
    sb2.window.P2W_ACTS.startUpgrade();
    if (!asked.some((u) => u.indexOf('/api/upgrade/pending') >= 0)) {
      throw new Error('下完没有重新问 pending，「立即重启」不会出现。问过的：'
                      + JSON.stringify(asked));
    }
  });

  ck('有下好等着装的时候，那两个按钮换成「立即重启」', () => {
    // 小蔡定的：不弹窗，就在升级区原地替换，也不要「稍后重启」。
    const h = fn(envSt({ action: 'install', picked: ['torch', 'torchvision'] }));
    // 🔴 按钮写着「立即重启」，动作却是 installUpgrade —— 故意的：
    //    点一下要把「装 + 重启」一气呵成，裸的 restartApp 只重启不装，
    //    重启完还得有人再按一次「装」，就回到原来那个坑了。
    if (!h.includes('data-act="installUpgrade"')) throw new Error('没有「立即重启」');
    if (!h.includes('立即重启')) throw new Error('按钮文案不对');
    if (h.includes('data-act="startUpgrade"')) throw new Error('「下载并升级」还在');
    if (h.includes('data-act="planUpgrade"')) throw new Error('「看看会动哪些包」还在');
    if (h.includes('稍后')) throw new Error('不该有「稍后重启」');
  });

  ck('没下过东西时还是原来那两个按钮', () => {
    const h = fn(envSt({ action: 'none' }));
    if (!h.includes('data-act="startUpgrade"')) throw new Error('「下载并升级」不见了');
  });

  ck('下好的包被清理掉了，要说重下而不是让人白等', () => {
    // 用户点过「清理转换临时文件」——CACHE 就住在 paths.TMP 底下
    const h = fn(envSt({ action: 'redownload', picked: ['torch'],
                         missing: ['torch'] }));
    if (!h.includes('已被清理')) throw new Error('没说包被清理了');
    if (h.includes('data-act="restartApp"')) throw new Error('包都没了还让人重启');
    if (!h.includes('data-act="startUpgrade"')) throw new Error('该给「重新下载」');
  });

  ck('正在装的时候显示进度，不显示按钮', () => {
    const st = envSt({ action: 'install', picked: ['torch'] });
    st.upgIns = { state: 'running', lines: ['Processing torch.whl'] };
    const h = fn(st);
    if (!h.includes('正在安装')) throw new Error('没说在装');
    if (h.includes('data-act="restartApp"')) throw new Error('装着呢还给重启按钮');
  });

  ck('装成功之后会自己重启，不用再点一次', () => {
    // 🔴 小蔡定的：只有「立即重启」一个按钮，点了就把整件事走完。
    //    装完还要用户再点一次的话，又回到「谁按下那一下」没人管的坑里。
    //
    // ⚠️ **这条是源码断言，测不到运行时行为** —— 装完重启这件事挂在
    //    fetch → json → then 的异步链末端，而这里的 ck 是同步的，
    //    撑不起那个链。所以它只能保证「ok 分支里确实写了 restart」，
    //    不能保证那个分支真的会被走到。别把它当成行为测试。
    const src = require('fs').readFileSync(
      'D:/claude_code_workspace/pdf_to_word/app/renderer/actions.js', 'utf8');
    const at = src.indexOf('function pollUpgIns');
    if (at < 0) throw new Error('找不到安装轮询');
    const seg = src.slice(at, at + 1200);
    if (!/d\.ok[\s\S]{0,400}window\.api\.restart\(\)/.test(seg)) {
      throw new Error('装成功之后没有重启，用户得自己再点一次');
    }
  });

  ck('装失败要把原因摆出来，并说明已经回滚', () => {
    const st = envSt({ action: 'install', picked: ['torch'] });
    st.upgIns = { state: 'done', ok: false, error: '装失败：磁盘满了',
                  rolled_back: true };
    const h = fn(st);
    if (!h.includes('磁盘满了')) throw new Error('原因没摆出来');
    if (!h.includes('已经回到升级前')) throw new Error('没说清楚回滚了，用户会以为环境坏了');
  });
}


// 🔴 **这个判断必须待在文件最末尾。** 它原来在中间（跑完前 115 条
//    就 exit），后面还有三个测试块 —— 那 15 条失败了退出码照样是 0，
//    末尾那句「前端全部通过」也照常打印。发版门禁认的就是这句话，
//    于是「红了也报绿」。（2026-09-06 加清理按钮那几条测试时发现：
//    自己写的两条明明是 ✗，脚本还是说全部通过。）
if (bad) {
  console.log('\u524d\u7aef\u68c0\u67e5\u5931\u8d25 ' + bad + ' \u9879');
  process.exit(1);
}

console.log('\u524d\u7aef\u5168\u90e8\u901a\u8fc7');
