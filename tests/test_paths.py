# -*- coding: utf-8 -*-
r"""落点集中管理。

小蔡定的规矩（2026-09-01）：运行中产生的一切文件都留在安装文件夹内，
只有导出的 Word 例外。这些测试盯住的就是这条 —— 任何一个落点跑到
用户目录去，都算破坏了「删文件夹即卸载干净」的承诺。
"""
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
