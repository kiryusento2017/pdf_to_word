# -*- coding: utf-8 -*-
r"""依赖状态检查。

**核心规矩：查不到就说查不到，绝不显示「已是最新」。**
这两个意思差很远，混了就是假绿灯 —— 用户以为自己是最新的，实际是
网络不通。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import deps  # noqa: E402


class Test查不到不能装作最新(unittest.TestCase):
    r"""这一组是这个模块最要紧的性质。"""

    def test_pip查不到时latest是空串(self):
        old = deps._pip_index_versions
        deps._pip_index_versions = lambda *a, **k: ('', [], '超时')
        try:
            r = deps.check_mineru()
        finally:
            deps._pip_index_versions = old
        self.assertEqual(r['latest'], '', '查不到时不能填任何版本号')
        self.assertEqual(r['error'], '超时')

    def test_torch查不到时也是空串(self):
        old = deps._pip_index_versions
        deps._pip_index_versions = lambda *a, **k: ('', [], '连不上')
        try:
            r = deps.check_torch()
        finally:
            deps._pip_index_versions = old
        self.assertEqual(r['latest'], '')
        self.assertTrue(r['error'])

    def test_模型查不到时upstream_time是空串(self):
        import urllib.request
        old = urllib.request.urlopen

        def boom(*a, **k):
            raise OSError('网络不通')

        urllib.request.urlopen = boom
        try:
            r = deps.check_models()
        finally:
            urllib.request.urlopen = old
        self.assertEqual(r['upstream_time'], '')
        self.assertTrue(r['error'], '查不到要有错误信息，不能静默')


class Test排除预览版(unittest.TestCase):
    r"""PyPI 上 mineru 正式版是 3.4.5，另有 4.0.0a6。alpha 不能当成
    「有新版本」推给用户 —— 那是内测版，装上去可能整个转换链都崩。"""

    def _fake(self, latest, avail):
        old = deps._pip_index_versions
        deps._pip_index_versions = lambda *a, **k: (latest, avail, '')
        return old

    def test_alpha不算新版本(self):
        old = self._fake('4.0.0a6', ['4.0.0a6', '3.4.5', '3.4.4'])
        try:
            r = deps.check_mineru()
        finally:
            deps._pip_index_versions = old
        self.assertEqual(r['latest'], '3.4.5', '应该退回最新的正式版')

    def test_beta和rc也不算(self):
        for pre in ('4.0.0b1', '4.0.0rc2'):
            old = self._fake(pre, [pre, '3.4.5'])
            try:
                r = deps.check_mineru()
            finally:
                deps._pip_index_versions = old
            self.assertEqual(r['latest'], '3.4.5', '%s 不该当成新版本' % pre)

    def test_全是预览版时latest为空(self):
        r"""一个正式版都没有 —— 那就是查不到，不能拿 alpha 顶替。"""
        old = self._fake('4.0.0a6', ['4.0.0a6', '4.0.0a5'])
        try:
            r = deps.check_mineru()
        finally:
            deps._pip_index_versions = old
        self.assertEqual(r['latest'], '')

    def test_正常版本号不受影响(self):
        old = self._fake('3.6.0', ['3.6.0', '3.5.0', '3.4.5'])
        try:
            r = deps.check_mineru()
        finally:
            deps._pip_index_versions = old
        self.assertEqual(r['latest'], '3.6.0')


class Test用哪个源查(unittest.TestCase):
    r"""🔴 用哪个源下载，就用哪个源查版本。

    否则会造出一种很难查的故障：官方说有 2.14.0，用户点了升级，
    而他实际用的那个镜像上根本没有这个版本。"""

    def test_torch按驱动挑通道(self):
        got = {}
        old = deps._pip_index_versions

        def spy(pkg, index_url=None, find_links=None):
            got['url'] = index_url
            return '2.11.0+cu128', [], ''

        deps._pip_index_versions = spy
        try:
            r = deps.check_torch()
        finally:
            deps._pip_index_versions = old
        self.assertIn('download.pytorch.org', got['url'])
        self.assertIn(r['channel'], got['url'],
                      '查的通道要跟 pick_channel 挑的那个一致')

    def test_通道信息要写进结果给界面显示(self):
        old = deps._pip_index_versions
        deps._pip_index_versions = lambda *a, **k: ('2.11.0+cu128', [], '')
        try:
            r = deps.check_torch()
        finally:
            deps._pip_index_versions = old
        self.assertTrue(r['channel'].startswith('cu'))
        self.assertIn('cu', r['source'])


class Test本地版本(unittest.TestCase):

    def test_读得出本地装的版本(self):
        v = deps.local_versions()
        self.assertIn('torch', v)
        self.assertIn('mineru', v)
        # 开发环境里这两个是装了的
        self.assertTrue(v['mineru'], 'mineru 应该读得出版本')

    def test_没装的包返回空串不抛异常(self):
        r"""发行版首启时 torch 还没装 —— 那时候不能炸。"""
        import importlib.metadata as md
        old = md.version

        def boom(name):
            raise md.PackageNotFoundError(name)

        md.version = boom
        try:
            v = deps.local_versions()
        finally:
            md.version = old
        self.assertEqual(v.get('torch'), '')


class Test模型没有版本号只能比时间(unittest.TestCase):
    r"""实测确认：models/ 下面只有 snapshots/master/，目录名是 master
    不是 commit hash，没有 refs、没有 .msc 元数据、没有 version 文件。

    所以只能比时间。"""

    def test_结果里给的是日期不是版本号(self):
        import urllib.request
        old = urllib.request.urlopen

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"Data":{"LastUpdatedTime":1781595122}}'

        urllib.request.urlopen = lambda *a, **k: FakeResp()
        try:
            r = deps.check_models()
        finally:
            urllib.request.urlopen = old
        self.assertEqual(r['upstream_time'], '2026-06-16')
        self.assertNotIn('version', r)

    def test_模型没下时也不炸(self):
        r = deps.check_models()
        self.assertIn('ready', r)
        self.assertIn('size', r)


class Test整体结构(unittest.TestCase):

    def test_check_all的形状(self):
        old = deps._pip_index_versions
        deps._pip_index_versions = lambda *a, **k: ('9.9.9', [], '')
        import urllib.request
        old_u = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(OSError('x'))
        try:
            r = deps.check_all()
        finally:
            deps._pip_index_versions = old
            urllib.request.urlopen = old_u
        self.assertTrue(r['ok'])
        for k in ('torch', 'mineru', 'models'):
            self.assertIn(k, r)
            self.assertIn('local', r[k]) if k != 'models' else None
            self.assertIn('error', r[k])


if __name__ == '__main__':
    unittest.main()
