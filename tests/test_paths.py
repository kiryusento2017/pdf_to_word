# -*- coding: utf-8 -*-
r"""落点集中管理。

小蔡定的规矩（2026-09-01）：运行中产生的一切文件都留在安装文件夹内，
只有导出的 Word 例外。这些测试盯住的就是这条 —— 任何一个落点跑到
用户目录去，都算破坏了「删文件夹即卸载干净」的承诺。
"""
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import paths  # noqa: E402


class Test所有落点都在安装目录内(unittest.TestCase):
    r"""这是这个文件存在的唯一理由。"""

    def test_每一个落点都在ROOT底下(self):
        for name in ('MODELS', 'TMP', 'TMP_EXTRACT', 'CONFIG',
                     'APPDATA', 'LOGS', 'RUNTIME', 'PANDOC'):
            p = getattr(paths, name)
            self.assertTrue(
                os.path.abspath(p).startswith(os.path.abspath(paths.ROOT)),
                '%s 跑到安装目录外面去了：%s' % (name, p))

    def test_没有任何落点在用户主目录(self):
        r"""模型 4.6 GB 落在 C:\Users 下是最容易犯也最难发现的错。"""
        home = os.path.abspath(os.path.expanduser('~'))
        for name in ('MODELS', 'TMP', 'CONFIG', 'APPDATA', 'LOGS'):
            p = os.path.abspath(getattr(paths, name))
            self.assertFalse(p.startswith(home + os.sep),
                             '%s 落在了用户主目录：%s' % (name, p))

    def test_ROOT就是安装目录(self):
        """paths.py 在 pipeline/ 下，上一层才是安装目录。"""
        self.assertTrue(os.path.isdir(os.path.join(paths.ROOT, 'pipeline')))
        self.assertTrue(os.path.isfile(os.path.join(paths.ROOT, 'README.md')))


class Test给子进程的环境变量(unittest.TestCase):
    r"""「全留在安装目录」全靠这三个变量，少一个就漏一处。"""

    def test_三个变量都指进安装目录(self):
        env = paths.child_env()
        self.assertEqual(env['MODELSCOPE_CACHE'], paths.MODELS)
        self.assertEqual(env['HF_HOME'], paths.MODELS)
        self.assertEqual(env['MINERU_TOOLS_CONFIG_JSON'], paths.CONFIG)

    def test_绝不指向用户的全局配置(self):
        r"""~/mineru.json 是 MinerU 的全局配置，用户机器上可能装着别的
        用 MinerU 的东西。改它等于动别人的配置。"""
        env = paths.child_env()
        globalcfg = os.path.abspath(os.path.join(os.path.expanduser('~'),
                                                 'mineru.json'))
        self.assertNotEqual(os.path.abspath(env['MINERU_TOOLS_CONFIG_JSON']),
                            globalcfg)

    def test_选中的源能合并进来(self):
        env = paths.child_env({'MINERU_MODEL_SOURCE': 'modelscope'})
        self.assertEqual(env['MINERU_MODEL_SOURCE'], 'modelscope')
        self.assertEqual(env['MODELSCOPE_CACHE'], paths.MODELS)  # 没被覆盖掉

    def test_保留原有环境变量(self):
        """PATH 之类不能丢，丢了子进程连 exe 都找不到。"""
        env = paths.child_env()
        self.assertIn('PATH', {k.upper(): v for k, v in env.items()})


class Test找可执行文件(unittest.TestCase):
    r"""发行版和开发环境用同一套查找顺序。

    这个函数存在的理由：_find_mineru / download_exe / tomath._NODE
    三处曾经各写各的路径，全都写死在 .venv\Scripts\ —— 而发行版里
    没有 .venv。散着写的话发行版要改三处，改漏一处就是「在我这儿好好的」。
    """

    def setUp(self):
        import shutil
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self._orig_root, self._orig_rt = paths.ROOT, paths.RUNTIME
        paths.ROOT = self.tmp
        paths.RUNTIME = os.path.join(self.tmp, 'runtime')
        os.makedirs(paths.RUNTIME)
        self._rm = shutil.rmtree

    def tearDown(self):
        paths.ROOT, paths.RUNTIME = self._orig_root, self._orig_rt
        self._rm(self.tmp, ignore_errors=True)

    def _touch(self, *parts):
        p = os.path.join(*parts)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with io.open(p, 'w') as f:
            f.write('x')
        return p

    def test_runtime_优先于_venv(self):
        r"""发行版打包进 runtime/ 的那份，必须盖过开发环境的 .venv。"""
        want = self._touch(paths.RUNTIME, 'node.exe')
        self._touch(paths.ROOT, '.venv', 'Scripts', 'node.exe')
        self.assertEqual(paths.find_exe('node'), want)

    def test_找得到发行版里pip装出来的exe(self):
        r"""🔴 发行版实测才发现的坑。

        mineru.exe 是 pip 装 mineru 时生成的入口点，落在 Python 的
        Scripts/ 下 —— 发行版的 Python 在 runtime/python/，所以在
        runtime/python/Scripts/mineru.exe。第一版 find_exe 没有这个候选，
        发行版跑起来报「转换引擎缺失」，而开发环境有 .venv/Scripts 兜着，
        这个漏洞永远暴露不出来。
        """
        want = self._touch(paths.RUNTIME, 'python', 'Scripts', 'mineru.exe')
        self.assertEqual(paths.find_exe('mineru'), want)

    def test_没有runtime时回落到venv(self):
        want = self._touch(paths.ROOT, '.venv', 'Scripts', 'mineru.exe')
        self.assertEqual(paths.find_exe('mineru'), want)

    def test_支持runtime下的子目录(self):
        want = self._touch(paths.RUNTIME, 'node', 'node.exe')
        self.assertEqual(paths.find_exe('node', subdirs=('node',)), want)

    def test_都没有时回落到系统PATH(self):
        r"""最后的退路。开发机上 node 装在系统里，找不到就该用它。"""
        got = paths.find_exe('cmd')          # Windows 一定有
        self.assertTrue(got, '连系统 PATH 都不找了')

    def test_彻底找不到返回空串而不是抛异常(self):
        self.assertEqual(paths.find_exe('绝对不存在的东西xyz'), '')


class Test可写检查(unittest.TestCase):

    def test_当前目录可写(self):
        r"""装进 Program Files 时这里会是 False，启动自检要拦下来 ——
        不能等用户拖完 PDF 点了开始转换，才报一个看不懂的权限错误。
        """
        self.assertTrue(paths.writable())


class Test模型就绪判断(unittest.TestCase):

    def test_没有models目录时判没有(self):
        if not os.path.isdir(paths.MODELS):
            self.assertFalse(paths.models_ready())

    def test_空目录不算就绪(self):
        r"""下载中断会留下空壳目录。只看「目录存在」会把它判成就绪，
        用户就卡在一个永远缺文件的转换里。"""
        import tempfile
        import shutil
        tmp = tempfile.mkdtemp()
        old = paths.MODELS
        try:
            paths.MODELS = tmp
            self.assertFalse(paths.models_ready(), '空目录被判成了就绪')
            # 放个小文件（模拟残留的配置/锁文件），仍然不算就绪
            with open(os.path.join(tmp, 'note.txt'), 'w') as f:
                f.write('x')
            self.assertFalse(paths.models_ready(), '小文件被当成了模型')
        finally:
            paths.MODELS = old
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
