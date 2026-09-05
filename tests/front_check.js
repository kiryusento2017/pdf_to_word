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
    let logEl = null;
    return {
      set innerHTML(v) {
        this._html = v;
        mainEl = { scrollTop: 0 };
        // 日志区跟 .main 一样，innerHTML 换掉之后是**新元素**。
        // 给出 scrollHeight/clientHeight，粘底判据才算得出来。
        logEl = v.indexOf('id="dllog"') >= 0
          ? { scrollTop: 0, scrollHeight: 1000, clientHeight: 100 } : null;
      },
      get innerHTML() { return this._html || ''; },
      querySelector: (s) => (s === '.main' ? mainEl
                           : (s === '#dllog' ? logEl : null)),
      get _main() { return mainEl; },
      get _log() { return logEl; },
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
                     'openFile', 'openPath', 'openOffice', 'openNode',
                     'cancelDownload', 'checkUpdate', 'closeUpdate',
                     'downloadUpdate', 'restartApp',
                     'openAbout', 'closeAbout', 'openEnvCheck',
                     'checkDeps', 'toggleMaint', 'toggleCache',
                     'doClean', 'copyDiag', 'toggleUpdNotes',
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

  ck('没装 Office 时拦住，并说清楚为什么要装', () => {
    // 2026-09-01 小蔡改定：XSL 是硬性要求，不再降级到 Pandoc。
    // 这一屏是被拦下来的老师唯一能看到的解释。
    const st = ready(sb);
    st.env.office = { ok: false };
    st.env.formula = { ok: false,
      why: '这台电脑没有装微软 Office。本软件把公式转成 Word 原生公式，'
         + '要用到 Office 自带的一个转换文件（MML2OMML.XSL），'
         + '那是微软的文件，不能随本软件分发，只能装了 Office 才有。' };
    const h = fn(st);
    if (h.includes('把 PDF 拖进来')) throw new Error('没装 Office 却放行了');
    if (!h.includes('需要先安装微软 Office')) throw new Error('没说要装什么');
    if (!h.includes('MML2OMML')) throw new Error('没解释为什么需要它');
    if (!h.includes('data-act="openOffice"')) throw new Error('没给去官网的入口');
    if (!h.includes('data-act="reload"')) throw new Error('装完之后没法重新检查');
    if (!h.includes('WPS')) throw new Error('没提醒「装 WPS 不管用」——这是最容易踩的坑');
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

  ck('转换进行中，关于按钮禁用但不消失', () => {
    // 🔴 小蔡 2026-09-03 定的。更新包覆盖的正是 pipeline/*.py，而转换
    //    每处理一份 PDF 就新起一次 MinerU 子进程 —— 转到一半换掉代码，
    //    后面几份读到的是新代码；装完还要重启，一重启这批全废。
    //
    //    **但不能把按钮拿掉**：上面那条注释写着「卡在安装任何一步的
    //    用户，唯一的自救手段就是更新到修好的版本」。禁用 ≠ 移除。
    const st = ready(sb);
    st.task = { state: 'running', items: [] };
    const h = fn(st);
    if (!h.includes('data-act="openAbout"')) throw new Error('按钮被拿掉了');
    const i = h.indexOf('data-act="openAbout"');
    const tag = h.slice(i, h.indexOf('>', i));
    if (!tag.includes('disabled')) throw new Error('转换中却还能点');
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
    // 连 Office 都没有的话，先解决那个 —— 更新了也用不了
    const st = ready(sb);
    st.env.formula = { ok: false, why: '没装 Office' };
    st.upd = { ok: true, has_update: true, local: 'v1', latest: 'v2', error: '' };
    const h = fn(st);
    if (h.includes('有新版本 v2')) throw new Error('更新面板盖住了 Office 拦截屏');
    if (!h.includes('需要先安装微软 Office')) throw new Error('该拦的没拦');
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
if (bad) {
  console.log('\u524d\u7aef\u68c0\u67e5\u5931\u8d25 ' + bad + ' \u9879');
  process.exit(1);
}

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

console.log('\u524d\u7aef\u5168\u90e8\u901a\u8fc7');
