// 三屏的渲染函数 + 所有动作。
//
// 每个 page 函数是纯函数：state 进，HTML 字符串出，不碰 DOM、不发请求。
// 这样能在假 window 里真渲染来测（tests/front_check.js）——
// 工作台那边的教训是「空态测试全绿也照样漏掉整段没写的代码」。
'use strict';

var S = window.P2W_BS;
var esc = window.P2W_ESC;
var F = window.P2W_FMT;

// ── 小构件 ─────────────────────────────────────────────────────────────
function chip(text, kind) {
  var skin = {
    ok: 'background:rgba(21,128,61,.10);color:var(--ok)',
    warn: 'background:rgba(180,83,9,.10);color:var(--warn)',
    bad: 'background:rgba(185,28,28,.10);color:var(--bad)',
    faint: 'background:#f4f4f4;color:var(--ink3)',
  }[kind] || 'background:#f4f4f4;color:var(--ink2)';
  return '<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
    + 'font-size:11px;white-space:nowrap;' + skin + '">' + esc(text) + '</span>';
}

function bar(cur, total) {
  var pct = total > 0 ? Math.round(100 * cur / total) : 0;
  return '<div class="bar"><i style="width:' + pct + '%"></i></div>';
}

// ── ① 环境自检 ─────────────────────────────────────────────────────────
function pageCheck(st) {
  if (st.envLoading) {
    return '<div style="' + S.card + '">'
      + '<div style="' + S.h1 + '">正在检查这台电脑</div>'
      + '<div style="' + S.body + '">显卡、Office、转换引擎…</div></div>';
  }
  if (st.envError) {
    return '<div style="' + S.card + ';border-color:rgba(185,28,28,.3)">'
      + '<div style="' + S.h1 + '">连不上后台服务</div>'
      + '<div style="' + S.body + ';white-space:pre-wrap">' + esc(st.envError) + '</div>'
      + '<div style="margin-top:16px"><button data-act="reload">重试</button></div>'
      + '</div>';
  }

  var e = st.env || {};
  var g = e.gpu || {};
  var rows = [
    ['显卡', g.ok, g.why || ''],
    ['转换引擎', (e.mineru || {}).ok,
      (e.mineru || {}).ok ? '已就绪' : '还没安装，装好之后才能转换'],
    ['公式引擎', (e.pandoc || {}).ok,
      (e.pandoc || {}).ok ? '内置，无需安装' : '内置引擎缺失，这是安装包的问题'],
    ['Office', (e.office || {}).ok,
      (e.office || {}).ok
        ? '检测到 Office，公式会用它转换，效果更好'
        : '没装 Office 也能用，公式改由内置引擎转换'],
  ];

  var list = rows.map(function (r) {
    var kind = r[1] ? 'ok' : (r[0] === 'Office' ? 'faint' : 'warn');
    var word = r[1] ? '正常' : (r[0] === 'Office' ? '未安装' : '注意');
    return '<div class="row" style="padding:14px 0;display:flex;gap:14px;align-items:flex-start">'
      + '<div style="width:66px;flex:none;font-weight:500">' + esc(r[0]) + '</div>'
      + '<div style="width:52px;flex:none">' + chip(word, kind) + '</div>'
      + '<div style="' + S.body + ';flex:1">' + esc(r[2]) + '</div>'
      + '</div>';
  }).join('');

  // 显卡不满足时让用户自己决定：退出还是硬来。**不替他做主**。
  var blocked = !(e.mineru || {}).ok;
  var foot;
  if (blocked) {
    foot = '<div style="' + S.body + '">转换引擎还没装好，装好后重开软件。</div>';
  } else if (!g.ok) {
    foot = '<div style="' + S.body + ';margin-bottom:14px">'
      + '显卡不满足要求。你可以继续用，但转换会很慢，页数多的书可能中途失败。</div>'
      + '<div style="display:flex;gap:10px">'
      + '<button class="primary" data-act="go" data-arg="pick">仍然继续</button>'
      + '<button data-act="quit">退出</button></div>';
  } else {
    foot = '<button class="primary" data-act="go" data-arg="pick">开始使用</button>';
  }

  return '<div style="' + S.card + '">'
    + '<div style="' + S.h1 + '">这台电脑的情况</div>'
    + '<div style="' + S.faint + ';margin-bottom:6px">只在每次打开时检查一遍</div>'
    + '<div style="margin:10px 0 18px">' + list + '</div>'
    + foot + '</div>';
}

// ── ② 选书 ─────────────────────────────────────────────────────────────

function pagePick(st) {
  var head = '<div style="display:flex;align-items:baseline;gap:12px;margin-bottom:16px">'
    + '<div style="' + S.h1 + ';margin:0">把 PDF 拖进来</div>'
    + '<div style="' + S.faint + '">单个文件、多个文件、整个文件夹都行</div>'
    + '</div>';

  var drop = '<div style="' + S.card + ';border-style:dashed;text-align:center;'
    + 'padding:36px 20px;'
    + (st.dragging ? 'border-color:var(--theme);background:var(--theme-soft)' : '')
    + '">'
    + '<div style="' + S.body + ';margin-bottom:16px">'
    + (st.dragging ? '松手就行' : '拖到这里，或者') + '</div>'
    + '<div style="display:flex;gap:10px;justify-content:center">'
    + '<button data-act="pickFiles">选文件</button>'
    + '<button data-act="pickDir">选文件夹</button>'
    + '</div></div>';

  if (!st.items.length) {
    return head + drop
      + (st.err ? '<div style="' + S.body + ';color:var(--bad);margin-top:14px">'
          + esc(st.err) + '</div>' : '');
  }

  var n = 0;
  var rows = st.items.map(function (it, i) {
    var name = F.base(it.path);
    if (!it.ok) {
      return '<div class="row" style="padding:12px 0;display:flex;gap:12px;align-items:center">'
        + '<div style="width:20px;flex:none"></div>'
        + '<div style="flex:1;min-width:0"><div style="color:var(--ink3);'
        + 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(name) + '</div>'
        + '<div style="' + S.faint + ';color:var(--bad)">' + esc(it.error) + '</div></div>'
        + '</div>';
    }
    var on = st.picked[it.path] !== false;
    if (on) n++;
    var note = '';
    if (it.scan_pages && it.scan_pages.length) {
      var sp = it.scan_pages;
      note = sp.length === it.pages
        ? '整份没有文字层'
        : ('第 ' + sp.slice(0, 4).join('、') + ' 页没有文字层'
           + (sp.length > 4 ? ' 等 ' + sp.length + ' 页' : ''));
    }
    return '<div class="row" style="padding:12px 0;display:flex;gap:12px;align-items:center;'
      + 'cursor:pointer" data-act="toggle" data-arg="' + esc(it.path) + '">'
      + '<div style="width:20px;flex:none;font-size:15px;color:'
      + (on ? 'var(--theme)' : 'var(--ink3)') + '">' + (on ? '✓' : '○') + '</div>'
      + '<div style="flex:1;min-width:0">'
      + '<div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
      + esc(name) + '</div>'
      + (note ? '<div style="' + S.faint + '">' + esc(note) + '</div>' : '')
      + '</div>'
      + '<div style="' + S.faint + ';flex:none">' + it.pages + ' 页</div>'
      + '</div>';
  }).join('');

  var out = st.outDir
    ? esc(st.outDir)
    : '跟原 PDF 放在一起';

  return head
    + '<div style="' + S.card + ';margin-bottom:14px">'
    + '<div style="display:flex;align-items:center;margin-bottom:6px">'
    + '<div style="' + S.h2 + ';flex:1">共 ' + st.items.length + ' 份，选中 ' + n + ' 份</div>'
    + '<button data-act="selAll" style="padding:5px 14px;font-size:13px">全选</button>'
    + '<button data-act="selNone" style="padding:5px 14px;font-size:13px;margin-left:8px">清空</button>'
    + '<button data-act="clear" style="padding:5px 14px;font-size:13px;margin-left:8px">移除全部</button>'
    + '</div>'
    + rows + '</div>'
    + '<div style="' + S.card + ';display:flex;align-items:center;gap:12px">'
    + '<div style="' + S.body + ';flex:1">转好的 Word 放在：<span style="color:var(--ink)">'
    + out + '</span></div>'
    + '<button data-act="pickOut" style="padding:6px 16px;font-size:13px">换个位置</button>'
    + (st.outDir ? '<button data-act="outDefault" style="padding:6px 16px;font-size:13px">用默认</button>' : '')
    + '</div>'
    + '<div style="margin-top:18px;display:flex;gap:10px;align-items:center">'
    + '<button class="primary" data-act="start"' + (n && !st.starting ? '' : ' disabled') + '>'
    + (st.starting ? '正在开始…' : '开始转换') + '</button>'
    + '<button data-act="pickFiles">继续添加</button>'
    + (st.err ? '<div style="' + S.body + ';color:var(--bad)">' + esc(st.err) + '</div>' : '')
    + '</div>';
}

// ── ③ 转换中 / 结果 ────────────────────────────────────────────────────
function pageRun(st) {
  var t = st.task;
  if (!t) {
    return '<div style="' + S.card + '"><div style="' + S.body + '">正在开始…</div></div>';
  }

  var done = t.state === 'done' || t.state === 'cancelled';
  var head = '<div style="display:flex;align-items:baseline;gap:12px;margin-bottom:16px">'
    + '<div style="' + S.h1 + ';margin:0">'
    + (done ? (t.state === 'cancelled' ? '已停止' : '转换完成') : '正在转换')
    + '</div>'
    + '<div style="' + S.faint + '">' + F.sec(t.elapsed) + '</div></div>';

  var body = '';
  if (!done) {
    // 最显眼的位置放「还要多久」—— 那是用户唯一关心的数。
    // MinerU 的阶段进度答不了它：各阶段耗时差 100 倍，跑满一整条
    // 也可能只花 1 秒。所以阶段进度降级成小字，只用来证明「还在动」。
    var eta = (t.remain === null || t.remain === undefined)
      ? '' : ('还要约 ' + F.sec(t.remain));
    body = '<div style="' + S.card + ';margin-bottom:14px">'
      + '<div style="display:flex;align-items:baseline;margin-bottom:12px">'
      + '<div style="flex:1;font-size:22px;font-weight:600;color:var(--ink)">'
      + esc(eta || '正在估算…') + '</div>'
      + (t.total > 1 ? '<div style="' + S.faint + '">第 ' + (t.current + 1)
          + ' / ' + t.total + ' 份</div>' : '')
      + '</div>'
      + bar(t.current + (t.stage_total ? t.stage_cur / t.stage_total : 0), t.total)
      + '<div style="' + S.faint + ';margin-top:12px;overflow:hidden;'
      + 'text-overflow:ellipsis;white-space:nowrap">'
      + esc(t.current_name || '') + '　·　'
      + esc(t.stage || '准备中')
      + (t.stage_total ? ' ' + t.stage_cur + '/' + t.stage_total : '')
      + '</div></div>'
      + '<div style="' + S.faint + ';margin-bottom:14px">'
      + '停止只在当前这份转完之后生效 —— 中途硬停会留下半截文件。</div>'
      + '<button data-act="cancel">停止</button>';
  }

  var results = (t.results || []).map(function (r) {
    if (!r.ok) {
      return '<div class="row" style="padding:14px 0">'
        + '<div style="display:flex;gap:10px;align-items:center">'
        + chip('失败', 'bad')
        + '<div style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;'
        + 'white-space:nowrap">' + esc(F.base(r.pdf)) + '</div></div>'
        + '<div style="' + S.body + ';color:var(--bad);margin-top:6px">'
        + esc(r.error) + '</div></div>';
    }
    return '<div class="row" style="padding:14px 0">'
      + '<div style="display:flex;gap:10px;align-items:center">'
      + chip('完成', 'ok')
      + '<div style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;'
      + 'white-space:nowrap">' + esc(F.base(r.docx)) + '</div>'
      + '<button data-act="openFile" data-arg="' + esc(r.docx) + '"'
      + ' style="padding:4px 14px;font-size:13px">打开</button>'
      + '<button data-act="openPath" data-arg="' + esc(r.docx) + '"'
      + ' style="padding:4px 14px;font-size:13px;margin-left:6px">所在文件夹</button>'
      + '</div>'
      + '<div style="' + S.body + ';margin-top:6px">' + esc(r.line || '') + '</div>'
      + '</div>';
  }).join('');

  var resultCard = results
    ? '<div style="' + S.card + ';margin-top:' + (done ? '0' : '18px') + '">'
      + '<div style="' + S.h2 + ';margin-bottom:4px">已完成 '
      + (t.results || []).length + ' 份</div>' + results + '</div>'
    : '';

  var foot = done
    ? '<div style="margin-top:18px;display:flex;gap:10px">'
      + '<button class="primary" data-act="go" data-arg="pick">再转一批</button>'
      + '</div>'
    : '';

  return head + body + resultCard + foot;
}

// ── ④ 下载模型（首次使用，只出现一次）──────────────────────────────
function pageModel(st) {
  var head = '<div style="' + S.h1 + '">第一次使用，需要下载识别模型</div>'
    + '<div style="' + S.faint + ';margin-bottom:18px">'
    + '大约 ' + (st.srcTotalGb || '4.6') + ' GB，下载一次，以后不用再下</div>';

  // 正在下载就显示下载 —— 这一判断必须在最前面。
  // 原先排在「源列表为空」的 early return 之后，结果下载进行中却因为
  // 列表为空而显示「还没测速」，用户会以为下载没开始。
  var dl = st.dl;
  if (dl && dl.running) {
    return '<div style="' + S.card + '">' + head
      + '<div style="' + S.body + ';margin-bottom:10px">正在下载…'
      + (dl.total ? '　' + Math.round(100 * dl.got / dl.total) + '%' : '') + '</div>'
      + bar(dl.got, dl.total || 1)
      + '<div style="' + S.faint + ';margin-top:12px">'
      + '下载中断了也不要紧，重开软件会接着上次的位置继续。</div></div>';
  }

  if (st.srcLoading) {
    return '<div style="' + S.card + '">' + head
      + '<div style="' + S.body + '">正在测试各个下载源的速度…</div></div>';
  }

  if (st.srcError) {
    return '<div style="' + S.card + '">' + head
      + '<div style="' + S.body + ';color:var(--bad)">' + esc(st.srcError) + '</div>'
      + '<div style="margin-top:16px"><button data-act="probeSources">重新测速</button></div>'
      + '</div>';
  }

  var items = st.sources || [];
  if (!items.length) {
    return '<div style="' + S.card + '">' + head
      + '<div style="' + S.body + '">还没测速。</div>'
      + '<div style="margin-top:16px">'
      + '<button class="primary" data-act="probeSources">测速</button></div></div>';
  }

  var anyOk = items.some(function (x) { return x.ok; });
  var rows = items.map(function (x) {
    var on = st.srcPick === x.id;
    return '<div class="row" style="padding:13px 0;display:flex;gap:12px;'
      + 'align-items:center;' + (x.ok ? 'cursor:pointer' : 'opacity:.5') + '"'
      + (x.ok ? ' data-act="pickSource" data-arg="' + esc(x.id) + '"' : '') + '>'
      + '<div style="width:20px;flex:none;color:'
      + (on ? 'var(--theme)' : 'var(--ink3)') + '">' + (on ? '●' : '○') + '</div>'
      + '<div style="flex:1;min-width:0">' + esc(x.name) + '</div>'
      + '<div style="' + S.faint + ';flex:none">'
      + esc(x.ok ? x.eta : (x.error ? '连不上' : '连不上')) + '</div>'
      + '</div>';
  }).join('');

  return '<div style="' + S.card + '">' + head + rows
    + '<div style="margin-top:18px;display:flex;gap:10px;align-items:center">'
    + '<button class="primary" data-act="startDownload"'
    + (anyOk ? '' : ' disabled') + '>开始下载</button>'
    + '<button data-act="probeSources">重新测速</button>'
    + '<button data-act="pickLocal">我已经有模型了</button>'
    + '</div>'
    + (anyOk ? '' : '<div style="' + S.body + ';color:var(--bad);margin-top:12px">'
        + '所有下载源都连不上，检查一下网络。</div>')
    + '</div>';
}

window.P2W_PAGES = { check: pageCheck, pick: pagePick, run: pageRun,
                     model: pageModel };
