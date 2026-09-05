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

function chr10() { return String.fromCharCode(10); }

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

function envLine(st, compact) {
  // compact：底部按钮多的屏（转换中、下载、选源）把这行缩成一个圆点，
  // 详情进 title，把宽度让给「检查更新」——那个按钮任何时候都不能被挤掉。
  // 判断顺序跟完整版共用一份，只有最后拼字符串时分岔。
  var color, text, cls;
  if (st.envLoading) {
    color = '#9ca3af'; text = '正在检查这台电脑…'; cls = 'f-dim';
  } else {
    var e = st.env || {};
    var g = e.gpu || {};
    if (st.envError) { color = '#b91c1c'; text = '后台没连上'; cls = 'f-bad'; }
    else if (!(e.writable || {}).ok) { color = '#b91c1c'; text = '安装目录不可写'; cls = 'f-bad'; }
    else if (!(e.mineru || {}).ok) { color = '#b91c1c'; text = '转换引擎缺失'; cls = 'f-bad'; }
    // Office 现在是硬性要求（2026-09-01 改定），不再是「有更好」。
    else if (!(e.formula || {}).ok) { color = '#b91c1c'; text = '缺少 Office'; cls = 'f-bad'; }
    else {
      var parts = [];
      parts.push(g.ok ? '显卡 ✓' : '显卡 ✗');
      parts.push('Office ✓');
      // C++ 运行库也摆出来 —— 它齐不齐决定那 2.8 GB 装不装得上，
      // 用户有权在点之前就看见，而不是下完才知道。
      if (e.vcredist) parts.push(e.vcredist.ok ? 'C++ 运行库 ✓' : 'C++ 运行库 ✗');
      color = g.ok ? '#15803d' : '#b45309';
      text = parts.join('　·　');
      cls = g.ok ? '' : 'f-warn';
    }
  }
  if (compact) return '<span title="' + esc(text) + '">' + dot(color) + '</span>';
  // 非 compact 的输出跟改造前逐字一致，包括「正在检查」那条本来就没有圆点。
  if (st.envLoading) return '<span class="f-dim">正在检查这台电脑…</span>';
  return dot(color) + ' <span'
    + (cls ? ' class="' + cls + '"' : '') + '>' + text + '</span>';
}

// 公共底栏。**每一屏都要有「关于」** —— 卡在安装任何一步的用户，
// 唯一的自救手段（检查更新）就在那一屏里；按钮不在，人就只能重下
// 安装包。（v0.0.1 那次正是如此：模型下不成 → 停在下载屏 → 那屏
// 底部没有这个按钮。）
//
// 🔴 2026-09-05 从「检查更新」改成「关于」，小蔡定的：行为统一，
//    处处都是同一个按钮。风险可控 —— 用户卡住时主区被 gateKind
//    拦着，底栏这个按钮是当时**唯一能点的东西**，而进去第一个
//    按钮就是「检查更新」。
// extra 是各屏自己的东西，放右边；compact 见 envLine。
// 🔴 **转换进行中禁用它**（小蔡 2026-09-03 定）。更新包覆盖的正是
//    `pipeline/*.py`，而转换每处理一份 PDF 就新起一次 MinerU 子进程 ——
//    转到一半换掉代码，后面几份读到的是新代码，前后不一致；装完还要
//    重启，一重启这批就全废了（老师那边可能已经等了几分钟）。
//    禁用比事后解释便宜得多。转完自己会亮回来。
function botBar(st, extra, compact) {
  var busy = isRunning(st);
  return envLine(st, compact)
    + btn('openAbout', '关于',
          { cls: 'link', off: busy,
            title: busy ? '正在转换，转完再看' : '' })
    + '<span class="grow"></span>'
    + (extra || '');
}

// 要不要占住主区拦一下。返回 '' 表示放行。
function gateKind(st) {
  if (st.envLoading || st.envError) return '';
  var e = st.env || {};
  // 按严重程度排：写不了盘 > 没公式引擎 > 没转换引擎 > 缺 GPU 运行库 > 显卡不够
  if (!(e.writable || {}).ok) return 'writable';    // 什么都干不了
  if (!(e.formula || {}).ok) return 'formula';      // 核心功能废，硬拦
  if (!(e.mineru || {}).ok) return 'engine';        // 硬拦，没得选
  // 🔴 C++ 运行库排在「缺 GPU 运行库」**前面** —— 它是那 2.8 GB 的前提。
  //
  //    小蔡 2026-09-02：「不是一整个必须第一个装！不要排在 gpu 库后面
  //    好吗！」原来这条只在点了「现在就装」之后才查，于是用户看着
  //    「显卡 ✓ Office ✓」以为没问题，下完 2.8 GB 才发现装不上，
  //    卸掉、退回原屏，白等半小时。
  if (!(e.vcredist || {}).ok) return 'vcredist';
  // 缺 GPU 运行库排在「显卡不够」前面：装不装得上运行库是能自己解决的事，
  // 显卡不行才是没办法的事。先让人做能做的那件。
  if (!(e.cuda_torch || {}).ok) return 'cudalib';   // 硬拦，但给一键安装
  if (!(e.gpu || {}).ok && !st.gateAck) return 'gpu';  // 软拦，用户自己选
  return '';
}

// ── 下载面板 ───────────────────────────────────────────────────────────
// 三处下载共用一个：装 GPU 运行库、下模型、下更新包。
//
// 小蔡 2026-09-02：「你在下载任何文件的时候，都应该显示一个进度条，
// 并且要弹出背后的命令，这样下载的人才可以知道完整的进度，而不是黑盒。」
//
// 所以四样都要有：进度条、真实字节数、**跑的那条命令**、滚动的输出。
// 只显示百分比不够 —— 总量常常是估的，百分比会冲到 103% 或停在 97%，
// 而「已下 2.3 GB」永远是真的。
function dlPanel(o) {
  var got = o.got || 0, total = o.total || 0;
  var pct = total ? Math.min(100, Math.round(100 * got / total)) : 0;
  var lines = o.lines || [];
  var h = '<div class="fill" style="justify-content:flex-start;gap:8px">'
    + '<div style="font-size:13px;font-weight:600;align-self:center">'
    + esc(o.title || '正在下载…') + (total ? '　' + pct + '%' : '') + '</div>'
    + '<div style="width:86%;align-self:center">' + bar(got, total || 1) + '</div>'
    + '<div class="f-dim" style="align-self:center">'
    + (total ? '已下 ' + F.gb(got) + ' / 约 ' + F.gb(total)
             : '已下 ' + F.gb(got))
    + '</div>';
  if (o.note) {
    h += '<div class="f-dim" style="align-self:center;max-width:88%;'
      + 'line-height:1.5">' + esc(o.note) + '</div>';
  }
  // 日志区：第一行是命令本身，后面是它的输出
  h += '<div class="log" id="dllog">';
  if (o.cmd) h += '<span class="l cmd">&gt; ' + esc(o.cmd) + '</span>';
  for (var i = 0; i < lines.length; i++) {
    h += '<span class="l">' + esc(lines[i]) + '</span>';
  }
  if (!o.cmd && !lines.length) {
    h += '<span class="l">（还没有输出）</span>';
  }
  h += '</div>';
  if (o.log) {
    h += '<div class="f-dim mono ell" style="align-self:center;'
      + 'max-width:92%;font-size:10px">完整日志：' + esc(o.log) + '</div>';
  }
  if (o.error) {
    // 失败原因摆在日志下面、按钮上面 —— 用户的视线是从上往下的，
    // 「出了什么事」要在「现在能做什么」之前。
    h += '<div class="f-bad" style="align-self:center;max-width:92%;'
      + 'line-height:1.6;text-align:left;font-size:12px">'
      + esc(o.error) + '</div>';
  }
  return h + '</div>';
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
    // 🔴 话术只能指发行版里**真的存在**的东西。
    //    这里原来写「安装办法见项目里的 README，跑一次 tools\setup_env.py」——
    //    而发行版根目录里这两样都没有（build_release.py 的 CODE 清单里
    //    就没打包 README 和 tools/）。老师撞上这一屏，看到的是一句指向
    //    不存在文件的说明加一个「重新检查」按钮，彻底卡死 ——
    //    而这还是新用户最容易撞上的一屏。
    return '<div class="fill">'
      + '<div style="font-size:14px;font-weight:600">转换引擎还没装好</div>'
      + '<div class="f-dim" style="max-width:450px;line-height:1.65;text-align:left">'
      + '多半是安装包没解压完整。<b>重新解压</b>一次最稳：'
      + '重新下载安装包，解压到一个新的空文件夹，'
      + '再打开里面的「PDF转Word.exe」。'
      + '<br>如果这个文件夹里有「首次安装.cmd」，双击跑一次也行（要联网几分钟）。'
      + '</div>'
      + '<div style="display:flex;gap:8px;margin-top:2px">'
      + btn('reload', '重新检查')
      + btn('quit', '退出') + '</div></div>';
  }
  if (kind === 'vcredist') {
    // 交给微软的安装程序之后，说清楚下一步就关掉自己。
    if (st.vcHandoff) {
      return '<div class="fill">'
        + '<div style="font-size:14px;font-weight:600">'
        + '安装程序已经打开了</div>'
        + '<div class="f-dim" style="max-width:430px;line-height:1.8">'
        + '接下来在微软那个窗口里装完（弹出权限确认框就点「是」），'
        + '<br><b>装好之后重新打开本软件</b>，会自己接着往下走。'
        + '<br><br>这个窗口马上自己关掉，不用管。'
        + '</div></div>';
    }
    if (st.vcBusy) {
      var vd = st.vcDl || {};
      // 🔴 装的时候**不摆进度条**。vc_redist 自己有进度界面，我们这边
      //    看不见它的进度，一个不动的条只会让人以为卡死了 ——
      //    小蔡 2026-09-02：「他有自己的进度条，而且不是一秒装完吗？
      //    现在的情况是 c++ 已经装完了都退出去了，你还在那里显示空进度条」。
      if (vd.installing) {
        return '<div class="fill">'
          + '<div style="font-size:14px;font-weight:600">正在装 C++ 运行库</div>'
          + '<div class="f-dim" style="max-width:440px;line-height:1.7">'
          + '微软的安装程序已经打开了，它有自己的进度界面。'
          + '<br><b>如果弹出权限确认框，点「是」。</b>'
          + '<br><br>装完这里会自动继续，不用管。'
          + '</div>'
          + (vd.cmd ? '<div class="log" style="max-width:470px">'
                      + esc(vd.cmd) + '</div>' : '')
          + '</div>';
      }
      // 下载那 25 MB 的时候才有真实进度可显示
      return dlPanel({
        title: '正在下载 C++ 运行库',
        got: vd.got, total: vd.total, cmd: vd.cmd, lines: vd.lines,
        note: '约 25 MB，下完会自动打开微软的安装程序。',
      });
    }
    return '<div class="fill">'
      + '<div style="font-size:14px;font-weight:600">第一步：装 C++ 运行库</div>'
      + '<div class="f-dim" style="max-width:470px;line-height:1.65;text-align:left">'
      + '微软的 Visual C++ 运行库，约 25 MB。<b>它是 GPU 运行库的前提</b> —— '
      + '那 2.8 GB 装上了也要靠它才能加载，顺序反了就是白下一趟。'
      + '<br><br>点下面的按钮会自动下载并安装，中途会弹出系统的权限确认框，'
      + '点「是」就行。已经装过的电脑再装一次也没事，微软的安装程序会自己'
      + '认出来直接跳过。'
      + '</div>'
      + (st.vcError
         ? '<div class="f-dim" style="max-width:470px;color:#c0392b;'
           + 'line-height:1.6;text-align:left">' + esc(st.vcError) + '</div>'
         : '')
      + '<div style="display:flex;gap:8px;margin-top:2px;flex-wrap:wrap;'
      + 'justify-content:center">'
      + btn('installVcRedist', st.vcError ? '再装一次' : '现在就装',
            { cls: 'primary' })
      + (st.vcError ? btn('openVcRedist', '自己去官网下') : '')
      + btn('reload', '重新检查')
      + btn('quit', '退出') + '</div></div>';
  }

  if (kind === 'cudalib') {
    // 装了 CPU 版 torch，还是压根没装 —— 两句话不一样，用后端给的 why。
    var cw = (e.cuda_torch || {}).why || '缺少 GPU 运行库。';
    var busy = st.gpuLibBusy;
    var line = st.gpuLibLine || '';
    if (busy) {
      var d = st.gpuLib || {};
      return dlPanel({
        title: '正在装 GPU 运行库',
        got: d.got, total: d.total, cmd: d.cmd, lines: d.lines, log: d.log,
        note: '要下约 2.8 GB，取决于网速。装好会自己进主界面，'
            + '这中间可以先去忙别的。',
      });
    }
    return '<div class="fill">'
      + '<div style="font-size:14px;font-weight:600">还差一个 GPU 运行库</div>'
      + '<div class="f-dim" style="max-width:460px;line-height:1.65;text-align:left">'
      + esc(cw)
      + '<br>这个软件只用显卡转换（不用 CPU 顶替，那样慢一倍还没人知道），'
      + '所以要下一份能调用显卡的运行库，约 2.8 GB，装一次就够。'
      + '</div>'
      + (st.gpuLibError
         ? '<div class="f-dim" style="max-width:460px;color:#c0392b;'
           + 'line-height:1.6;text-align:left">' + esc(st.gpuLibError) + '</div>'
         : '')
      + '<div style="display:flex;gap:8px;margin-top:2px;flex-wrap:wrap;'
      + 'justify-content:center">'
      + btn('installGpuLib', st.gpuLibError ? '再装一次' : '现在就装',
            { cls: 'primary' })
      // 装失败时才给这两个 —— 没失败的时候摆出来只会让人以为还得先干别的
      + (st.gpuLibError && /Visual C\+\+|运行库/.test(st.gpuLibError)
         ? btn('openVcRedist', '下载 Visual C++ 运行库') : '')
      + (st.gpuLibError && /驱动/.test(st.gpuLibError)
         ? btn('openDriver', '更新显卡驱动') : '')
      + btn('reload', '重新检查')
      + btn('quit', '退出') + '</div></div>';
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
// 查更新走了哪几条线路。**每一屏都挂它，包括失败那屏** ——
// 「连不上 GitHub」这句话没有任何可操作性，而「五条里三条超时、两条
// 403」是能让人判断到底断网还是被墙的。这是「不给黑盒」那条规矩在
// 更新这条路上的落实（下载面板早就这么做，检查更新一直是个例外）。
//
// 平时只占一行，点开才是表。**不做成底栏按钮**：620x440 的底栏已经
// 挤满了，而且用户平时不需要「选」，只在出事时想知道「为什么」。
function updLines(st, u) {
  var ls = (u && u.lines) || [];
  if (!ls.length) return '';

  var used = null, okN = 0, i;
  for (i = 0; i < ls.length; i++) {
    if (ls[i].used && !used) used = ls[i];
    if (ls[i].ok) okN++;
  }
  var open = !!st.updLinesOpen;

  // 折叠那一行：只说走了谁、几条通。
  // 🔴 **不在这儿报秒数。** 那个数是查版本的响应延迟，而用户看到一个
  //    跟在线路名后面的数字，只会理解成「这条线路多快」——
  //    延迟低不代表下得快。给一个语义模糊的数字，跟给假数据一样坏。
  //    真实速度只有点了「测速」才有（小蔡 2026-09-03 定的规矩）。
  var head = used
    ? '经 ' + esc(used.name) + '　·　' + okN + '/' + ls.length + ' 条可用'
    : (okN ? okN + '/' + ls.length + ' 条线路可用'
           : ls.length + ' 条线路全部失败');

  var toggle = '<div data-act="toggleUpdLines" style="cursor:pointer;'
    + 'display:flex;gap:6px;align-items:center;justify-content:center;'
    + 'font-size:11px;opacity:.75;margin-top:2px"'
    + ' title="点开看每条线路的实测情况">'
    + '<span>' + head + '</span><span>线路 ' + (open ? '▴' : '▾')
    + '</span></div>';

  if (!open) return toggle;

  // 展开态。样式跟选源屏那张表一致 —— 同一个软件里同一件事
  // （挑一条网络线路）不该长成两个样子。
  var rows = ls.map(function (x) {
    // 🔴 **「你选了谁」和「这次用了谁」是两件事，别混。**
    //    混起来的后果：默认状态下「自动」和「本次采用的那条」两个圆点
    //    同时亮着，看上去像选了两个（2026-09-03 渲染出来才看见，
    //    六条前端断言全绿也没抓到 —— 断言查的是有没有，不是好不好看）。
    //    选中态只跟手动选择走；这次实际走了谁，右边那个 ✓ 负责。
    var on = st.updPick === x.id;
    // 🔴 **速度这一列只放实测出来的字节率。没测过就是空的（—）。**
    //    绝不拿响应延迟去顶替：那是另一件事，填进来就是在暗示一个
    //    我们并没有测过的结论。
    var right = x.ok
      ? (x.bps ? F.gb(x.bps) + '/s' : '—')
      : esc(x.error || '连不上');
    return '<div class="it' + (on ? ' on' : '') + '"'
      + (x.ok ? ' data-act="pickUpdLine" data-arg="' + esc(x.id) + '"'
              : ' style="opacity:.5"')
      + ' title="' + esc(x.ok ? x.name : (x.error || '连不上')) + '">'
      + '<input type="radio" style="pointer-events:none;width:12px;height:12px;'
      + 'flex:none;margin:0"' + (on ? ' checked' : '')
      + (x.ok ? '' : ' disabled') + '>'
      + '<span class="grow ell">' + esc(x.name) + '</span>'
      + '<span class="rt" style="width:104px;text-align:right">' + right
      + (x.used ? '　✓' : '') + '</span></div>';
  }).join('');

  // 「自动」在最上面且是默认 —— 手动选是给网络环境特殊的人留的后门，
  // 不该变成常态。
  var auto = '<div class="it' + (st.updPick ? '' : ' on') + '"'
    + ' data-act="pickUpdLine" data-arg="" title="每次都挑最快的那条">'
    + '<input type="radio" style="pointer-events:none;width:12px;height:12px;'
    + 'flex:none;margin:0"' + (st.updPick ? '' : ' checked') + '>'
    + '<span class="grow ell">自动（用最快的）</span>'
    + '<span class="rt" style="width:104px;text-align:right">推荐</span></div>';

  // 表头。存在的理由是那一列的「—」要有个解释 —— 没有表头的话，
  // 用户看到一排横杠只会以为是坏了，而不是「还没测」。
  var hd = '<div class="hd"><span style="width:12px"></span>'
    + '<span class="grow">线路</span>'
    + '<span style="width:104px;text-align:right">下载速度</span></div>';

  var anyBps = false;
  for (i = 0; i < ls.length; i++) { if (ls[i].bps) { anyBps = true; break; } }

  var probe = '<div style="display:flex;gap:8px;align-items:center;'
    + 'justify-content:center;margin-top:4px">'
    + btn('probeUpdSpeed', st.updProbing ? '正在实测…' : '测下载速度',
          { off: !!st.updProbing })
    + '<span class="f-dim" style="font-size:11px">'
    + (st.updProbing ? '每条各下 2 秒'
       : (anyBps ? '数字是刚才实测的' : '没测过，所以是空的'))
    + '</span></div>';

  // 高度按 .it 的 24px 行高算，取 6.5 行 = 156px：
  //   · 查版本是 5 条线路 + 「自动」= 6 行，正好全显示，不出滚动条
  //   · 测速后是 7 条 + 「自动」= 8 行，露出半行 —— 那半行就是「下面还有」
  // 卡成整行反而糟：第 7 行一点不露，用户根本不知道能滚。
  return toggle
    + '<div style="width:100%;max-width:430px;max-height:156px;overflow:auto;'
    + 'text-align:left;margin-top:4px">' + hd + auto + rows + '</div>'
    + probe;
}


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

  // 拿不到官方校验值 —— **报警，但不阻拦**（跟显卡那条一个道理）。
  // 原来这里是硬拒绝，结果更新按钮直接作废：小蔡 2026-09-02 点更新看到
  // 「出于安全没有下载」就走不下去了，而那句话挡住的是正常更新、
  // 不是攻击。安全规则挡住正常路径的时候，该改的是规则。
  if (u.needConfirm) {
    return '<div class="fill">'
      + '<div style="font-size:14px;font-weight:600">没法验证这个更新包</div>'
      + '<div class="f-dim" style="max-width:460px;line-height:1.65;text-align:left">'
      + esc(u.confirmWhy || '拿不到 GitHub 给的校验值。')
      + '<br><br>更新包会覆盖软件里的程序文件，所以装一个**没法验证来源**的包'
      + '是有风险的。你可以选择仍然安装，或者关掉这里、到项目的 Release '
      + '页面手动下载。</div>'
      + '<div style="display:flex;gap:8px;margin-top:2px">'
      + btn('installAnyway', '仍然安装', { cls: 'primary' })
      + close + '</div></div>';
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

  // 真出错：连不上 GitHub、请求崩了。只有这种该说「没法检查更新」，
  // 也只有这种给「再试一次」—— 重试一个已经查成功的结果没有意义。
  if (!u.ok) {
    return '<div class="fill">'
      + '<div style="font-size:14px;font-weight:600">暂时没法检查更新</div>'
      + '<div class="f-dim" style="max-width:460px;line-height:1.6">'
      + esc(u.error || '不知道为什么') + '</div>'
      + updLines(st, u)
      + '<div style="display:flex;gap:8px">'
      + btn('checkUpdate', '再试一次') + close + '</div></div>';
  }

  // 这次更新需要的依赖本地没有 —— 更新包只有 .py 和 .js，补不上。
  // 判据是**依赖清单跟本地实际装的比对**，不是版本号（小蔡 2026-09-02：
  // 「禁止版本号作为判断依据」）。
  // 这不是失败，是「这次得换个方式更新」，所以单独一屏、说清楚为什么。
  if (u.need_full) {
    return '<div class="fill">'
      + '<div style="font-size:14px;font-weight:600">这次要重新下载安装包</div>'
      + '<div class="f-dim" style="max-width:460px;line-height:1.65;text-align:left">'
      + esc(u.error || '') + '</div>'
      + '<div class="f-dim" style="max-width:460px;line-height:1.6">'
      + '你已经下好的模型和 GPU 运行库不受影响 —— 装到同一个文件夹覆盖就行。'
      + '</div>'
      + '<div style="display:flex;gap:8px;margin-top:2px">'
      + btn('openReleases', '去下载页', { cls: 'primary' })
      + close + '</div></div>';
  }

  // 🔴 查成功了，但有话要说。check() 有四种 ok=true 却带 error 的结果：
  //      · 本地版本比仓库里的还新
  //      · 仓库里还没有发布任何版本
  //      · 不知道当前是哪个版本（version.json 缺失）
  //      · 有新版本，但那个 Release 没附更新包
  //    这些都是**说明**，不是错误。原来的分支写成 `!u.ok || u.error`，
  //    四种全塞进错误分支，于是标题「暂时没法检查更新」和正文
  //    「本地版本比仓库里的还新」自相矛盾。
  if (u.error) {
    return '<div class="fill">'
      + '<div style="font-size:14px;font-weight:600">'
      + (u.has_update ? '有新版本，但拿不到更新包' : '没有可用的更新') + '</div>'
      + '<div class="f-dim" style="max-width:460px;line-height:1.6">'
      + esc(u.error) + '</div>'
      + updLines(st, u)
      + '<div style="display:flex;gap:8px">' + close + '</div></div>';
  }

  // 已是最新
  if (!u.has_update) {
    return '<div class="fill">'
      + '<div style="font-size:14px;font-weight:600">已经是最新版本</div>'
      + '<div class="f-dim">' + esc(u.local || '') + '</div>'
      + updLines(st, u)
      + '<div>' + close + '</div></div>';
  }

  // 有更新。
  //
  // 🔴 **默认只给摘要**（Release 正文里分隔线之前那段）。原来是拿
  //    全文砍前 6 行，而正文是长篇散文，砍出来的是个残缺的开头 ——
  //    v0.1.1 那版用户看到的头 4 行是「## 修了两件事 / ### 1. 装在
  //    中文路径里转换必失败 / 空行 / 有用户把软件放在桌面的…」，
  //    一条实质信息都没有。
  //
  //    老 Release 的正文里没有分隔线，后端会把全文当摘要返回 ——
  //    那种情况下这里的行为跟改之前一样，只是不再硬砍 6 行。
  var notesOpen = !!st.updNotesOpen;
  var brief = u.notes_brief || u.notes || '';
  var full = u.notes_full || u.notes || '';
  var hasMore = full && full !== brief;
  var shown = notesOpen ? full : brief;
  var notes = shown.split('\n')
    .map(function (x) { return esc(x); }).join('<br>');
  return '<div class="fill" style="justify-content:flex-start;padding-top:14px">'
    + '<div style="font-size:14px;font-weight:600">有新版本 ' + esc(u.latest) + '</div>'
    + '<div class="f-dim">当前 ' + esc(u.local)
    + (u.published ? '　·　发布于 ' + esc(u.published) : '')
    + (u.asset && u.asset.size ? '　·　' + F.gb(u.asset.size) : '') + '</div>'
    + (notes ? '<div class="f-dim" style="max-width:90%;text-align:left;'
        + 'line-height:1.6;max-height:' + (notesOpen ? '150' : '110')
        + 'px;overflow:auto">' + notes + '</div>' : '')
    + (hasMore ? '<div data-act="toggleUpdNotes" class="f-dim" '
        + 'style="cursor:pointer;user-select:none;font-size:11px">'
        + (notesOpen ? '收起 ▴' : '完整说明 ▾') + '</div>' : '')
    + updLines(st, u)
    + '<div style="display:flex;gap:8px;margin-top:4px">'
    + btn('downloadUpdate', '更新', { cls: 'primary' })
    + btn('closeUpdate', '暂不更新') + '</div></div>';
}


// ── 关于 / 环境检测 ────────────────────────────────────────────────────
//
// 占主区不弹窗，跟更新面板一个路数 —— 620x440 的窗口里，弹窗放不下
// 这些内容。

// 本软件用到的开源组件。GPL 要求「分发须附协议全文」，光把文件放在
// 硬盘上不够 —— 用户要在界面上看得到。
var LICENSES = [
  ['MinerU', 'Apache 2.0'],
  ['Pandoc', 'GPL-2.0+'],
  ['PyTorch', 'BSD-3-Clause'],
  ['KaTeX', 'MIT'],
  ['Node.js', 'MIT'],
  ['Electron', 'MIT'],
];

function aboutView(st) {
  var d = st.diag || {};
  var ver = d.tag || (st.env && st.env.version) || '';
  var rows = LICENSES.map(function (x) {
    return '<span style="display:inline-block;min-width:118px">'
      + esc(x[0]) + ' <span class="f-dim">' + esc(x[1]) + '</span></span>';
  }).join('');

  return '<div class="fill" style="justify-content:flex-start;'
    + 'padding-top:16px;gap:10px">'
    + '<div style="font-size:15px;font-weight:600">PDF 转 Word</div>'
    + '<div class="f-dim">' + esc(ver)
    + (d.sha ? '　·　' + esc(d.sha) : '') + '</div>'
    + '<div class="f-dim" style="line-height:1.8;text-align:left">'
    + '作者　终末诗篇<br>'
    + '许可　GPL-3.0-or-later<br>'
    + '项目　github.com/kiryusento2017/pdf_to_word'
    + '</div>'
    + '<div class="f-dim" style="max-width:92%;text-align:left;'
    + 'line-height:1.8;font-size:11px">'
    + '本软件使用了以下开源组件：<br>' + rows
    + '<br>公式转换用微软 Office 的 MML2OMML.XSL，那是你本机 Office '
    + '的文件，不随本软件分发。'
    + '</div>'
    + '<div style="display:flex;gap:8px;margin-top:6px">'
    + btn('checkUpdate', '检查更新', { cls: 'primary' })
    + btn('openEnvCheck', '环境检测')
    + btn('closeAbout', '关闭')
    + '</div></div>';
}


// 一行「本地 / 上游」对比。
//
// 🔴 **没查过就是破折号，查不到就说查不到。** 绝不显示「已是最新」——
//    那两个意思差很远，混了就是假绿灯：用户以为自己是最新的，
//    实际是网络不通。这是 README 里那条既有规矩（「速度那一列没测过
//    就是空的」）在这一屏的落实。
function depRow(label, local, up) {
  var right = '<span class="f-dim">—</span>';
  if (up) {
    if (up.error) {
      right = '<span class="f-dim" title="' + esc(up.error) + '">查不到</span>';
    } else if (up.latest) {
      var same = up.latest === local;
      right = esc(up.latest) + (same ? ' <span class="f-dim">已最新</span>' : '');
    } else if (up.upstream_time) {
      right = esc(up.upstream_time) + ' 更新';
    }
  }
  return '<tr><td style="padding:1px 10px 1px 0">' + esc(label) + '</td>'
    + '<td style="padding:1px 14px 1px 0">' + (esc(local) || '<span class="f-dim">未安装</span>') + '</td>'
    + '<td style="padding:1px 0">' + right + '</td></tr>';
}


// 一行占用。cleanable 的给勾选框。
function useRow(st, it) {
  var on = !!st.maintPick[it.key];
  var box = it.cleanable
    ? '<span data-act="toggleMaint" data-arg="' + esc(it.key) + '" '
      + 'style="cursor:pointer;user-select:none">' + (on ? '☑' : '☐') + '</span>'
    : '<span class="f-dim">·</span>';
  return '<tr><td style="padding:1px 8px 1px 0">' + box + '</td>'
    + '<td style="padding:1px 10px 1px 0">' + esc(it.label) + '</td>'
    + '<td style="padding:1px 10px 1px 0;text-align:right">' + F.gb(it.size) + '</td>'
    + '<td class="f-dim" style="padding:1px 0;font-size:11px">' + esc(it.note || '') + '</td>'
    + '</tr>';
}


// 升级区。**只在查过上游、且确实有新版本时才出现** —— 没新版本时
// 摆一堆勾选框只是噪音。
//
// 🔴 策略（requires.json 的 upgrade 段）说不能升的，**不给勾选框**，
//    只显示理由。默认是「没测过」，那时给框但写清楚我们没测过。
function upgradeBox(st) {
  var up = st.deps || {};
  var pol = (st.upd && st.upd.upgrade) || {};
  var rows = [];

  [['torch', up.torch], ['mineru', up.mineru]].forEach(function (x) {
    var name = x[0], d = x[1];
    if (!d || !d.latest || d.latest === d.local) return;   // 没新版本
    var p = pol[name] || {};
    if (p.ok === false) {
      // 实测不能升 —— 只说理由，不给框
      rows.push('<tr><td style="padding:1px 8px 1px 0">'
        + '<span class="f-dim">✕</span></td>'
        + '<td style="padding:1px 10px 1px 0">' + esc(name) + ' → '
        + esc(d.latest) + '</td>'
        + '<td class="f-dim" style="font-size:11px">'
        + esc(p.note || '实测不建议升级') + '</td></tr>');
      return;
    }
    var on = !!st.upgPick[name];
    rows.push('<tr><td style="padding:1px 8px 1px 0">'
      + '<span data-act="toggleUpg" data-arg="' + esc(name) + '" '
      + 'style="cursor:pointer;user-select:none">'
      + (on ? '☑' : '☐') + '</span></td>'
      + '<td style="padding:1px 10px 1px 0">' + esc(name) + ' '
      + esc(d.local || '') + ' → ' + esc(d.latest) + '</td>'
      + '<td class="f-dim" style="font-size:11px">'
      + (p.ok === true ? esc(p.note || '实测可升')
          : '我们没测过，升不升你自己定') + '</td></tr>');
  });

  if (!rows.length) return '';

  var picked = 0;
  for (var k in st.upgPick) { if (st.upgPick[k]) picked++; }

  // 预演结果
  var plan = '';
  var pl = st.upgPlan;
  if (pl) {
    if (!pl.ok) {
      plan = '<div class="f-dim" style="font-size:11px;max-width:94%;'
        + 'text-align:left">装不了：' + esc(pl.error || '') + '</div>';
    } else {
      var cs = pl.changes || [];
      var head2 = '这次会动 ' + cs.length + ' 个包';
      // 🔴 默认折叠 —— 一次升级动十几个包很正常，全摊开会吓着人。
      var body = st.upgDetail
        ? '<div style="max-height:80px;overflow:auto;font-size:11px;'
          + 'text-align:left">' + cs.map(function (c) {
            return esc(c.name) + ' ' + esc(c.from || '(新增)')
              + ' → ' + esc(c.to);
          }).join('<br>') + '</div>'
        : '';
      plan = '<div data-act="toggleUpgDetail" class="f-dim" '
        + 'style="cursor:pointer;user-select:none;font-size:11px">'
        + head2 + (st.upgDetail ? ' ▴' : ' ▾') + '</div>' + body;
    }
  }

  // 下载进度
  var dl = '';
  var d2 = st.upgDl;
  if (d2) {
    if (d2.state === 'running') {
      dl = '<div class="f-dim" style="font-size:11px">正在后台下载，'
        + '这期间可以照常转 PDF</div>';
    } else if (d2.ok) {
      dl = '<div class="f-dim" style="font-size:11px">下载完成 —— '
        + '重启之后才会真正安装</div>';
    } else {
      dl = '<div class="f-dim" style="font-size:11px;max-width:94%">'
        + '下载失败：' + esc(d2.error || '') + '</div>';
    }
  }

  return '<div style="width:96%;text-align:left">'
    + '<div class="f-dim" style="font-size:11px">可以升级的：</div>'
    + '<table style="font-size:12px">' + rows.join('') + '</table>'
    + plan + dl
    + '<div style="display:flex;gap:8px;margin-top:3px">'
    + btn('planUpgrade', st.upgBusy ? '正在算…' : '看看会动哪些包',
          { off: !picked || st.upgBusy })
    + btn('startUpgrade', '下载并升级',
          { off: !picked || !(pl && pl.ok),
            title: !(pl && pl.ok) ? '先看一眼会动哪些包' : '' })
    + '</div></div>';
}


function envCheckView(st) {
  var d = st.diag || {};
  var g = (d.gpu && d.gpu.gpu) || {};
  var m = st.maint || {};
  var v = d.versions || {};
  var up = st.deps || {};

  // 模型旧了没有。**只有查过上游、且拿到了上游时间，才谈得上判断** ——
  // 没查过就是不知道，不是「已最新」。
  var mu = up.models || {};
  var modelStale = !!(mu.ready && mu.upstream_time && !mu.error
                      && mu.local_time && mu.upstream_time > mu.local_time);

  // 顶部：机器信息
  var head = '<div class="f-dim" style="text-align:left;line-height:1.7;'
    + 'font-size:11px;max-width:96%">'
    + (g.name ? esc(g.name) + '　驱动 ' + esc(g.driver || '?')
        + '　' + Math.round((g.vram_mb || 0) / 1024) + ' GB' : '读不到显卡')
    + (up.torch && up.torch.channel ? '　通道 ' + esc(up.torch.channel) : '')
    + '<br>' + esc(d.root || '')
    + (d.free_gb ? '　剩余 ' + d.free_gb + ' GB' : '')
    + '</div>';

  // 版本对比表
  var deps = '<table style="font-size:12px;text-align:left">'
    + '<tr class="f-dim" style="font-size:11px">'
    + '<td style="padding:1px 10px 1px 0">组件</td>'
    + '<td style="padding:1px 14px 1px 0">你机器上</td>'
    + '<td>上游</td></tr>'
    + depRow('torch', v.torch || '', up.torch)
    + depRow('mineru', v.mineru || '', up.mineru)
    + depRow('模型', d.models_ready ? F.gb(d.models_size) : '', up.models)
    + '</table>';

  // 占用与清理
  var items = m.items || [];
  var use = items.length
    ? '<table style="font-size:12px;text-align:left">'
      + items.map(function (it) { return useRow(st, it); }).join('')
      + '</table>'
    : '<div class="f-dim">' + (st.maintBusy ? '正在扫描…' : (m.error || '')) + '</div>';

  // pip 缓存明细。🔴 必须能展开看 —— 缓存是按 Windows 用户共用的，
  //    里面混着别的程序下的包，只给总数会让用户误伤别人。
  var cache = '';
  var pip = m.pip || {};
  if ((pip.items || []).length) {
    var rows = pip.items.slice(0, st.cacheOpen ? 40 : 0).map(function (x) {
      return '<tr><td style="padding:0 10px 0 0">' + esc(x.name) + '</td>'
        + '<td style="padding:0 10px 0 0;text-align:right">' + F.gb(x.size) + '</td>'
        + '<td class="f-dim">' + (x.ours ? '本软件' : '其他程序') + '</td></tr>';
    }).join('');
    cache = '<div data-act="toggleCache" class="f-dim" style="cursor:pointer;'
      + 'user-select:none;font-size:11px">'
      + (st.cacheOpen ? '收起缓存明细 ▴' : '缓存明细（' + pip.items.length + ' 项）▾')
      + '</div>'
      + (st.cacheOpen ? '<div style="max-height:96px;overflow:auto;'
        + 'font-size:11px;text-align:left"><table>' + rows + '</table></div>' : '');
  }

  // 清理结果
  var res = '';
  var cr = st.cleanResult;
  if (cr) {
    res = '<div class="f-dim" style="font-size:11px">已清理 ' + F.gb(cr.freed || 0)
      + ((cr.failed || []).length ? '　·　' + cr.failed.length + ' 项没删掉：'
          + esc(cr.failed.slice(0, 2).join('；')) : '') + '</div>';
  }

  var picked = 0;
  for (var k in st.maintPick) { if (st.maintPick[k]) picked++; }

  return '<div class="fill" style="justify-content:flex-start;'
    + 'padding-top:10px;gap:7px">'
    + head
    + deps
    + '<div style="display:flex;gap:8px;align-items:center">'
    + btn('checkDeps', st.depsBusy ? '正在查…' : '检查上游',
          { off: st.depsBusy || isRunning(st),
            title: isRunning(st) ? '正在转换，转完再查' : '' })
    // 🔴 模型有更新时才出现这个按钮。
    //
    //    点了**不清空 models/**，直接重跑下载命令 —— 2026-09-05
    //    实测确认底层是增量的（原样再跑 0.9 秒 vs 全新 21 秒，
    //    删掉一个文件再跑只补那一个）。所以中途失败旧模型还在，
    //    用户照常能转 PDF。
    + (modelStale ? btn('updateModels', '更新模型',
          { off: isRunning(st),
            title: isRunning(st) ? '正在转换，转完再更新' : '' }) : '')
    + (up.error ? '<span class="f-dim" style="font-size:11px">'
        + esc(up.error) + '</span>' : '')
    + '</div>'
    + upgradeBox(st)
    + use
    + cache
    + res
    + '<div style="display:flex;gap:8px;margin-top:2px">'
    + btn('doClean', picked ? '清理选中的 ' + picked + ' 项' : '清理',
          { off: !picked || st.maintBusy })
    + btn('copyDiag', st.copied ? '已复制' : '复制诊断信息')
    + btn('openAbout', '返回')
    + '</div></div>';
}


// 诊断信息拼成一段文本。老师微信发过来，能省十几轮问答。
//
// 最值钱的四样：**安装路径原文**（中文路径的坑栽过好几次）、
// **系统语言**（GBK 编码问题的来源）、**最近一次错误**、**代码 sha**
//（确认他跑的到底是不是你以为的那版）。
function diagText(st) {
  var d = st.diag || {};
  if (!d.tag && !d.root) return '';
  var g = (d.gpu && d.gpu.gpu) || {};
  var v = d.versions || {};
  var m = st.maint || {};
  var L = [];
  L.push('PDF转Word ' + (d.tag || '?') + (d.sha ? '  (sha ' + d.sha + ')' : ''));
  L.push(d.os || '');
  L.push('安装目录 ' + (d.root || ''));
  L.push('  可写 ' + (d.writable ? '是' : '否')
    + '   剩余 ' + (d.free_gb || '?') + ' GB'
    + (d.admin ? '   ⚠ 以管理员身份运行' : ''));
  L.push('');
  L.push('显卡 ' + (g.name || '读不到') + ' / 驱动 ' + (g.driver || '?')
    + ' / ' + Math.round((g.vram_mb || 0) / 1024) + ' GB'
    + ' / 算力 ' + (g.compute_cap || '?'));
  L.push('torch ' + (v.torch || '未装') + '   mineru ' + (v.mineru || '未装')
    + '   模型 ' + (d.models_ready ? F.gb(d.models_size) + ' 已就绪' : '未就绪'));
  if ((m.items || []).length) {
    L.push('占用: ' + m.items.map(function (x) {
      return x.label.split('（')[0] + ' ' + F.gb(x.size);
    }).join(' / '));
  }
  var r = d.last_run;
  if (r) {
    L.push('最近一次转换: ' + (r.time || '') + '  ' + (r.file || '')
      + '  ' + (r.ok ? '成功' : '失败 ' + (r.error || ''))
      + (r.formulas ? '  公式 ' + r.formulas : '')
      + (r.took_sec ? '  ' + r.took_sec + ' 秒' : ''));
  }
  var e = d.last_error;
  if (e) {
    L.push('最近一次错误: ' + (e.time || '') + '  [' + (e.where || '') + '] '
      + (e.msg || '') + (e.hint ? '  （' + e.hint + '）' : ''));
  }
  return L.join(chr10());
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
      botBar(st, ''));
  }

  var gate = gateKind(st);
  if (gate) {
    return shell('<span class="f-dim" style="padding:0 4px">PDF 转 Word</span>',
                 gateView(st, gate), botBar(st, ''));
  }

  // 更新面板排在 gate 之后 —— 环境有问题的话，先解决环境。
  if (st.upd || st.updBusy) {
    return shell('<span class="f-dim" style="padding:0 4px">检查更新</span>',
                 updateView(st), envLine(st));
  }

  // 关于 / 环境检测。排在更新面板后面 —— 点了「检查更新」就该
  // 看更新，不该被关于页盖住。
  if (st.about) {
    if (st.about === 'env') {
      // 诊断文本算好存进 state，copyDiag 直接读 —— 不用隐式全局，
      // 那个前端检查专门在防（2026-09-02 栽过）。
      st.diagText = diagText(st);
      return shell('<span class="f-dim" style="padding:0 4px">环境检测</span>',
                   envCheckView(st), envLine(st));
    }
    return shell('<span class="f-dim" style="padding:0 4px">关于</span>',
                 aboutView(st), envLine(st));
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
      botBar(st, '<span class="f-dim">读取中…</span>'));
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
    return shell(top, main,
      botBar(st, '<span class="f-dim">还没有文件</span>'));
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

  var bot = botBar(st,
    '<span>' + (st.err ? '<span class="f-bad">' + esc(st.err) + '</span>'
        : ('选中 ' + n + ' 份 · ' + pages + ' 页')) + '</span>'
    + btn('start', st.starting ? '正在开始…' : '开始转换',
          { cls: 'primary', off: !n || st.starting || st.scanning }));

  return shell(top, head + rows, bot);
}

// ── 主屏 · 转换中 / 结果 ───────────────────────────────────────────────
function mainRun(st) {
  var t = st.task;
  if (!t) {
    return shell('<span class="f-dim" style="padding:0 4px">PDF 转 Word</span>',
      '<div class="fill"><div class="f-dim">正在开始…</div></div>',
      botBar(st, ''));
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
    // 估不出来就说估不出来；估完了还没转完，认账 —— 总比让一个
    // 「还要约 0 秒」挂在那儿不动强。
    var eta;
    // 估不出来也不能让屏幕静止 —— 已用时是永远在跳的那个数。
    if (t.remain === null || t.remain === undefined) {
      eta = '正在估算…（已用 ' + F.sec(t.elapsed) + '）';
    }
    else if (t.remain <= 0) eta = '你的 GPU 真垃圾';
    else eta = '还要约 ' + F.sec(t.remain);
    top = '<span style="font-size:13px;font-weight:600;color:var(--theme);'
      + 'white-space:nowrap">' + esc(eta) + '</span>'
      + '<span class="grow" style="padding:0 4px">'
      + bar(t.current + (t.stage_total ? t.stage_cur / t.stage_total : 0), t.total)
      + '</span>'
      + (t.total > 1 ? '<span class="f-dim" style="white-space:nowrap">第 '
          + (t.current + 1) + ' / ' + t.total + ' 份</span>' : '');
  } else {
    // 比出厂估值快四成以上才夸。**全靠缓存的那种不算** ——
    // 秒回是没跑 GPU，不是 GPU 快，夸错了对象。
    var est = (t.pages || []).reduce(function (a, b) { return a + b; }, 0)
      * (t.sec_per_page || 26);
    var anyReal = res.some(function (r) { return !r.cached; });
    var fast = anyReal && est > 0 && t.elapsed < est * 0.6;
    top = '<span style="font-size:13px;font-weight:600">'
      + (t.state === 'cancelled' ? '已停止'
         : (fast ? '你的 GPU 真牛逼' : '转换完成')) + '</span>'
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
      // 悬停能看到具体是第几个公式没转成 —— math_note 里写着，
      // 以前那个字段没有任何地方读，等于白写。
      return '<div class="it" title="' + esc(r.docx
               + (r.math_note ? (chr10() + r.math_note) : '')) + '">'
        + dot('#15803d')
        + '<span class="grow ell">' + esc(F.base(r.docx)) + '</span>'
        // 秒回的那几份得说清楚为什么 —— 不标的话用户会以为根本没转。
        + (r.cached ? '<span class="rt f-dim" title="这份 PDF 和参数都没变，'
            + '直接用了上次的识别结果，没有重跑 GPU">缓存</span>' : '')
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
        + '<span class="rt f-bad ell" style="max-width:200px">失败：'
        + esc(r.error || '') + '</span>'
        // 次品也是四分钟换来的：正文、表格、图片都在，只是公式没转全。
        // 名字里带着【公式未完全转换】，不会被当成正品。
        + (r.degraded
            ? btn('openFile', '打开次品', { cls: 'link', arg: r.degraded })
              + btn('openPath', '文件夹', { cls: 'link', arg: r.degraded })
            : '') + '</div>';
    }
    // 还没轮到 / 正在转。current 是已完成的份数，所以它就是当前这份的下标。
    var cur = !done && i === t.current;
    if (cur) {
      return '<div class="it on">' + dot('#1d4ed8')
        + '<span class="grow ell">' + esc(name) + '</span>'
        // 🔴 `cur > 0` 才显示数字和小进度条。
        //    MinerU 有些阶段（「准备版面」这类）根本不吐中间进度，
        //    一路 0 直到跳走 —— 显示一个永远不动的「0/11」加一条空条，
        //    比什么都不显示更糟：用户会以为卡住了。
        //    小蔡 2026-09-02 真机原话：「准备版面一直是 0 然后突然跳到
        //    识别 2/11」，还问了句「这阶段真的有用吗」。
        //    阶段名本身有用（知道在干什么），假的进度数字没用。
        // 🔴 **不显示阶段内的 x/y 数字**，只显示阶段名和一条比例条。
        //
        //    MinerU 换阶段时总数会换单位：先按页（0→11），下一个阶段
        //    按检测到的文本块（0→247）。用户看到「5/11」变成「5/247」，
        //    第一反应是出 bug 了。小蔡 2026-09-02 原话：「刚刚文件本来是
        //    5/11，现在是 5/247，我无语了」。
        //
        //    在这之前他还问过「准备版面一直是 0，这阶段真的有用吗」——
        //    同一个东西绊了他两次。数字本身没错，是它压根不该给用户看：
        //    单位在变、有些阶段不吐中间值，而用户真正要的是「还要多久」，
        //    那个数在顶上单独显示。
        + '<span class="rt">' + esc(t.stage || '准备中') + '</span>'
        + '<span style="width:56px;flex:none">'
        + (t.stage_cur > 0 ? bar(t.stage_cur, t.stage_total || 1) : '')
        + '</span></div>';
    }
    return '<div class="it">' + dot('#d0d0d0')
      + '<span class="grow ell f-dim">' + esc(name) + '</span>'
      + '<span class="rt">' + (done ? '未处理' : '等待') + '</span></div>';
  }).join('');

  // ── 状态栏 ──────────────────────────────────────────────────────────
  var bot;
  if (!done) {
    // 「停止只在当前这份转完之后生效」那句提示删了 —— 2026-09-02 起
    // 停止是当场生效的（extract._spawn 里有 watch 线程杀进程树）。
    // 留着一句过时的免责声明，比什么都不写更坏。
    bot = botBar(st, '<span>已用 ' + F.sec(t.elapsed) + '</span>'
      + btn('toggleLog', st.showLog ? '返回列表' : '日志')
      + btn('cancel', '停止'), true);
  } else {
    bot = botBar(st,
      '<span>' + (badN ? ('成功 ' + okN + ' 份 · <span class="f-bad">失败 '
          + badN + ' 份</span>') : ('全部完成 ' + okN + ' 份'))
      + ' · 用时 ' + F.sec(t.elapsed) + '</span>'
      // 转完了也留一个入口 —— 有失败的时候，日志正是最该看的东西
      + btn('toggleLog', st.showLog ? '返回列表' : '日志'), true);
  }

  // 🔴 日志覆盖主区，但顶部（剩余时间 + 总进度条）留着。
  //    620x440 太小，日志和文件表分屏的话两边都看不清；而整体进度
  //    在顶上，看日志的时候不会「不知道跑到哪了」。
  if (st.showLog) {
    var lg = t.lines || [];
    var main = '<div class="fill" style="justify-content:flex-start;gap:6px">'
      + '<div class="log" id="dllog">'
      + (lg.length
          ? lg.map(function (x) {
              // 跑的那条命令用另一个颜色，跟输出分开 —— 跟下载面板一个待遇
              return '<span class="' + (x.charAt(0) === '$' ? 'l cmd' : 'l')
                + '">' + esc(x) + '</span>';
            }).join('')
          : '<span class="l">（还没有输出。MinerU 刚起来时会安静一阵子，'
            + '模型加载要几十秒）</span>')
      + '</div>'
      // 🔴 进度行**钉在日志下面单独一行**，原地刷新。
      //    它不混进日志流：混进去的话往上翻会被一堆「识别中 98/247」
      //    这种过期数字挡路；钉住则任何时候都在屏幕上，翻日志也翻不走。
      //    这是「一直有数字在跳」最直接的那一个。
      + (t.progress_line
          ? '<div class="log" style="flex:none;min-height:0;overflow:hidden;'
            + 'white-space:nowrap;text-overflow:ellipsis">'
            + '<span class="l">' + esc(t.progress_line) + '</span></div>'
          : '')
      + (t.log ? '<div class="f-dim mono ell" style="align-self:center;'
                 + 'max-width:92%;font-size:10px">完整日志：' + esc(t.log)
                 + '（含进度刷屏，这里只显示关键行）</div>' : '')
      + '</div>';
    return shell(top, main, bot);
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
  // 失败了也要留在这一屏 —— 日志和错误原因都在面板里，
  // 跳走等于把用户唯一能看懂的线索藏起来。
  if (dl && (dl.running || dl.error)) {
    // 显示真实字节数而不只是百分比：总量 4.6 GB 是估的，百分比可能
    // 冲到 103% 或停在 97%，但「已下 2.3 GB」永远是真的。
    // phase 分两段：先装 GPU 运行库，再下模型。标题要说清楚现在在等哪一件，
    // 不然用户看着一个从头开始的进度条会以为下载重来了。
    var isLib = dl.phase === 'gpulib';
    return shell(top,
      dlPanel({
        title: dl.error
          ? (isLib ? 'GPU 运行库没装成' : '模型没下成')
          : (isLib ? '正在装 GPU 运行库' : '正在下载模型'),
        got: dl.got, total: dl.total, cmd: dl.cmd, lines: dl.lines,
        log: dl.log, error: dl.error,
        note: dl.error ? '' : (isLib
          ? '这是第一步，约 2.8 GB。装完接着下识别模型。'
          : '下载中断了也不要紧，重开软件会接着上次的位置继续。'),
      }),
      botBar(st, dl.error
        ? ('<span class="f-dim">上面是完整的输出，出错的原因通常在最后几行</span>'
           + btn('startDownload', '再试一次', { cls: 'primary' })
           + btn('probeSources', '换个源'))
        : ('<span class="f-dim">'
           + (isLib ? '第 1 步，共 2 步' : '第 2 步，共 2 步') + '</span>'
           + btn('cancelDownload', '停止')), true));
  }

  if (st.srcLoading) {
    return shell(top,
      '<div class="fill"><div class="f-dim">正在测试各个下载源的速度…</div></div>',
      botBar(st, '<span class="f-dim">同时连所有源实测 2-3 秒，比 ping 准</span>'));
  }

  if (st.srcError) {
    return shell(top,
      '<div class="fill">'
      + '<div class="f-bad" style="max-width:440px">' + esc(st.srcError) + '</div>'
      + '<div>' + btn('probeSources', '重新测速') + '</div></div>',
      botBar(st, '<span class="f-dim">测速失败</span>'));
  }

  var items = st.sources || [];
  if (!items.length) {
    return shell(top,
      '<div class="fill"><div class="f-dim">还没测速。</div>'
      + '<div>' + btn('probeSources', '测速', { cls: 'primary' }) + '</div></div>',
      botBar(st, '<span class="f-dim">先测速再选源</span>'));
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

  var bot = botBar(st,
    btn('startDownload', '开始下载', { cls: 'primary', off: !anyOk })
    + btn('probeSources', '重新测速')
    + btn('pickLocal', '我已经有模型了')
    + (anyOk ? '' : '<span class="f-bad">所有下载源都连不上，检查一下网络</span>'), true);

  return shell(top, head + rows, bot);
}

// upgradeBox 也导出 —— 它的分支（策略说不能升就不给勾选框）在主屏
// 路由下测不到（upd 一有值就跳去更新面板了），单独导出才验得了。
window.P2W_PAGES = { main: pageMain, model: pageModel,
                     upgradeBox: upgradeBox };
