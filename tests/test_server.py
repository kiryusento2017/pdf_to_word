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

    def test_预计剩余时间随页数走(self):
        r"""每页秒数是实测的（GPU 26 秒/页、CPU 46 秒/页），
        页数在体检时就知道 —— 这两个数一乘就是用户唯一关心的答案。"""
        t = {'pages': [10, 20], 'results': [], 'sec_per_page': 26.0}
        r = srv._remain(t, elapsed=0)
        self.assertEqual(r, int(30 * 26))          # 30 页 x 26 秒

    def test_跑完的用真实速率反推(self):
        r"""转到第三份时，前两份的真实速度比出厂估值准得多。"""
        t = {'pages': [10, 10], 'results': [{'ok': True}], 'sec_per_page': 26.0}
        # 第一份 10 页真实花了 500 秒（这台机器慢），剩下 10 页也该按 50 秒/页估
        r = srv._remain(t, elapsed=500)
        self.assertEqual(r, 500)

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


if __name__ == '__main__':
    unittest.main()
