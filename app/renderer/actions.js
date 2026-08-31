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
    go: function (page) {
      st.page = page;
      st.err = '';
      render();
    },

    reload: function () { window.location.reload(); },

    quit: function () { window.close(); },

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
      }).then(function (d) {
        st.taskId = d.task_id;
        st.task = null;
        st.starting = false;
        st.page = 'run';
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

    openFile: function (p) { window.api.openFile(p); },
    openPath: function (p) { window.api.openPath(p); },
  };
}());
