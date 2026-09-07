# -*- coding: utf-8 -*-
r"""占用扫描与清理。

这个模块存在的理由：用户装完之后 C 盘莫名少几个 G，而他永远发现不了
是谁干的 —— pip 缓存藏在隐藏文件夹里、文件名是哈希、扩展名还是 .body。

**看得到，才谈得上删不删。**
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import maint  # noqa: E402

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


class TestTEMP里的pip残骸(unittest.TestCase):
    r"""pip 装大 wheel 时会在 `%TEMP%` 下开工作目录，正常跑完自己清掉，
    **中断就永久留下**。2026-09-06 开发机上扫出 45 个目录 1709.6 MB，
    最大的一个 1640.2 MB 是 8-21 下到 62.5% 断掉的 torch wheel。

    🔴 **这一组的重点不是「能不能扫出来」，是「会不会删到临时目录
    外面去」。** `%TEMP%` 跟 pip 缓存不一样 —— 它是本机任何程序都能写
    的公共目录，谁都能往里放一个指向别处的目录联接（junction）。

    2026-09-06 实测过原来那段 rm_tree：临时目录里放一个指向别处的
    junction，跑一遍，**外面的文件真被删了，而且一个错都不报，
    报告「清理成功」**。`os.path.islink()` 对 junction 返回 False，
    `os.walk` 的 followlinks=False 拦不住它。
    """

    def setUp(self):
        self.box = os.path.join(WORK, 'tempbox')
        shutil.rmtree(self.box, ignore_errors=True)
        self.fake = os.path.join(self.box, 'faketmp')
        self.outside = os.path.join(self.box, '临时目录外面')
        os.makedirs(self.fake)
        os.makedirs(self.outside)
        self.victim = os.path.join(self.outside, '不能删.txt')
        io.open(self.victim, 'w', encoding='utf-8').write('重要文件')
        self._real = tempfile.gettempdir
        tempfile.gettempdir = lambda: self.fake

    def tearDown(self):
        tempfile.gettempdir = self._real
        shutil.rmtree(self.box, ignore_errors=True)

    def _mk(self, name, age_h):
        r"""造一个残骸目录。

        🔴 **目录和里面的文件都要调老。** 判年龄看的是「目录里最新的
        东西什么时候动的」，只调目录的话文件还是刚写的，会被判成
        「正在用」。
        """
        d = os.path.join(self.fake, name)
        os.makedirs(d)
        f = os.path.join(d, 'x.whl')
        io.open(f, 'w', encoding='utf-8').write('y' * 1000)
        t = time.time() - age_h * 3600
        os.utime(f, (t, t))
        os.utime(d, (t, t))
        return d

    def _junction(self, link, target, age_h=8):
        r"""造一个目录联接，**并且把它的时间调老**。

        🔴 时间必须调 —— 不调的话它是「刚创建」的，会被年龄门槛顺手
        挡掉，于是链接防护那几条测试**根本没碰到链接防护就绿了**。
        2026-09-06 变异测试抓出来的：把两道链接防护全拆光，这几条
        照样全绿。（跟 CLAUDE.md 第 4 条同一个形状：测试和实现一起
        错，于是一起绿。）
        """
        p = subprocess.run(['cmd', '/c', 'mklink', '/J', link, target],
                           capture_output=True)
        if p.returncode != 0 or not os.path.exists(link):
            self.skipTest('这台机器造不出目录联接')
        t = time.time() - age_h * 3600
        try:
            os.utime(link, (t, t), follow_symlinks=False)
        except (NotImplementedError, OSError):
            os.utime(link, (t, t))
        return link

    def test_只认pip前缀(self):
        self._mk('pip-unpack-abc', 8)
        self._mk('notpip-abc', 8)
        self._mk('tmpXYZ', 8)
        self.assertEqual([r['name'] for r in maint.temp_pip_dirs()],
                         ['pip-unpack-abc'])

    def test_刚动过的不碰(self):
        r"""下 2.7 GB 的 torch 要几十分钟，正在解包的目录必须躲开。"""
        self._mk('pip-unpack-old', 8)
        self._mk('pip-install-fresh', 1)
        names = [r['name'] for r in maint.temp_pip_dirs()]
        self.assertIn('pip-unpack-old', names)
        self.assertNotIn('pip-install-fresh', names)

    def test_目录时间没变但文件在写就不算老残骸(self):
        r"""🔴 往目录里**已有的**文件追加内容，目录的 mtime **一动不动**
        —— 只有新建 / 删除条目才更新它。实测：追加 100 KB 之后目录时间
        没变。

        pip 下一个 2.7 GB 的 wheel 正是这个形状：创建文件那一刻目录时间
        更新，之后几十分钟都在往同一个文件里写。只看目录 mtime 会把
        **正在下载的目录**判成老残骸删掉。

        前面那道 busy 闸只挡得住本软件自己的安装 —— 用户在别的程序里
        跑 pip（比如另一个 Python 项目装包）我们不知道，这条判据是那
        种场景下唯一的防线。
        """
        d = self._mk('pip-unpack-downloading', 8)
        before = os.path.getmtime(d)
        with io.open(os.path.join(d, 'x.whl'), 'a', encoding='utf-8') as h:
            h.write('z' * 100)
        self.assertEqual(os.path.getmtime(d), before,
                         '前提变了：往已有文件写，目录 mtime 居然变了')
        self.assertNotIn('pip-unpack-downloading',
                         [r['name'] for r in maint.temp_pip_dirs()],
                         '目录时间是老的，但里面的文件正在写，不能当老残骸')

    def test_问不出时间的目录不许当成老残骸(self):
        r"""🔴 兜底的方向不能搞反。问不出 mtime 通常是目录正被占用或者
        刚消失，两种都该躲开；而「当成很老」会让代码**主动去删**。

        方向反了的判据比没有判据更危险 —— 没判据是不删，反了是乱删。
        """
        d = self._mk('pip-unpack-nostat', 8)
        real = maint.os.path.getmtime

        def boom(p):
            if os.path.normcase(p) == os.path.normcase(d):
                raise OSError('模拟问不出时间')
            return real(p)

        maint.os.path.getmtime = boom
        try:
            names = [r['name'] for r in maint.temp_pip_dirs()]
        finally:
            maint.os.path.getmtime = real
        self.assertNotIn('pip-unpack-nostat', names,
                         '问不出时间的目录被当成老残骸了')

    def test_目录联接不列出来(self):
        self._junction(os.path.join(self.fake, 'pip-evil'), self.outside)
        self.assertEqual(maint.temp_pip_dirs(), [])

    def test_清理不会顺着目录联接删到外面(self):
        self._junction(os.path.join(self.fake, 'pip-evil'), self.outside)
        maint.clean(keys=['temp_pip'])
        self.assertTrue(os.path.isfile(self.victim),
                        '顺着目录联接把临时目录外面的文件删了！')

    def test_残骸里面藏的联接也不跟进(self):
        r"""外层判过了不等于安全 —— 联接可以藏在残骸目录里面一层。"""
        d = self._mk('pip-install-inner', 8)
        self._junction(os.path.join(d, 'sub'), self.outside)
        t = time.time() - 8 * 3600
        os.utime(d, (t, t))
        maint.clean(keys=['temp_pip'])
        self.assertTrue(os.path.isfile(self.victim),
                        '顺着残骸里面的目录联接删到外面了！')
        self.assertFalse(os.path.exists(d), '残骸本身该被删掉')

    def test_正常残骸能删掉(self):
        d = self._mk('pip-unpack-old', 8)
        r = maint.clean(keys=['temp_pip'])
        self.assertFalse(os.path.exists(d))
        self.assertEqual(r['failed'], [])
        self.assertGreater(r['freed'], 0)

    def test_删不掉的时候要报出是哪个目录(self):
        r"""🔴「删不掉的要老实报」这条规矩已经有了，但粒度得对得上：
        这一支删的是**目录**，而 rm_file 报的是**文件名**。三个残骸
        目录里的文件可能重名，只报文件名等于没报。
        """
        d = self._mk('pip-unpack-locked', 8)
        h = io.open(os.path.join(d, 'x.whl'), 'rb')   # 占住句柄，删不掉
        try:
            r = maint.clean(keys=['temp_pip'])
        finally:
            h.close()
        self.assertTrue(os.path.isdir(d), '前提变了：被占用的文件居然删掉了')
        self.assertTrue(any('pip-unpack-locked' in x for x in r['failed']),
                        '没说清楚是哪个目录没删掉：%r' % (r['failed'],))

    def test_没选这一项就不删(self):
        d = self._mk('pip-unpack-old', 8)
        maint.clean(keys=['logs'])
        self.assertTrue(os.path.exists(d))

    def test_这一项即使是0也要显示(self):
        r"""这一屏存在的意义是「看得见 C 盘被谁占了」。只在有东西时
        才冒出来的行，用户不会知道软件替他看过这个地方。"""
        keys = [it['key'] for it in maint.scan()['items']]
        self.assertIn('temp_pip', keys)



class Test转换历史(unittest.TestCase):
    r"""转完关掉软件，之前转过什么全查不到 —— 因为记录是**覆盖写**的，
    记第二条时把第一条盖掉，永远只剩最后一条。

    🔴 **`last_run.json` 一个字节都不动**（诊断报告读的是它）。历史另起
    一个文件，每条多三样：源 PDF 全路径、产物 Word 全路径、没截断的报错。
    """

    def setUp(self):
        self._run, self._err = maint.LAST_RUN, maint.LAST_ERROR
        self._runs = maint.RUNS
        shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)
        maint.LAST_RUN = os.path.join(WORK, 'last_run.json')
        maint.LAST_ERROR = os.path.join(WORK, 'last_error.json')
        maint.RUNS = os.path.join(WORK, 'runs.json')

    def tearDown(self):
        maint.LAST_RUN, maint.LAST_ERROR = self._run, self._err
        maint.RUNS = self._runs
        shutil.rmtree(WORK, ignore_errors=True)

    def _rep(self, name, ok=True, err=''):
        return {'ok': ok, 'pdf': r'D:\讲义\%s.pdf' % name,
                'docx': r'D:\输出\%s.docx' % name, 'pages': 12,
                'formulas': 100, 'formulas_xsl': 99, 'error': err}

    def test_转三份留三条不是只剩最后一条(self):
        for n in ('甲', '乙', '丙'):
            maint.note_run(self._rep(n), pdf_name=n + '.pdf', took_sec=10)
        rows = maint.runs()
        self.assertEqual([r['file'] for r in rows], ['丙.pdf', '乙.pdf', '甲.pdf'],
                         '最新的要在最前面，而且一条都不能少')

    def test_超过上限扔掉最老的(self):
        keep = maint.RUNS_KEEP
        for i in range(keep + 5):
            maint.note_run(self._rep('第%d' % i), pdf_name='%d.pdf' % i)
        rows = maint.runs()
        self.assertEqual(len(rows), keep)
        self.assertEqual(rows[0]['file'], '%d.pdf' % (keep + 4), '最新的没留住')
        self.assertEqual(rows[-1]['file'], '5.pdf', '该扔的没扔掉')

    def test_要几条给几条(self):
        r"""🔴 `limit=0` 是「一条都不要」，不是「没给上限」。

        判据写成 `if limit` 的话 0 是 falsy，`/api/runs?limit=0` 会拿回
        全部 200 条 —— 要 0 条给 200 条，接口在做没被要求的事。
        """
        for n in ('甲', '乙', '丙'):
            maint.note_run(self._rep(n), pdf_name='%s.pdf' % n)
        self.assertEqual(len(maint.runs()), 3, '不给 limit 就是全部')
        self.assertEqual(len(maint.runs(2)), 2)
        self.assertEqual(maint.runs(0), [], '要 0 条却给了 %d 条'
                         % len(maint.runs(0)))

    def test_三个新字段都记下来了(self):
        long_err = 'X' * 500
        maint.note_run(self._rep('丁', ok=False, err=long_err), pdf_name='丁.pdf')
        r = maint.runs()[0]
        self.assertEqual(r['pdf'], r'D:\讲义\丁.pdf', '源路径没记，没法一键重转')
        self.assertEqual(r['docx'], r'D:\输出\丁.docx', '产物路径没记，没法打开文件')
        self.assertEqual(r['error_full'], long_err, '报错被截了，查不到当时到底怎么了')

    def test_诊断报告那份行为一个字不变(self):
        r"""🔴 last_run.json 是正在被用的东西。报错仍然截到 200 字符，
        仍然只存最后一条。"""
        maint.note_run(self._rep('戊', ok=False, err='Y' * 500), pdf_name='戊.pdf')
        one = maint.last_run()
        self.assertEqual(one['file'], '戊.pdf')
        self.assertEqual(len(one['error']), 200, '截断行为被改了')
        self.assertNotIn('pdf', one, 'last_run 不该多出新字段')

    def test_历史文件读坏了也要能接着记(self):
        r"""不能因为读不出旧的就连新的也不记。

        🔴 **两种坏法都要测，它们走的不是同一条分支**：
        半截 JSON 解析失败，`_read_json` 返回 None；而一个**合法但不是
        列表**的 JSON（老版本留下的单条记录就长这样）能解析成功，
        判据只写 `r or []` 的话它会原样返回那个 dict，接着 insert 就炸。
        2026-09-06 变异测试抓出来的：第一版只测了前一种，把判据
        换成 `r or []` 照样全绿。
        """
        for broken in ('{半截的不是合法 JSON', '{"time": "老版本的单条记录"}'):
            shutil.rmtree(WORK, ignore_errors=True)
            os.makedirs(WORK)
            io.open(maint.RUNS, 'w', encoding='utf-8').write(broken)
            maint.note_run(self._rep('己'), pdf_name='己.pdf')
            rows = maint.runs()
            self.assertEqual(len(rows), 1, '坏成 %r 时没接着记' % broken[:12])
            self.assertEqual(rows[0]['file'], '己.pdf')

    def test_写不进去也不能把转换搞崩(self):
        maint.RUNS = os.path.join(WORK, '不存在的目录', '别的', 'runs.json')
        try:
            maint.note_run(self._rep('庚'), pdf_name='庚.pdf')
        except Exception as e:
            self.fail('记历史把主流程搞崩了：%s' % e)

    def test_没有历史时返回空列表不是None(self):
        self.assertEqual(maint.runs(), [])


class Test清日志不许连坐(unittest.TestCase):
    r"""🔴 转换历史（runs.json）和学到的模型总量（models_size.json）都住在
    `logs/` 下，而环境检测页那一栏在界面上写的只是**「日志」**。

    用户点一下清理，200 条转换历史（他要靠它找回转过的文件、查当时的报错）
    和下载进度条的分母会一起没了，界面上一个字都没提。
    """

    def setUp(self):
        self._logs = paths_LOGS_backup()
        self._runs = maint.RUNS
        self.w = tempfile.mkdtemp(prefix='p2w_logs_')
        maint.paths.LOGS = self.w
        maint.RUNS = os.path.join(self.w, 'runs.json')

    def tearDown(self):
        maint.paths.LOGS = self._logs
        maint.RUNS = self._runs
        shutil.rmtree(self.w, ignore_errors=True)

    def test_清日志要保住历史和学到的分母(self):
        io.open(os.path.join(self.w, 'convert.log'), 'w',
                encoding='utf-8').write('一些日志')
        io.open(maint.RUNS, 'w', encoding='utf-8').write('[{"file": "讲义.pdf"}]')
        io.open(os.path.join(self.w, maint.SIZE_FILE_NAME), 'w',
                encoding='utf-8').write('{"bytes": 4923616015}')

        maint.clean(keys=['logs'])

        self.assertFalse(os.path.isfile(os.path.join(self.w, 'convert.log')),
                         '日志本身没被清掉')
        self.assertTrue(os.path.isfile(maint.RUNS), '转换历史被连坐删了')
        self.assertTrue(os.path.isfile(os.path.join(self.w, maint.SIZE_FILE_NAME)),
                        '学到的模型总量被连坐删了')

    def test_历史内容还在不是留个空壳(self):
        io.open(maint.RUNS, 'w', encoding='utf-8').write('[{"file": "讲义.pdf"}]')
        maint.clean(keys=['logs'])
        self.assertEqual(maint.runs()[0]['file'], '讲义.pdf')


def paths_LOGS_backup():
    return maint.paths.LOGS


class Test下好的升级包不该混在转换临时文件里(unittest.TestCase):
    r"""🔴 2026-09-07 小蔡实测踩到：下好 2.5 GB 的 torch，界面上却只有
    一行**「转换临时文件」**——因为 `CACHE = paths.TMP/upgrade_cache`，
    它就住在那底下。用户点一下清理，2.5 GB 没了，而状态文件在 logs/ 下
    毫发无损，仍写着「已下好」，于是重启后装不上、还得重下一次。

    单独列一行，让用户看得见自己在删什么。
    """

    def setUp(self):
        self._tmp, self._uc = maint.paths.TMP, maint.UPGRADE_CACHE
        self.w = tempfile.mkdtemp(prefix='p2w_upgi_')
        maint.paths.TMP = self.w
        self.cache = os.path.join(self.w, 'upgrade_cache')
        # UPGRADE_CACHE 是模块级常量（跟 RUNS 一个风格，运行时 TMP 不变），
        # 测试里要跟 paths.TMP 一起换掉。
        maint.UPGRADE_CACHE = self.cache
        os.makedirs(self.cache)
        io.open(os.path.join(self.cache, 'torch-2.14.0.whl'), 'wb').write(b'x' * 5000)
        os.makedirs(os.path.join(self.w, 'extract'))
        io.open(os.path.join(self.w, 'extract', 'a.md'), 'wb').write(b'y' * 100)

    def tearDown(self):
        maint.paths.TMP, maint.UPGRADE_CACHE = self._tmp, self._uc
        shutil.rmtree(self.w, ignore_errors=True)

    def _items(self):
        return {i['key']: i for i in maint.scan()['items']}

    def test_升级包单独一行看得见(self):
        it = self._items()
        self.assertIn('upgrade_cache', it, '下好的安装包没单独列出来')
        self.assertGreater(it['upgrade_cache']['size'], 4000)

    def test_转换临时文件那行不再把升级包算进去(self):
        r"""不然用户看到「转换临时文件 2.5 GB」，根本想不到那是安装包。"""
        it = self._items()
        self.assertLess(it['tmp']['size'], 4000,
                        '升级包还被算在「转换临时文件」里')

    def test_清转换临时文件不许连坐删掉升级包(self):
        maint.clean(keys=['tmp'])
        self.assertTrue(os.path.isfile(os.path.join(self.cache, 'torch-2.14.0.whl')),
                        '下好的 2.5 GB 被「清理转换临时文件」带走了')
        self.assertFalse(os.path.isfile(os.path.join(self.w, 'extract', 'a.md')),
                         '该清的转换临时文件没清掉')

    def test_明确勾了升级包才删它(self):
        maint.clean(keys=['upgrade_cache'])
        self.assertFalse(os.path.isdir(self.cache), '勾了却没删')


if __name__ == '__main__':
    unittest.main()
