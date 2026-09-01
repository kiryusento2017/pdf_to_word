# -*- coding: utf-8 -*-
r"""检查更新。

要盯住的两条，错了都是用户直接受害：
  1. **挑对 asset** —— 挑成完整包的话，老师为 0.4 MB 的改动重下 0.69 GB
  2. **版本比较要看方向** —— tag 不同不等于有更新，也可能本地比远端新
     （小蔡手动发给老师的版本会踩这个，装上就一直提示更新）
"""
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import update  # noqa: E402


def _rel(tag='v1.1.0', published='2026-09-05T10:00:00Z', assets=None, body=''):
    return {'tag_name': tag, 'published_at': published, 'body': body,
            'assets': assets if assets is not None else [
                {'name': 'pdf_to_word-%s-update.zip' % tag,
                 'browser_download_url': 'https://x/u.zip', 'size': 400000}]}


class Test挑对更新包(unittest.TestCase):

    def test_有update包时必须挑它而不是完整包(self):
        r"""挑错的代价：老师为 0.4 MB 的改动重下 0.69 GB。"""
        rel = _rel(assets=[
            {'name': 'pdf_to_word-v1.1.0-full.zip',
             'browser_download_url': 'https://x/full.zip', 'size': 740000000},
            {'name': 'pdf_to_word-v1.1.0-update.zip',
             'browser_download_url': 'https://x/up.zip', 'size': 400000},
        ])
        got = update._pick_asset(rel)
        self.assertIn('update', got['name'])
        self.assertEqual(got['size'], 400000)

    def test_顺序颠倒也要挑对(self):
        rel = _rel(assets=[
            {'name': 'a-update.zip', 'browser_download_url': 'u', 'size': 1},
            {'name': 'b-full.zip', 'browser_download_url': 'f', 'size': 999},
        ])
        self.assertIn('update', update._pick_asset(rel)['name'])

    def test_只有一个包时就用它(self):
        rel = _rel(assets=[{'name': 'anything.zip',
                            'browser_download_url': 'x', 'size': 5}])
        self.assertEqual(update._pick_asset(rel)['name'], 'anything.zip')

    def test_没有zip时返回None(self):
        rel = _rel(assets=[{'name': 'notes.txt',
                            'browser_download_url': 'x', 'size': 5}])
        self.assertIsNone(update._pick_asset(rel))

    def test_一个附件都没有(self):
        self.assertIsNone(update._pick_asset(_rel(assets=[])))


class Test版本比较看方向(unittest.TestCase):
    r"""tag 不同**不等于**有更新。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_vf = update.VERSION_FILE
        self._orig_api = update._api
        update.VERSION_FILE = os.path.join(self.tmp, 'version.json')

    def tearDown(self):
        update.VERSION_FILE = self._orig_vf
        update._api = self._orig_api
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _local(self, tag, published):
        io.open(update.VERSION_FILE, 'w', encoding='utf-8').write(
            json.dumps({'tag': tag, 'published_at': published}))

    def _remote(self, rel):
        update._api = lambda url: rel

    def test_远端更新时报有更新(self):
        self._local('v1.0.0', '2026-09-01T00:00:00Z')
        self._remote(_rel('v1.1.0', '2026-09-05T00:00:00Z'))
        r = update.check()
        self.assertTrue(r['has_update'])
        self.assertEqual(r['latest'], 'v1.1.0')
        self.assertTrue(r['asset'])

    def test_一样的版本不报更新(self):
        self._local('v1.1.0', '2026-09-05T00:00:00Z')
        self._remote(_rel('v1.1.0', '2026-09-05T00:00:00Z'))
        self.assertFalse(update.check()['has_update'])

    def test_本地比远端新时不报更新(self):
        r"""🔴 小蔡手动发给老师的测试版会踩这个：tag 不同，但本地更新。
        只比「相不相等」的话，那台机器会永远提示有更新。"""
        self._local('v1.2.0', '2026-09-10T00:00:00Z')
        self._remote(_rel('v1.1.0', '2026-09-05T00:00:00Z'))
        r = update.check()
        self.assertFalse(r['has_update'], '本地更新却报了「有新版本」')
        self.assertIn('比仓库里的还新', r['error'])

    def test_有更新但没附更新包时说清楚(self):
        self._local('v1.0.0', '2026-09-01T00:00:00Z')
        self._remote(_rel('v1.1.0', '2026-09-05T00:00:00Z', assets=[]))
        r = update.check()
        self.assertFalse(r['has_update'], '没包却让用户去下')
        self.assertIn('没有附更新包', r['error'])

    def test_不知道本地版本时不瞎猜(self):
        self._remote(_rel())
        r = update.check()
        self.assertFalse(r['has_update'])
        self.assertIn('不知道当前是哪个版本', r['error'])

    def test_仓库没发过版本时说人话(self):
        self._local('v1.0.0', '2026-09-01T00:00:00Z')

        def boom(url):
            raise Exception('HTTP Error 404: Not Found')
        update._api = boom
        r = update.check()
        self.assertTrue(r['ok'])
        self.assertFalse(r['has_update'])
        self.assertIn('还没有发布任何版本', r['error'])

    def test_连不上时报原因而不是崩(self):
        self._local('v1.0.0', '2026-09-01T00:00:00Z')

        def boom(url):
            raise Exception('timed out')
        update._api = boom
        r = update.check()
        self.assertFalse(r['ok'])
        self.assertIn('连不上 GitHub', r['error'])


class Test安装更新包(unittest.TestCase):
    r"""解压覆盖。小蔡定的体验：点更新 → 自动下载 → 自动装好 → 重启。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_root = update.paths.ROOT
        update.paths.ROOT = self.tmp

    def tearDown(self):
        update.paths.ROOT = self._orig_root
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _zip(self, entries):
        import zipfile
        p = os.path.join(self.tmp, '_pkg.zip')
        with zipfile.ZipFile(p, 'w') as z:
            for name, data in entries:
                z.writestr(name, data)
        return p

    def test_正常覆盖(self):
        old = os.path.join(self.tmp, 'pipeline')
        os.makedirs(old)
        io.open(os.path.join(old, 'a.py'), 'w', encoding='utf-8').write('旧内容')
        z = self._zip([('pipeline/a.py', '新内容'),
                       ('server/b.py', 'x'),
                       ('version.json', '{"tag":"v9"}')])
        ok, err, n = update.apply_update(z)
        self.assertTrue(ok, err)
        self.assertEqual(n, 3)
        self.assertEqual(io.open(os.path.join(old, 'a.py'), encoding='utf-8').read(), '新内容')
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, 'server', 'b.py')))

    def test_挡住路径穿越(self):
        r"""🔴 zip slip：包里的 `../../xxx` 会写到安装目录外面去。

        更新包是从网上下的，名字由打包方给定 —— 只要那一环被人动过手脚，
        或者哪天打包脚本手滑，就能往用户的系统目录里写文件。
        必须逐个成员算出最终落点，确认在安装目录内才写。
        """
        z = self._zip([('../../evil.txt', '坏东西'),
                       ('pipeline/ok.py', '好的')])
        ok, err, n = update.apply_update(z)
        self.assertFalse(ok, '路径穿越没被挡住')
        self.assertIn('安装目录外面', err)
        # 一个都不该写 —— 先全解到临时目录再搬，中途发现问题就整批不动
        self.assertFalse(os.path.isfile(os.path.join(self.tmp, 'pipeline', 'ok.py')),
                         '发现坏成员后还是写了别的文件')

    def test_绝对路径也要挡(self):
        z = self._zip([('C:/Windows/evil.txt', '坏东西')])
        ok, err, _n = update.apply_update(z)
        self.assertFalse(ok)

    def test_空包不算成功(self):
        z = self._zip([])
        ok, err, _n = update.apply_update(z)
        self.assertFalse(ok)
        self.assertIn('空', err)

    def test_坏zip报人话(self):
        p = os.path.join(self.tmp, 'bad.zip')
        io.open(p, 'wb').write('这不是 zip'.encode('utf-8'))
        ok, err, _n = update.apply_update(p)
        self.assertFalse(ok)
        self.assertIn('没下完整', err)

    def test_文件不存在(self):
        ok, err, _n = update.apply_update(os.path.join(self.tmp, '没有.zip'))
        self.assertFalse(ok)
        self.assertIn('不见了', err)


class Test镜像(unittest.TestCase):

    def test_候选里必须留一条直连(self):
        r"""某些网络下反而是直连通 —— 全指望镜像的话那些人一个源都没有。"""
        self.assertTrue(any(m['prefix'] == '' for m in update.GH_MIRRORS))

    def test_镜像不止一个(self):
        r"""实测六个候选当场坏三个，只留一个等于把命押在它不挂上。"""
        self.assertGreaterEqual(len(update.GH_MIRRORS), 3)

    def test_下载空url时不发请求(self):
        ok, err, via = update.download('', os.path.join(self.__class__.__name__))
        self.assertFalse(ok)
        self.assertIn('没有可下载', err)


if __name__ == '__main__':
    unittest.main()
