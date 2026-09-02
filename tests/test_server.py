# -*- coding: utf-8 -*-
r"""本地 HTTP 服务的接口。

用 FastAPI 的 TestClient，不真起端口 —— 起端口的测试会在 CI 上抢占资源、
在本机上跟正在运行的软件撞车。
"""
import io
import os
import shutil
import sys
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
        self.assertEqual(seen.get('size'), 123, 'size 也该传（长度校验要用）')

    def test_后端不看前端传的url(self):
        r"""服务只绑 127.0.0.1，但本机任意进程都能 POST 一个自己的地址，
        让它下载并解压覆盖安装目录里会被执行的 .py。"""
        import inspect
        import server.main as sm
        src = inspect.getsource(sm._upd_work)
        self.assertIn('update.check()', src,
                      '后端没有自己去查 Release')
        self.assertNotIn('req.url', src, '还在用前端传的地址')


if __name__ == '__main__':
    unittest.main()
