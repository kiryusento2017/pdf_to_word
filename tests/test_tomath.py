# -*- coding: utf-8 -*-
r"""公式转换：LaTeX → Word 原生公式对象（OMML）。

链路：LaTeX --KaTeX(node)--> MathML --MML2OMML.XSL--> OMML

**小蔡定的优先级（2026-08-31）**：有 XSL 先用 XSL，没有才启用内置的 Pandoc。
所以这个模块只管 XSL 这条路，拿不到就明确说拿不到，由上层决定退到 Pandoc。

Office 路径**不写死**。工作台那边写死了 `Office16` 一条路径，
换台机器（Office 2013、32 位版、装在 D 盘）就直接失效且无从察觉。
"""
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

    def tearDown(self):
        tomath.XSL_CANDIDATES = self._orig
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

    def test_空输入返回空列表(self):
        self.assertEqual(tomath.batch_to_omml([]), [])

    def test_XSL不在时全部返回None且长度不变(self):
        orig = tomath.XSL_CANDIDATES
        tomath.XSL_CANDIDATES = ['/根本不存在/MML2OMML.XSL']
        try:
            got = tomath.batch_to_omml(['x', 'y', 'z'])
            self.assertEqual(len(got), 3, '长度契约被破坏了')
            self.assertEqual(got, [None, None, None])
            self.assertTrue(tomath.last_error(), '失败了却没留下原因')
        finally:
            tomath.XSL_CANDIDATES = orig


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
