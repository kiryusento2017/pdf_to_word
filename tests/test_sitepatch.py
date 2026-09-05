# -*- coding: utf-8 -*-
r"""中文安装路径。

2026-09-03 真机报错：

    Failed to load FastText model:
    C:\Users\32854\Desktop\新建文件夹 (2)\runtime\python\Lib\site-packages
    \fast_langdetect\ft_detect\resources\lid.176.ftz cannot be opened for loading!

用户把软件装在名叫「新建文件夹 (2)」的目录里，MinerU 子进程当场
退出码 1，一个产物都没有。

根因不是文件缺失 —— `fast_langdetect/ft_detect/infer.py` 先做
`model_path.exists()` 检查，不存在会报另一句话。报到「cannot be
opened」说明**文件就在那儿，是 fasttext 打不开它**：

  · Python 把 str 路径按 UTF-8 编码交给 pybind11
  · fasttext 的 C++ 层用 std::ifstream，按系统 ACP(936/GBK) 解
  · 中文字节对不上，文件在 C++ 眼里就是不存在

实测（本机 D 盘，ACP=936，PYTHONUTF8 开与不开结果一致）：

    ASCII 路径 + str          OK
    中文路径 + str            FAIL   ← 用户踩的
    中文路径 + 8.3 短路径     不可用  ← Win10 起非系统盘默认关闭
    中文路径 + mbcs bytes     OK     ← 解法
    中文路径 + utf-8 bytes    FAIL

「中文路径不可避免」是小蔡定的前提，所以不能靠「让用户改路径」了事。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))
sys.path.insert(0, os.path.join(ROOT, 'pipeline', 'sitepatch'))

import paths        # noqa: E402
import sitecustomize  # noqa: E402

# 中文目录名，直接抄真机上那个
ZH = '新建文件夹 (2)'

try:
    import fasttext  # noqa: F401
    HAS_FASTTEXT = True
except Exception:
    HAS_FASTTEXT = False

try:
    from fast_langdetect.ft_detect.infer import LOCAL_SMALL_MODEL_PATH
    MODEL = str(LOCAL_SMALL_MODEL_PATH)
except Exception:
    MODEL = None


class Test路径编码转换(unittest.TestCase):
    r"""`native_path()` 是整个补丁的全部判断逻辑，行为必须是死的。"""

    def test_ASCII路径原样返回(self):
        r"""🔴 **绝大多数用户的路径是 ASCII 的，那条路必须一个字节都不变。**

        补丁只该在真正会出事的路径上生效。ASCII 路径返回 bytes 也能用，
        但那等于让 100% 的用户去承担 1% 的人才需要的改动。
        """
        p = r'C:\Users\alice\PDF2Word\lid.176.ftz'
        self.assertIs(sitecustomize.native_path(p), p)

    def test_中文路径转成mbcs字节(self):
        p = 'D:\\%s\\lid.176.ftz' % ZH
        got = sitecustomize.native_path(p)
        self.assertIsInstance(got, bytes)
        self.assertEqual(got, p.encode('mbcs'))

    def test_非字符串原样返回(self):
        r"""调用方可能传 bytes（已经转过）或别的东西，不要二次加工。"""
        b = b'already-bytes'
        self.assertIs(sitecustomize.native_path(b), b)
        self.assertIs(sitecustomize.native_path(None), None)

    def test_当前代码页表示不了的字符要当场说清楚(self):
        r"""文件夹名里带 emoji —— 任何 ANSI 代码页都编不出来。

        这时候**必须报一句人能看懂的话**，不能让它抛一个
        UnicodeEncodeError 了事：那种堆栈冒到界面上，用户只会看到
        一串英文，完全不知道该改什么。

        （一开始拿日文假名当例子，结果 GBK 本身就包含假名，编得出来，
          这条一直是 skip —— 等于没测。换成 emoji 才真正覆盖到。）
        """
        p = 'D:\\\U0001f680\\lid.176.ftz'      # 🚀
        try:
            p.encode('mbcs')
        except UnicodeEncodeError:
            pass
        else:
            self.skipTest('当前系统代码页能表示这个字符，这条测不了')

        with self.assertRaises(ValueError) as cm:
            sitecustomize.native_path(p)
        msg = str(cm.exception)
        self.assertIn(p, msg, '没把出问题的路径带出来，用户不知道改哪个')
        self.assertIn('英文', msg, '没告诉用户该怎么办')


class Test补丁挂载(unittest.TestCase):
    r"""补丁必须挂得上、且只挂一次。"""

    @unittest.skipUnless(HAS_FASTTEXT, '本机没装 fasttext')
    def test_load_model被换掉了(self):
        import fasttext
        self.assertTrue(getattr(fasttext.load_model, '_zh_path_patched', False),
                        'fasttext.load_model 没被补丁接管')

    @unittest.skipUnless(HAS_FASTTEXT, '本机没装 fasttext')
    def test_重复打补丁不会套娃(self):
        r"""sitecustomize 正常只被 import 一次，但测试会重复 import。
        套两层的话，第二层拿到的是 bytes，判断逻辑会全部落空。"""
        import fasttext
        first = fasttext.load_model
        sitecustomize.patch_fasttext()
        self.assertIs(fasttext.load_model, first, '又包了一层')

    @unittest.skipUnless(HAS_FASTTEXT, '本机没装 fasttext')
    def test_fasttext模块内部的引用也换掉了(self):
        r"""`fasttext.load_model` 和 `fasttext.FastText.load_model` 是
        同一个函数对象。只换外面那个，从 FastText 子模块导入的调用方
        照样踩坑。"""
        import fasttext
        import fasttext.FastText as F
        self.assertIs(F.load_model, fasttext.load_model)


class Test真的在中文路径下加载模型(unittest.TestCase):
    r"""🔴 **这条是这个文件存在的理由。**

    上面那些测的是「我写的判断对不对」，只有这条测的是
    「fasttext 到底吃不吃这一套」—— 而后者才是用户遇到的问题。
    """

    def setUp(self):
        if not (HAS_FASTTEXT and MODEL and os.path.exists(MODEL)):
            self.skipTest('本机没有 lid.176.ftz，跳过')
        self.tmp = tempfile.mkdtemp(prefix='zhpath_')
        self.zhdir = os.path.join(self.tmp, ZH)
        os.makedirs(self.zhdir, exist_ok=True)
        self.zhmodel = os.path.join(self.zhdir, 'lid.176.ftz')
        shutil.copy2(MODEL, self.zhmodel)

    def tearDown(self):
        shutil.rmtree(getattr(self, 'tmp', ''), ignore_errors=True)

    def test_打补丁之后中文路径能加载(self):
        import fasttext
        fasttext.load_model(self.zhmodel)      # 抛异常就算失败

    def test_不打补丁的原函数在中文路径下确实会失败(self):
        r"""🔴 **反向验证：证明这个 bug 真的存在。**

        少了这条，上面那条测试在「fasttext 其实一直都支持中文路径」
        的世界里也会绿 —— 那等于什么都没测。
        """
        import fasttext
        orig = getattr(fasttext.load_model, '_zh_path_orig', None)
        self.assertIsNotNone(orig, '拿不到原函数，没法反向验证')
        with self.assertRaises(Exception) as cm:
            orig(self.zhmodel)
        self.assertIn('cannot be opened', str(cm.exception))

    def test_ASCII路径照常能加载(self):
        r"""补丁不能把本来好的那条路弄坏。"""
        import fasttext
        ascii_dir = os.path.join(self.tmp, 'ascii')
        os.makedirs(ascii_dir, exist_ok=True)
        p = os.path.join(ascii_dir, 'lid.176.ftz')
        shutil.copy2(MODEL, p)
        fasttext.load_model(p)


class Test端到端子进程(unittest.TestCase):
    r"""🔴 **整个方案的命门在这儿。**

    上面所有测试都在**当前进程**里验，可真正要生效的地方是
    `_spawn()` 起的那个 MinerU 子进程 —— 补丁能不能被 `site` 自动
    import，全看 `child_env()` 里的 PYTHONPATH 有没有挂对。

    少了这条，哪天有人把 PYTHONPATH 那几行删了，上面 12 条照样全绿，
    而装在中文目录里的用户一份都转不了。
    """

    def setUp(self):
        if not (HAS_FASTTEXT and MODEL and os.path.exists(MODEL)):
            self.skipTest('本机没有 lid.176.ftz，跳过')
        self.tmp = tempfile.mkdtemp(prefix='zhpath_e2e_')
        zhdir = os.path.join(self.tmp, ZH)
        os.makedirs(zhdir, exist_ok=True)
        self.zhmodel = os.path.join(zhdir, 'lid.176.ftz')
        shutil.copy2(MODEL, self.zhmodel)

        # 一个「假装自己是 MinerU」的模块：完全不知道有补丁这回事，
        # 就照 MinerU 的用法直接加载模型。放进 sitepatch/ 才能被引导器
        # 的 runpy 找到（那个目录就是子进程的 sys.path[0]），跑完删掉。
        self.probe = os.path.join(ROOT, 'pipeline', 'sitepatch',
                                  '_e2e_probe.py')
        with open(self.probe, 'w', encoding='utf-8') as f:
            f.write('import fasttext, sys\n'
                    'fasttext.load_model(sys.argv[1])\n'
                    'print("LOADED")\n')
        self.addCleanup(lambda: os.path.exists(self.probe)
                        and os.remove(self.probe))

    def tearDown(self):
        shutil.rmtree(getattr(self, 'tmp', ''), ignore_errors=True)

    def test_引导脚本起的子进程能在中文路径下加载(self):
        r"""走的是**真实的那条命令形态**：解释器 + 引导脚本 + 模块名。"""
        r = subprocess.run(
            [sys.executable, paths.BOOT, '_e2e_probe', self.zhmodel],
            capture_output=True, env=paths.child_env())
        self.assertEqual(r.returncode, 0,
                         '子进程失败了：%s'
                         % r.stderr.decode('utf-8', 'replace')[-400:])
        self.assertIn(b'LOADED', r.stdout)

    def test_对照组_不走引导脚本就是用户那个报错(self):
        r"""🔴 **反向验证。** 证明这条测试真的在测东西 —— 绕开引导脚本
        必须复现 2026-09-03 真机上那句 `cannot be opened for loading!`。"""
        r = subprocess.run(
            [sys.executable, self.probe, self.zhmodel],   # 直接跑，不经引导
            capture_output=True, env=paths.child_env())
        self.assertNotEqual(r.returncode, 0, '没走引导脚本竟然也成功了')
        self.assertIn('cannot be opened',
                      r.stderr.decode('utf-8', 'replace'),
                      '失败了，但不是我们要修的那个原因')

    def test_引导脚本必须原样传出退出码(self):
        r"""🔴 **这条比看上去要紧得多。**

        `extract.py` 的判据是「有产物不等于成功，rc != 0 一律判失败」——
        因为 MinerU 是边处理边写的，十页崩在第七页也会留下前六页的 .md。
        引导脚本如果把非零退出码吃掉（比如 runpy 抛的 SystemExit 被
        try/except 拦下），**残缺的 Word 会被当成品交给老师**，
        而界面显示「转好了」。

        这是加了这一层之后新出现的风险，原来 `-m` 直连没有中间人。
        """
        cases = [('import sys; sys.exit(3)', 3),
                 ('raise SystemExit(7)', 7),
                 ("raise RuntimeError('boom')", 1),
                 ("print('fine')", 0)]
        probe = os.path.join(ROOT, 'pipeline', 'sitepatch', '_rc_probe.py')
        self.addCleanup(lambda: os.path.exists(probe) and os.remove(probe))
        for code, want in cases:
            with open(probe, 'w', encoding='utf-8') as f:
                f.write(code + '\n')
            r = subprocess.run([sys.executable, paths.BOOT, '_rc_probe'],
                               capture_output=True, env=paths.child_env())
            self.assertEqual(r.returncode, want,
                             '%r 应当退出 %d，实际 %d —— 退出码被吃掉了，'
                             '转换失败会被当成功' % (code, want, r.returncode))

    def test_引导脚本会把模块名之后的参数原样转交(self):
        r"""转交错了的话，MinerU 会拿不到 -p / -o，报的却是它自己的
        用法错误 —— 跟中文路径毫无关系，极难往这边想。"""
        r = subprocess.run(
            [sys.executable, paths.BOOT, '_e2e_probe', self.zhmodel],
            capture_output=True, env=paths.child_env())
        self.assertIn(b'LOADED', r.stdout,
                      '模块没拿到它的参数：%s'
                      % r.stderr.decode('utf-8', 'replace')[-200:])


class Test子进程命令改写(unittest.TestCase):
    r"""MinerU 自己起的那个进程，也得走引导器。

    2026-09-05 发现的第二层：`run_mineru.py` 的补丁只管到
    `mineru.cli.client` 那个进程，而 hybrid-engine 后端会再起一个
    `python -m mineru.cli.fast_api` 干活（`cli/api_client.py:511`，
    多 GPU 那条 `cli/router.py:429` 形状一样）。

    **语言检测只在后面那个进程里发生** —— 调 `utils/language.py` 的全在
    `backend/` 下，CLI 侧一处都没有。所以补丁挂在 CLI 那层，等于挂在了
    一个根本不会触发这个 bug 的地方：8-31 起每个版本都有这个洞，
    9-03 修完照样炸，而 298 条测试全绿。
    """

    def setUp(self):
        self.boot = os.path.join(ROOT, 'pipeline', 'sitepatch', 'run_mineru.py')

    def test_mineru模块的_m启动会被改写成走引导器(self):
        argv = ['py.exe', '-m', 'mineru.cli.fast_api', '--host', '127.0.0.1']
        self.assertEqual(
            sitecustomize.boot_argv(argv),
            ['py.exe', self.boot, 'mineru.cli.fast_api', '--host', '127.0.0.1'])

    def test_模块名与后续参数一字不动(self):
        r"""转交错了的话，MinerU 拿不到 --host/--port，报的却是它自己的
        用法错误，跟中文路径毫无关系，极难往这边想。"""
        argv = ['py.exe', '-m', 'mineru.cli.fast_api', '--port', '4302', '-x']
        got = sitecustomize.boot_argv(argv)
        self.assertEqual(got[2], 'mineru.cli.fast_api')
        self.assertEqual(got[3:], ['--port', '4302', '-x'])

    def test_不该动的命令一律原样返回(self):
        r"""🔴 这个补丁挂在**所有** Popen 调用上，跟我们无关的必须一个不碰。"""
        for argv in (['py.exe', '-m', 'pip', 'install', 'x'],
                     ['py.exe', '-m', 'mineru_other.thing'],   # 前缀像但不是
                     ['py.exe', '-c', 'print(1)'],
                     ['py.exe', 'some_script.py', '-m'],
                     ['py.exe']):
            self.assertIs(sitecustomize.boot_argv(argv), argv, argv)

    def test_字符串命令原样返回(self):
        r"""shell=True 那种写法，动不得。"""
        s = 'python -m mineru.cli.fast_api'
        self.assertIs(sitecustomize.boot_argv(s), s)

    def test_已经是引导器形式不会套第二层(self):
        argv = ['py.exe', self.boot, 'mineru.cli.fast_api']
        self.assertIs(sitecustomize.boot_argv(argv), argv)


class Test改写不能破坏Popen本身(unittest.TestCase):
    r"""🔴 只能包 `Popen.__init__`，不能把 `Popen` 换成函数。

    MinerU 有两处会当场炸：`cli/api_client.py:49` 写了
    `subprocess.Popen[bytes]`（类型下标），`:283` 写了
    `isinstance(process, subprocess.Popen)` —— 函数两样都不支持。
    换错写法的话，中文路径是好了，所有人的转换全挂。
    """

    def test_类型下标还能用(self):
        self.assertIsNotNone(subprocess.Popen[bytes])

    def test_isinstance还能用(self):
        p = subprocess.Popen([sys.executable, '-c', 'pass'])
        p.wait()
        self.assertIsInstance(p, subprocess.Popen)

    def test_补丁挂上了(self):
        self.assertTrue(
            getattr(subprocess.Popen.__init__, '_zh_path_patched', False),
            'import sitecustomize 没有把 Popen 补丁挂上')

    def test_重复打补丁不会套娃(self):
        before = subprocess.Popen.__init__
        sitecustomize.patch_subprocess()
        self.assertIs(subprocess.Popen.__init__, before)


class Test引导器的spawn守卫(unittest.TestCase):
    r"""引导器被 multiprocessing 重新执行时，绝不能再去解析 argv。

    Windows 上 multiprocessing 用 spawn，子进程会把主脚本**重新跑一遍**
    来重建命名空间（`run_name='__mp_main__'`），`sys.argv` 原样继承父
    进程 —— 那一次没有模块名，`pop(1)` 拿到的是 `--host`，于是
    `ImportError: No module named --host`，MinerU 的 PDF 渲染进程池整个
    起不来，任务照样零产物。

    2026-09-05 端到端实测抓到的：同一份 PDF、同一个中文路径，
    加守卫前 0 个产物，加守卫后 36 个。
    """

    def test_按mp_main重跑时不碰argv(self):
        boot = os.path.join(ROOT, 'pipeline', 'sitepatch', 'run_mineru.py')
        code = ('import runpy, sys\n'
                'sys.argv = ["run_mineru.py", "--host", "127.0.0.1"]\n'
                'runpy.run_path(%r, run_name="__mp_main__")\n'
                'print("ARGV=" + repr(sys.argv))\n' % boot)
        r = subprocess.run([sys.executable, '-c', code],
                           capture_output=True, env=paths.child_env())
        out = (r.stdout + r.stderr).decode('utf-8', 'replace')
        self.assertEqual(r.returncode, 0,
                         '守卫没挡住，spawn 重跑时炸了：%s' % out[-400:])
        self.assertNotIn('No module named', out)
        self.assertIn("ARGV=['run_mineru.py', '--host', '127.0.0.1']", out,
                      'argv 被 pop 掉了 —— 守卫没生效：%s' % out[-300:])


class Test补丁目录只暴露这一个文件(unittest.TestCase):
    r"""sitepatch/ 是通过 PYTHONPATH 塞进子进程 sys.path 的。

    🔴 **不能把 pipeline/ 整个目录塞进去。** 那里面有 paths.py、
    models.py、update.py 这些名字很普通的模块，一旦进了 MinerU 子进程的
    sys.path，就可能盖掉它自己或它依赖的同名模块 —— 那种冲突极难查。
    单开一个只放补丁的目录，是为了把这个风险降到零。
    """

    def test_sitepatch里只有该有的那两个(self):
        r"""补丁本体 + 引导器，就这两个。

        引导器用脚本路径启动，Python 会把**这个目录**放进 sys.path[0]，
        所以这里每多一个 .py，MinerU 子进程的搜索路径里就多一个可能
        盖掉它自己或它依赖的同名模块的东西。
        """
        d = os.path.join(ROOT, 'pipeline', 'sitepatch')
        got = sorted(f for f in os.listdir(d)
                     if not f.startswith('__') and f != '__pycache__')
        self.assertEqual(got, ['run_mineru.py', 'sitecustomize.py'],
                         'sitepatch/ 里多了别的模块，会污染子进程的 sys.path：%s' % got)

    def test_补丁目录在安装目录内(self):
        d = os.path.abspath(paths.SITEPATCH)
        self.assertTrue(d.startswith(os.path.abspath(paths.ROOT)),
                        '补丁目录跑到安装目录外面去了：%s' % d)


if __name__ == '__main__':
    unittest.main(verbosity=2)
