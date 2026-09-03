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
from unittest import mock

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

    def test_强制子进程用UTF8输出(self):
        r"""中文 Windows 的默认代码页是 cp936，而所有读子进程输出的地方
        都按 UTF-8 解 —— 不设这两个变量，中文全变成一片问号。

        最要命的是它专挑最需要看清楚的时候坏事：torch 加载不了时抛的
        WinError 1114 在中文系统上本身就是中文的，而 explain_load_error()
        要靠解析它把错误翻译成人话。
        """
        env = paths.utf8_env()
        self.assertEqual(env['PYTHONIOENCODING'], 'utf-8')
        self.assertEqual(env['PYTHONUTF8'], '1')

    def test_child_env也带着UTF8(self):
        """模型下载和装 GPU 运行库两条日志链都走 child_env。"""
        env = paths.child_env()
        self.assertEqual(env['PYTHONIOENCODING'], 'utf-8')
        self.assertEqual(env['PYTHONUTF8'], '1')
        self.assertEqual(env['MINERU_DEVICE_MODE'], 'cuda')   # 别顾此失彼

    def test_不靠PYTHONPATH挂中文路径补丁(self):
        r"""🔴 **这条钉的是 2026-09-03 差点发出去的一个洞。**

        中文路径补丁一度是靠往 child_env 加 `PYTHONPATH` 来挂的，
        开发环境测试全绿 —— 而发行版的 Python 是 embeddable 版，目录里
        有 `python312._pth`；**`._pth` 一存在，PYTHONPATH 就被整个忽略**。
        实测发行版子进程的 sys.path 只有三条，补丁一次都没生效过，
        而所有测试照样绿着。

        现在改走引导脚本（见下面那条）。这里反过来钉死：**别再回到
        PYTHONPATH 那条路**，它在真实发行版上是不通的。
        """
        with mock.patch.dict(os.environ, {'PYTHONPATH': r'C:\his\own\libs'}):
            env = paths.child_env()
        self.assertEqual(env.get('PYTHONPATH'), r'C:\his\own\libs',
                         '又往 PYTHONPATH 里塞东西了 —— 发行版收不到，'
                         '而且顺手改了用户自己的设置')


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
        

        ⚠️ 2026-09-02 起**转换和下模型都不再走这条路**了：那种 exe 是
           pip 生成的 launcher，硬编码着打包机器上的解释器路径，
           换台机器就废（见 Test绝不调pip生成的launcher）。
           这条测试留着只是钉住 find_exe 本身的查找顺序 ——
           它现在服务的是 node、pandoc 这类真正的独立可执行文件。
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


class Test绝不调pip生成的launcher(unittest.TestCase):
    r"""🔴 2026-09-02 网吧实测暴露、字节层面确认的致命缺陷。

    pip 装包时给 console_scripts 生成的 `Scripts/xxx.exe` 是个小 launcher，
    **尾部硬编码了生成它那一刻的 python.exe 绝对路径**。实测 v0.0.1 的
    `mineru-models-download.exe` 尾部字节：

        #!D:\claude_code_workspace\pdf_to_word\dist\PDF2Word\runtime\python\python.exe

    那是**打包机器上的路径**。用户把包解压到 D:\PDF2Word，这个路径不存在，
    launcher 找不到解释器 —— 模型下载和 PDF 转换全废。

    而它在开发机上永远是好的（包就是在那儿打的，路径真实存在），
    启动自检也是绿的（自检只查了文件在不在）。这是「在我这儿好好的」
    最标准的一个形态：**测速正常、下载一直失败**，因为测速走的是我们
    自己的 Python 代码，不经过那些 exe。

    修法：一律用「解释器 + 我们自己的 .py」的方式跑，路径全部运行时算。

    （2026-09-03 起中间多了一层引导脚本 `sitepatch/run_mineru.py`，
      它负责在 MinerU 起来之前打中文路径补丁，再把模块名转交下去。
      本条要钉的东西没变：**除了解释器自己，命令里不许出现 .exe**。）
    """

    def test_跑MinerU用解释器加引导脚本而不是exe(self):
        cmd = paths.mineru_cmd()
        self.assertGreaterEqual(len(cmd), 3)
        self.assertTrue(cmd[1].endswith('run_mineru.py'),
                        '没走引导脚本，中文路径补丁就挂不上：%s' % cmd[1])
        self.assertEqual(cmd[2], 'mineru.cli.client')
        # argv[0] 是解释器；后面不许再出现任何 .exe
        for a in cmd[1:]:
            self.assertNotIn('.exe', a.lower(), '命令里混进了 exe：%s' % a)

    def test_下模型也走同一个引导脚本(self):
        cmd = paths.models_download_cmd()
        self.assertTrue(cmd[1].endswith('run_mineru.py'))
        self.assertEqual(cmd[2], 'mineru.cli.models_download')
        for a in cmd[1:]:
            self.assertNotIn('.exe', a.lower())

    def test_引导脚本真的存在(self):
        r"""路径拼错了不会有人发现 —— 子进程起不来的报错是
        「can't open file」，跟中文路径八竿子打不着。"""
        self.assertTrue(os.path.isfile(paths.BOOT), paths.BOOT)

    def test_解释器就是正在跑自己的这个(self):
        r"""它一定装着 mineru —— server 能起来就证明这个环境是齐的。
        再去别处找解释器，就又给「找错那个」留了空间。"""
        self.assertEqual(paths.python_exe(), sys.executable)

    def test_判MinerU在不在不能只看文件在不在(self):
        r"""原来的自检是 `bool(find_exe('mineru'))` —— 文件确实在，
        所以一路绿灯，而它根本跑不起来。判据必须是「这个解释器能不能
        找到 mineru 这个包」。"""
        self.assertTrue(paths.mineru_available(),
                        '开发环境装着 mineru 却判成没有')

    @unittest.skipUnless(
        os.path.isfile(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'dist', 'PDF2Word', 'runtime', 'python', 'Scripts',
            'mineru.exe')),
        '本机没有打好的发行版')
    def test_发行版里的launcher确实带着别人的绝对路径(self):
        r"""把这个事实钉在测试里，免得哪天有人觉得「用 exe 更直接」又改回去。"""
        p = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'dist', 'PDF2Word', 'runtime', 'python', 'Scripts', 'mineru.exe')
        with io.open(p, 'rb') as f:
            b = f.read()
        i = b.rfind(b'#!')
        self.assertGreater(i, 0, 'launcher 里没找到 shebang')
        line = b[i:b.find(b'\n', i)].decode('utf-8', 'replace')
        self.assertIn(':', line, 'shebang 里不是绝对路径？那就是换实现了')
        # 它指向的是打包机器上的路径，跟「运行时算出来的解释器」不是一回事
        self.assertTrue(line.lower().endswith('python.exe'), line)


if __name__ == '__main__':
    unittest.main()
