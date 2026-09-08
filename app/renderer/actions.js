// 所有用户动作。页面渲染是纯函数，副作用全在这里。
'use strict';

(function () {
  var st = window.P2W_STATE;
  var render = window.P2W_RENDER;
  var HTTP = window.P2W_HTTP;

  // 升级下载的轮询。跟模型下载那套一个路数。
  function pollUpgrade() {
    HTTP.get('/api/upgrade/download').then(function (d) {
      st.upgDl = d;
      render();
      if (d && d.state === 'running') {
        setTimeout(pollUpgrade, 1000);
      } else {
        // 🔴 **下完必须重新问一次**。按钮显示什么取决于 st.upgPending，
        //    而那是开机时问的那一次 —— 当时还没下载，结果是「没有待装的」。
        //    不在这儿刷新的话，用户看到「下载完成」却等不到「立即重启」，
        //    要关掉软件重开才冒出来。
        //    2026-09-07 小蔡实测撞上：「显示下载完成，要重启，但是没有
        //    重启按钮。」跟同一轮刚修完的「接口对 ≠ 有人调」一个形状。
        loadUpgPending();
      }
    }).catch(function () {
      setTimeout(pollUpgrade, 3000);
    });
  }

  var poller = null;

  function stopPolling() {
    if (poller) { clearInterval(poller); poller = null; }
  }

  // 问一句：有没有下好、还没装的升级。开机调一次，装完再调一次。
  function loadUpgPending() {
    HTTP.get('/api/upgrade/pending').then(function (d) {
      st.upgPending = d || null;
      render();
    }).catch(function () { /* 问不到就当没有，不挡主流程 */ });
  }

  // 装的过程要轮询 —— 2.5 GB 解压落盘要几分钟，不能让界面停在那儿。
  function pollUpgIns() {
    HTTP.get('/api/upgrade/install').then(function (d) {
      st.upgIns = d;
      render();
      if (d && d.state === 'running') {
        setTimeout(pollUpgIns, 1000);
      } else if (d && d.ok) {
        // 🔴 **装成功就直接重启**，不再问一次。小蔡定的：只有「立即重启」
        //    一个按钮，点了就把整件事走完 —— 装完还要用户再点一次的话，
        //    又回到了「谁按下那一下」没人管的坑里。
        //    新版本的 torch 要重启进程才真正生效（旧的还在内存里）。
        window.api.restart();
      } else {
        loadUpgPending();     // 装失败了，重新问一次状态好显示按钮
      }
    }).catch(function () { setTimeout(pollUpgIns, 3000); });
  }

  // 拉转换历史。主屏空着的时候显示它 —— 打开软件就看得见上次转了什么。
  // **读不出来就当没有**，不打扰用户：历史是锦上添花，不能让它挡住主流程。
  function loadRuns() {
    // 🔴 要满 200 条 —— 跟后端 RUNS_KEEP 对齐。写 50 的话界面上那句
    //    「共 N 份」写的是拿回来的条数、不是存了多少（2026-09-07 小蔡报
    //    「但是怎么只有 50 份」）。
    HTTP.get('/api/runs?limit=200').then(function (d) {
      st.runs = (d && d.rows) || [];
      render();
    }).catch(function () { /* 没有历史就算了 */ });
  }

  function addPaths(paths) {
    if (!paths || !paths.length) { render(); return; }
    st.scanning = true;
    st.err = '';
    render();
    HTTP.post('/api/scan', { paths: paths }).then(function (d) {
      // 合并进已有清单，去重（用户常常分几次拖进来）
      var seen = {};
      st.items.forEach(function (x) { seen[x.path] = true; });
      (d.items || []).forEach(function (x) {
        if (!seen[x.path]) { st.items.push(x); seen[x.path] = true; }
      });
      if (!st.items.length) st.err = '这里面没有找到 PDF';
      st.scanning = false;
      render();
    }).catch(function (e) {
      st.scanning = false;
      st.err = String(e && e.message || e);
      render();
    });
  }

  // 转换进行中加进来的文件 —— 进待办队列，不碰正在跑的那批。
  //
  // 走的是跟 addPaths 同一个 /api/scan（体检拿页数），只是落点不同。
  // 🔴 **去重要比三样**：待办里已有的、正在转的这批（st.items）。用户很
  //    可能把已经在转的某份又拖一次，那份转出来会覆盖同一个 .docx，
  //    白跑一趟 GPU。addPaths 原来只比 st.items 一样，这里得比全。
  function addPending(paths) {
    if (!paths || !paths.length) { render(); return; }
    st.pendingBusy = true;
    st.err = '';
    render();
    HTTP.post('/api/scan', { paths: paths }).then(function (d) {
      var seen = {};
      st.pending.forEach(function (x) { seen[x.path] = true; });
      st.items.forEach(function (x) { seen[x.path] = true; });
      (d.items || []).forEach(function (x) {
        // 体检不过的不进待办 —— 它在主队列里同样会当场失败，
        // 提前挡掉比让用户等到晋升之后才看见一个红叉好。
        if (x.ok && !seen[x.path]) { st.pending.push(x); seen[x.path] = true; }
      });
      st.pendingBusy = false;
      // 🔴 **体检的这十几秒里，这一批可能已经转完了。**
      //
      //    /api/scan 扫一个文件夹要十几秒（456 份实测 16 秒）。等它回来时
      //    poll 可能早就拿到 done 了 —— 而那一刻 st.pending 还是空的，
      //    所以 poll 走的是「没有待办」那条路：停轮询、不晋升。
      //    轮询全项目只在 start() 里启动一处，之后再没有任何东西会碰
      //    这些文件：既不转、也不显示（done 时待办列表整块不渲染）、
      //    连「移除」都点不到，而且会一直躺着，等用户下次手动开一批
      //    转完时被突然拉起来 —— 那时他早忘了自己拖过什么。
      //
      //    所以体检回来必须自己补一次判断，不能指望 poll。
      if (st.task && st.task.state === 'done' && st.pending.length) {
        promotePending();
        return;
      }
      if (st.task && st.task.state === 'cancelled' && st.pending.length) {
        mergePendingToItems();
      }
      render();
    }).catch(function (e) {
      st.pendingBusy = false;
      st.err = String(e && e.message || e);
      render();
    });
  }

  // 待办晋升成主队列。**这批转完的那一刻自动调，不问用户。**
  //
  // 复用 start() 起新任务，不另写一条发起转换的路 —— start() 里已经把
  // progMax / showReport / openStage 三样归位了（那是「上一批的界面状态
  // 不许串到新一批」的既有教训，front_check 里有一条源码扫描钉着它）。
  // 另起一条路等于把那三行漏掉，而且测试扫不到。
  function promotePending() {
    // 上一批的结果留一份，报告要靠它。放在改 items 之前 —— 下面那句
    // 一改，st.task 还在但列表已经是新一批的了。
    st.lastResults = (st.task && st.task.results) || null;
    // 待办变成新的待转清单。picked 一并重建：待办里的都是要转的，
    // 老的 picked 是上一批的勾选状态，留着会串。
    st.items = st.pending.slice();
    st.picked = {};
    st.items.forEach(function (x) { st.picked[x.path] = true; });
    st.pending = [];
    // 自引用照抄文件里既有的写法（startDownload / downloadUpdate 都是这么调的）。
    // front_check 那条「不许用不存在的全局」会跳过 P2W_ACTS，因为本文件
    // 自己就有 `window.P2W_ACTS = `。
    window.P2W_ACTS.start();
  }

  // ── 下载模型的轮询。跟转换那个分开：两者可以先后发生，
  //    共用一个计时器会在切换时互相踩。
  var dlPoller = null;

  function stopDlPolling() {
    if (dlPoller) { clearInterval(dlPoller); dlPoller = null; }
  }

  var updPoller = null;

  function stopUpdPolling() {
    if (updPoller) { clearInterval(updPoller); updPoller = null; }
  }

  // 查更新的倒计时。**结果早到也压着**，等数到 0 那一刻一起亮出来 ——
  // 小蔡 2026-09-05 定的：倒计时归零和界面出现必须是同一时刻，不能
  // 倒计时自己转、结果自己蹦。代价是网络好的时候本来 1.1 秒能出结果，
  // 也要等满 4 秒。
  //
  // 🔴 4 必须大于后端的 API_DETAIL_BUDGET（3.8），留 0.2 秒给网络往返，
  //    这样数到 0 时结果一定已经在手上。两个数写在两个文件里，
  //    test_update.py 有一条测试盯着它俩的关系。
  var UPD_COUNTDOWN = 4;
  var updTimer = null, updHeld = null;

  function stopUpdCountdown() {
    if (updTimer) { clearInterval(updTimer); updTimer = null; }
    updHeld = null;
    st.updLeft = 0;
  }

  function pollUpd() {
    HTTP.get('/api/update/download').then(function (d) {
      if (!st.upd) return;
      st.upd.dlGot = d.got || 0;
      st.upd.dlTotal = d.total || 0;
      st.upd.phase = d.state;   // running / installing / done / error / need_confirm
      // step 是 running 内部的细分：checking / probing / running。
      // 界面靠它说清楚「真在下」之前那几秒到底在等什么。
      st.upd.step = d.step || '';
      if (d.state === 'need_confirm') {
        // 后端拿不到校验值，等用户拿主意 —— 这不是失败，别当错误报
        stopUpdPolling();
        st.updBusy = false;
        st.upd.ok = true;
        st.upd.needConfirm = true;
        st.upd.confirmWhy = d.error || '';
        render();
        return;
      }
      if (d.state === 'done') {
        stopUpdPolling();
        st.updBusy = false;
        // 后端已经解压覆盖好了，剩下的只有重启。
        st.upd.installed = true;
        st.upd.files = d.files || 0;
        st.upd.via = d.via;
      } else if (d.state === 'error') {
        stopUpdPolling();
        st.updBusy = false;
        st.upd.error = d.error || '更新失败';
      }
      render();
    }).catch(function () {});
  }

  function pollDl() {
    HTTP.get('/api/models/download').then(function (d) {
      st.dl = {
        running: d.state === 'running',
        got: d.got || 0, total: d.total || 0, line: d.line || '',
        // 下面这几样是给下载面板用的：滚动日志、跑的那条命令、
        // 完整日志的路径、现在处在哪一步（装运行库 / 下模型）
        lines: d.lines || [], cmd: d.cmd || '', log: d.log || '',
        phase: d.phase || '',
      };
      // 🔴 完成**只认后端的 state**，不看 d.ready。
      //    d.ready 是 models.ready()，判据是「模型目录里有没有 >1 MB 的
      //    文件」—— 下载才开始、第一个文件刚落盘它就成立了。
      //    2026-09-02 真机现象：点完下载，进度条跑了一点点就跳到主界面，
      //    后台还在下。用户以为下完了直接去转换，而模型只有一小部分。
      //    只有后端跑完整个流程才会把 state 置成 'done'。
      if (d.state === 'done') {
        stopDlPolling();
        st.dl = null;
        if (st.env) st.env.models = { ok: true, dir: '' };
        st.page = 'main';                 // 下好了直接进主界面开工
      } else if (d.state === 'error') {
        stopDlPolling();
        // 🔴 **不要清掉 st.dl**。清了整个下载面板连同日志一起消失，
        //    然后落到「测速失败」那一屏 —— 下载失败被显示成测速失败，
        //    用户会去怀疑网络和下载源，而真正的原因一个字都看不到。
        //    2026-09-02 真机上，那次失败的原因是 torch 的 c10.dll
        //    加载不了，跟网络毫无关系；小蔡是靠界面给的日志路径
        //    自己去翻文件才找到的。日志就该留在眼前。
        st.dl.running = false;
        st.dl.error = d.error || '下载失败';
      }
      render();
    }).catch(function () {
      // 单次轮询失败不惊动用户，下一轮还会问
    });
  }

  // 用户摁了停止 —— 待办不清掉、也不问，并回待转清单，下次点「开始转换」
  // 时一起转。勾选状态设成勾上：他刚拖进来的，本来就是要转的。
  function mergePendingToItems() {
    var seen = {};
    st.items.forEach(function (x) { seen[x.path] = true; });
    st.pending.forEach(function (x) {
      if (!seen[x.path]) { st.items.push(x); st.picked[x.path] = true; }
    });
    st.pending = [];
  }

  function poll() {
    if (!st.taskId) return;
    HTTP.get('/api/convert/' + st.taskId).then(function (d) {
      st.task = d;
      if (d.state === 'done' || d.state === 'cancelled') {
        stopPolling();
        // 🔴 主队列结束 → 待办自动晋升成主队列，**不问，直接开始**。
        //
        //    只有 done 才晋升。cancelled 是用户主动摁的停止，那会儿
        //    再自动开一批等于没停成 —— 待办改为并回待转清单。
        //
        //    ⚠️ 转换失败时 state 仍然是 done（只是多个 error，见 _work
        //    的兜底分支），所以**失败也照常晋升**。理由：一批失败不代表
        //    下一批也失败，待办里的文件跟失败原因通常无关。要是想让
        //    环境级失败拦住后面几批，条件加在这儿。
        if (d.state === 'cancelled') {
          if (st.pending.length) mergePendingToItems();
        } else if (st.pending.length) {
          promotePending();
          return;              // start() 自己会 render，别再来一次
        }
      }
      render();
    }).catch(function (e) {
      // 🔴 **任务不存在就别再问了。**
      //
      //    以前后端的任务表永不清理，`/api/convert/{id}` 不可能 404，所以
      //    这里吞掉一切是安全的。加了任务表上限之后 404 第一次成为可能：
      //    前端万一漏收了终态那一次轮询，之后又跑完几批把老 id 挤掉，
      //    这个 catch 会让它**每秒空转一次 404**，界面还停在旧状态。
      //    再问一万次也是 404，停掉。
      var msg = String((e && e.message) || '');
      if (msg.indexOf('没有这个任务') >= 0 || msg.indexOf('404') >= 0) {
        stopPolling();
        return;
      }
      // 其余（服务正忙、瞬时失败）不必惊动用户，下一轮还会问
    });
  }

  window.P2W_ACTS = {
    reload: function () { window.location.reload(); },

    // 引导去装 Office。主进程那边有域名白名单，这里传什么都只可能
    // 打开微软自己的站。
    openOffice: function () {
      window.api.openUrl('https://www.microsoft.com/zh-cn/microsoft-365');
    },

    // GPU 运行库加载不了时最常见的解法：装 Visual C++ 运行库。
    // 直接给下载地址，不让人自己去搜 —— 搜「vc运行库」出来的
    // 前几条常常是第三方打包站。
    // 第一步：装 C++ 运行库。照着 installGpuLib 的写法 ——
    //
    // 🔴 第一版这里编了两个不存在的名字（window.P2W_RELOAD_ENV、
    //    裸的 post/get），点下去 JS 直接抛异常，轮询压根没启动，
    //    界面永远停在「正在装」。小蔡 2026-09-02：「c++ 已经装完了
    //    都退出去了，你还在那里显示 c++ 的空进度条」。
    installVcRedist: function () {
      st.vcBusy = true;
      st.vcError = '';
      st.vcDl = { got: 0, total: 0, lines: [], cmd: '', installing: false };
      render();
      var poll = setInterval(function () {
        HTTP.get('/api/vcredist/install').then(function (d) {
          st.vcDl = {
            got: d.got || 0, total: d.total || 0,
            lines: d.lines || [], cmd: d.cmd || '',
            installing: !!d.running_installer,
          };
          if (d.state === 'done') {
            clearInterval(poll);
            st.vcBusy = false;
            // 🔴 **不重载，退出**。小蔡 2026-09-02：「一旦开始装你就退出，
            //    然后用户装完了再把你点开来不好吗」。微软的安装程序这时
            //    已经在前台跑了，我们杵在这儿只会挡路，而且它有时要求
            //    重启电脑。留一屏话说清楚下一步，两秒后自己关掉。
            st.vcHandoff = true;
            render();
            setTimeout(function () { window.close(); }, 2200);
          } else if (d.state === 'error') {
            clearInterval(poll);
            st.vcBusy = false;
            st.vcError = d.error || '装失败了';
          }
          render();
        }).catch(function () { /* 轮询失败下次再说 */ });
      }, 1000);
      HTTP.post('/api/vcredist/install', {}).catch(function (e) {
        clearInterval(poll);
        st.vcBusy = false;
        st.vcError = String(e && e.message || e);
        render();
      });
    },

    openVcRedist: function () {
      window.api.openUrl('https://aka.ms/vs/17/release/vc_redist.x64.exe');
    },

    // 驱动太旧时的出路
    openDriver: function () {
      window.api.openUrl('https://www.nvidia.cn/geforce/drivers/');
    },

    // node 缺失时的出路。以前那句「重装一次应该能解决」是错话 ——
    // setup_env.py 根本不装 node，重装我们的软件不会带来它。
    openNode: function () {
      window.api.openUrl('https://nodejs.org/zh-cn/download');
    },

    quit: function () { window.close(); },

    // 显卡不满足时用户选了「仍然继续」。**只有用户能按这个** ——
    // 软件不替他做主，但按过之后就不再拦第二次。
    ackGate: function () {
      st.gateAck = true;
      // 放行之后补上首启该做的那一步：没模型就去选源。
      // 不补的话用户会停在主界面，而模型根本还没下。
      if (!window.P2W_BLOCKED(st) && !((st.env || {}).models || {}).ok) {
        st.page = 'model';
      }
      render();
    },

    // 转完之后回到待转清单。列表留着不清：勾选重置成「只勾失败的」，
    // 于是「再转一批」对全成功的人是清爽的空勾选，对有失败的人正好是一键重试。
    newBatch: function () {
      var failed = {};
      var ran = {};
      ((st.task && st.task.results) || []).forEach(function (r) {
        ran[r.pdf] = true;
        if (!r.ok) failed[r.pdf] = true;
      });
      // 🔴 **只重设这一批真转过的那些。**
      //
      //    原来是 `st.picked[x.path] = !!failed[x.path]` 一律重设。但自从
      //    有了待办队列，`st.items` 里可能混着**根本没转过**的文件：
      //    摁停止时并回来的待办、晋升时 start() 失败留下的那批。
      //    它们在 results 里没有记录，于是被一律设成未勾选 ——
      //    用户看到的是「软件把我刚拖进来的东西吃了」。
      st.items.forEach(function (x) {
        if (ran[x.path]) st.picked[x.path] = !!failed[x.path];
      });
      st.task = null;
      st.taskId = '';
      st.err = '';
      // 🔴 手动开新一批 = 不再关心上一批。不清的话 lastResults 会**隔着一批
      //    串味**：A 晋升 B（lastResults 记的是 A）→ B 转完没有待办 →
      //    点「再转一批」手动转 C → C 运行中点「上一批的报告」，看到的是 A，
      //    B 整个被跳过。报告是拿去核对 Word 的清单，指错批次等于指错文件。
      st.lastResults = null;
      st.showLastReport = false;
      stopPolling();
      render();
    },

    addPaths: addPaths,
    addPending: addPending,

    pickFiles: function () {
      window.api.pickFiles().then(addPaths);
    },

    // 转换中的「再加几份」。跟 pickFiles 走同一个原生对话框，
    // 只是收进待办队列。
    pickMore: function () {
      window.api.pickFiles().then(addPending);
    },

    // 从待办里去掉一份（加错了、或者临时不想转了）。
    delPending: function (p) {
      st.pending = st.pending.filter(function (x) { return x.path !== p; });
      render();
    },

    // 看上一批的报告 / 收起。跟主报告共用 showReport 这个开关不行 ——
    // 那个是「当前这批」的，两批的报告会互相顶掉。单开一个。
    toggleLastReport: function () {
      st.showLastReport = !st.showLastReport;
      render();
    },

    pickDir: function () {
      window.api.pickDir().then(addPaths);
    },

    pickOut: function () {
      window.api.pickOutDir().then(function (d) {
        if (d) { st.outDir = d; render(); }
      });
    },

    outDefault: function () { st.outDir = ''; render(); },

    toggle: function (path) {
      st.picked[path] = st.picked[path] === false;
      render();
    },

    selAll: function () {
      st.items.forEach(function (x) { if (x.ok) st.picked[x.path] = true; });
      render();
    },

    selNone: function () {
      st.items.forEach(function (x) { st.picked[x.path] = false; });
      render();
    },

    clear: function () {
      st.items = [];
      st.picked = {};
      st.err = '';
      render();
    },

    start: function () {
      var paths = st.items.filter(function (x) {
        return x.ok && st.picked[x.path] !== false;
      }).map(function (x) { return x.path; });
      if (!paths.length) return;
      st.starting = true;
      st.err = '';
      render();
      HTTP.post('/api/convert', {
        paths: paths, out_dir: st.outDir, prefer_xsl: true,
        source: st.srcPick,
      }).then(function (d) {
        st.taskId = d.task_id;
        st.task = null;
        st.starting = false;
        // 不切页 —— 同一张表原地变进度。task 一有值主屏就自己换形态。
        render();
        stopPolling();
        // 一秒一问。转换本身以分钟计，问得再勤也只是多耗电。
        st.progMax = 0;      // 新一批开始，总进度从头算
        // 这几个是上一批留下的界面状态，不归位会串到新一批：
        // 报告页会自己冒出来，展开过的那一行也还开着。
        st.showReport = false;
        st.openStage = null;
        // 「上一批的报告」也一样要收起来。不收的话：看着 A 的报告时
        // B 转完、C 晋升，lastResults 换成了 B，而开关还开着 ——
        // 界面会自己弹出一份用户没点开的报告。
        st.showLastReport = false;
        poller = setInterval(poll, 1000);
        poll();
      }).catch(function (e) {
        st.starting = false;
        st.err = String(e && e.message || e);
        render();
      });
    },

    cancel: function () {
      if (!st.taskId) return;
      HTTP.post('/api/convert/' + st.taskId + '/cancel', {}).catch(function () {});
    },

    // 转换时看实时日志。覆盖主区，顶上的剩余时间和总进度条留着。
    // 不做成固定一块是因为转一批书时文件列表本来就长，挤不起。
    toggleLog: function () {
      st.showLog = !st.showLog;
      render();
    },

    probeSources: function () {
      st.srcLoading = true;
      st.srcError = '';
      render();
      HTTP.get('/api/sources').then(function (d) {
        st.sources = d.items || [];
        st.srcPick = d.best || '';
        st.srcTotalGb = d.total_gb;
        st.srcLoading = false;
        render();
      }).catch(function (e) {
        st.srcLoading = false;
        st.srcError = String(e && e.message || e);
        render();
      });
    },

    pickSource: function (id) { st.srcPick = id; render(); },

    // 真的开始下。这个按钮以前只有一句 `st.page='main'` —— 用户点完
    // 界面跳走，他以为下好了，其实一个字节都没下；不转换就关软件的话，
    // 下次开机又被丢进这一屏，无限循环。
    //
    // 下载本身交给 MinerU 自带的下载器（不自己实现，见 models.download
    // 的注释），这里只负责起任务 + 轮询进度。
    startDownload: function () {
      st.srcError = '';
      st.dl = { running: true, got: 0, total: 0, line: '',
                lines: [], cmd: '', log: '', phase: '' };
      render();
      HTTP.post('/api/models/download', { source: st.srcPick }).then(function () {
        stopDlPolling();
        dlPoller = setInterval(pollDl, 1000);
        pollDl();
      }).catch(function (e) {
        st.dl = null;
        st.srcError = String(e && e.message || e);
        render();
      });
    },

    // 检查更新。请求由后端发 —— 页面的 CSP 只放行 127.0.0.1，
    // 让前端直连 GitHub 得放宽 CSP，那是拿安全性换一个小功能。
    checkUpdate: function () {
      stopUpdCountdown();
      st.updBusy = true;
      st.upd = null;
      st.updLeft = UPD_COUNTDOWN;
      render();

      function show(d) {
        stopUpdCountdown();
        st.updBusy = false;
        st.upd = d;
        render();
      }

      updTimer = setInterval(function () {
        if (st.updLeft > 0) st.updLeft--;
        // 数到 0 且结果在手 → 就是这一刻，一起亮出来。
        // 数到 0 结果还没到（后端卡死，罕见）→ 停在 0 等着，
        // 界面换成「就快好了…」，结果一到立刻出（见下面的 else 分支）。
        if (st.updLeft === 0 && updHeld) show(updHeld);
        else render();
      }, 1000);

      HTTP.get('/api/update/check').then(function (d) {
        if (st.updLeft > 0) updHeld = d;   // 早到了，压着等倒计时
        else show(d);                       // 已经归零，立刻出
      }).catch(function (e) {
        var d = { ok: false, error: String(e && e.message || e) };
        if (st.updLeft > 0) updHeld = d;
        else show(d);
      });
    },

    closeUpdate: function () {
      // 🔴 不清定时器的话，关掉窗口 4 秒后 show() 会把它又打开。
      stopUpdCountdown();
      st.upd = null;
      st.updAllowUnverified = false;
      st.updLinesOpen = false;
      st.updNotesOpen = false;
      render();
    },

    // ── 关于 / 环境检测 ──────────────────────────────────────────

    // 打开「关于」。底栏那个按钮任何时候都指到这里 —— 卡在安装
    // 任何一步的用户，自救手段（检查更新）就在这一屏里。
    openAbout: function () {
      st.about = 'about';
      render();
    },

    closeAbout: function () {
      st.about = null;
      st.maintPick = {};
      st.cacheOpen = false;
      st.copied = false;
      render();
    },

    // 打开环境检测。**进来就拉本地信息**（不联网，很快），
    // 上游版本要用户另点按钮 —— 见 checkDeps 的说明。
    openEnvCheck: function () {
      st.about = 'env';
      st.maintBusy = true;
      render();
      HTTP.get('/api/maint/scan').then(function (d) {
        st.maint = d;
        st.maintBusy = false;
        render();
      }).catch(function (e) {
        st.maintBusy = false;
        st.maint = { ok: false, error: String(e && e.message || e) };
        render();
      });
      HTTP.get('/api/diag').then(function (d) {
        st.diag = d;
        render();
      }).catch(function () {});
      // 升级备份的明细。**这个接口以前做好了没人调** —— 跟 rollback
      // 一个毛病：功能齐了、界面上摸不到。拉不到就当没有，不挡这一屏。
      HTTP.get('/api/upgrade/backups').then(function (d) {
        st.backups = (d && d.items) || [];
        render();
      }).catch(function () { st.backups = []; });
    },

    // 点第一次只是亮出确认，点第二次才真退。**回滚要删掉 site-packages
    // 里的 torch 再拷回来**，误触的代价是等好几分钟拷 4 GB，值得多问一次。
    askRollback: function (name) {
      st.rollbackAsk = (st.rollbackAsk === name) ? '' : name;
      render();
    },

    doRollback: function (name) {
      if (st.rollbackBusy) return;
      st.rollbackBusy = true;
      st.rollbackAsk = '';
      st.err = '';
      render();
      HTTP.post('/api/upgrade/rollback', { name: name }).then(function (d) {
        st.rollbackBusy = false;
        if (d && d.ok) {
          // 拷回去的是文件，而当前进程里 torch 早就加载进内存了 ——
          // 必须重启才算数。跟装升级那边同一个道理。
          st.err = '已经退回，点「立即重启」或者关掉软件重开才生效。';
        } else {
          st.err = '退回失败：' + ((d && d.error) || '没说原因');
        }
        render();
      }).catch(function (e) {
        st.rollbackBusy = false;
        st.err = '退回失败：' + String(e && e.message || e);
        render();
      });
    },

    // 查上游有没有新版本。
    //
    // 🔴 **不自动查。** 这是照搬 README 里那条既有规矩：「速度那一列
    //    没测过就是空的，不拿别的数字顶替」。没查过就显示破折号，
    //    查不到就显示「查不到」—— **绝不显示「已是最新」**，那两个
    //    意思差很远，混了就是假绿灯。
    //
    //    实测约 3.6 秒（两次 pip 子进程 + 一次 HTTP）。
    checkDeps: function () {
      st.depsBusy = true;
      render();
      HTTP.get('/api/deps/check').then(function (d) {
        st.deps = d;
        st.depsBusy = false;
        render();
      }).catch(function (e) {
        st.depsBusy = false;
        st.deps = { ok: false, error: String(e && e.message || e) };
        render();
      });
    },

    // 勾选要清理的项。
    // arg 是 data-arg 里那个 key（事件分发的签名是 fn(arg, el)）。
    toggleMaint: function (arg) {
      var k = arg;
      if (!k) return;
      st.maintPick[k] = !st.maintPick[k];
      render();
    },

    // 模型有更新时重新下一次。
    //
    // 🔴 **不清空 models/** —— 2026-09-05 实测确认 mineru 的下载器
    //    是增量的（原样再跑 0.9 秒 vs 全新 21 秒；删掉一个文件再跑
    //    只补那一个）。直接重跑就行，中途失败旧模型还在，用户照常
    //    能转 PDF。「先删后下」中途断网就把人坑了。
    //
    //    跳到 model 那一屏，进度条、日志区、断点续传全是现成的 ——
    //    跟首次下载走的是同一条路。
    updateModels: function () {
      st.about = null;
      st.page = 'model';
      window.P2W_ACTS.startDownload();
    },

    // 勾选要升级的包。
    toggleUpg: function (arg) {
      if (!arg) return;
      st.upgPick[arg] = !st.upgPick[arg];
      st.upgPlan = null;      // 选择变了，之前的预演作废
      render();
    },

    // 预演：这次升级到底会动哪些包。**不真装。**
    //
    // 🔴 用 pip 自己的 --dry-run。如果它解不出来（比如只勾 mineru
    //    但新版要求更新的 torch，而 torch 被约束文件钉住了），这里
    //    会显示报错 —— **那正是约束文件要的效果**：显式暴露冲突，
    //    而不是偷偷装出一个坏组合。
    planUpgrade: function () {
      var picked = [];
      for (var k in st.upgPick) { if (st.upgPick[k]) picked.push(k); }
      if (!picked.length) return;
      st.upgBusy = true;
      render();
      HTTP.post('/api/upgrade/plan', { picked: picked, targets: {} })
        .then(function (d) {
          st.upgPlan = d;
          st.upgBusy = false;
          render();
        }).catch(function (e) {
          st.upgBusy = false;
          st.upgPlan = { ok: false, error: String(e && e.message || e) };
          render();
        });
    },

    // 展开/收起完整的变更清单。
    toggleUpgDetail: function () {
      st.upgDetail = !st.upgDetail;
      render();
    },

    // 开始下载。**后台跑，用户可以继续转 PDF。**
    startUpgrade: function () {
      var picked = [];
      for (var k in st.upgPick) { if (st.upgPick[k]) picked.push(k); }
      if (!picked.length) return;
      HTTP.post('/api/upgrade/download', { picked: picked, targets: {} })
        .then(function () {
          st.upgDl = { state: 'running', lines: [] };
          render();
          pollUpgrade();
        }).catch(function (e) {
          st.upgDl = { state: 'done', ok: false,
                       error: String(e && e.message || e) };
          render();
        });
    },

    // 展开/收起 pip 缓存的明细。
    //
    // 🔴 **必须能看到明细。** 缓存是按 Windows 用户共用的，里面混着
    //    别的程序下的包（实测扫出过 pyside6、torch+cpu 那些）。
    //    只给一个总数加清理按钮的话，用户一点就误伤别人。
    // 点文件那一行，展开/收起它走过的步骤清单。
    // 🔴 只有**正在转的**和**已经转完的**能点开 —— 还没轮到的那几份
    //    一步都没走过，摆一张空清单没有意义。这个限制在 pages.js 里
    //    体现为：只有那两种行才带 data-act。
    // 打开 / 关掉转换历史那一屏。
    //
    // 🔴 **每次进去都重新拉一次**，不复用开机拉的那份 —— 用户常是刚转完
    //    一批就想看，用缓存的会正好少掉最关心的那几条。代价只是一个本地
    //    请求。
    //
    // 🔴 **转换中不禁用。** 旁边的「关于」在转换时是灰的（怕误操作），
    //    但历史是纯只读的，转换中恰恰是最想看「上一份存哪了」的时候。
    openHistory: function () {
      st.about = 'history';
      render();
      loadRuns();
    },

    toggleStages: function (arg) {
      var i = parseInt(arg, 10);
      st.openStage = (st.openStage === i) ? null : i;
      render();
    },

    toggleCache: function () {
      st.cacheOpen = !st.cacheOpen;
      render();
    },

    // 清理。
    doClean: function () {
      var keys = [];
      for (var k in st.maintPick) {
        if (st.maintPick[k]) keys.push(k);
      }
      if (!keys.length) return;
      st.maintBusy = true;
      render();
      HTTP.post('/api/maint/clean', { keys: keys, paths: [] })
        .then(function (d) {
          st.maintPick = {};
          st.cleanResult = d;
          return HTTP.get('/api/maint/scan');
        }).then(function (d) {
          st.maint = d;
          st.maintBusy = false;
          render();
        }).catch(function (e) {
          st.maintBusy = false;
          st.cleanResult = { ok: false, failed: [String(e && e.message || e)] };
          render();
        });
    },

    // 把诊断信息复制到剪贴板。老师微信发过来，能省十几轮问答。
    // 转完之后看报告。**不落盘**，只在这儿看和复制。
    toggleReport: function () {
      st.showReport = !st.showReport;
      render();
    },

    // 复制转换报告到剪贴板：新剪贴板 API 不行就退回 textarea + execCommand。
    // （诊断信息那边原本也是这套写法，2026-09-08 改成生成文件了，
    //   现在整个前端只剩这一处还在用剪贴板。）
    copyReport: function () {
      var text = st.reportText || '';
      if (!text) return;
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text);
        } else {
          var ta = document.createElement('textarea');
          ta.value = text;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
        }
        st.copied = true;
        render();
        setTimeout(function () { st.copied = false; render(); }, 2000);
      } catch (e) { /* 复制失败就算了，文本还在屏幕上 */ }
    },

    // 生成诊断文件。**取代了原来的「复制诊断信息」**（小蔡 2026-09-08：
    // 「干脆一点，复制的按钮直接改生成，任何事情都生成」）。
    //
    // 剪贴板那条路砍掉的理由：十行粘进微信还行，三百行就没人看了；
    // 而真出问题时要的恰恰是那三百行（日志尾部、50 条历史、JS 报错）。
    // 发个文件是一次拖拽，比粘一堵墙强。
    exportDiag: function () {
      if (st.diagBusy) return;              // 防连点，生成要一两秒
      st.diagBusy = true;
      st.err = '';
      render();
      // 🔴 fetch 没有超时。后端要是卡住不返回，promise 永不 settle，
      //    diagBusy 就永远是 true —— 按钮卡在「正在生成…」，只能重启软件。
      //    30 秒后强行解禁：正常一两秒就好，到这个点基本可以断定它不回来了。
      setTimeout(function () {
        if (st.diagBusy) {
          st.diagBusy = false;
          st.err = '生成诊断文件超时了（30 秒没有回应），可以再点一次试试。';
          render();
        }
      }, 30000);
      HTTP.post('/api/diag/export', {
        // 🔴 摘要用**前端已经拼好的那份**（pages.js 的 diagText），
        //    后端不重拼一遍 —— 重拼就是两处逻辑，改一处忘另一处。
        //    它在渲染环境检测页时算好存进 st，而这个按钮就在那一屏，
        //    所以点得到它的时候必然已经有值。
        //    ⚠️ 哪天把这个按钮挪到别的屏，记得先触发一次收集。
        summary: st.diagText || '',
        errors: st.jsErrors || [],
        ua: navigator.userAgent,
        screen: window.innerWidth + 'x' + window.innerHeight,
        task: st.task || {},
        pending: st.pending || [],
      }).then(function (d) {
        st.diagBusy = false;
        var made = (d && d.path) || '';
        render();
        // 🔴 **弹资源管理器并选中它** —— 「一键」成不成立全看这一步。
        //    少了它，老师看到「已生成」然后就卡住了：他不知道去哪找。
        //    main.js 的 open-path 对文件走 showItemInFolder，会打开目录
        //    并把文件高亮选中，直接拖进微信就行。
        if (made) window.api.openPath(made);
      }).catch(function (e) {
        st.diagBusy = false;
        // 不静默 —— 后端三个位置都写不进去时会把原因带上来。
        st.err = '生成诊断文件失败：' + String(e && e.message || e);
        render();
      });
    },

    // 展开/收起完整的更新说明。默认只给摘要那几行 —— 620x440 的
    // 窗口塞不下长文，而摘要（Release 正文里分隔线之前那段）已经
    // 够判断「这次更新值不值得现在装」。
    toggleUpdNotes: function () {
      st.updNotesOpen = !st.updNotesOpen;
      render();
    },

    // 展开/收起线路表。折叠是默认 —— 平时没人关心走的哪条路，
    // 只有连不上的时候才想知道为什么。
    toggleUpdLines: function () {
      st.updLinesOpen = !st.updLinesOpen;
      render();
    },

    // 手动指定走哪条线路。空串 = 回到「自动挑最快的」。
    // 留这个后门是因为**最快的未必最稳**：别人的网络环境跟这台机器
    // 可能完全不同，测速赢的那条也可能下到一半就断。
    pickUpdLine: function (id) {
      st.updPick = id || '';
      render();
    },

    // 实测下载速度。**只有用户主动点才跑** —— 查更新顺手拿到的是
    // 响应快慢（谁先答话），跟下载快慢是两回事；但为了后者让每个人
    // 都多等几秒不划算，想精确知道的人自己点。
    probeUpdSpeed: function () {
      if (st.updProbing) return;
      var asset = st.upd && st.upd.asset;
      if (!asset || !asset.url) return;   // 没东西要下，就没什么好测的
      st.updProbing = true;
      render();
      // 不传地址：后端自己去查该测哪个文件（本机任意进程都能 POST 到
      // 这个端口，接受外部 URL 等于把「去访问任意地址」的能力递出去）
      HTTP.post('/api/update/probe', {}).then(function (d) {
        st.updProbing = false;
        if (st.upd && d && d.lines && d.lines.length) {
          st.upd.lines = d.lines;
          // 🔴 **测出来的结果要真的生效。**
          //    原来这里只更新显示，而下载走的是 st.updPick（用户手动
          //    点选的那条）—— 没手动点就是空串，后端 prefer='' 落到
          //    名单第一条。等于测了个寂寞：数字重排了，下的还是老那条。
          //    （2026-09-05 小蔡问「自动真的是实时用最快的吗」才查出来）
          //    现在自动选中最快的，用户仍可手动改回「自动」或别的线路。
          var best = null;
          d.lines.forEach(function (x) {
            if (x.ok && x.bps && (!best || x.bps > best.bps)) best = x;
          });
          if (best) st.updPick = best.id;
        }
        render();
      }).catch(function () {
        st.updProbing = false;
        render();
      });
    },

    // 跨大版本时去 Release 页面下完整安装包
    openReleases: function () {
      window.api.openUrl(
        'https://github.com/kiryusento2017/pdf_to_word/releases/latest');
    },

    // 拿不到官方校验值，用户看过风险说明之后仍然要装。
    // 跟显卡那条规矩一样：报警，但不替他做主。
    installAnyway: function () {
      st.updAllowUnverified = true;
      // 🔴 不是 actions.downloadUpdate() —— 那个变量不存在，
      //    点下去会抛 ReferenceError（2026-09-05 复查发现）。
      window.P2W_ACTS.downloadUpdate();
    },

    // 装 GPU 运行库（CUDA 版 PyTorch，约 2.8 GB）。
    // 发行版里不带它 —— 解压后 4.2 GB，打进安装包会让包涨到 1.5~2 GB，
    // 逼近 GitHub 单文件 2 GiB 上限，而且没显卡的人也得跟着下。
    installGpuLib: function () {
      st.gpuLibBusy = true;
      st.gpuLibError = '';
      st.gpuLib = { got: 0, total: 0, lines: [], cmd: '', log: '' };
      render();
      var poll = setInterval(function () {
        HTTP.get('/api/gpulib/install').then(function (d) {
          st.gpuLibLine = d.line || '';
          st.gpuLib = {
            got: d.got || 0, total: d.total || 0,
            lines: d.lines || [], cmd: d.cmd || '', log: d.log || '',
          };
          // 同上：完成只认后端 state。d.ready 是 torchdep.ready()，
          // 判据是 torch/version.py 在不在 —— pip 装到一半那个文件就已经
          // 落盘了，拿它当完成条件同样会提前跳走。
          if (d.state === 'done') {
            clearInterval(poll);
            st.gpuLibBusy = false;
            // 装好了整页重载 —— 会重新体检，cuda_torch 变绿，
            // 拦截屏自己就散了。这是 reload 那个 action 用的同一招。
            window.location.reload();
          } else if (d.state === 'error') {
            clearInterval(poll);
            st.gpuLibBusy = false;
            st.gpuLibError = d.error || '装失败了';
          }
          render();
        }).catch(function () { /* 轮询失败下次再说 */ });
      }, 1000);
      HTTP.post('/api/gpulib/install', {}).catch(function (e) {
        clearInterval(poll);
        st.gpuLibBusy = false;
        st.gpuLibError = String(e && e.message || e);
        render();
      });
    },

    // 更新装好之后重启。**必须重启才生效** —— 覆盖的是 .py 和 .js，
    // 当前进程跑的还是加载时那份旧代码。
    restartApp: function () { window.api.restart(); },

    // 下更新包并装好。**一口气做完**，不让人自己去覆盖文件 ——
    // 小蔡的原话：「点了更新按钮，自动下载文件，然后就完成更新」
    // 「没有人会去开 github」。装完提示重启，重启才生效。
    //
    // 不传下载地址：后端自己去 GitHub 查。前端传什么后端都不看 ——
    // 服务虽然只绑 127.0.0.1，本机任意进程照样能 POST 一个自己的 URL，
    // 让它下载并覆盖安装目录里会被执行的 .py。
    // 点「更新」就是点更新 —— **前端不再自己先测一次速**。
    //
    // 🔴 2026-09-05 之前这里干过一件绕事：没手动选线路时，前端先打一次
    //    /api/update/probe 测速、挑出最快的塞进 line，再发下载请求。
    //    那是为了绕开后端的一个坑 —— probe_mirrors 有「小包不测速」的
    //    捷径，而更新包 0.55 MB 正好落在里面，于是后端的测速排序拿到
    //    一堆 bps=0，每次都落到名单第一条。
    //
    //    问题是这么一来，「自动挑最快」这件事被拆到了前后端两处：界面上
    //    写着「自动（用最快的）」，真正让它成立的却是前端这段补丁。哪天
    //    补丁被删，后端会**静默**退回按名单顺序试第一条，而那句话还挂着
    //    —— 正好撞上项目铁律「不承诺不存在的行为」，还没有测试拦得住。
    //
    //    现在测速收回后端一处（_upd_work 调 download 时故意不传 size），
    //    前端只管发请求、按 step 显示后端在干什么。
    downloadUpdate: function () {
      var a = (st.upd || {}).asset;
      if (!a || !a.url) return;
      st.updBusy = true;
      render();
      // line 是唯一另一个后端会看的字段。它不是地址，只是一个在
      // GH_MIRRORS 里查表的键，查不到就回到自动挑最快的。
      HTTP.post('/api/update/download',
                { allow_unverified: !!st.updAllowUnverified,
                  line: st.updPick || '' })
        .then(function () {
          stopUpdPolling();
          updPoller = setInterval(pollUpd, 800);
          pollUpd();
        }).catch(function (e) {
          st.updBusy = false;
          if (st.upd) st.upd.error = String(e && e.message || e);
          render();
        });
    },

    cancelDownload: function () {
      HTTP.post('/api/models/download/cancel', {}).catch(function () {});
    },

    // 「我已经有模型了」。以前这里只是跳页，什么也没做 —— 用户选完
    // 目录，界面装作接受了，转换时 MinerU 照样从头下 4.6 GB。
    // 现在真的把目录写进我们自己那份 mineru.json。
    pickLocal: function () {
      window.api.pickDir().then(function (d) {
        var dir = Array.isArray(d) ? d[0] : d;
        if (!dir) return;
        st.srcError = '';
        st.srcLoading = true;
        render();
        HTTP.post('/api/models/use-local', { dir: dir }).then(function (r) {
          st.srcLoading = false;
          if (r && r.ready) {
            if (st.env) st.env.models = { ok: true, dir: r.pipeline || dir };
            st.page = 'main';
          } else {
            st.srcError = '这个文件夹里没找到可用的模型。';
          }
          render();
        }).catch(function (e) {
          st.srcLoading = false;
          st.srcError = String(e && e.message || e);
          render();
        });
      });
    },

    loadRuns: loadRuns,
    loadUpgPending: loadUpgPending,

    // 装下好的那批。**点「立即重启」走的是这条**：先装，装完再重启。
    // 🔴 顺序不能反 —— 先重启的话，重启之后还得有人来按一次「装」，
    //    又回到了原来那个「谁按下那个装」没人管的坑里。
    installUpgrade: function () {
      st.upgIns = { state: 'running', lines: [] };
      render();
      HTTP.post('/api/upgrade/install', {})
        .then(function () { pollUpgIns(); })
        .catch(function (e) {
          st.upgIns = { state: 'done', ok: false,
                        error: String(e && e.message || e) };
          render();
        });
    },

    openFile: function (p) { window.api.openFile(p); },
    openPath: function (p) { window.api.openPath(p); },
  };
}());
