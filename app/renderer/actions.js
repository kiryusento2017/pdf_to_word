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

    quit: function () { window.close(); },

    // 显卡不满足时用户选了「仍然继续」。**只有用户能按这个** ——
    // 软件不替他做主，但按过之后就不再拦第二次。
    ackGate: function () {
      st.gateAck = true;
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

    startDownload: function () {
      // 模型下载由 MinerU 自己在首次提取时触发（它认 MINERU_MODEL_SOURCE），
      // 这里只把选中的源记下来并放行 —— 自己再实现一套下载器等于跟它抢活，
      // 两边对模型清单的理解一旦不一致就会下出一个跑不起来的半套。
      st.page = 'main';
      render();
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
