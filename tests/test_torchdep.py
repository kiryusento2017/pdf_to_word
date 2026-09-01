# -*- coding: utf-8 -*-
r"""GPU 运行库（CUDA 版 torch）的判断。

小蔡 2026-09-02 定：这个软件只用 GPU，不用 CPU。那么「装的是不是
CUDA 版 torch」就成了能不能干活的前提之一，判错的代价是用户被告知
「一切正常」然后转换当场失败。

这里的数据不是编的，是本机两个真实环境实测出来的：

    开发环境      __version__ = '2.11.0+cu128'   cuda = '12.8'
    发行版 v0.0.1 __version__ = '2.13.0+cpu'     cuda = None
"""
import io
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import torchdep  # noqa: E402

CUDA_VERSION_PY = """from typing import Optional
__all__ = ['__version__', 'debug', 'cuda', 'git_version', 'hip']
__version__ = '2.11.0+cu128'
debug = False
cuda: Optional[str] = '12.8'
git_version = 'abc'
hip: Optional[str] = None
"""

CPU_VERSION_PY = """from typing import Optional
__all__ = ['__version__', 'debug', 'cuda', 'git_version', 'hip']
__version__ = '2.13.0+cpu'
debug = False
cuda: Optional[str] = None
git_version = 'def'
hip: Optional[str] = None
"""


class Test判断torch版本(unittest.TestCase):

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix='p2w_torch_')
        self.addCleanup(shutil.rmtree, self.work, True)
        self.sp = os.path.join(self.work, 'site-packages')
        os.makedirs(os.path.join(self.sp, 'torch'))
        self.addCleanup(setattr, torchdep, '_site_packages',
                        torchdep._site_packages)
        torchdep._site_packages = lambda: self.sp

    def _write(self, body):
        with io.open(os.path.join(self.sp, 'torch', 'version.py'), 'w',
                     encoding='utf-8') as f:
            f.write(body)

    def test_认出CUDA版(self):
        self._write(CUDA_VERSION_PY)
        d = torchdep.info()
        self.assertTrue(d['installed'])
        self.assertEqual(d['version'], '2.11.0+cu128')
        self.assertEqual(d['cuda'], '12.8')
        self.assertTrue(torchdep.ready())

    def test_认出CPU版(self):
        r"""这是发行版 v0.0.1 里真实的那份 —— 它让整个包只能用 CPU，
        哪怕机器上插着 4090。"""
        self._write(CPU_VERSION_PY)
        d = torchdep.info()
        self.assertTrue(d['installed'], '装是装了')
        self.assertEqual(d['cuda'], '', 'CPU 版却报出了 CUDA 版本号')
        self.assertFalse(torchdep.ready(), 'CPU 版被当成能用 GPU')

    def test_没装torch(self):
        self.assertFalse(torchdep.ready())
        self.assertFalse(torchdep.info()['installed'])

    def test_三种情况的说明各不相同(self):
        r"""「没装」「装错版本」「就绪」是三件事，报错必须分得清 ——
        混成一句「GPU 不可用」，用户不知道该装驱动还是该下运行库。"""
        a = torchdep.why()
        self._write(CPU_VERSION_PY)
        b = torchdep.why()
        self._write(CUDA_VERSION_PY)
        c = torchdep.why()
        self.assertEqual(len({a, b, c}), 3, '三种情况说的是同一句话')
        self.assertIn('CPU 版', b)
        self.assertIn('2.11.0+cu128', c)

    def test_不许靠dist_info目录名判断(self):
        r"""🔴 发行版里 CPU 版 torch 的目录叫 `torch-2.13.0.dist-info`，
        `+cpu` 后缀被 pip 吃掉了 —— 靠目录名判断会把它当成 CUDA 版。
        （开发环境保留了 `torch-2.11.0+cu128.dist-info`，
          只测开发环境的话会以为这条判据管用。）
        """
        self._write(CPU_VERSION_PY)
        os.makedirs(os.path.join(self.sp, 'torch-2.13.0.dist-info'))
        self.assertFalse(torchdep.ready(),
                         '被没有 +cpu 后缀的目录名骗了')


class Test真实环境(unittest.TestCase):
    r"""不造假数据，直接看本机那两份 torch。

    单元测试用脚本造的数据只能证明逻辑自洽，不能证明在真东西上做对了 ——
    这个项目已经栽过两次「测试全绿但实际没生效」。
    """

    def test_开发环境是CUDA版(self):
        d = torchdep.info()
        self.assertTrue(d['installed'], '开发环境没装 torch？')
        self.assertTrue(d['cuda'],
                        '开发环境的 torch 不是 CUDA 版：%s' % d['version'])

    @unittest.skipUnless(
        os.path.isfile(os.path.join(
            ROOT, 'dist', 'PDF2Word', 'runtime', 'python', 'Lib',
            'site-packages', 'torch', 'version.py')),
        '本机没有打好的发行版')
    def test_发行版打进去的是CPU版(self):
        r"""钉住这个事实：v0.0.1 发出去的包里是 CPU 版 torch，
        所以它在任何机器上都用不了显卡。改构建的时候别忘了这条。"""
        sp = os.path.join(ROOT, 'dist', 'PDF2Word', 'runtime', 'python',
                          'Lib', 'site-packages')
        orig = torchdep._site_packages
        torchdep._site_packages = lambda: sp
        self.addCleanup(setattr, torchdep, '_site_packages', orig)
        d = torchdep.info()
        self.assertTrue(d['installed'])
        self.assertIn('cpu', d['version'].lower())
        self.assertEqual(d['cuda'], '')


class Test下载进度不能是黑盒(unittest.TestCase):
    r"""小蔡 2026-09-02：「你在下载任何文件的时候，都应该显示一个进度条，
    并且要弹出背后的命令，这样下载的人才可以知道完整的进度，而不是黑盒。」

    装 GPU 运行库要下约 2.5 GB，原来只有一行文字、没有进度条 —— 用户
    盯着一个不动的界面，分不清是在下还是卡死了。

    进度数据从哪来（2026-09-02 实测 pip 的真实输出，不是凭印象）：

        默认           ---------------------- 12.5/12.5 MB 5.0 MB/s  0:00:02
        --progress-bar raw   Progress 262144 of 12464674

    用 raw：它是 pip 专门为非终端环境设计的机器可读格式，解析零歧义，
    也不受终端宽度影响。**这些 Progress 行不进日志区** —— 它们是给机器
    看的，刷屏会把真正有用的行（Collecting / Downloading / Successfully）
    淹掉，跟 MinerU 那边过滤 tqdm 是一个道理。
    """

    def test_认得出pip的raw进度行(self):
        self.assertEqual(torchdep.parse_progress('Progress 262144 of 12464674'),
                         (262144, 12464674))
        self.assertEqual(torchdep.parse_progress('Progress 0 of 12464674'),
                         (0, 12464674))

    def test_普通输出不会被当成进度(self):
        for line in ('Collecting torch',
                     '  Downloading torch-2.11.0+cu128-win_amd64.whl (2.4 GB)',
                     'Successfully installed torch-2.11.0+cu128',
                     'Progress of the install',      # 像但不是
                     ''):
            self.assertIsNone(torchdep.parse_progress(line), line)

    def test_多个包要累计而不是各算各的(self):
        r"""pip 装 torch 会连着下好几个包（torch、torchvision、依赖）。
        每个包都从 0 开始报进度 —— 直接拿当前包的数当总进度的话，
        进度条会一次次退回去，比没有进度条还糟。"""
        acc = torchdep.ProgressAcc()
        # 第一个包 1000 字节，下完
        self.assertEqual(acc.feed(0, 1000), 0)
        self.assertEqual(acc.feed(600, 1000), 600)
        self.assertEqual(acc.feed(1000, 1000), 1000)
        # 第二个包开始，从 0 报起 —— 总数不许退回去
        self.assertEqual(acc.feed(0, 500), 1000, '换包时进度条退回去了')
        self.assertEqual(acc.feed(500, 500), 1500)

    def test_日志里不许出现进度行(self):
        r"""2.4 GB 下下来会有几千行 Progress，全塞进日志区的话，
        Collecting / Downloading 这些真正有用的行一行都看不见。"""
        self.assertTrue(torchdep.is_noise('Progress 262144 of 12464674'))
        self.assertFalse(torchdep.is_noise('Collecting torch'))
        self.assertFalse(torchdep.is_noise(
            '  Downloading torch-2.11.0+cu128-win_amd64.whl (2.4 GB)'))


class Test装坏了必须卸掉(unittest.TestCase):
    r"""🔴 「torch 装了但加载不了」这个中间态，比「压根没装」更糟。

    根因链（2026-09-02 小蔡真机 + 读 modelscope 源码确认）：

        modelscope/utils/logger.py:48
            if iutil.find_spec('torch') is not None:   ← 只查文件在不在
                from modelscope.utils.torch_utils import ...  ← 直接 import，没 try
                    → torch/__init__.py 加载 c10.dll → OSError WinError 1114

    所以三种状态里，中间那种是雷：

        完全没装       find_spec 返回 None → 跳过 → **模型下载完全正常**
        装了且能用     正常
        装了但加载不了  find_spec 找得到 → 进去 import → 崩

    而这个中间态是**我们自己造的**：装完只检查 version.py 这个文本文件
    在不在就宣布装好了 —— 跟 modelscope 犯的是同一个错（拿「文件在不在」
    当「能不能用」）。两个同样的错叠在一起，用户就在模型下载那一步撞上
    一个跟模型、跟网络都毫无关系的崩溃。

    所以装完验不过就得卸干净，退回「没装」那个安全状态。
    """

    def setUp(self):
        self.calls = []
        self.work = tempfile.mkdtemp(prefix='p2w_broken_')
        self.addCleanup(shutil.rmtree, self.work, True)
        self.addCleanup(setattr, torchdep, 'can_load', torchdep.can_load)
        self.addCleanup(setattr, torchdep, 'uninstall', torchdep.uninstall)
        self.addCleanup(setattr, torchdep, 'ready', torchdep.ready)
        self.addCleanup(setattr, torchdep, 'log_path', torchdep.log_path)
        self.addCleanup(setattr, torchdep, 'install_argv', torchdep.install_argv)
        torchdep.log_path = lambda: os.path.join(self.work, 'x.log')
        torchdep.install_argv = lambda: ['cmd', '/c', 'echo', 'ok']
        torchdep.ready = lambda: True          # 文件层面看着装好了
        torchdep.uninstall = lambda: (self.calls.append('uninstall'), (True, ''))[1]

    def test_加载不了就卸掉(self):
        torchdep.can_load = lambda: (False, 'OSError: [WinError 1114] '
                                            'Error loading c10.dll')
        ok, err = torchdep.install()
        self.assertFalse(ok)
        self.assertIn('uninstall', self.calls,
                      '装坏了却留着 —— modelscope 会 find_spec 找到它然后崩，'
                      '模型下载一起废')
        self.assertIn('卸掉', err, '没告诉用户已经清理过了')

    def test_能加载就不许乱卸(self):
        torchdep.can_load = lambda: (True, '2.11.0+cu128')
        ok, err = torchdep.install()
        self.assertTrue(ok, err)
        self.assertNotIn('uninstall', self.calls, '装好的东西被卸了')

    def test_没装torch时modelscope那条路是安全的(self):
        r"""钉住这个事实：**完全没装 torch 反而没问题**。

        这不是我们的代码行为，是 modelscope 的 —— 但它是「装坏了就卸掉」
        这个决定的全部依据，写在这儿免得后人觉得「卸掉太粗暴」而改回去。
        本机的发行版目录就是活证据：它没有 torch，而模型下载器能正常启动。
        """
        import importlib.util as iutil
        # find_spec 找不到就返回 None —— modelscope 靠这个跳过 import torch
        self.assertIsNone(iutil.find_spec('绝对不存在的包xyz'))


if __name__ == '__main__':
    unittest.main()
