// 前端检查：在假 window 里**真渲染**三个页面。
//
// 存在的理由是工作台那边的教训：题库页从头到尾没写渲染选项的代码，
// 而所有测试都绿着 —— 因为它们只测空态，空态根本走不到那段分支。
// 所以这里每一屏都喂真数据，并断言关键内容真的出现在 HTML 里。
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
  const sb = {
    console,
    document: {
      getElementById: () => ({ set innerHTML(v) { this._html = v; }, get innerHTML() { return this._html || ''; } }),
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

console.log('\u4e09\u4e2a\u9875\u9762\u90fd\u80fd\u52a0\u8f7d\uff1a');
{
  const sb = mkSandbox();
  ck('三个渲染函数都注册了', () => {
    const p = sb.window.P2W_PAGES;
    for (const k of ['check', 'pick', 'run']) {
      if (typeof p[k] !== 'function') throw new Error('缺 ' + k);
    }
  });
  ck('动作表齐全', () => {
    const a = sb.window.P2W_ACTS;
    for (const k of ['go', 'pickFiles', 'pickDir', 'toggle', 'selAll',
                     'selNone', 'clear', 'start', 'cancel', 'openFile']) {
      if (typeof a[k] !== 'function') throw new Error('缺动作 ' + k);
    }
  });
}

console.log('');
console.log('\u73af\u5883\u81ea\u68c0\u5c4f\uff1a');
{
  const sb = mkSandbox();
  const fn = sb.window.P2W_PAGES.check;

  ck('还在检查时有话说', () => {
    const st = baseState(sb);
    st.envLoading = true;
    const h = fn(st);
    if (!h.includes('正在检查')) throw new Error('加载态没提示');
  });

  ck('连不上后端时把原因原样显示', () => {
    const st = baseState(sb);
    st.envLoading = false;
    st.envError = 'ECONNREFUSED 127.0.0.1:9999';
    const h = fn(st);
    if (!h.includes('ECONNREFUSED')) throw new Error('吞掉了错误原因');
    if (!h.includes('重试')) throw new Error('没给重试的路');
  });

  ck('显卡够用时能进下一步', () => {
    const st = baseState(sb);
    st.envLoading = false;
    st.env = { gpu: { ok: true, why: '显卡「RTX 4060」，显存 8.0 GB，满足要求。' },
               office: { ok: true }, node: { ok: true },
               pandoc: { ok: true }, mineru: { ok: true } };
    const h = fn(st);
    if (!h.includes('RTX 4060')) throw new Error('没显示显卡型号');
    if (!h.includes('开始使用')) throw new Error('没有进入下一步的按钮');
  });

  ck('显卡不够时让用户自己选，不替他做主', () => {
    const st = baseState(sb);
    st.envLoading = false;
    st.env = { gpu: { ok: false, why: '这台电脑没有找到可用的独立显卡。' },
               office: { ok: false }, node: { ok: true },
               pandoc: { ok: true }, mineru: { ok: true } };
    const h = fn(st);
    if (!h.includes('仍然继续')) throw new Error('没给「硬来」这条路');
    if (!h.includes('退出')) throw new Error('没给退出这条路');
  });

  ck('没装 Office 说清楚不影响使用', () => {
    const st = baseState(sb);
    st.envLoading = false;
    st.env = { gpu: { ok: true, why: 'ok' }, office: { ok: false },
               node: { ok: true }, pandoc: { ok: true }, mineru: { ok: true } };
    const h = fn(st);
    if (!h.includes('也能用')) throw new Error('会让人以为必须装 Office');
  });

  ck('转换引擎缺失时不让往下走', () => {
    const st = baseState(sb);
    st.envLoading = false;
    st.env = { gpu: { ok: true, why: 'ok' }, office: { ok: true },
               node: { ok: true }, pandoc: { ok: true }, mineru: { ok: false } };
    const h = fn(st);
    if (h.includes('开始使用')) throw new Error('引擎没装好却放行了');
  });
}

console.log('');
console.log('\u9009\u4e66\u5c4f\uff1a');
{
  const sb = mkSandbox();
  const fn = sb.window.P2W_PAGES.pick;

  ck('空态给拖放区和两个入口', () => {
    const h = fn(baseState(sb));
    if (!h.includes('拖到这里')) throw new Error('没有拖放提示');
    if (!h.includes('选文件') || !h.includes('选文件夹')) throw new Error('缺入口');
  });

  ck('拖动时落点有反馈', () => {
    const st = baseState(sb);
    st.dragging = true;
    if (!fn(st).includes('松手就行')) throw new Error('拖动时没反馈');
  });

  ck('列表把每份的页数显示出来', () => {
    const st = baseState(sb);
    st.items = [{ ok: true, path: 'D:\\书\\解不等式.pdf', pages: 10,
                  scan_pages: [], error: '' }];
    const h = fn(st);
    if (!h.includes('解不等式.pdf')) throw new Error('文件名没显示');
    if (!h.includes('10 页')) throw new Error('页数没显示');
  });

  ck('无文字层的页要标出来', () => {
    const st = baseState(sb);
    st.items = [{ ok: true, path: 'D:\\a.pdf', pages: 30,
                  scan_pages: [27, 28, 30], error: '' }];
    const h = fn(st);
    if (!h.includes('27')) throw new Error('没标出哪几页没有文字层');
  });

  ck('整份没文字层时说人话', () => {
    const st = baseState(sb);
    st.items = [{ ok: true, path: 'D:\\a.pdf', pages: 3,
                  scan_pages: [1, 2, 3], error: '' }];
    if (!fn(st).includes('整份')) throw new Error('该说整份没有文字层');
  });

  ck('坏文件显示原因而不是消失', () => {
    const st = baseState(sb);
    st.items = [{ ok: false, path: 'D:\\bad.pdf', pages: 0, scan_pages: [],
                  error: '这份 PDF 有密码，请先解密' }];
    const h = fn(st);
    if (!h.includes('有密码')) throw new Error('坏文件的原因被吞了');
  });

  ck('没选中任何一份时不能开始', () => {
    const st = baseState(sb);
    st.items = [{ ok: true, path: 'D:\\a.pdf', pages: 1, scan_pages: [], error: '' }];
    st.picked['D:\\a.pdf'] = false;
    const h = fn(st);
    if (!/开始转换<\/button>/.test(h)) throw new Error('按钮不见了');
    if (!h.includes('disabled')) throw new Error('一份没选却能点开始');
  });

  ck('默认输出位置说得明白', () => {
    const st = baseState(sb);
    st.items = [{ ok: true, path: 'D:\\a.pdf', pages: 1, scan_pages: [], error: '' }];
    if (!fn(st).includes('跟原 PDF 放在一起')) throw new Error('没说清楚放哪');
  });

  ck('文件名里的尖括号被转义', () => {
    const st = baseState(sb);
    st.items = [{ ok: true, path: 'D:\\<script>x.pdf', pages: 1,
                  scan_pages: [], error: '' }];
    const h = fn(st);
    if (h.includes('<script>x.pdf')) throw new Error('文件名没转义，可被注入');
  });
}

console.log('');
console.log('\u8f6c\u6362\u5c4f\uff1a');
{
  const sb = mkSandbox();
  const fn = sb.window.P2W_PAGES.run;

  ck('最显眼的位置是「还要多久」', () => {
    // 用户唯一关心的就是这个数。MinerU 的阶段进度答不了它：
    // 各阶段耗时差 100 倍，跑满一整条也可能只花 1 秒。
    const st = baseState(sb);
    st.task = { state: 'running', total: 3, current: 1, current_name: '解不等式.pdf',
                stage: '识别公式', stage_cur: 4, stage_total: 10,
                results: [], elapsed: 65, remain: 320 };
    const h = fn(st);
    if (!h.includes('还要约 5 分 20 秒')) throw new Error('没显示剩余时间');
    // 剩余时间要比阶段名更显眼（字号更大）
    const iEta = h.indexOf('还要约');
    const iStage = h.indexOf('识别公式');
    if (iEta < 0 || iStage < 0) throw new Error('内容不全');
    if (iEta > iStage) throw new Error('剩余时间排在阶段名后面，不够显眼');
  });

  ck('当前文件和阶段仍然看得到，只是降级成小字', () => {
    const st = baseState(sb);
    st.task = { state: 'running', total: 3, current: 1, current_name: '解不等式.pdf',
                stage: '识别公式', stage_cur: 4, stage_total: 10,
                results: [], elapsed: 65, remain: 320 };
    const h = fn(st);
    if (!h.includes('解不等式.pdf')) throw new Error('没显示当前文件');
    if (!h.includes('识别公式')) throw new Error('没显示当前阶段');
    if (!h.includes('4/10')) throw new Error('没显示阶段内进度');
  });

  ck('只转一份时不显示「第几份」', () => {
    // 一份的时候「第 1 / 1 份」是噪声
    const st = baseState(sb);
    st.task = { state: 'running', total: 1, current: 0, current_name: 'a.pdf',
                stage: '识别公式', stage_cur: 1, stage_total: 10,
                results: [], elapsed: 10, remain: 200 };
    const h = fn(st);
    if (h.includes('第 1 / 1 份')) throw new Error('一份也显示了「第几份」');
  });

  ck('估不出来时说正在估算，不瞎猜一个数', () => {
    const st = baseState(sb);
    st.task = { state: 'running', total: 1, current: 0, current_name: 'a.pdf',
                stage: '', stage_cur: 0, stage_total: 0,
                results: [], elapsed: 1, remain: null };
    const h = fn(st);
    if (!h.includes('正在估算')) throw new Error('估不出来却没说');
    if (h.includes('还要约')) throw new Error('估不出来还是给了个数');
  });

  ck('转换中能停止，并说清楚停止的时机', () => {
    const st = baseState(sb);
    st.task = { state: 'running', total: 1, current: 0, current_name: 'a.pdf',
                stage: '', stage_cur: 0, stage_total: 0, results: [], elapsed: 1 };
    const h = fn(st);
    if (!h.includes('停止')) throw new Error('没有停止按钮');
    if (!h.includes('转完之后')) throw new Error('没说清停止不是立刻生效');
  });

  ck('完成后每份都能打开', () => {
    const st = baseState(sb);
    st.task = { state: 'done', total: 1, current: 1, current_name: '',
                stage: '', stage_cur: 0, stage_total: 0, elapsed: 240,
                results: [{ ok: true, pdf: 'D:\\a.pdf', docx: 'D:\\a.docx',
                            line: '10 页 ｜ 公式 213 ｜ 表格 2 ｜ 图 4 ｜ 公式走 Office' }] };
    const h = fn(st);
    if (!h.includes('转换完成')) throw new Error('没说完成');
    if (!h.includes('打开')) throw new Error('不能打开产物');
    if (!h.includes('所在文件夹')) throw new Error('不能打开所在文件夹');
    if (!h.includes('公式 213')) throw new Error('没显示这一份的摘要');
  });

  ck('失败的那份显示原因，不是干瞪眼', () => {
    const st = baseState(sb);
    st.task = { state: 'done', total: 1, current: 1, current_name: '',
                stage: '', stage_cur: 0, stage_total: 0, elapsed: 5,
                results: [{ ok: false, pdf: 'D:\\a.pdf', docx: '',
                            error: '提取跑完了但没找到产物（退出码 1）' }] };
    const h = fn(st);
    if (!h.includes('退出码 1')) throw new Error('失败原因被吞了');
  });

  ck('一份失败一份成功要分别显示', () => {
    const st = baseState(sb);
    st.task = { state: 'done', total: 2, current: 2, current_name: '',
                stage: '', stage_cur: 0, stage_total: 0, elapsed: 9,
                results: [{ ok: false, pdf: 'D:\\a.pdf', docx: '', error: '坏了' },
                          { ok: true, pdf: 'D:\\b.pdf', docx: 'D:\\b.docx',
                            line: '3 页' }] };
    const h = fn(st);
    if (!h.includes('失败')) throw new Error('没标出失败的那份');
    if (!h.includes('完成')) throw new Error('没标出成功的那份');
  });

  ck('停止之后说的是已停止而不是已完成', () => {
    const st = baseState(sb);
    st.task = { state: 'cancelled', total: 3, current: 1, current_name: '',
                stage: '', stage_cur: 0, stage_total: 0, elapsed: 60, results: [] };
    const h = fn(st);
    if (!h.includes('已停止')) throw new Error('停止了却说完成');
  });

  ck('任务还没回来时不空白', () => {
    const st = baseState(sb);
    st.task = null;
    if (!fn(st).trim()) throw new Error('渲染了个空白页');
  });
}

console.log('');
console.log('\u4e0b\u8f7d\u6a21\u578b\u5c4f\uff1a');
{
  const sb = mkSandbox();
  const fn = sb.window.P2W_PAGES.model;

  ck('测速中有话说', () => {
    const st = baseState(sb);
    st.srcLoading = true;
    if (!fn(st).includes('测试各个下载源')) throw new Error('测速时没提示');
  });

  ck('显示的是预计几分钟，不是 MB/s', () => {
    const st = baseState(sb);
    st.sources = [
      { id: 'modelscope', name: 'ModelScope（阿里，国内快）', ok: true, eta: '约 8 分钟', error: '' },
      { id: 'hf-mirror', name: 'HF-Mirror（国内镜像）', ok: true, eta: '约 21 分钟', error: '' },
    ];
    st.srcPick = 'modelscope';
    const h = fn(st);
    if (!h.includes('约 8 分钟')) throw new Error('没显示预计耗时');
    for (const bad of ['MB/s', 'KB/s', 'bps']) {
      if (h.includes(bad)) throw new Error('显示了 ' + bad + '，老师看不懂');
    }
  });

  ck('最快的默认选中，电脑盲直接点开始即可', () => {
    const st = baseState(sb);
    st.sources = [
      { id: 'modelscope', name: '快的', ok: true, eta: '约 8 分钟', error: '' },
      { id: 'huggingface', name: '慢的', ok: true, eta: '约 40 分钟', error: '' },
    ];
    st.srcPick = 'modelscope';
    const h = fn(st);
    const i = h.indexOf('快的');
    const j = h.indexOf('慢的');
    if (i < 0 || j < 0) throw new Error('源没列出来');
    if (h.slice(0, i).lastIndexOf('\u25cf') < 0) throw new Error('最快的那个没被选中');
    if (!h.includes('开始下载')) throw new Error('没有开始下载的按钮');
  });

  ck('连不上的源变灰且不能选', () => {
    const st = baseState(sb);
    st.sources = [
      { id: 'a', name: '能用的', ok: true, eta: '约 8 分钟', error: '' },
      { id: 'b', name: '连不上的', ok: false, eta: '连不上', error: 'timeout' },
    ];
    st.srcPick = 'a';
    const h = fn(st);
    if (!h.includes('连不上')) throw new Error('没标出连不上的源');
    const seg = h.slice(h.indexOf('连不上的') - 300, h.indexOf('连不上的'));
    if (seg.includes('data-act="pickSource" data-arg="b"')) {
      throw new Error('连不上的源还能点');
    }
  });

  ck('全都连不上时不让点开始并说明原因', () => {
    const st = baseState(sb);
    st.sources = [{ id: 'a', name: 'x', ok: false, eta: '连不上', error: 'timeout' }];
    const h = fn(st);
    if (!h.includes('disabled')) throw new Error('全连不上却还能点开始');
    if (!h.includes('检查一下网络')) throw new Error('没说该怎么办');
  });

  ck('给已有模型的人一条路', () => {
    const st = baseState(sb);
    st.sources = [{ id: 'a', name: 'x', ok: true, eta: '约 8 分钟', error: '' }];
    if (!fn(st).includes('我已经有模型了')) throw new Error('没给本地导入的入口');
  });

  ck('下载中说明断了也能续', () => {
    const st = baseState(sb);
    st.dl = { running: true, got: 1200, total: 4600 };
    const h = fn(st);
    if (!h.includes('接着上次的位置')) throw new Error('没说断点续传，人会不敢关');
  });

  ck('测速失败时把原因显示出来', () => {
    const st = baseState(sb);
    st.srcError = 'Failed to fetch';
    const h = fn(st);
    if (!h.includes('Failed to fetch')) throw new Error('吞掉了错误');
    if (!h.includes('重新测速')) throw new Error('没给重试的路');
  });
}

console.log('');
if (bad) { console.log('\u5931\u8d25 ' + bad + ' \u9879'); process.exit(1); }
console.log('\u524d\u7aef\u5168\u90e8\u901a\u8fc7');
