// 两屏的渲染函数。
//
// 每个 page 函数是纯函数：state 进，HTML 字符串出，不碰 DOM、不发请求。
// 这样能在假 window 里真渲染来测（tests/front_check.js）——
// 工作台那边的教训是「空态测试全绿也照样漏掉整段没写的代码」。
//
// 结构是固定的三段：工具条 / 主区 / 状态栏。主区 flex:1，所以**任何状态下
// 都铺满窗口**，不靠 padding 凑。主体永远是那张文件表：待转时是待转清单，
// 转换中原地变进度，转完变结果 —— 不跳屏，用户的视线不用重新找位置。
'use strict';

var esc = window.P2W_ESC;
var F = window.P2W_FMT;
var running = window.P2W_RUNNING;

// ── 小构件 ─────────────────────────────────────────────────────────────
function bar(cur, total) {
  var pct = total > 0 ? Math.round(100 * cur / total) : 0;
  if (pct < 0) pct = 0; if (pct > 100) pct = 100;
  return '<div class="bar"><i style="width:' + pct + '%"></i></div>';
}

function dot(color) {
  return '<span class="dot" style="background:' + color + '"></span>';
}

function btn(act, text, opt) {
  opt = opt || {};
  return '<button data-act="' + act + '"'
    + (opt.arg ? ' data-arg="' + esc(opt.arg) + '"' : '')
    + (opt.cls ? ' class="' + opt.cls + '"' : '')
    + (opt.title ? ' title="' + esc(opt.title) + '"' : '')
    + (opt.off ? ' disabled' : '') + '>' + esc(text) + '</button>';
}

// 三段外壳。页面函数只管填这三块，高度分配由 CSS 保证。
function shell(top, main, bot) {
  return '<div class="chrome-top">' + top + '</div>'
    + '<div class="main">' + main + '</div>'
    + '<div class="chrome-bot">' + bot + '</div>';
}

// ── 环境自检的两种呈现 ─────────────────────────────────────────────────
// 一是压进状态栏的一句话，二是必须让用户拿主意时占住主区的拦截页。

function envLine(st) {
  if (st.envLoading) return '<span class="f-dim">正在检查这台电脑…</span>';
  if (st.envError) return dot('#b91c1c') + ' <span class="f-bad">后台没连上</span>';
  var e = st.env || {};
  var g = e.gpu || {};
  if (!(e.writable || {}).ok) return dot('#b91c1c') + ' <span class="f-bad">安装目录不可写</span>';
  if (!(e.mineru || {}).ok) return dot('#b91c1c') + ' <span class="f-bad">转换引擎缺失</span>';
  // Office 现在是硬性要求（2026-09-01 改定），不再是「有更好」。
  if (!(e.formula || {}).ok) return dot('#b91c1c') + ' <span class="f-bad">缺少 Office</span>';
  var parts = [];
  parts.push(g.ok ? '显卡 ✓' : '显卡 ✗');
  parts.push('Office ✓');
  return dot(g.ok ? '#15803d' : '#b45309') + ' <span'
    + (g.ok ? '' : ' class="f-warn"') + '>' + parts.join('　·　') + '</span>';
}

// 要不要占住主区拦一下。返回 '' 表示放行。
function gateKind(st) {
  if (st.envLoading || st.envError) return '';
  var e = st.env || {};
  // 按严重程度排：写不了盘 > 没公式引擎 > 没转换引擎 > 显卡不够
  if (!(e.writable || {}).ok) return 'writable';    // 什么都干不了
  if (!(e.formula || {}).ok) return 'formula';      // 核心功能废，硬拦
  if (!(e.mineru || {}).ok) return 'engine';        // 硬拦，没得选
  if (!(e.gpu || {}).ok && !st.gateAck) return 'gpu';  // 软拦，用户自己选
  return '';
}

function gateView(st, kind) {
  var e = st.env || {};

  if (kind === 'writable') {
    // 「所有文件留在安装文件夹」的代价：装到 Program Files 就没法用。
    // 与其等用户拖完 PDF 点了转换才报权限错，不如开门就说清楚。
    return '<div class="fill">'
      + '<div style="font-size:14px;font-weight:600">这个位置不能写文件</div>'
      + '<div class="f-dim" style="max-width:460px;line-height:1.6">'
      + '本软件把模型和临时文件都放在自己的文件夹里（不往系统盘乱塞东西），'
      + '所以不能装在「C:\\Program Files」这类需要管理员权限的地方。'
      + '<br>把整个文件夹剪切到 D 盘之类的位置，再打开就好。</div>'
      + '<div class="f-dim mono" style="font-size:11px">'
      + esc((e.writable || {}).dir || '') + '</div>'
      + '<div style="display:flex;gap:8px">'
      + btn('reload', '我挪好了，重新检查', { cls: 'primary' })
      + btn('quit', '退出') + '</div></div>';
  }

  if (kind === 'formula') {
    // 小蔡 2026-09-01 定：必须有微软 Office，不再降级。
    // 这一屏是被拦下来的老师唯一能看到的解释，必须说清楚三件事：
    // 为什么需要、要装什么、装完怎么办。
    var f = e.formula || {};
    var noNode = (e.node || {}).ok === false && (e.office || {}).ok;
    return '<div class="fill">'
      + '<div style="font-size:14px;font-weight:600">'
      + (noNode ? '安装包不完整' : '需要先安装微软 Office') + '</div>'
      + '<div class="f-dim" style="max-width:470px;line-height:1.65;text-align:left">'
      + esc(f.why || '') + '</div>'
      + (noNode ? '' :
         '<div class="f-dim" style="max-width:470px;line-height:1.65;text-align:left">'
         + '装 Microsoft 365、或者 Office 2021 / 2024 都可以，'
         + '装好之后回来点「重新检查」。<br>'
         + '<b>只装 WPS 不行</b> —— WPS 没有这个转换文件（我们查过它的安装目录）。'
         + '</div>')
      + '<div style="display:flex;gap:8px;margin-top:2px">'
      + (noNode ? btn('openNode', '去 nodejs.org 下载', { cls: 'primary' })
                : btn('openOffice', '去微软官网看看', { cls: 'primary' }))
      + btn('reload', '重新检查')
      + btn('quit', '退出') + '</div></div>';
  }

  if (kind === 'engine') {
    return '<div class="fill">'
      + '<div style="font-size:14px;font-weight:600">转换引擎还没装好</div>'
      + '<div class="f-dim" style="max-width:420px">'
      + '装好之后重开软件。安装办法见项目里的 README，'
      + '跑一次 tools\\setup_env.py 就行。</div>'
      + '<div>' + btn('reload', '重新检查') + '</div></div>';
  }
  // 显卡不满足：**让用户自己选退出还是硬来，不替他做主**。
  // 话术里必须有具体分钟数（后端 gpu.py 给的 why 就带着），
  // 「会慢很多」这种等于没说。
  var why = (e.gpu || {}).why || '这台电脑的显卡不满足要求。';
  return '<div class="fill">'
    + '<div style="font-size:14px;font-weight:600">显卡不满足要求</div>'
    + '<div class="f-dim" style="max-width:440px;line-height:1.6">' + esc(why) + '</div>'
    + '<div style="display:flex;gap:8px;margin-top:2px">'
    + btn('ackGate', '仍然继续', { cls: 'primary' })
    + btn('quit', '退出') + '</div></div>';
}

// ── 更新面板 ───────────────────────────────────────────────────────────
// 用户主动点「检查更新」才出现，盖住主区；关掉就回到原来的地方。
// 放主区而不是弹窗：620x440 的窗口里，更新说明有好几行，弹窗放不下。
function updateView(st) {
  var u = st.upd || {};
  var close = btn('closeUpdate', '关闭');

  if (st.updBusy && !u.dlTotal && !u.latest) {
    return '<div class="fill"><div class="f-dim">正在查看有没有新版本…</div></div>';
  }

  // 装好了，等重启
  if (u.installed) {
    return '<div class="fill">'
      + '<div style="font-size:14px;font-weight:600">更新完成</div>'
      + '<div class="f-dim">已更新 ' + (u.files || 0) + ' 个文件'
      + (u.via ? '　·　来自 ' + esc(u.via) : '') + '</div>'
      + '<div class="f-dim" style="max-width:440px;line-height:1.6">'
      + '重启一下就生效。你的模型和转好的文件都不受影响。</div>'
      + '<div style="display:flex;gap:8px;margin-top:2px">'
      + btn('restartApp', '立即重启', { cls: 'primary' })
      + btn('closeUpdate', '稍后重启') + '</div></div>';
  }

  // 正在下载 / 正在安装
  if (st.updBusy && u.asset) {
    if (u.phase === 'installing') {
      return '<div class="fill">'
        + '<div style="font-size:13px;font-weight:600">正在安装…</div>'
        + '<div class="f-dim">马上就好</div></div>';
    }
    var pct = u.dlTotal ? Math.min(100, Math.round(100 * (u.dlGot || 0) / u.dlTotal)) : 0;
    return '<div class="fill">'
      + '<div style="font-size:13px;font-weight:600">正在下载… ' + pct + '%</div>'
      + '<div style="width:70%">' + bar(u.dlGot || 0, u.dlTotal || 1) + '</div>'
      + '<div class="f-dim">' + F.gb(u.dlGot || 0)
      + (u.dlTotal ? ' / ' + F.gb(u.dlTotal) : '') + '</div>'
      + '<div class="f-dim">下完会自动装好，不用你动手</div></div>';
  }

  // 出错 / 说不清楚
  if (!u.ok || u.error) {
    return '<div class="fill">'
      + '<div style="font-size:14px;font-weight:600">'
      + (u.has_update ? '有新版本，但拿不到更新包' : '暂时没法检查更新') + '</div>'
      + '<div class="f-dim" style="max-width:460px;line-height:1.6">'
      + esc(u.error || '不知道为什么') + '</div>'
      + '<div style="display:flex;gap:8px">'
      + btn('checkUpdate', '再试一次') + close + '</div></div>';
  }

  // 已是最新
  if (!u.has_update) {
    return '<div class="fill">'
      + '<div style="font-size:14px;font-weight:600">已经是最新版本</div>'
      + '<div class="f-dim">' + esc(u.local || '') + '</div>'
      + '<div>' + close + '</div></div>';
  }

  // 有更新
  var notes = (u.notes || '').split('\n').slice(0, 6)
    .map(function (x) { return esc(x); }).join('<br>');
  return '<div class="fill" style="justify-content:flex-start;padding-top:14px">'
    + '<div style="font-size:14px;font-weight:600">有新版本 ' + esc(u.latest) + '</div>'
    + '<div class="f-dim">当前 ' + esc(u.local)
    + (u.published ? '　·　发布于 ' + esc(u.published) : '')
    + (u.asset && u.asset.size ? '　·　' + F.gb(u.asset.size) : '') + '</div>'
    + (notes ? '<div class="f-dim" style="max-width:90%;text-align:left;'
        + 'line-height:1.6;max-height:150px;overflow:auto">' + notes + '</div>' : '')
    + '<div style="display:flex;gap:8px;margin-top:4px">'
    + btn('downloadUpdate', '更新', { cls: 'primary' })
    + btn('closeUpdate', '暂不更新') + '</div></div>';
}


// ── 主屏 ───────────────────────────────────────────────────────────────
function pageMain(st) {
  // 后端没连上是致命的，占住主区说清楚，别让人对着空列表发呆。
  if (st.envError) {
    return shell(
      '<span class="f-dim" style="padding:0 4px">PDF 转 Word</span>',
      '<div class="fill">'
        + '<div style="font-size:14px;font-weight:600">连不上后台服务</div>'
        + '<div class="f-dim mono" style="max-width:460px;white-space:pre-wrap;'
        + 'text-align:left">' + esc(st.envError) + '</div>'
        + '<div>' + btn('reload', '重试') + '</div></div>',
      envLine(st));
  }

  var gate = gateKind(st);
  if (gate) {
    return shell('<span class="f-dim" style="padding:0 4px">PDF 转 Word</span>',
                 gateView(st, gate), envLine(st));
  }

  // 更新面板排在 gate 之后 —— 环境有问题的话，先解决环境。
  if (st.upd || st.updBusy) {
    return shell('<span class="f-dim" style="padding:0 4px">检查更新</span>',
                 updateView(st), envLine(st));
  }

  return running(st) || st.task ? mainRun(st) : mainPick(st);
}

// ── 主屏 · 待转态 ──────────────────────────────────────────────────────
function mainPick(st) {
  var out = st.outDir || '';
  var top = btn('pickFiles', '添加文件')
    + btn('pickDir', '添加文件夹')
    + (st.items.length ? btn('clear', '移除全部') : '')
    + '<span class="grow"></span>'
    + '<span class="f-dim ell" style="max-width:230px" title="'
    + esc(out || '跟原 PDF 放在一起') + '">输出：'
    + esc(out ? F.base(out) : '跟原 PDF 放在一起') + '</span>'
    + btn('pickOut', '更改')
    + (out ? btn('outDefault', '默认') : '');

  // 正在读文件夹时必须说话。实测 456 份的讲义库要 16 秒，这期间要是
  // 界面一个字不变，用户只会以为软件卡死了 —— 「反应很慢」的抱怨多半
  // 来自这里，而不是真的慢。
  if (st.scanning) {
    return shell(top,
      '<div class="fill">'
      + '<div style="font-size:13px;font-weight:600">正在读取…</div>'
      + '<div class="f-dim">逐份检查页数和文字层，文件夹里书多的话要等几秒</div>'
      + (st.items.length ? '<div class="f-dim">已经在列表里的 '
          + st.items.length + ' 份不受影响</div>' : '')
      + '</div>',
      envLine(st) + '<span class="grow"></span><span class="f-dim">读取中…</span>');
  }

  // 空列表也要铺满 —— 拖放区撑满整个主区，而不是一个居中的小方框。
  if (!st.items.length) {
    var main = '<div class="fill' + (st.dragging ? ' drop' : '') + '">'
      + '<div style="font-size:14px;font-weight:600">'
      + (st.dragging ? '松手就行' : '把 PDF 拖进来') + '</div>'
      + '<div class="f-dim">单个文件、多个文件、整个文件夹都行</div>'
      + '<div style="display:flex;gap:8px;margin-top:4px">'
      + btn('pickFiles', '选文件') + btn('pickDir', '选文件夹') + '</div>'
      + (st.err ? '<div class="f-bad" style="margin-top:6px">' + esc(st.err) + '</div>' : '')
      + '</div>';
    return shell(top, main, envLine(st)
      + btn('checkUpdate', '检查更新', { cls: 'link' })
      + '<span class="grow"></span>'
      + '<span class="f-dim">还没有文件</span>');
  }

  var n = 0, pages = 0;
  var rows = st.items.map(function (it) {
    var name = F.base(it.path);
    // 读不了的文件留在列表里显示原因 —— 悄悄消失会让人以为自己没选中。
    if (!it.ok) {
      return '<div class="it" title="' + esc(it.error || '') + '">'
        + '<span class="dot" style="background:#b91c1c"></span>'
        + '<span class="grow ell f-dim">' + esc(name) + '</span>'
        + '<span class="rt f-bad ell" style="max-width:220px">'
        + esc(it.error || '读不了') + '</span></div>';
    }
    var on = st.picked[it.path] !== false;
    if (on) { n++; pages += (it.pages || 0); }
    var note = '';
    if (it.scan_pages && it.scan_pages.length) {
      var sp = it.scan_pages;
      note = sp.length === it.pages
        ? '整份没有文字层'
        : ('第 ' + sp.slice(0, 3).join('、') + ' 页没有文字层'
           + (sp.length > 3 ? ' 等 ' + sp.length + ' 页' : ''));
    }
    // 勾选状态由 checkbox 表达就够了，**不再给整行染背景** ——
    // 十份书全勾上就是十行蓝，那是噪音不是信息。行高亮留给鼠标悬停，
    // 以及转换态里「当前正在处理的那一行」。没勾的把文件名调淡区分。
    return '<div class="it" data-act="toggle" data-arg="'
      + esc(it.path) + '" title="' + esc(name) + '">'
      + '<input type="checkbox" style="pointer-events:none"'
      + (on ? ' checked' : '') + '>'
      + '<span class="grow ell' + (on ? '' : ' f-dim') + '">' + esc(name) + '</span>'
      + (note ? '<span class="rt f-warn">' + esc(note) + '</span>' : '')
      + '<span class="rt" style="width:52px;text-align:right">'
      + (it.pages || 0) + ' 页</span></div>';
  }).join('');

  var head = '<div class="hd">'
    + '<span style="width:13px"></span>'
    + '<span class="grow">文件（共 ' + st.items.length + ' 份）</span>'
    + btn('selAll', '全选', { cls: 'link' })
    + btn('selNone', '全不选', { cls: 'link' })
    + '<span style="width:52px;text-align:right">页数</span></div>';

  var bot = envLine(st)
    + btn('checkUpdate', '检查更新', { cls: 'link' })
    + '<span class="grow"></span>'
    + '<span>' + (st.err ? '<span class="f-bad">' + esc(st.err) + '</span>'
        : ('选中 ' + n + ' 份 · ' + pages + ' 页')) + '</span>'
    + btn('start', st.starting ? '正在开始…' : '开始转换',
          { cls: 'primary', off: !n || st.starting || st.scanning });

  return shell(top, head + rows, bot);
}

// ── 主屏 · 转换中 / 结果 ───────────────────────────────────────────────
function mainRun(st) {
  var t = st.task;
  if (!t) {
    return shell('<span class="f-dim" style="padding:0 4px">PDF 转 Word</span>',
      '<div class="fill"><div class="f-dim">正在开始…</div></div>',
      envLine(st));
  }

  var done = t.state === 'done' || t.state === 'cancelled';
  var res = t.results || [];
  var okN = 0, badN = 0;
  res.forEach(function (r) { if (r.ok) okN++; else badN++; });

  // ── 顶部条：转换中这里是「还要多久」，窗口最显眼的位置 ──────────────
  // MinerU 的阶段进度答不了「还要多久」：各阶段耗时差 100 倍，跑满一整条
  // 也可能只花 1 秒。所以剩余时间加粗放顶上，阶段名降级到列表行里的小字。
  var top;
  if (!done) {
    var eta = (t.remain === null || t.remain === undefined)
      ? '正在估算…' : ('还要约 ' + F.sec(t.remain));
    top = '<span style="font-size:13px;font-weight:600;color:var(--theme);'
      + 'white-space:nowrap">' + esc(eta) + '</span>'
      + '<span class="grow" style="padding:0 4px">'
      + bar(t.current + (t.stage_total ? t.stage_cur / t.stage_total : 0), t.total)
      + '</span>'
      + (t.total > 1 ? '<span class="f-dim" style="white-space:nowrap">第 '
          + (t.current + 1) + ' / ' + t.total + ' 份</span>' : '');
  } else {
    top = '<span style="font-size:13px;font-weight:600">'
      + (t.state === 'cancelled' ? '已停止' : '转换完成') + '</span>'
      + '<span class="grow"></span>'
      + btn('newBatch', '再转一批', { cls: 'primary' });
  }

  // ── 列表：同一张表原地变，不跳屏 ────────────────────────────────────
  var byPath = {};
  res.forEach(function (r) { byPath[r.pdf] = r; });

  var rows = st.items.filter(function (it) {
    return it.ok && st.picked[it.path] !== false;
  }).map(function (it, i) {
    var name = F.base(it.path);
    var r = byPath[it.path];

    if (r && r.ok) {
      return '<div class="it" title="' + esc(r.docx) + '">'
        + dot('#15803d')
        + '<span class="grow ell">' + esc(F.base(r.docx)) + '</span>'
        + (r.line ? '<span class="rt ell" style="max-width:150px">'
            + esc(r.line) + '</span>' : '')
        + btn('openFile', '打开', { cls: 'link', arg: r.docx })
        + btn('openPath', '文件夹', { cls: 'link', arg: r.docx })
        + '</div>';
    }
    if (r && !r.ok) {
      return '<div class="it" title="' + esc(r.error || '') + '">'
        + dot('#b91c1c')
        + '<span class="grow ell f-dim">' + esc(name) + '</span>'
        + '<span class="rt f-bad ell" style="max-width:260px">失败：'
        + esc(r.error || '') + '</span></div>';
    }
    // 还没轮到 / 正在转。current 是已完成的份数，所以它就是当前这份的下标。
    var cur = !done && i === t.current;
    if (cur) {
      return '<div class="it on">' + dot('#1d4ed8')
        + '<span class="grow ell">' + esc(name) + '</span>'
        + '<span class="rt">' + esc(t.stage || '准备中')
        + (t.stage_total ? ' ' + t.stage_cur + '/' + t.stage_total : '') + '</span>'
        + '<span style="width:56px;flex:none">'
        + bar(t.stage_cur, t.stage_total || 1) + '</span></div>';
    }
    return '<div class="it">' + dot('#d0d0d0')
      + '<span class="grow ell f-dim">' + esc(name) + '</span>'
      + '<span class="rt">' + (done ? '未处理' : '等待') + '</span></div>';
  }).join('');

  // ── 状态栏 ──────────────────────────────────────────────────────────
  var bot;
  if (!done) {
    bot = '<span>已用 ' + F.sec(t.elapsed) + '</span>'
      + '<span class="grow f-dim ell">停止只在当前这份转完之后生效 ——'
      + ' 中途硬停会留下半截文件</span>'
      + btn('cancel', '停止');
  } else {
    bot = envLine(st)
      + '<span class="grow"></span>'
      + '<span>' + (badN ? ('成功 ' + okN + ' 份 · <span class="f-bad">失败 '
          + badN + ' 份</span>') : ('全部完成 ' + okN + ' 份'))
      + ' · 用时 ' + F.sec(t.elapsed) + '</span>';
  }

  return shell(top, rows || '<div class="fill"><div class="f-dim">没有要转的文件</div></div>', bot);
}

// ── 下载模型（首次使用，只出现一次）────────────────────────────────────
function pageModel(st) {
  var top = '<span style="font-weight:600;padding:0 4px">第一次使用，需要下载识别模型</span>'
    + '<span class="grow"></span>'
    + '<span class="f-dim">约 ' + esc(st.srcTotalGb || '4.6') + ' GB，只下这一次</span>';

  // 正在下载就显示下载 —— 这一判断必须在最前面。
  // 原先排在「源列表为空」的 early return 之后，结果下载进行中却因为
  // 列表为空而显示「还没测速」，用户会以为下载没开始。
  var dl = st.dl;
  if (dl && dl.running) {
    // 显示真实字节数而不只是百分比：总量 4.6 GB 是估的，百分比可能
    // 冲到 103% 或停在 97%，但「已下 2.3 GB」永远是真的。
    var pct = dl.total ? Math.min(100, Math.round(100 * dl.got / dl.total)) : 0;
    return shell(top,
      '<div class="fill">'
      + '<div style="font-size:13px;font-weight:600">正在下载模型…'
      + (dl.total ? '　' + pct + '%' : '') + '</div>'
      + '<div style="width:70%">' + bar(dl.got, dl.total || 1) + '</div>'
      + '<div class="f-dim">已下 ' + F.gb(dl.got) + ' / 约 ' + F.gb(dl.total) + '</div>'
      + (dl.line ? '<div class="f-dim mono ell" style="max-width:80%;font-size:11px">'
          + esc(dl.line) + '</div>' : '')
      + '<div class="f-dim">下载中断了也不要紧，重开软件会接着上次的位置继续。</div>'
      + '</div>',
      '<span class="f-dim">下载完成后自动进入主界面</span>'
      + '<span class="grow"></span>'
      + btn('cancelDownload', '停止下载'));
  }

  if (st.srcLoading) {
    return shell(top,
      '<div class="fill"><div class="f-dim">正在测试各个下载源的速度…</div></div>',
      '<span class="f-dim">同时连所有源实测 2-3 秒，比 ping 准</span>');
  }

  if (st.srcError) {
    return shell(top,
      '<div class="fill">'
      + '<div class="f-bad" style="max-width:440px">' + esc(st.srcError) + '</div>'
      + '<div>' + btn('probeSources', '重新测速') + '</div></div>',
      '<span class="f-dim">测速失败</span>');
  }

  var items = st.sources || [];
  if (!items.length) {
    return shell(top,
      '<div class="fill"><div class="f-dim">还没测速。</div>'
      + '<div>' + btn('probeSources', '测速', { cls: 'primary' }) + '</div></div>',
      '<span class="f-dim">先测速再选源</span>');
  }

  var anyOk = items.some(function (x) { return x.ok; });
  var head = '<div class="hd"><span style="width:13px"></span>'
    + '<span class="grow">下载源</span>'
    + '<span style="width:96px;text-align:right">预计耗时</span></div>';

  // 显示「预计几分钟」而不是 MB/s —— 电脑盲不必做换算题，
  // 最快的那个已经默认选好了，直接点开始就行。
  var rows = items.map(function (x) {
    var on = st.srcPick === x.id;
    return '<div class="it' + (on ? ' on' : '') + '"'
      + (x.ok ? ' data-act="pickSource" data-arg="' + esc(x.id) + '"'
              : ' style="opacity:.5"')
      + ' title="' + esc(x.ok ? x.name : (x.error || '连不上')) + '">'
      + '<input type="radio" style="pointer-events:none;width:13px;height:13px;'
      + 'flex:none;margin:0"' + (on ? ' checked' : '')
      + (x.ok ? '' : ' disabled') + '>'
      + '<span class="grow ell">' + esc(x.name) + '</span>'
      + '<span class="rt" style="width:96px;text-align:right">'
      + esc(x.ok ? x.eta : '连不上') + '</span></div>';
  }).join('');

  var bot = btn('startDownload', '开始下载', { cls: 'primary', off: !anyOk })
    + btn('probeSources', '重新测速')
    + btn('pickLocal', '我已经有模型了')
    + '<span class="grow"></span>'
    + (anyOk ? '' : '<span class="f-bad">所有下载源都连不上，检查一下网络</span>');

  return shell(top, head + rows, bot);
}

window.P2W_PAGES = { main: pageMain, model: pageModel };
