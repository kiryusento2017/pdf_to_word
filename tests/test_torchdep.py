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


if __name__ == '__main__':
    unittest.main()
