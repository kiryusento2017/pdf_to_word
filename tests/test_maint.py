# -*- coding: utf-8 -*-
r"""占用扫描与清理。

这个模块存在的理由：用户装完之后 C 盘莫名少几个 G，而他永远发现不了
是谁干的 —— pip 缓存藏在隐藏文件夹里、文件名是哈希、扩展名还是 .body。

**看得到，才谈得上删不删。**
"""
import io
import os
import shutil
import sys
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import maint  # noqa: E402
import paths  # noqa: E402

WORK = os.path.join(ROOT, '_tmp', 'tests', 'maint')


def _fake_wheel(path, name):
    """造一个假的缓存文件：内容是 zip，里面有 <name>.dist-info/"""
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('%s.dist-info/METADATA' % name, 'Name: x\n')
        z.writestr('pkg/__init__.py', '# ' + 'x' * 2 * 1024 * 1024)


class Test认出哪个包是我们的(unittest.TestCase):
    r"""缓存里混着别的程序下的包 —— 实测小蔡机器上有 pyside6（他自己
    那个 Qt 项目的）和 torch-2.13.0+cpu（别的项目的）。

    **pip 缓存按 Windows 用户走，不按 Python 环境走**，所以一键清理
    会误伤。每一项都要标清楚是不是本软件的。"""

    def test_我们装的包认得出来(self):
        for n in ('torch-2.11.0+cu128', 'mineru-3.4.5', 'pymupdf-1.28.2',
                  'onnxruntime_gpu-1.28.0', 'python_docx-1.2.0'):
            self.assertTrue(maint._is_ours(n), '%s 该认成我们的' % n)

    def test_别人的包不能认成我们的(self):
        for n in ('pyside6_addons-6.11.2', 'pyside6_essentials-6.11.2',
                  'scipy-1.18.0', 'llvmlite-0.48.0', 'pandas-2.0.0'):
            self.assertFalse(maint._is_ours(n), '%s 不是我们的' % n)

    def test_torch的其他构建也算我们的(self):
        r"""用户可能装过我们的老版本，那也是我们的 —— 只比包名不比版本。
        比版本会误判：cu126 通道装的是同一个 torch，版本号不一样。"""
        for n in ('torch-2.13.0', 'torch-2.7.1+cu118', 'torch-2.14.0+cu126'):
            self.assertTrue(maint._is_ours(n))

    def test_名字读不出来时不算我们的(self):
        self.assertFalse(maint._is_ours(''))
        self.assertFalse(maint._is_ours(None))


class Test从缓存文件里读包名(unittest.TestCase):

    def setUp(self):
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)

    def tearDown(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def test_能从zip里读出dist_info(self):
        p = os.path.join(WORK, 'abc123.body')
        _fake_wheel(p, 'torch-2.11.0+cu128')
        self.assertEqual(maint._wheel_name(p), 'torch-2.11.0+cu128')

    def test_不是zip的文件不炸(self):
        p = os.path.join(WORK, 'junk.body')
        io.open(p, 'w', encoding='utf-8').write('这不是 zip')
        self.assertEqual(maint._wheel_name(p), '')

    def test_文件不存在不炸(self):
        self.assertEqual(maint._wheel_name(os.path.join(WORK, '没有这个')), '')


class Test清理(unittest.TestCase):

    def setUp(self):
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)

    def tearDown(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def test_只删缓存目录里的东西(self):
        r"""🔴 路径是前端传来的，必须验。server 只绑 127.0.0.1，但那
        不等于只有我们能连 —— 本机任意进程都能 POST 一个自己的路径。"""
        outsider = os.path.join(WORK, '不该被删.txt')
        io.open(outsider, 'w', encoding='utf-8').write('重要文件')

        r = maint.clean(keys=(), pip_paths=[outsider])
        self.assertTrue(os.path.isfile(outsider), '缓存目录外的文件被删了！')
        self.assertTrue(r['failed'], '拒绝删除时要报出来，不能静默')
        self.assertIn('拒绝', r['failed'][0])

    def test_删不掉的要老实报不能假装成功(self):
        r"""转换正在跑的时候某个文件可能被占用，Windows 上删不掉。
        那种情况必须说清楚，不能报「全清了」。"""
        r = maint.clean(keys=(), pip_paths=[os.path.join(WORK, '不存在.body')])
        self.assertEqual(r['freed'], 0)
        self.assertTrue(r['failed'])

    def test_什么都不选就什么都不删(self):
        r = maint.clean(keys=(), pip_paths=())
        self.assertEqual(r['freed'], 0)
        self.assertEqual(r['failed'], [])

    def test_问不出pip缓存目录时不能退化成删当前目录(self):
        r"""🔴 **os.path.abspath('') 返回的是当前工作目录。**

        pip 坏了 / 没装好时 pip_cache_dir() 返回空串，白名单就会从
        「pip 缓存目录」悄悄变成「当前工作目录」—— 本机任意进程
        POST 一个安装目录下的路径就能把文件删掉。上面那条
        test_只删缓存目录里的东西 盖不住这个形状：它跑的时候
        pip_cache_dir() 是有值的。（2026-09-05 全量审查发现）
        """
        victim = os.path.join(WORK, '安装目录下的文件.txt')
        io.open(victim, 'w', encoding='utf-8').write('重要文件')

        real = maint.pip_cache_dir
        cwd = os.getcwd()
        maint.pip_cache_dir = lambda: ''       # 模拟 pip 问不出来
        try:
            os.chdir(ROOT)      # victim 落在 cwd 底下，正是触发的形状
            r = maint.clean(keys=(), pip_paths=[victim])
        finally:
            os.chdir(cwd)
            maint.pip_cache_dir = real

        self.assertTrue(os.path.isfile(victim),
                        '问不出 pip 缓存目录时，当前目录下的文件被删了！')
        self.assertTrue(r['failed'], '拒绝删除时要报出来，不能静默')


class Test运行记录(unittest.TestCase):
    r"""诊断报告里最值钱的两条，而 convert.log 给不了 —— 它只记时间和
    路径，**没有结果**。解析一个本来就没记结果的文件那是编数据。"""

    def setUp(self):
        self._run = maint.LAST_RUN
        self._err = maint.LAST_ERROR
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)
        maint.LAST_RUN = os.path.join(WORK, 'last_run.json')
        maint.LAST_ERROR = os.path.join(WORK, 'last_error.json')

    def tearDown(self):
        maint.LAST_RUN = self._run
        maint.LAST_ERROR = self._err
        shutil.rmtree(WORK, ignore_errors=True)

    def test_记一次成功的转换(self):
        # 🔴 **字段名必须跟 convert.pdf_to_word 真实返回的一致。**
        #    这里原来手工编了 formulas_src / formulas_ok，而 convert 的
        #    rep 里叫 formulas / formulas_xsl —— 测试自己造了一套假契约，
        #    于是 400 条全绿，真实运行时 last_run.json 里却永远是 "?/?"
        #    （2026-09-05 全量审查时从落盘文件查出来的）。
        rep = {'ok': True, 'pages': 23, 'formulas': 213,
               'formulas_xsl': 213, 'pdf': r'D:\x\电场.pdf'}
        self.assertTrue(maint.note_run(rep, took_sec=96))
        got = maint.last_run()
        self.assertTrue(got['ok'])
        self.assertEqual(got['pages'], 23)
        self.assertEqual(got['formulas'], '213/213')
        self.assertEqual(got['took_sec'], 96)
        self.assertEqual(got['file'], '电场.pdf')

    def test_记一次失败的转换(self):
        rep = {'ok': False, 'error': '这份 PDF 有密码，请先解密'}
        maint.note_run(rep, pdf_name='加密的.pdf')
        got = maint.last_run()
        self.assertFalse(got['ok'])
        self.assertIn('密码', got['error'])

    def test_记错误(self):
        maint.note_error('torch 加载', 'WinError 1114: c10.dll 加载失败',
                         '缺 C++ 运行库')
        got = maint.last_error()
        self.assertEqual(got['where'], 'torch 加载')
        self.assertIn('1114', got['msg'])
        self.assertEqual(got['hint'], '缺 C++ 运行库')

    def test_没有记录时返回None而不是炸(self):
        self.assertIsNone(maint.last_run())
        self.assertIsNone(maint.last_error())

    def test_写不进去也不能炸(self):
        r"""🔴 记日志这件事**绝不能把转换搞崩**。走到记录那一步，
        用户的 Word 已经转好了。"""
        maint.LAST_RUN = os.path.join(WORK, '没有这个目录', 'x', 'y.json')
        # 造一个写不进去的路径（父目录是个文件）
        blocker = os.path.join(WORK, '挡路的')
        io.open(blocker, 'w', encoding='utf-8').write('x')
        maint.LAST_RUN = os.path.join(blocker, 'last_run.json')
        ok = maint.note_run({'ok': True})     # 不该抛异常
        self.assertFalse(ok, '写失败要返回 False，但不能抛')

    def test_超长的错误信息会被截断(self):
        maint.note_error('x', 'e' * 1000)
        self.assertLessEqual(len(maint.last_error()['msg']), 300)


class Test扫描不炸(unittest.TestCase):
    r"""真跑一次扫描。这几条不断言具体数字（每台机器不一样），
    只保证不抛异常、结构对。"""

    def test_扫日志和临时文件(self):
        r = maint.scan_logs()
        self.assertIn('logs', r)
        self.assertIn('tmp', r)
        self.assertIsInstance(r['logs'], int)

    def test_全量扫描的结构(self):
        r = maint.scan()
        self.assertTrue(r['ok'])
        keys = [it['key'] for it in r['items']]
        for k in ('pip_cache', 'logs', 'tmp', 'models'):
            self.assertIn(k, keys)
        for it in r['items']:
            self.assertIn('label', it)
            self.assertIn('size', it)
            self.assertIn('cleanable', it)

    def test_模型那项不给清(self):
        r"""4.6 GB，清了要重下。不能让用户手滑点掉。"""
        models = [it for it in maint.scan()['items'] if it['key'] == 'models'][0]
        self.assertFalse(models['cleanable'])


if __name__ == '__main__':
    unittest.main()
