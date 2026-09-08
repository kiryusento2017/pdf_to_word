# -*- coding: utf-8 -*-
r"""公式转换：LaTeX → Word 原生公式对象（OMML）。

链路：LaTeX --KaTeX(node)--> MathML --MML2OMML.XSL--> OMML

**小蔡定的优先级（2026-08-31）**：有 XSL 先用 XSL，没有才启用内置的 Pandoc。
所以这个模块只管 XSL 这条路，拿不到就明确说拿不到，由上层决定退到 Pandoc。

Office 路径**不写死**。工作台那边写死了 `Office16` 一条路径，
换台机器（Office 2013、32 位版、装在 D 盘）就直接失效且无从察觉。
"""
import io
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import tomath  # noqa: E402

WORK = os.path.join(ROOT, '_tmp', 'tests', 'tomath')


class Test找Office的XSL(unittest.TestCase):

    def setUp(self):
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)
        self._orig = tomath.XSL_CANDIDATES
        # find_xsl 会先查注册表。装了 Office 的机器上那条真能查到，
        # 只 mock XSL_CANDIDATES 的话「找不到」的用例根本不成立。
        self._orig_reg = tomath.registry_candidates
        tomath.registry_candidates = lambda: []
        # 🔴 2026-09-09 起 find_xsl **第一步先看自带的那份**
        #    （runtime/xsl/MML2OMML.XSL，随软件分发）。这一条不堵上，
        #    下面这些用例全都测不到 Office 探测那条兜底路径 —— 它们会一路
        #    拿到自带的真文件。这个类测的就是兜底那条，所以这里堵掉。
        #    「自带的优先」本身另有用例，见 Test自带的XSL。
        self._orig_bundled = tomath.bundled_xsl
        tomath.bundled_xsl = lambda: None

    def tearDown(self):
        tomath.XSL_CANDIDATES = self._orig
        tomath.registry_candidates = self._orig_reg
        tomath.bundled_xsl = self._orig_bundled
        shutil.rmtree(WORK, ignore_errors=True)

    def test_候选路径不止一条(self):
        r"""工作台写死了 Office16 一条路径，换台机器就失效且无从察觉。
        Office 2013 是 Office15、32 位版在 Program Files (x86)、还有人装在 D 盘。"""
        self.assertGreater(len(self._orig), 3,
                           '候选路径太少，换个 Office 版本就找不到了')
        blob = ' '.join(self._orig)
        self.assertIn('Office16', blob)
        self.assertIn('Office15', blob)
        self.assertIn('(x86)', blob, '没考虑 32 位版 Office')

    def test_找得到就返回路径(self):
        fake = os.path.join(WORK, 'MML2OMML.XSL')
        with open(fake, 'w', encoding='utf-8') as f:
            f.write('<xsl:stylesheet/>')
        tomath.XSL_CANDIDATES = [fake]
        self.assertEqual(tomath.find_xsl(), fake)
        self.assertTrue(tomath.xsl_available())

    def test_找不到就是找不到不许假装(self):
        tomath.XSL_CANDIDATES = [os.path.join(WORK, '不存在.XSL')]
        self.assertIsNone(tomath.find_xsl())
        self.assertFalse(tomath.xsl_available())

    def test_按候选顺序取第一个命中的(self):
        a = os.path.join(WORK, 'a.XSL')
        b = os.path.join(WORK, 'b.XSL')
        for p in (a, b):
            with open(p, 'w', encoding='utf-8') as f:
                f.write('<xsl:stylesheet/>')
        tomath.XSL_CANDIDATES = [os.path.join(WORK, '没有.XSL'), a, b]
        self.assertEqual(tomath.find_xsl(), a)


class Test批量转换的硬契约(unittest.TestCase):
    r"""返回列表必须与输入**等长**，转不了的位置是 None。

    等长是硬契约：上层靠下标把结果对回原公式，长度对不上就没法退回源码，
    也没法知道是第几个公式失败的。
    """

    def test_注册表命中时优先于目录扫描(self):
        r"""注册表存的是**实际**安装路径，比猜目录准 —— 能找到装在
        E:\SomeFolder\ 这种地方的 Office。顺序调换了要有提示。"""
        import tempfile
        d = tempfile.mkdtemp()
        try:
            reg_hit = os.path.join(d, 'reg.XSL')
            dir_hit = os.path.join(d, 'dir.XSL')
            for p in (reg_hit, dir_hit):
                with io.open(p, 'w', encoding='utf-8') as f:
                    f.write('<xsl/>')
            orig_reg, orig_cand = tomath.registry_candidates, tomath.XSL_CANDIDATES
            orig_bundled = tomath.bundled_xsl       # 自带的排在这两条之前，先堵上
            try:
                tomath.bundled_xsl = lambda: None
                tomath.registry_candidates = lambda: [reg_hit]
                tomath.XSL_CANDIDATES = [dir_hit]
                self.assertEqual(tomath.find_xsl(), reg_hit, '目录扫描盖过了注册表')
            finally:
                tomath.registry_candidates = orig_reg
                tomath.XSL_CANDIDATES = orig_cand
                tomath.bundled_xsl = orig_bundled
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_注册表读不到时不抛异常(self):
        r"""注册表结构因 Office 版本而异，为了探测把启动自检搞崩不值得。"""
        got = tomath.registry_candidates()
        self.assertIsInstance(got, list)

    def test_空输入返回空列表(self):
        self.assertEqual(tomath.batch_to_omml([]), [])

    def test_XSL不在时全部返回None且长度不变(self):
        orig = tomath.XSL_CANDIDATES
        orig_reg = tomath.registry_candidates
        orig_bundled = tomath.bundled_xsl
        tomath.XSL_CANDIDATES = ['/根本不存在/MML2OMML.XSL']
        tomath.registry_candidates = lambda: []      # 注册表那条也得堵上
        tomath.bundled_xsl = lambda: None            # 自带那条也是（2026-09-09 起）
        try:
            got = tomath.batch_to_omml(['x', 'y', 'z'])
            self.assertEqual(len(got), 3, '长度契约被破坏了')
            self.assertEqual(got, [None, None, None])
            self.assertTrue(tomath.last_error(), '失败了却没留下原因')
        finally:
            tomath.XSL_CANDIDATES = orig
            tomath.registry_candidates = orig_reg
            tomath.bundled_xsl = orig_bundled


class Test自带的XSL(unittest.TestCase):
    r"""2026-09-09 起 `runtime/xsl/MML2OMML.XSL` 随软件分发，不再要求用户
    装 Office（决定和风险见 tomath.py 顶部）。这一组盯的就是这件事。"""

    def test_自带的文件真的在(self):
        p = tomath.bundled_xsl()
        self.assertTrue(p, 'runtime/xsl/MML2OMML.XSL 不见了 —— '
                           '发行版会转不了公式，而且要到用户点转换才炸')
        self.assertTrue(os.path.isfile(p))

    def test_自带的优先于用户的Office(self):
        r"""发出去的那份就是验证过的那份，不受用户装了哪版 Office 影响。"""
        orig_reg, orig_cand = tomath.registry_candidates, tomath.XSL_CANDIDATES
        try:
            # 把 Office 那两条都指向「存在的别的文件」，自带的仍该赢
            other = os.path.abspath(__file__)
            tomath.registry_candidates = lambda: [other]
            tomath.XSL_CANDIDATES = [other]
            self.assertEqual(tomath.find_xsl(), tomath.bundled_xsl(),
                             '用户的 Office 盖过了自带的那份')
        finally:
            tomath.registry_candidates = orig_reg
            tomath.XSL_CANDIDATES = orig_cand

    def test_自带的没了才回头找Office(self):
        r"""兜底那条没删：万一自带文件被杀软删了，还能自己找回来。"""
        orig_b, orig_reg, orig_cand = (tomath.bundled_xsl,
                                       tomath.registry_candidates,
                                       tomath.XSL_CANDIDATES)
        try:
            other = os.path.abspath(__file__)
            tomath.bundled_xsl = lambda: None
            tomath.registry_candidates = lambda: []
            tomath.XSL_CANDIDATES = [other]
            self.assertEqual(tomath.find_xsl(), other, '兜底那条断了')
        finally:
            tomath.bundled_xsl = orig_b
            tomath.registry_candidates = orig_reg
            tomath.XSL_CANDIDATES = orig_cand


@unittest.skipUnless(tomath.xsl_available() and tomath.node_available(),
                     '本机没有 Office 的 XSL 或没有 node，跳过真转换')
class Test真转换(unittest.TestCase):
    r"""本机装了 Office 才跑。这一组是「XSL 这条路真的能出 OMML」的唯一证据 ——
    前面那些只证明了找路径和长度契约。"""

    def test_简单公式转出OMML(self):
        got = tomath.batch_to_omml(['x^2 + y^2 = z^2'])
        self.assertEqual(len(got), 1)
        self.assertIsNotNone(got[0], tomath.last_error())
        xml = tomath.omml_to_string(got[0])
        self.assertIn('oMath', xml, '转出来的不是 OMML')

    def test_真实讲义里的公式(self):
        r"""取自解不等式那份，含集合花括号和不等号 —— 工作台那边
        正是在这个公式上栽过（反转义把 \\{ \\} 吃掉了）。"""
        tex = r'S = \{x \mid x > -2\}'
        got = tomath.batch_to_omml([tex])
        self.assertIsNotNone(got[0], tomath.last_error())

    def test_一批里坏的不影响好的(self):
        got = tomath.batch_to_omml(['x + 1', r'\这不是合法命令{', 'y = 2'])
        self.assertEqual(len(got), 3)
        self.assertIsNotNone(got[0])
        self.assertIsNone(got[1], '非法 LaTeX 应该转不出来')
        self.assertIsNotNone(got[2], '坏的把后面的带崩了')
        self.assertTrue(tomath.last_error(), '有失败却没留原因')


if __name__ == '__main__':
    unittest.main()
