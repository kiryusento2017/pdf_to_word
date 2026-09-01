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

import gpu
import paths  # noqa: E402


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

    def test_不许说没有数的废话(self):
        r"""「会慢很多」「可能不行」这种话等于没说。

        2026-08-31 这条测试要求话术里必须有**具体分钟数** —— 当时的实测
        （GPU 262 秒 / 纯 CPU 460 秒，慢 2.0 倍）把「没显卡」从「用不了」
        改成了「能用，就是得等」，那个数直接影响用户划不划算的判断。

        2026-09-02 小蔡定了只用 GPU，CPU 不再是退路，分钟数也就没了意义 ——
        再写「10 页约 8 分钟」反而是在承诺一件不会发生的事。
        要求跟着改：话术必须让人知道**这台机器到底行不行、不行怎么办**，
        而不是一句模模糊糊的「可能有问题」。
        """
        for v in (gpu.judge(None, wmi_names=['Intel(R) UHD Graphics']),
                  gpu.judge(self._g(6.1, 11264))):
            why = v['why']
            # 得说清楚后果（失败 / 报错 / 用不了），不能只说「不满足要求」
            self.assertTrue(
                any(k in why for k in ('失败', '报错', '用不了', '太老')),
                '没说清楚后果，等于没说：%s' % why)
            # 得给出路（换机器 / 关掉别的程序 / 少转几份）
            self.assertTrue(
                any(k in why for k in ('换一台', '换台', '关掉', '少转', '更新')),
                '没给出路，用户只能干瞪眼：%s' % why)

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


class Test只用GPU的规矩(unittest.TestCase):
    r"""小蔡 2026-09-02 定：这个软件只用 GPU，不用 CPU。显卡不达标要报警，
    但**不阻拦**用户去点 —— 点了当场报错（实测是
    `RuntimeError: No CUDA GPUs are available`），不会白等半小时。

    这里钉两件事：环境变量真的写死成 cuda；话术里不许再出现
    「会退回 CPU」这类**已经不存在**的退路。承诺一条不存在的退路，
    比什么都不说更坏 —— 用户会照着那个预期做决定。
    """

    def test_子进程环境强制走显卡(self):
        env = paths.child_env()
        self.assertEqual(env.get('MINERU_DEVICE_MODE'), 'cuda',
                         'MinerU 的 get_device() 会自己探测，探不到就默默用 CPU')

    def test_选源带的变量不许覆盖掉设备模式(self):
        env = paths.child_env({'MINERU_MODEL_SOURCE': 'modelscope'})
        self.assertEqual(env.get('MINERU_DEVICE_MODE'), 'cuda')

    def test_话术里不许再承诺退回CPU(self):
        cases = [
            # 架构太老
            gpu.judge({'name': 'GTX 960', 'compute_cap': 5.2,
                       'vram_mb': 4096}),
            # 显存不足
            gpu.judge({'name': 'GTX 1660', 'compute_cap': 7.5,
                       'vram_mb': 4096}),
            # 完全没有 N 卡
            gpu.judge(None, wmi_names=['Intel(R) UHD Graphics']),
        ]
        import re
        # 只抓**肯定式**的承诺。「不会退回 CPU」是在澄清，不是承诺 ——
        # 断言写成 assertNotIn('退回 CPU') 会把澄清也判成违规。
        bad = re.compile(r'(?<!不)会退回\s*CPU|会用\s*CPU\s*转换|能用，就是得等')
        for r in cases:
            why = r['why']
            hit = bad.search(why)
            self.assertIsNone(hit,
                              '又承诺了不存在的退路（%s）：%s'
                              % (hit.group(0) if hit else '', why))

    def test_没有N卡时要说清楚这台机器用不了(self):
        r"""不阻拦不等于不说清楚 —— 让人反复试才是最坏的。"""
        r = gpu.judge(None, wmi_names=['Intel(R) UHD Graphics 630'])
        self.assertFalse(r['ok'])
        self.assertIn('显卡', r['why'])
        self.assertTrue('失败' in r['why'] or '用不了' in r['why'],
                        '没说清楚这台机器转不了：%s' % r['why'])


if __name__ == '__main__':
    unittest.main()
