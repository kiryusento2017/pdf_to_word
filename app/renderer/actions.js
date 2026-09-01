// 所有用户动作。页面渲染是纯函数，副作用全在这里。
'use strict';

(function () {
  var st = window.P2W_STATE;
  var render = window.P2W_RENDER;
  var HTTP = window.P2W_HTTP;

  var poller = null;

  function stopPolling() {
    if (poller) { clearInterval(poller); poller = null; }
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

  function pollUpd() {
    HTTP.get('/api/update/download').then(function (d) {
      if (!st.upd) return;
      st.upd.dlGot = d.got || 0;
      st.upd.dlTotal = d.total || 0;
      st.upd.phase = d.state;   // running / installing / done / error / need_confirm
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

  function poll() {
    if (!st.taskId) return;
    HTTP.get('/api/convert/' + st.taskId).then(function (d) {
      st.task = d;
      if (d.state === 'done' || d.state === 'cancelled') stopPolling();
      render();
    }).catch(function () {
      // 单次轮询失败不必惊动用户（服务正忙），下一轮还会问
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
    // 第一步：装 C++ 运行库。跟装 GPU 运行库同一套轮询。
    installVcRedist: function () {
      var st = window.P2W_STATE;
      st.vcBusy = true;
      st.vcError = '';
      st.vcDl = { got: 0, total: 0, cmd: '', lines: [] };
      window.P2W_RENDER();
      post('/api/vcredist/install').then(function () {
        var tick = function () {
          get('/api/vcredist/install').then(function (d) {
            st.vcDl = d;
            if (d.state === 'running') { setTimeout(tick, 500); return; }
            st.vcBusy = false;
            if (d.state === 'error') { st.vcError = d.error || '装失败了'; }
            window.P2W_RELOAD_ENV();
            window.P2W_RENDER();
          });
        };
        setTimeout(tick, 400);
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
      ((st.task && st.task.results) || []).forEach(function (r) {
        if (!r.ok) failed[r.pdf] = true;
      });
      st.items.forEach(function (x) { st.picked[x.path] = !!failed[x.path]; });
      st.task = null;
      st.taskId = '';
      st.err = '';
      stopPolling();
      render();
    },

    addPaths: addPaths,

    pickFiles: function () {
      window.api.pickFiles().then(addPaths);
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
      st.updBusy = true;
      st.upd = null;
      render();
      HTTP.get('/api/update/check').then(function (d) {
        st.updBusy = false;
        st.upd = d;
        render();
      }).catch(function (e) {
        st.updBusy = false;
        st.upd = { ok: false, error: String(e && e.message || e) };
        render();
      });
    },

    closeUpdate: function () {
      st.upd = null;
      st.updAllowUnverified = false;
      render();
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
      actions.downloadUpdate();
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
    downloadUpdate: function () {
      var a = (st.upd || {}).asset;
      if (!a || !a.url) return;
      st.updBusy = true;
      render();
      HTTP.post('/api/update/download',
                st.updAllowUnverified ? { allow_unverified: true } : {})
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

    openFile: function (p) { window.api.openFile(p); },
    openPath: function (p) { window.api.openPath(p); },
  };
}());
