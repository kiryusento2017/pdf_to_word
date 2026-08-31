# -*- coding: utf-8 -*-
r"""GPU 检测。

小蔡定的方案（2026-08-31）：**首次打开时强制检测**，满足就继续，
不满足让用户自己选「退出」还是「硬来」—— 不猜、不替用户做主。

判据：compute_cap ≥ 7.5（Turing 架构）且显存 ≥ 6 GB。
数据来源 `nvidia-smi --query-gpu=...`，本机实测返回：
    NVIDIA GeForce RTX 4060 Laptop GPU, 8188 MiB, 8.9, 572.83

没有 N 卡时用 WMI 兜底查显卡名 —— 好区分「压根没独显」和
「有卡但驱动没装」，这两种给用户的话术完全不同。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import gpu  # noqa: E402


class Test解析nvidia_smi(unittest.TestCase):

    def test_解析本机的真实输出(self):
        r"""这行是本机实测抓下来的，格式变了这条会红。"""
        out = ('name, memory.total [MiB], compute_cap, driver_version\n'
               'NVIDIA GeForce RTX 4060 Laptop GPU, 8188 MiB, 8.9, 572.83\n')
        g = gpu.parse_smi(out)
        self.assertIsNotNone(g)
        self.assertEqual(g['name'], 'NVIDIA GeForce RTX 4060 Laptop GPU')
        self.assertEqual(g['vram_mb'], 8188)
        self.assertEqual(g['compute_cap'], 8.9)
        self.assertEqual(g['driver'], '572.83')

    def test_多张卡取第一张(self):
        out = ('name, memory.total [MiB], compute_cap, driver_version\n'
               'NVIDIA A100, 40960 MiB, 8.0, 535.10\n'
               'NVIDIA T4, 15360 MiB, 7.5, 535.10\n')
        g = gpu.parse_smi(out)
        self.assertEqual(g['name'], 'NVIDIA A100')

    def test_输出为空时返回None而不是炸(self):
        self.assertIsNone(gpu.parse_smi(''))
        self.assertIsNone(gpu.parse_smi('name, memory.total [MiB]\n'))

    def test_格式意外时返回None(self):
        self.assertIsNone(gpu.parse_smi('command not found'))


class Test判够不够用(unittest.TestCase):
    r"""判据：compute_cap ≥ 7.5 且显存 ≥ 6 GB。"""

    def _g(self, cap, vram):
        return {'name': 'X', 'vram_mb': vram, 'compute_cap': cap, 'driver': '1'}

    def test_本机的4060够用(self):
        v = gpu.judge(self._g(8.9, 8188))
        self.assertTrue(v['ok'], v['why'])

    def test_Turing刚好够(self):
        r"""7.5 正好是 Turing。卡在边界上的必须判够，不然 2080/T4 全被挡在门外。"""
        self.assertTrue(gpu.judge(self._g(7.5, 8192))['ok'])

    def test_Pascal架构不够(self):
        r"""1080Ti 是 6.1，显存再大也不满足 —— MinerU 要 Turing 以上。"""
        v = gpu.judge(self._g(6.1, 11264))
        self.assertFalse(v['ok'])
        self.assertIn('架构', v['why'])

    def test_显存不够(self):
        v = gpu.judge(self._g(8.9, 4096))
        self.assertFalse(v['ok'])
        self.assertIn('显存', v['why'])

    def test_没有卡时给的是没有卡的话术(self):
        v = gpu.judge(None)
        self.assertFalse(v['ok'])
        self.assertTrue(v['why'])

    def test_理由必须是人话(self):
        r"""这句会直接显示给老师看，不能是 compute_capability < 7.5 这种。"""
        v = gpu.judge(self._g(6.1, 4096))
        for bad in ('compute_cap', 'vram_mb', 'None', 'False'):
            self.assertNotIn(bad, v['why'])


class Test没有N卡时的兜底(unittest.TestCase):

    def test_能认出有卡但驱动没装(self):
        r"""WMI 看得见显卡名但 nvidia-smi 跑不了 —— 这是「驱动没装」，
        跟「压根没独显」是两回事，给用户的话术不一样。"""
        v = gpu.judge(None, wmi_names=['NVIDIA GeForce RTX 3060'])
        self.assertFalse(v['ok'])
        self.assertIn('驱动', v['why'])

    def test_压根没独显(self):
        v = gpu.judge(None, wmi_names=['Intel(R) UHD Graphics 630'])
        self.assertFalse(v['ok'])
        self.assertNotIn('驱动', v['why'])

    def test_虚拟显示器不算独显(self):
        r"""本机 WMI 列出来一堆 Todesk / 向日葵的虚拟显示器，别把它们当显卡。"""
        v = gpu.judge(None, wmi_names=['Todesk Virtual Display Adapter',
                                       'OrayIddDriver Device'])
        self.assertFalse(v['ok'])
        self.assertNotIn('驱动', v['why'])


class Test真机检测(unittest.TestCase):
    """真跑一次 nvidia-smi。没有 N 卡的机器上应当优雅返回，而不是抛异常。"""

    def test_detect不抛异常(self):
        r = gpu.detect()
        self.assertIn('ok', r)
        self.assertIn('why', r)
        self.assertIsInstance(r['why'], str)


if __name__ == '__main__':
    unittest.main()
