# -*- coding: utf-8 -*-
r"""多源测速与下载。

设计出处：工作台 BACKLOG B35。这里守的是它的验收标准，
不是我自己想的规矩：

  · 测速阶段 <= 5 秒出结果
  · 界面显示「预计几分钟」而不是 MB/s（老师看得懂前者）
  · 电脑盲直接点开始即可，不必做选择题（最快的默认选中）
  · 某源失效自动跳过，不影响整体
  · 不存历史成绩、不用 ping 判优

**不联网**：真去测速的测试会看网络脸色，在没网的机器上红一片。
这里把 urlopen 换掉，测的是逻辑。
"""
import io
import os
import shutil
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import sources  # noqa: E402

WORK = os.path.join(ROOT, '_tmp', 'tests', 'sources')


class _FakeResp(object):
    """假的 HTTP 响应：按给定速度吐字节。"""

    def __init__(self, bps=1024 * 1024, status=200, total=None, fail=False):
        self.bps, self.status, self.fail = bps, status, fail
        self.headers = {'Content-Length': str(total)} if total else {}
        self._left = total

    def read(self, n):
        if self.fail:
            raise IOError('假装断线')
        if self._left is not None:
            if self._left <= 0:
                return b''
            n = min(n, self._left)
            self._left -= n
        else:
            time.sleep(n / float(self.bps))     # 模拟带宽
        return b'x' * n

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Test测速(unittest.TestCase):

    def test_并发测所有源不串行(self):
        r"""B35 验收：测速阶段 <= 5 秒出结果。串行测三个源要 7 秒以上，
        人会以为卡住了。"""
        def slow(*a, **kw):
            return _FakeResp(bps=4 * 1024 * 1024)
        t0 = time.time()
        with mock.patch('sources.urllib.request.urlopen', side_effect=slow):
            rows = sources.probe_all(seconds=1.0)
        dt = time.time() - t0
        self.assertEqual(len(rows), len(sources.MODEL_SOURCES))
        self.assertLess(dt, 3.0, '三个源花了 %.1f 秒，说明是串行测的' % dt)

    def test_快的排前面(self):
        speeds = {'modelscope': 8 << 20, 'hf-mirror': 2 << 20, 'huggingface': 1 << 20}

        def by_url(req, **kw):
            url = req.full_url if hasattr(req, 'full_url') else str(req)
            for s in sources.MODEL_SOURCES:
                if s['probe'] == url:
                    return _FakeResp(bps=speeds[s['id']])
            return _FakeResp(bps=1024)
        with mock.patch('sources.urllib.request.urlopen', side_effect=by_url):
            rows = sources.probe_all(seconds=0.35)
        self.assertEqual(rows[0]['id'], 'modelscope')
        self.assertGreater(rows[0]['bps'], rows[-1]['bps'])

    def test_某个源连不上不影响其余(self):
        r"""B35 验收：某源失效（404/超时）自动跳过，不影响整体完成。"""
        def some_fail(req, **kw):
            url = req.full_url if hasattr(req, 'full_url') else str(req)
            if 'hf-mirror' in url:
                raise IOError('连不上')
            return _FakeResp(bps=4 << 20)
        with mock.patch('sources.urllib.request.urlopen', side_effect=some_fail):
            rows = sources.probe_all(seconds=0.3)
        self.assertEqual(len(rows), 3, '有源失败就少返回了')
        dead = [r for r in rows if r['bps'] == 0]
        self.assertEqual(len(dead), 1)
        self.assertTrue(dead[0]['error'], '失败了却没留原因')

    def test_连不上的沉底(self):
        def some_fail(req, **kw):
            url = req.full_url if hasattr(req, 'full_url') else str(req)
            if 'modelscope' in url:
                raise IOError('连不上')
            return _FakeResp(bps=4 << 20)
        with mock.patch('sources.urllib.request.urlopen', side_effect=some_fail):
            rows = sources.probe_all(seconds=0.3)
        self.assertEqual(rows[-1]['id'], 'modelscope')

    def test_最快的默认选中(self):
        r"""B35 验收：电脑盲直接点「开始下载」即可，不必做选择题。"""
        rows = [{'id': 'a', 'bps': 0}, {'id': 'b', 'bps': 5 << 20}, {'id': 'c', 'bps': 1 << 20}]
        self.assertEqual(sources.pick_best(rows)['id'], 'b')

    def test_全都连不上时说不出话来而不是瞎选(self):
        self.assertIsNone(sources.pick_best([{'id': 'a', 'bps': 0}]))


class Test预计耗时说人话(unittest.TestCase):
    r"""B35 验收：界面展示「预计几分钟」而不是 MB/s —— 老师看得懂前者。"""

    def test_不出现MB每秒这种说法(self):
        w = sources.eta_words(4600 << 20, 5 << 20)
        for bad in ('MB/s', 'bps', 'KB', 'B/s'):
            self.assertNotIn(bad, w)

    def test_几分钟(self):
        # 4.6 GB / 10 MB每秒 约等于 8 分钟
        self.assertIn('分钟', sources.eta_words(4600 << 20, 10 << 20))

    def test_很快时不说0分钟(self):
        w = sources.eta_words(10 << 20, 50 << 20)
        self.assertNotIn('0 分钟', w)
        self.assertIn('2 分钟', w)

    def test_很慢时给小时(self):
        w = sources.eta_words(4600 << 20, 300 << 10)
        self.assertIn('小时', w)

    def test_连不上时直说(self):
        self.assertEqual(sources.eta_words(100, 0), '连不上')


class Test断点续传(unittest.TestCase):

    def setUp(self):
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)
        self.dest = os.path.join(WORK, 'model.bin')

    def tearDown(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def test_正常下完(self):
        with mock.patch('sources.urllib.request.urlopen',
                        side_effect=lambda *a, **kw: _FakeResp(total=5000)):
            r = sources.download('http://x/model.bin', self.dest)
        self.assertTrue(r['ok'], r['error'])
        self.assertEqual(os.path.getsize(self.dest), 5000)

    def test_有半截时带Range续传(self):
        r"""模型 4.6 GB，家用网络断一次就得从头来的话，人会直接卸载。"""
        with io.open(self.dest + '.part', 'wb') as f:
            f.write(b'x' * 2000)
        seen = {}

        def cap(req, **kw):
            seen['range'] = req.headers.get('Range')
            return _FakeResp(total=3000, status=206)
        with mock.patch('sources.urllib.request.urlopen', side_effect=cap):
            r = sources.download('http://x/model.bin', self.dest)
        self.assertTrue(r['ok'], r['error'])
        self.assertEqual(seen['range'], 'bytes=2000-', '没带 Range 头，等于没续传')
        self.assertEqual(os.path.getsize(self.dest), 5000)

    def test_服务器不支持Range就从头写(self):
        r"""服务器忽略 Range 会返回 200 + 完整内容。这时候还往后追加
        就会得到一个前 2000 字节重复的坏文件。"""
        with io.open(self.dest + '.part', 'wb') as f:
            f.write(b'x' * 2000)
        with mock.patch('sources.urllib.request.urlopen',
                        side_effect=lambda *a, **kw: _FakeResp(total=3000, status=200)):
            r = sources.download('http://x/model.bin', self.dest)
        self.assertTrue(r['ok'])
        self.assertEqual(os.path.getsize(self.dest), 3000, '把重复内容追加进去了')

    def test_下载中报进度(self):
        seen = []
        with mock.patch('sources.urllib.request.urlopen',
                        side_effect=lambda *a, **kw: _FakeResp(total=200000)):
            sources.download('http://x/m.bin', self.dest,
                             on_progress=lambda a, b: seen.append((a, b)))
        self.assertTrue(seen, '一次进度都没报')
        self.assertEqual(seen[-1][0], 200000)

    def test_失败重试用尽后说人话(self):
        with mock.patch('sources.urllib.request.urlopen',
                        side_effect=IOError('网络断了')):
            r = sources.download('http://x/m.bin', self.dest, retries=2)
        self.assertFalse(r['ok'])
        self.assertIn('网络断了', r['error'])
        self.assertFalse(os.path.isfile(self.dest), '失败了却留下了半成品当成品')

    def test_没下完不会冒充下完了(self):
        r"""下到一半的文件必须留在 .part，绝不能改名成正式文件 ——
        那样下次启动会以为模型齐了，然后在真正用的时候莫名其妙地失败。"""
        with mock.patch('sources.urllib.request.urlopen',
                        side_effect=IOError('断了')):
            sources.download('http://x/m.bin', self.dest, retries=1)
        self.assertFalse(os.path.isfile(self.dest))


class Test源清单(unittest.TestCase):

    def test_至少三个候选(self):
        r"""B35：每类源内置多个候选。只有一个源就是写死，
        而实测证明没有哪个源普遍最优。"""
        self.assertGreaterEqual(len(sources.MODEL_SOURCES), 3)

    def test_国内外都有(self):
        ids = [s['id'] for s in sources.MODEL_SOURCES]
        self.assertIn('modelscope', ids)
        self.assertIn('huggingface', ids)

    def test_每个源都有人话名字和探测地址(self):
        for s in sources.MODEL_SOURCES:
            self.assertTrue(s['name'])
            self.assertTrue(s['probe'].startswith('http'))
            self.assertTrue(s['env'], '没说明这个源怎么让 MinerU 用上')

    def test_探测地址不是首页(self):
        r"""拿首页测速没意义：HTML 小、且常被 CDN 缓存在边缘节点，
        测出来的数跟真实下载速度无关。"""
        for s in sources.MODEL_SOURCES:
            path = s['probe'].split('://', 1)[-1]
            self.assertIn('/', path, '%s 的探测地址是域名首页' % s['id'])
            self.assertGreater(len(path.split('/', 1)[1]), 3,
                               '%s 的探测地址太浅，多半是首页' % s['id'])


if __name__ == '__main__':
    unittest.main()
