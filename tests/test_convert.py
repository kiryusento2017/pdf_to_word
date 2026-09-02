# -*- coding: utf-8 -*-
r"""编排层：三步串起来之后，报告里该说的话有没有说出来。

这个文件是 2026-09-02 占位符改造时补的 —— 那次发现 `math_note`
（「第几个公式没转成」写在这里）**没有任何地方读**：summary_line 不读、
前端 0 处引用。信息写进了没人看的字段，等于没写。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import convert  # noqa: E402


def _rep(**kw):
    r = {'ok': True, 'error': '', 'pages': 10, 'formulas': 613,
         'formulas_xsl': 613, 'tables': 18, 'images': 132,
         'math_engine': 'xsl', 'math_note': '', 'scan_pages': []}
    r.update(kw)
    return r


class Test一行人话(unittest.TestCase):
    def test_全转成了就不提没转成的事(self):
        line = convert.summary_line(_rep())
        self.assertIn('公式 613', line)
        self.assertNotIn('没转成', line)

    def test_有公式没转成必须说出来(self):
        r"""占位符改造之后，少数公式转不成不再废掉整份 —— 那就更要说清楚
        这一份有几个没转成，否则用户会以为拿到的是完整的。"""
        line = convert.summary_line(_rep(formulas_xsl=612))
        self.assertIn('1 个公式没转成', line)

    def test_失败时给的是失败原因(self):
        line = convert.summary_line(_rep(ok=False, error='一个公式都没能转成'))
        self.assertIn('失败', line)
        self.assertIn('一个公式都没能转成', line)
