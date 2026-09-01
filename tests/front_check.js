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
    let mainEl = { scrollTop: 0 };
    return {
      set innerHTML(v) { this._html = v; mainEl = { scrollTop: 0 }; },
      get innerHTML() { return this._html || ''; },
      querySelector: (s) => (s === '.main' ? mainEl : null),
      get _main() { return mainEl; },
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

// 一切正常的环境，绝大多数用例从它出发
function goodEnv() {
  return {
    gpu: { ok: true, why: '显卡「RTX 4060」，显存 8.0 GB，满足要求。' },
    mineru: { ok: true }, pandoc: { ok: true }, office: { ok: true },
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
                     'openFile', 'openPath']) {
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
    el._main.scrollTop = 500;          // 用户滚到中间
    sb2.window.P2W_RENDER();           // 勾一下 / 轮询一次都会走到这
    if (el._main.scrollTop !== 500) {
      throw new Error('滚动位置丢了，弹回了 ' + el._main.scrollTop);
    }
  });

  ck('在顶部时不做多余的滚动设置', () => {
    const sb2 = mkSandbox();
    const el = sb2.document.getElementById('app');
    sb2.window.P2W_RENDER();
    if (el._main.scrollTop !== 0) throw new Error('本来在顶部，却被挪到了别处');
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

  ck('没装 Office 说清楚不影响使用', () => {
    const st = ready(sb);
    st.env.office = { ok: false };
    const h = fn(st);
    if (h.includes('data-act="ackGate"')) throw new Error('没装 Office 不该拦');
    if (!h.includes('用内置引擎')) throw new Error('没说清楚不影响使用');
  });

  ck('转换引擎缺失时拦死，且没有「仍然继续」', () => {
    const st = ready(sb);
    st.env.mineru = { ok: false };
    const h = fn(st);
    if (h.includes('data-act="ackGate"')) throw new Error('引擎都没有还能继续？');
    if (!h.includes('转换引擎还没装好')) throw new Error('没说清楚缺什么');
    if (!h.includes('setup_env')) throw new Error('没告诉人怎么装');
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
    if (!h.includes('4/10')) throw new Error('没显示阶段内进度');
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

  ck('转换中能停止，并说清楚停止的时机', () => {
    const h = fn(runState());
    if (!h.includes('data-act="cancel"')) throw new Error('没有停止按钮');
    if (!h.includes('当前这份转完之后生效')) throw new Error('没说清楚停止时机');
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

  ck('「再转一批」把失败的自动勾上、成功的取消勾选', () => {
    // 全成功的人得到清爽的空勾选；有失败的人点一下开始就是重试
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
    if (real.picked['C:\\a\\好.pdf'] !== false) throw new Error('成功的没取消勾选');
    if (real.picked['C:\\a\\坏.pdf'] !== true) throw new Error('失败的没勾上');
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

  ck('下载中说明断了也能续', () => {
    const st = ready(sb);
    st.dl = { running: true, got: 1e9, total: 4.6e9 };
    const h = fn(st);
    if (!h.includes('正在下载')) throw new Error('没显示下载中');
    if (!h.includes('接着上次的位置继续')) throw new Error('没说明能续传');
    if (!h.includes('22%')) throw new Error('没显示百分比');
  });

  ck('测速失败时把原因显示出来', () => {
    const st = ready(sb);
    st.srcError = 'getaddrinfo ENOTFOUND modelscope.cn';
    const h = fn(st);
    if (!h.includes('getaddrinfo ENOTFOUND modelscope.cn')) throw new Error('没显示原因');
    if (!h.includes('data-act="probeSources"')) throw new Error('没给重试的路');
  });
}

console.log('');
if (bad) {
  console.log('\u524d\u7aef\u68c0\u67e5\u5931\u8d25 ' + bad + ' \u9879');
  process.exit(1);
}
console.log('\u524d\u7aef\u5168\u90e8\u901a\u8fc7');
