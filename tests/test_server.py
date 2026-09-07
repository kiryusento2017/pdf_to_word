# -*- coding: utf-8 -*-
r"""本地 HTTP 服务的接口。

用 FastAPI 的 TestClient，不真起端口 —— 起端口的测试会在 CI 上抢占资源、
在本机上跟正在运行的软件撞车。
"""
import io
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))
sys.path.insert(0, os.path.join(ROOT, 'server'))

from fastapi.testclient import TestClient  # noqa: E402

import main as srv  # noqa: E402

WORK = os.path.join(ROOT, '_tmp', 'tests', 'server')
client = TestClient(srv.app)


def _make_pdf(path, pages=1, text='enough characters here'):
    import pymupdf
    doc = pymupdf.open()
    for _ in range(pages):
        pg = doc.new_page()
        if text:
            pg.insert_text((72, 100), text, fontsize=12)
    doc.save(path)
    doc.close()


class Test环境自检(unittest.TestCase):

    def test_一次给齐首屏要的全部信息(self):
        r"""分三个请求只会让首屏闪三次。"""
        r = client.get('/api/env')
        self.assertEqual(r.status_code, 200)
        d = r.json()
        for key in ('gpu', 'office', 'node', 'pandoc', 'mineru'):
            self.assertIn(key, d, '缺 %s' % key)
            self.assertIn('ok', d[key])

    def test_显卡结论带人话理由(self):
        d = client.get('/api/env').json()
        self.assertIsInstance(d['gpu']['why'], str)
        self.assertTrue(d['gpu']['why'], '没给理由')

    def test_pandoc是内置的(self):
        d = client.get('/api/env').json()
        self.assertTrue(d['pandoc']['ok'], '内置 pandoc 找不到')
        self.assertIn('runtime', d['pandoc']['path'])


class Test选书(unittest.TestCase):

    def setUp(self):
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)

    def tearDown(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def test_拖进文件夹会递归找出PDF(self):
        sub = os.path.join(WORK, '第一章')
        os.makedirs(sub)
        _make_pdf(os.path.join(WORK, 'a.pdf'))
        _make_pdf(os.path.join(sub, 'b.pdf'))
        r = client.post('/api/scan', json={'paths': [WORK]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()['items']), 2)

    def test_同一份拖两次只算一份(self):
        p = os.path.join(WORK, 'a.pdf')
        _make_pdf(p)
        r = client.post('/api/scan', json={'paths': [p, p, WORK]})
        self.assertEqual(len(r.json()['items']), 1)

    def test_非PDF直接忽略(self):
        io.open(os.path.join(WORK, 'note.txt'), 'w', encoding='utf-8').write('x')
        r = client.post('/api/scan', json={'paths': [WORK]})
        self.assertEqual(r.json()['items'], [])

    def test_坏文件也要返回而不是整批失败(self):
        r"""用户拖进来的文件夹里什么都可能有，不能因为一个坏的就全不给。"""
        _make_pdf(os.path.join(WORK, 'good.pdf'))
        io.open(os.path.join(WORK, 'bad.pdf'), 'w', encoding='utf-8').write('坏的')
        items = client.post('/api/scan', json={'paths': [WORK]}).json()['items']
        self.assertEqual(len(items), 2)
        self.assertEqual(sorted(x['ok'] for x in items), [False, True])

    def test_体检结果带页数和无文字层的页(self):
        _make_pdf(os.path.join(WORK, 'a.pdf'), pages=2, text=None)
        item = client.post('/api/scan',
                           json={'paths': [WORK]}).json()['items'][0]
        self.assertEqual(item['pages'], 2)
        self.assertEqual(item['scan_pages'], [1, 2])


class Test转换任务(unittest.TestCase):

    def setUp(self):
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)
        self.pdf = os.path.join(WORK, 'a.pdf')
        _make_pdf(self.pdf)
        self._orig = srv.convert.pdf_to_word

    def tearDown(self):
        srv.convert.pdf_to_word = self._orig
        shutil.rmtree(WORK, ignore_errors=True)

    def _fake_convert(self, delay=0.0, ok=True):
        def fake(pdf, out, work, on_progress=None, **kw):
            if on_progress:
                on_progress('识别公式', 1, 2)
                on_progress('识别公式', 2, 2)
            if delay:
                time.sleep(delay)
            return {'ok': ok, 'error': '' if ok else '假装失败',
                    'pdf': pdf, 'docx': out, 'pages': 3, 'scan_pages': [],
                    'formulas': 10, 'formulas_xsl': 10, 'tables': 1,
                    'images': 2, 'math_engine': 'xsl', 'math_note': '',
                    'auto_dir': ''}
        srv.convert.pdf_to_word = fake

    def _wait(self, tid, timeout=10):
        t0 = time.time()
        while time.time() - t0 < timeout:
            d = client.get('/api/convert/%s' % tid).json()
            if d['state'] in ('done', 'cancelled'):
                return d
            time.sleep(0.02)
        self.fail('等超时了')

    def test_空清单直接拒绝(self):
        r = client.post('/api/convert', json={'paths': []})
        self.assertEqual(r.status_code, 400)

    def test_转完能拿到逐份结果(self):
        self._fake_convert()
        tid = client.post('/api/convert',
                          json={'paths': [self.pdf],
                                'out_dir': WORK}).json()['task_id']
        d = self._wait(tid)
        self.assertEqual(d['state'], 'done')
        self.assertEqual(len(d['results']), 1)
        self.assertTrue(d['results'][0]['ok'])
        self.assertIn('line', d['results'][0], '没给一行人话的摘要')

    def test_一份失败不影响其余(self):
        r"""用户一次拖一整个文件夹，中间有一份坏的很正常。"""
        p2 = os.path.join(WORK, 'b.pdf')
        _make_pdf(p2)
        calls = [0]

        def fake(pdf, out, work, on_progress=None, **kw):
            calls[0] += 1
            bad = calls[0] == 1
            return {'ok': not bad, 'error': '假装失败' if bad else '',
                    'pdf': pdf, 'docx': out, 'pages': 1, 'scan_pages': [],
                    'formulas': 0, 'formulas_xsl': 0, 'tables': 0,
                    'images': 0, 'math_engine': 'pandoc', 'math_note': '',
                    'auto_dir': ''}
        srv.convert.pdf_to_word = fake
        tid = client.post('/api/convert',
                          json={'paths': [self.pdf, p2],
                                'out_dir': WORK}).json()['task_id']
        d = self._wait(tid)
        self.assertEqual(len(d['results']), 2)
        self.assertEqual([x['ok'] for x in d['results']], [False, True])

    def test_进度里有阶段名(self):
        r"""界面上要显示「在识别公式」而不是一个转圈。"""
        self._fake_convert(delay=0.25)
        tid = client.post('/api/convert',
                          json={'paths': [self.pdf],
                                'out_dir': WORK}).json()['task_id']
        seen = ''
        for _ in range(60):
            d = client.get('/api/convert/%s' % tid).json()
            if d['stage']:
                seen = d['stage']
                break
            time.sleep(0.02)
        self._wait(tid)
        self.assertTrue(seen, '轮询期间一次都没拿到阶段名')

    def test_后台线程炸了也不能让任务永远转圈(self):
        r"""后台线程的异常会被 Python 悄悄吞掉，任务停在 running 不动，
        界面上就是转到天荒地老。实测撞见过：漏一个 import，
        四条测试全部等到超时才失败。"""
        def boom(*a, **kw):
            raise RuntimeError('假装内部炸了')
        srv.convert.pdf_to_word = boom
        tid = client.post('/api/convert',
                          json={'paths': [self.pdf],
                                'out_dir': WORK}).json()['task_id']
        d = self._wait(tid, timeout=5)
        self.assertEqual(d['state'], 'done', '任务卡在 running 了')
        self.assertIn('假装内部炸了', d.get('error', ''), '炸了却没说原因')

    def test_落日志用到的模块都import了(self):
        r"""🔴 server/main.py 用了 io.open 却没 import io。异常被
        `except Exception` 静默吞掉 —— convert.log 两个月一次都没生成过，
        而它正是「远程排查唯一的凭据」（RELEASE.md 的硬约束之一）。

        2026-09-02 靠新加的防御（不再静默吞异常）才把它揪出来：
        NameError: name 'io' is not defined。
        """
        for name in ('io', 'os', 'time', 'threading', 'uuid', 'paths'):
            self.assertTrue(hasattr(srv, name),
                            'server/main.py 缺 import %s' % name)

    def test_转换日志真的能落盘(self):
        r"""光有 import 还不够 —— 真开一次文件才算数。
        这条钉的是 RELEASE.md 那句「没有日志 = 远程排查等于零」。"""
        log = os.path.join(srv.paths.ensure(srv.paths.LOGS), 'convert.log')
        existed = os.path.isfile(log)
        f = io.open(log, 'a', encoding='utf-8', errors='replace', newline='')
        try:
            f.write('')
            f.flush()
        finally:
            f.close()
        self.assertTrue(os.path.isfile(log))
        if not existed:
            os.remove(log)

    def test_预计剩余时间随页数走(self):
        r"""每页秒数是实测的（GPU 26 秒/页、CPU 46 秒/页），
        页数在体检时就知道 —— 这两个数一乘就是用户唯一关心的答案。"""
        t = {'pages': [10, 20], 'results': [], 'sec_per_page': 26.0}
        r = srv._remain(t, elapsed=0)
        self.assertEqual(r, int(30 * 26))          # 30 页 x 26 秒

    def test_跑完的用真实速率反推(self):
        r"""转到第三份时，前两份的真实速度比出厂估值准得多。

        速率取自 done_elapsed（上一份转完那一刻），不是当前的 elapsed。
        """
        t = {'pages': [10, 10], 'results': [{'ok': True}],
             'sec_per_page': 26.0, 'done_elapsed': 500.0,
             'real_elapsed': 500.0, 'real_pages': 10}
        # 第一份 10 页真实花了 500 秒（这台机器慢），剩下 10 页照 50 秒/页估
        self.assertEqual(srv._remain(t, elapsed=500), 500)

    def test_剩余时间必须随时间递减(self):
        r"""🔴 这条是为一个真实 bug 立的桩。

        原实现 `spp = elapsed / done_pages` 把当前这份正在跑的时间也算进了
        「每页耗时」，于是转得越久估得越久。实测三份书的第二份进行中时：

            已跑 260 秒 → 还要  520 秒
            已跑 380 秒 → 还要  760 秒
            已跑 700 秒 → 还要 1400 秒

        用户拖一个文件夹进来就会撞上 —— 而且每个单点看着都"挺合理"，
        只有连起来看才知道荒谬。所以必须测序列，不能测单点。
        """
        t = {'pages': [10, 10, 10], 'results': [{'ok': True}],
             'sec_per_page': 26.0, 'done_elapsed': 260.0,
             'real_elapsed': 260.0, 'real_pages': 10}
        seq = [srv._remain(t, elapsed=e) for e in (260, 320, 380, 500, 700)]
        for a, b in zip(seq, seq[1:]):
            self.assertGreaterEqual(a, b,
                                    '剩余时间涨了：%s（等得越久说要等越久）' % seq)
        self.assertLess(seq[-1], seq[0], '完全没动：%s' % seq)

    def test_第一份进行中也要递减(self):
        r"""done=0 时走出厂估值，同样必须随时间往下走。"""
        t = {'pages': [10, 10, 10], 'results': [], 'sec_per_page': 26.0}
        seq = [srv._remain(t, elapsed=e) for e in (0, 60, 120, 240)]
        for a, b in zip(seq, seq[1:]):
            self.assertGreater(a, b, '第一份进行中没有递减：%s' % seq)

    def test_一份转完后剩余时间不该突然暴涨(self):
        r"""跨越「一份转完」这个边界时，估算不该跳变太离谱 ——
        那一刻速率从出厂估值切换成实测值，是最容易出突刺的地方。
        """
        pages = [10, 10]
        before = srv._remain({'pages': pages, 'results': [],
                              'sec_per_page': 26.0}, elapsed=259)
        after = srv._remain({'pages': pages, 'results': [{'ok': True}],
                             'sec_per_page': 26.0, 'done_elapsed': 260.0,
                             'real_elapsed': 260.0, 'real_pages': 10},
                            elapsed=261)
        # 第一份正好按预期速度跑完，切换前后应当基本连续
        self.assertLess(abs(after - before), 60,
                        '切换瞬间跳了 %d 秒（%d → %d）' % (abs(after - before), before, after))

    def test_缓存命中的份不进速率(self):
        r"""缓存两秒转完一整本，混进速率会把「还要多久」拉到荒谬的低。

        第一份 23 页走缓存（2 秒），第二份 10 页真跑（240 秒）。
        速率必须按 240/10 算，而不是 242/33。
        """
        t = {'pages': [23, 10, 10], 'results': [{'ok': True}, {'ok': True}],
             'sec_per_page': 26.0, 'done_elapsed': 242.0,
             'real_elapsed': 240.0, 'real_pages': 10}
        self.assertEqual(srv._remain(t, elapsed=242), int(10 * 24.0))

    def test_全部命中缓存时退回出厂估值(self):
        r"""一份真跑的都没有，就没有可信的实测速率 ——
        宁可用粗的出厂估值，也不能拿缓存那两秒推出每页 0.2 秒。"""
        t = {'pages': [10, 10], 'results': [{'ok': True}],
             'sec_per_page': 26.0, 'done_elapsed': 2.0,
             'real_elapsed': 0.0, 'real_pages': 0}
        self.assertEqual(srv._remain(t, elapsed=2), int(10 * 26.0))

    def test_全跑完剩余是0(self):
        t = {'pages': [10], 'results': [{'ok': True}], 'sec_per_page': 26.0}
        self.assertEqual(srv._remain(t, elapsed=260), 0)

    def test_估不出来就返回None而不是瞎猜(self):
        r"""界面上宁可显示「正在估算」，也不能给一个编的数。"""
        self.assertIsNone(srv._remain({'pages': [], 'results': []}, elapsed=5))

    def test_剩余不会是负数(self):
        r"""跑得比估计慢时，剩余会算成负的 —— 显示「还要约 -3 分钟」是笑话。"""
        t = {'pages': [10], 'results': [], 'sec_per_page': 26.0}
        self.assertGreaterEqual(srv._remain(t, elapsed=9999), 0)

    def test_轮询返回里带着剩余时间(self):
        self._fake_convert(delay=0.2)
        tid = client.post('/api/convert',
                          json={'paths': [self.pdf],
                                'out_dir': WORK}).json()['task_id']
        d = client.get('/api/convert/%s' % tid).json()
        self.assertIn('remain', d, '轮询没带剩余时间，界面拿什么显示')
        self._wait(tid)

    def test_查不存在的任务给404(self):
        self.assertEqual(client.get('/api/convert/nope').status_code, 404)

    def test_取消在两份之间生效(self):
        r"""MinerU 那步是子进程，中途硬杀会留半截产物，比多等一会儿麻烦。"""
        p2 = os.path.join(WORK, 'b.pdf')
        _make_pdf(p2)
        self._fake_convert(delay=0.3)
        tid = client.post('/api/convert',
                          json={'paths': [self.pdf, p2],
                                'out_dir': WORK}).json()['task_id']
        time.sleep(0.05)
        self.assertEqual(client.post('/api/convert/%s/cancel' % tid).status_code, 200)
        d = self._wait(tid)
        self.assertEqual(d['state'], 'cancelled')
        self.assertLess(len(d['results']), 2, '取消了却还是全转完了')


class Test更新的接缝(unittest.TestCase):
    r"""🔴 后端必须把 digest 传给 download。

    2026-09-02：更新功能从发出去那天起就是坏的 —— 任何人点更新都会看到
    「GitHub 没给这个更新包的校验值」。原因是 server 调 download 时
    **压根没传 digest 参数**：

        ok, err, via = update.download(url, dest, on_progress=on_prog)
                                                          ↑ 少了 digest

    而 220 条测试全绿。因为 update.py 那边测得很细（有 digest 就校验、
    没有就拒绝），server 那边也测过路由，**唯独没人测「server 到底有没有
    把 digest 传进去」** —— 两个模块各自正确，接缝处断掉。

    这类 bug 只能靠「跨模块的那一手」来钉。
    """

    def test_后端把digest传给了download(self):
        import server.main as sm

        seen = {}

        def fake_download(url, dest, **kw):
            seen.update(kw)
            seen['url'] = url
            return False, '假装失败', 'x'

        orig_dl = sm.update.download
        orig_check = sm.update.check
        sm.update.download = fake_download
        sm.update.check = lambda: {
            'ok': True, 'has_update': True, 'latest': 'v9.9.9',
            'asset': {'name': 'u.zip', 'url': 'https://x/u.zip',
                      'size': 123, 'digest': 'a' * 64},
            'error': '',
        }
        self.addCleanup(setattr, sm.update, 'download', orig_dl)
        self.addCleanup(setattr, sm.update, 'check', orig_check)

        sm._upd_work()

        self.assertIn('digest', seen, 'server 没把 digest 传给 download')
        self.assertEqual(seen['digest'], 'a' * 64)
        # 🔴 **size 故意不传。** 它在 download() 里唯一的用途是喂给
        #    _download_order -> probe_mirrors 的「小包不测速」捷径判断，
        #    跟长度校验毫无关系 —— 那个用的是 HTTP 响应头的 Content-Length
        #    （见 _fetch_one）。更新包 0.55 MB 会触发捷径，让 bps 全变 0、
        #    排序作废，「自动（用最快的）」当场退化成「按名单顺序试第一条」。
        #
        #    这条断言原来是反的，理由写着「长度校验要用」—— **理由是错的**，
        #    2026-09-05 把测速收回后端一处时才发现。又一次「测试和实现
        #    一起错，于是一起绿」（CLAUDE.md 第 4 条）。
        self.assertIsNone(seen.get('size'),
                          'size 传给了 download —— 会触发小包捷径，'
                          '自动挑最快的线路作废')

    def test_后端不看前端传的url(self):
        r"""服务只绑 127.0.0.1，但本机任意进程都能 POST 一个自己的地址，
        让它下载并解压覆盖安装目录里会被执行的 .py。"""
        import inspect
        import server.main as sm
        src = inspect.getsource(sm._upd_work)
        self.assertIn('update.check()', src,
                      '后端没有自己去查 Release')
        self.assertNotIn('req.url', src, '还在用前端传的地址')


class Test环境检测的接口(unittest.TestCase):
    r"""「关于 → 环境检测」那一屏要的几个接口。

    这一屏的存在理由：用户装完之后 C 盘莫名少几个 G，而他永远发现
    不了是谁干的（pip 缓存藏在隐藏文件夹、文件名是哈希、扩展名是
    .body）。**看得到，才谈得上删不删。**
    """

    def test_扫占用给得出四类(self):
        r = client.get('/api/maint/scan')
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d['ok'])
        keys = [it['key'] for it in d['items']]
        for k in ('pip_cache', 'logs', 'tmp', 'models'):
            self.assertIn(k, keys)

    def test_模型那项不给清(self):
        r"""4.6 GB，清了要重下。不能让用户手滑点掉。"""
        d = client.get('/api/maint/scan').json()
        m = [it for it in d['items'] if it['key'] == 'models'][0]
        self.assertFalse(m['cleanable'])

    def test_转换进行中不许清理(self):
        r"""_tmp 里有正在用的中间产物，删了当场炸。
        跟「转换中禁用检查更新」一个规矩。"""
        tid = 'faketask'
        with srv._LOCK:
            srv._TASKS[tid] = {'state': 'running'}
        try:
            r = client.post('/api/maint/clean', json={'keys': ['tmp']})
            self.assertEqual(r.status_code, 409)
            self.assertIn('正在转换', r.json()['detail'])
        finally:
            with srv._LOCK:
                srv._TASKS.pop(tid, None)

    def test_安装进行中不许清理(self):
        r"""🔴 装东西的三类任务各用一个全局字典，**一个都不在 `_TASKS`
        里** —— `_DL`（模型 / GPU 运行库 / vcredist）、`_UPG`（依赖升级）、
        `_UPD`（软件自更新）。只判 `_TASKS` 会把它们全放行：pip 正往
        `%TEMP%` 的工作目录里解包时被清掉，几 GB 白下。

        （2026-09-06 加 temp_pip 那一项时查出来的既有缺口。`_UPD` 的
        `installing` 也算在跑 —— 它的状态机有六种，别只判 running。）
        """
        for name, state in (('_DL', 'running'), ('_UPG', 'running'),
                            ('_UPD', 'running'), ('_UPD', 'installing')):
            d = getattr(srv, name)
            old = dict(d)
            with srv._LOCK:
                d['state'] = state
            try:
                r = client.post('/api/maint/clean', json={'keys': ['tmp']})
                self.assertEqual(r.status_code, 409,
                                 '%s=%s 时清理没被拦住' % (name, state))
                self.assertIn('安装', r.json()['detail'])
            finally:
                with srv._LOCK:
                    d.clear()
                    d.update(old)

    def test_没在装的时候不许拦(self):
        r"""拦过头比不拦更烦人 —— 清理会永远点不了，而用户不知道为什么。
        `need_confirm` 是在等用户点确认，不算在跑。"""
        old = dict(srv._UPD)
        try:
            for state in ('idle', 'done', 'error', 'need_confirm'):
                with srv._LOCK:
                    srv._UPD['state'] = state
                r = client.post('/api/maint/clean',
                                json={'keys': [], 'paths': []})
                self.assertEqual(r.status_code, 200, '%s 时不该拦' % state)
        finally:
            with srv._LOCK:
                srv._UPD.clear()
                srv._UPD.update(old)

    def test_什么都不选就什么都不删(self):
        r = client.post('/api/maint/clean', json={'keys': [], 'paths': []})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['freed'], 0)

    def test_不许删缓存目录外的文件(self):
        r"""🔴 路径是前端传来的。server 只绑 127.0.0.1，但那不等于
        只有我们能连 —— 本机任意进程都能 POST 一个自己的路径过来。"""
        r = client.post('/api/maint/clean',
                        json={'keys': [], 'paths': [r'C:\Windows\notepad.exe']})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['freed'], 0)
        self.assertTrue(r.json()['failed'])
        self.assertIn('拒绝', r.json()['failed'][0])

    def test_本地版本不联网就能给(self):
        r"""打开页面时就要显示，不能等联网。"""
        r = client.get('/api/deps/local')
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d['ok'])
        self.assertIn('versions', d)
        self.assertIn('mineru', d['versions'])

    def test_诊断报告该有的都有(self):
        r"""老师打电话说「用不了」时，这一段能省掉十几轮问答。"""
        r = client.get('/api/diag')
        self.assertEqual(r.status_code, 200)
        d = r.json()
        for k in ('tag', 'os', 'root', 'writable', 'free_gb',
                  'versions', 'models_ready', 'gpu', 'admin',
                  'last_run', 'last_error'):
            self.assertIn(k, d, '诊断报告缺 %s' % k)

    def test_诊断报告带安装路径原文(self):
        r"""中文路径的坑栽过好几次，路径本身就是证据。"""
        d = client.get('/api/diag').json()
        self.assertTrue(d['root'])
        self.assertIn('pdf_to_word', d['root'].replace(chr(92), '/'))

    def test_没有运行记录时给None而不是炸(self):
        d = client.get('/api/diag').json()
        # 可能有也可能没有，但不能报错
        self.assertIn('last_run', d)


class Test转换会记下运行结果(unittest.TestCase):
    r"""诊断报告里最值钱的两条，而 convert.log 给不了 —— 它只记时间
    和路径，**没有结果**。"""

    def test_note_run接在唯一的汇合点上(self):
        r"""convert.pdf_to_word 有 5 个 return 点，逐个插就是
        「散着写、漏一处」。必须插在 server 里那个唯一的汇合点。"""
        src = io.open(os.path.join(ROOT, 'server', 'main.py'),
                      encoding='utf-8').read()
        self.assertEqual(src.count('maint.note_run('), 1,
                         'note_run 应该只有一处调用')
        # 且必须在 summary_line 后面（那时 rep 已经完整）
        i = src.find("rep['line'] = convert.summary_line(rep)")
        j = src.find('maint.note_run(')
        self.assertGreater(j, i, 'note_run 要在 rep 完整之后调')

    def test_记日志被try包着(self):
        r"""🔴 记日志绝不能把转换搞崩 —— 走到那一步用户的 Word
        已经转好了。"""
        src = io.open(os.path.join(ROOT, 'server', 'main.py'),
                      encoding='utf-8').read()
        i = src.find('maint.note_run(')
        seg = src[max(0, i - 200):i]
        self.assertIn('try:', seg, 'note_run 调用没被 try 包住')


class Test升级接口(unittest.TestCase):
    r"""依赖升级。**升不升由用户决定，但过程必须可预测。**"""

    def test_转换进行中不许升级(self):
        r"""升级会换掉 pipeline 用的包，转到一半换等于让后面几份
        跑在不同的代码上。"""
        tid = 'faketask2'
        with srv._LOCK:
            srv._TASKS[tid] = {'state': 'running'}
        try:
            r = client.post('/api/upgrade/plan', json={'picked': ['mineru']})
            self.assertEqual(r.status_code, 409)
            self.assertIn('正在转换', r.json()['detail'])
        finally:
            with srv._LOCK:
                srv._TASKS.pop(tid, None)

    def test_没选包时不乱跑(self):
        r = client.post('/api/upgrade/plan', json={'picked': []})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['ok'])

    def test_开机问一次有没有没做完的(self):
        r = client.get('/api/upgrade/pending')
        self.assertEqual(r.status_code, 200)
        self.assertIn('action', r.json())
        self.assertIn(r.json()['action'], ('none', 'install', 'rollback'))

    def test_备份列表拿得到(self):
        r = client.get('/api/upgrade/backups')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['ok'])
        self.assertIsInstance(r.json()['items'], list)

    def test_没有待装的东西时install不乱装(self):
        r = client.post('/api/upgrade/install')
        self.assertEqual(r.status_code, 200)
        # 正常情况下没有待装的升级
        d = r.json()
        if not d['ok']:
            self.assertIn('没有', d['error'])

    def test_没有备份时rollback不装作成功(self):
        r = client.post('/api/upgrade/rollback')
        self.assertEqual(r.status_code, 200)
        d = r.json()
        if not d['ok']:
            self.assertTrue(d['error'])


class Test模型更新(unittest.TestCase):
    r"""模型有更新时重新下一次。

    🔴 **不清空 models/** —— 2026-09-05 实测确认 mineru 用的
    modelscope.snapshot_download 是增量的（原样再跑 0.9 秒 vs 全新
    21 秒，删掉一个文件再跑只补那一个）。所以「更新」和「首次下载」
    走的是同一条路。
    """

    def test_转换进行中不许下模型(self):
        r"""增量下载虽然不删旧文件，但写 models/ 目录仍然跟正在跑的
        MinerU 抢文件锁。跟「转换中禁用清理」一个规矩。

        首次下载时撞不上这条（那时还没法转换），是「更新模型」这个
        入口把它变成了真实场景。"""
        tid = 'faketask3'
        with srv._LOCK:
            srv._TASKS[tid] = {'state': 'running'}
            was = srv._DL.get('state')
            srv._DL['state'] = 'idle'
        try:
            r = client.post('/api/models/download', json={'source': 'modelscope'})
            self.assertEqual(r.status_code, 409)
            self.assertIn('正在转换', r.json()['detail'])
        finally:
            with srv._LOCK:
                srv._TASKS.pop(tid, None)
                if was is not None:
                    srv._DL['state'] = was

    def test_已经在下时不重复启动(self):
        with srv._LOCK:
            was = srv._DL.get('state')
            srv._DL['state'] = 'running'
        try:
            r = client.post('/api/models/download', json={'source': 'modelscope'})
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json().get('already'))
        finally:
            with srv._LOCK:
                if was is not None:
                    srv._DL['state'] = was


class Test倒计时越用越准(unittest.TestCase):
    r"""只转一份时，原来全程用写死的 26 秒/页，一动不动。

    现在改成：开工前从这台机器的历史里学速度、估一次总时长，之后
    **老实倒数中途不改**（小蔡定的）。准不准交给跨次积累。
    """

    def setUp(self):
        self._runs = srv.maint.runs

    def tearDown(self):
        srv.maint.runs = self._runs

    def _hist(self, rows):
        srv.maint.runs = lambda limit=None: rows

    def test_没有历史时用出厂值(self):
        r"""出厂值取自 2026-09-06 干净环境那次实测：56 页 1814 秒。
        估出来 1784 秒，误差 1.7%。"""
        self._hist([])
        total, w = srv._estimate([56])
        self.assertLess(abs(total - 1814) / 1814.0, 0.05,
                        '出厂值估出 %d 秒，跟实测 1814 秒差太多' % total)
        self.assertAlmostEqual(w['pass1'] + w['pass2'] + w['other'], 1.0, places=3)

    def test_权重跟实测对得上(self):
        r"""实测两轮 13 分 17 秒 / 15 分 03 秒，约 44% / 51%，其余 5%。"""
        self._hist([])
        _t, w = srv._estimate([56])
        self.assertAlmostEqual(w['pass1'], 0.44, delta=0.03)
        self.assertAlmostEqual(w['pass2'], 0.51, delta=0.03)

    def test_有历史就用学来的速度(self):
        r"""这台机器比出厂值慢一倍，估出来的时间就该长一截。"""
        self._hist([{'pages': 10, 'elements': 200,
                     'pass1_sec': 284, 'pass2_sec': 328}] * 3)
        total, _w = srv._estimate([10])
        self.assertGreater(total, 500, '学到的慢速度没用上：%d' % total)

    def test_缓存命中的那几份不进统计(self):
        r"""🔴 秒回是没跑显卡，不是显卡快。混进去会把速度学成离谱的快，
        之后所有估值全线崩坏。

        ⚠️ **这条有两道防线，它只测到结果，测不出是哪道在起作用**：
        `_learned_rates` 里的 `and a > 0` 是第一道，`_median` 里的
        「过滤掉 0」是第二道。2026-09-06 变异测试确认：把第一道拆掉，
        这条照样绿 —— 第二道兜住了。下面那条专门测第二道。
        """
        self._hist([{'pages': 23, 'elements': 0, 'pass1_sec': 0, 'pass2_sec': 0}] * 5)
        total, _w = srv._estimate([56])
        self.assertGreater(total, 1000, '被缓存记录带歪了：%d 秒' % total)

    def test_算速度时零和负数一律不算数(self):
        r"""缓存命中那几份的耗时是 0。中位数这一层必须自己把 0 挡掉，
        不能指望调用方每次都记得过滤。"""
        self.assertEqual(srv._median([0, 0, 0], 7.5), 7.5, '全是 0 时该退回兜底值')
        self.assertEqual(srv._median([0, 4.0, 0], 7.5), 4.0, '0 混进来把中位数带歪了')
        self.assertEqual(srv._median([], 7.5), 7.5)

    def test_偶尔一次抢显卡的慢样本带不歪(self):
        r"""🔴 用中位数不用平均数。一边转一边开别的软件那次会特别慢
        （实测 >64 秒/页 vs 干净环境 32.4），平均数会被它拖歪。"""
        normal = {'pages': 10, 'elements': 200, 'pass1_sec': 142, 'pass2_sec': 164}
        slow = {'pages': 10, 'elements': 200, 'pass1_sec': 1420, 'pass2_sec': 1640}
        self._hist([normal, normal, slow, normal, normal])
        total, _w = srv._estimate([10])
        self.assertLess(total, 500, '被那次慢的拖歪了：%d 秒' % total)

    def test_估过就老实倒数不再重算(self):
        r"""🔴 小蔡：「一开始是多少就老老实实的一点一点倒计时。」
        边跑边改正是「转得越久说要等得越久」那个事故的土壤。"""
        t = {'est_total': 1800, 'pages': [56], 'results': []}
        self.assertEqual(srv._remain(t, elapsed=0), 1800)
        self.assertEqual(srv._remain(t, elapsed=600), 1200)
        self.assertEqual(srv._remain(t, elapsed=1800), 0)

    def test_倒计时不会变成负数(self):
        t = {'est_total': 100, 'pages': [10], 'results': []}
        self.assertEqual(srv._remain(t, elapsed=99999), 0)

    def test_估不出来时老算法还在(self):
        r"""🔴 体检没拿到页数时走不到新路，老那套按已完成份数反推的算法
        必须还在 —— 它守着「转得越久说要等得越久」那几条事故教训。"""
        t = {'pages': [10, 20], 'results': [], 'sec_per_page': 26.0}
        self.assertEqual(srv._remain(t, elapsed=0), int(30 * 26))



class Test学速度这条链不许断(unittest.TestCase):
    r"""🔴 **这一条不许用 mock。**

    2026-09-07 栽过：`convert` 把 pass1_sec / pass2_sec / elements 算得
    好好的，`maint.note_run` 却没把它们写进历史（那里是逐字段挑的白名单），
    于是 `_learned_rates` 永远读不到东西、倒计时恒吃出厂常量 ——
    **「越用越准」在生产路径上等于没做**。

    而当时唯一相关的测试把 `maint.runs` 整个换成手工捏的行数据，
    断链被盖得严严实实、全绿。跟 CLAUDE.md 第 4 条（formulas_ok /
    formulas_src 那次）是同一个形状：**测试和实现一起错，于是一起绿**。

    所以这条测试的规矩是：
      · 报告由 `convert.pdf_to_word` **真的吐出来**，字段名一个都不手写
      · `note_run` 真的写文件，`runs()` 真的读回来
      · 中间任何一环少写一个字段，这条就红
    """

    def setUp(self):
        import convert
        self.convert = convert
        self._probe, self._run = convert.probe.probe_pdf, convert.extract.run
        self._todocx = convert.todocx.md_to_docx
        self._runs, self._last = srv.maint.RUNS, srv.maint.LAST_RUN
        self.w = tempfile.mkdtemp(prefix='p2w_chain_')
        srv.maint.RUNS = os.path.join(self.w, 'runs.json')
        srv.maint.LAST_RUN = os.path.join(self.w, 'last_run.json')

    def tearDown(self):
        self.convert.probe.probe_pdf = self._probe
        self.convert.extract.run = self._run
        self.convert.todocx.md_to_docx = self._todocx
        srv.maint.RUNS, srv.maint.LAST_RUN = self._runs, self._last
        shutil.rmtree(self.w, ignore_errors=True)

    def _real_rep(self, pages, elements, t1, t2):
        """让 convert 真的跑一遍编排层，拿它自己吐的报告 —— 字段名不手写。"""
        c = self.convert
        c.probe.probe_pdf = lambda p: {'ok': True, 'pages': pages,
                                       'scan_pages': [], 'error': ''}
        box = {}

        def fake_run(pdf, work_dir, **kw):
            cb = kw.get('on_progress')
            cb('识别中', 1, pages)                 # 第一轮：分母 == 页数
            box['t'] = time.time()
            cb('识别中', 1, elements)              # 第二轮：分母是元素数
            return {'ok': False, 'error': '到此为止（测试用）'}

        c.extract.run = fake_run
        rep = c.pdf_to_word('x.pdf', 'x.docx', 'w', on_progress=lambda *a: None)
        # 计时靠真实时钟，测试里就是 0；直接按已知耗时覆盖，字段名仍来自 rep
        rep['pass1_sec'], rep['pass2_sec'] = t1, t2
        return rep

    def test_convert算出来的速度数据要能一路走到倒计时(self):
        for i in range(3):
            rep = self._real_rep(pages=10, elements=200, t1=284, t2=328)
            srv.maint.note_run(rep, pdf_name='第%d份.pdf' % i, took_sec=612)

        row = srv.maint.runs()[0]
        for k in ('pass1_sec', 'pass2_sec', 'elements'):
            self.assertIn(k, row, '%s 没被写进历史 —— 倒计时学不到东西' % k)

        p1, p2, ep = srv._learned_rates()
        self.assertAlmostEqual(p1, 28.4, places=1, msg='每页秒没学到真实值')
        self.assertAlmostEqual(p2, 1.64, places=2, msg='每元素秒没学到真实值')
        self.assertAlmostEqual(ep, 20.0, places=1, msg='每页元素数没学到真实值')

    def test_学到的速度真的会改变估值(self):
        r"""光「学到了」不够 —— 还得真的influence到用户看见的那个数。"""
        base, _w = srv._estimate([10])
        for i in range(3):
            rep = self._real_rep(pages=10, elements=200, t1=1420, t2=1640)
            srv.maint.note_run(rep, pdf_name='慢%d.pdf' % i, took_sec=3060)
        after, _w2 = srv._estimate([10])
        self.assertGreater(after, base * 2,
                           '学了一台慢五倍的机器，估值却没变：%d → %d' % (base, after))

    def test_缓存命中的那几份不会污染历史统计(self):
        r"""秒回的份 pass1_sec/pass2_sec/elements 全是 0（convert 里
        `if _sw['pass2_at']` 挡着）。它们进了历史也不能被学进去。"""
        for i in range(3):
            rep = self._real_rep(pages=10, elements=200, t1=284, t2=328)
            srv.maint.note_run(rep, pdf_name='真跑%d.pdf' % i, took_sec=612)
        cached = self._real_rep(pages=23, elements=0, t1=0, t2=0)
        cached['elements'] = 0
        for i in range(5):
            srv.maint.note_run(cached, pdf_name='秒回%d.pdf' % i, took_sec=2)
        p1, _p2, _ep = srv._learned_rates()
        self.assertAlmostEqual(p1, 28.4, places=1,
                               msg='被缓存命中的记录带歪了：%.2f' % p1)



class Test转换历史有出口(unittest.TestCase):
    r"""🔴 历史记下来了却没人看得见，等于没做。

    2026-09-07 外部复查指出：`maint.runs()` 当时只被「学速度」那处读，
    前端没有任何界面、也没有接口 —— 小蔡要的四件事（找回转好的文件、
    确认转没转过、失败重转、翻当时的报错）一件都办不到。
    """

    def setUp(self):
        self._runs = srv.maint.RUNS
        self.w = tempfile.mkdtemp(prefix='p2w_api_')
        srv.maint.RUNS = os.path.join(self.w, 'runs.json')

    def tearDown(self):
        srv.maint.RUNS = self._runs
        shutil.rmtree(self.w, ignore_errors=True)

    def test_接口能把历史给出去(self):
        io.open(srv.maint.RUNS, 'w', encoding='utf-8').write(
            '[{"file": "\u8bb2\u4e49.pdf", "ok": true, "pdf": "D:/x.pdf"}]')
        d = client.get('/api/runs').json()
        self.assertTrue(d['ok'])
        self.assertEqual(d['rows'][0]['file'], '讲义.pdf')

    def test_没有历史时给空列表不是报错(self):
        d = client.get('/api/runs').json()
        self.assertTrue(d['ok'])
        self.assertEqual(d['rows'], [])

    def test_历史文件坏了也不能让这一屏废掉(self):
        io.open(srv.maint.RUNS, 'w', encoding='utf-8').write('{不是合法 JSON')
        r = client.get('/api/runs')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['rows'], [])

    def test_能限制条数(self):
        rows = ','.join('{"file": "%d.pdf"}' % i for i in range(30))
        io.open(srv.maint.RUNS, 'w', encoding='utf-8').write('[' + rows + ']')
        self.assertEqual(len(client.get('/api/runs?limit=5').json()['rows']), 5)



if __name__ == '__main__':
    unittest.main()
