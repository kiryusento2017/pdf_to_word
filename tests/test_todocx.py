# -*- coding: utf-8 -*-
r"""Markdown → Word。

**两条路的分工（小蔡 2026-08-31 定：有 XSL 先用 XSL）**：

    Pandoc 出骨架（段落、表格、图片，以及它自己转的公式）
      ↓
    有 XSL → 把骨架里的公式逐个换成 XSL 转出来的
    没 XSL → 就用 Pandoc 转的那批

这样 XSL 真的被优先用上了，又不必自己重写整个 docx 生成器
（段落、样式、表格、合并单元格、图片嵌入全都得手写，那是几百行）。

替换靠**顺序一一对应**。数量对不上就整批不换、保留 Pandoc 的结果，
并在报告里写明原因 —— 宁可用次优的那条路，也不能张冠李戴把公式换错位置。
"""
import io
import os
import shutil
import sys
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import todocx  # noqa: E402
import tomath  # noqa: E402

WORK = os.path.join(ROOT, '_tmp', 'tests', 'todocx')
HAS_PANDOC = todocx.pandoc_available()


def _omath_count(docx_path):
    z = zipfile.ZipFile(docx_path)
    xml = z.read('word/document.xml').decode('utf-8')
    z.close()
    return xml.count('<m:oMath>') + xml.count('<m:oMath ')


def _tbl_count(docx_path):
    z = zipfile.ZipFile(docx_path)
    xml = z.read('word/document.xml').decode('utf-8')
    z.close()
    return xml.count('<w:tbl>')


class Test找Pandoc(unittest.TestCase):

    def test_内置的pandoc在runtime里(self):
        r"""pandoc 是打包分发的（GPL，独立调用不传染），必须在 runtime/ 下，
        不能依赖用户自己装。"""
        self.assertTrue(HAS_PANDOC,
                        '找不到内置 pandoc，路径：%s' % todocx.PANDOC)
        self.assertIn('runtime', todocx.PANDOC)

    def test_许可证文件跟着一起放(self):
        r"""GPL 分发义务：附协议全文、注明来源。少了这个就是违约。"""
        d = os.path.dirname(todocx.PANDOC)
        got = [f for f in os.listdir(d) if 'COPYRIGHT' in f.upper()
               or 'LICENSE' in f.upper()]
        self.assertTrue(got, 'runtime/pandoc 下没有许可证文件')


@unittest.skipUnless(HAS_PANDOC, '没有 pandoc')
class Test基本转换(unittest.TestCase):

    def setUp(self):
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)

    def tearDown(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def _md(self, text):
        p = os.path.join(WORK, 'in.md')
        io.open(p, 'w', encoding='utf-8').write(text)
        return p

    def test_纯文字转得出docx(self):
        md = self._md('# 标题\n\n这是一段正文。\n')
        out = os.path.join(WORK, 'out.docx')
        r = todocx.md_to_docx(md, out)
        self.assertTrue(r['ok'], r.get('error'))
        self.assertTrue(os.path.isfile(out))
        self.assertTrue(zipfile.is_zipfile(out), '产物不是合法的 docx')

    def test_公式转成Word原生对象而不是源码(self):
        md = self._md('题目：设 $x^2 + y^2 = z^2$ 成立。\n')
        out = os.path.join(WORK, 'out.docx')
        r = todocx.md_to_docx(md, out)
        self.assertTrue(r['ok'], r.get('error'))
        self.assertEqual(_omath_count(out), 1, '公式没转成 OMML')
        self.assertEqual(r['formulas_src'], 1)

    def test_HTML表格能转成真表格(self):
        r"""MinerU 的表格是原始 HTML。不预处理的话 pandoc 会把它当
        raw HTML 保留，而 docx 不支持 raw HTML，整块丢弃 —— 实测 w:tbl = 0。"""
        md = self._md('<table><tr><td>年份</td><td>分值</td></tr>'
                      '<tr><td>2024</td><td>5</td></tr></table>\n')
        out = os.path.join(WORK, 'out.docx')
        r = todocx.md_to_docx(md, out)
        self.assertTrue(r['ok'], r.get('error'))
        self.assertEqual(_tbl_count(out), 1, 'HTML 表格丢了')

    def test_表格里的公式也要转(self):
        r"""实测栽过：html reader 不认识美元符号包起来的是数学，
        把 \\Delta 转义成 \\\\Delta，LaTeX 当场报废（213 掉到 202，残留 11）。
        解法是让 html reader 自己认数学。"""
        md = self._md('<table><tr><td>$\\Delta = b^2 - 4ac$</td></tr></table>\n')
        out = os.path.join(WORK, 'out.docx')
        r = todocx.md_to_docx(md, out)
        self.assertTrue(r['ok'], r.get('error'))
        self.assertEqual(_tbl_count(out), 1)
        self.assertGreaterEqual(_omath_count(out), 1, '表格里的公式没转成 OMML')


@unittest.skipUnless(HAS_PANDOC, '没有 pandoc')
class Test两条路的优先级(unittest.TestCase):
    r"""小蔡定的：有 XSL 先用 XSL，没有才用 Pandoc。"""

    def setUp(self):
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)
        self.md = os.path.join(WORK, 'in.md')
        io.open(self.md, 'w', encoding='utf-8').write(
            '第一个 $a + b$ 第二个 $c \\times d$ 完。\n')
        self.out = os.path.join(WORK, 'out.docx')

    def tearDown(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def test_报告里要写明这次走了哪条路(self):
        r"""不写明的话，同一份文件在两台机器上转出不同结果，
        而人根本不知道为什么 —— 这正是「静默」最难查的地方。"""
        r = todocx.md_to_docx(self.md, self.out)
        self.assertIn(r['math_engine'], ('xsl', 'pandoc'),
                      '没报告用了哪条路')

    def test_强制不用XSL时走pandoc(self):
        r = todocx.md_to_docx(self.md, self.out, prefer_xsl=False)
        self.assertTrue(r['ok'], r.get('error'))
        self.assertEqual(r['math_engine'], 'pandoc')
        self.assertEqual(_omath_count(self.out), 2)

    @unittest.skipUnless(tomath.xsl_available() and tomath.node_available(),
                         '本机没有 Office 的 XSL 或没有 node')
    def test_有XSL时优先用XSL(self):
        r = todocx.md_to_docx(self.md, self.out, prefer_xsl=True)
        self.assertTrue(r['ok'], r.get('error'))
        self.assertEqual(r['math_engine'], 'xsl',
                         '本机有 XSL 却没用它：%s' % r.get('math_note'))
        self.assertEqual(r['formulas_replaced'], 2, '没把公式换成 XSL 的结果')
        self.assertEqual(_omath_count(self.out), 2, '替换后公式数量变了')

    @unittest.skipUnless(tomath.xsl_available() and tomath.node_available(),
                         '本机没有 Office 的 XSL 或没有 node')
    def test_数量对不上就整批不换且判失败(self):
        r"""替换靠顺序一一对应。数量对不上说明对应关系已经不可信，
        这时候**绝不能张冠李戴**把公式换错位置 —— 这个判断没变。

        变的是不换之后怎么算：2026-09-01 小蔡定 XSL 硬性要求之前，
        这里保留 Pandoc 的结果并判成功，用户拿到一份含 ⌀（Pandoc 把
        空集 ∅ 转错了）的 Word 却以为是好的，界面上那句提示还挤在
        150px 宽的省略号里根本看不见。现在判失败，宁可让他知道
        这一份没转好。
        """
        orig = todocx._extract_tex_in_order
        todocx._extract_tex_in_order = lambda text, cwd=None: ['只有一个']
        try:
            r = todocx.md_to_docx(self.md, self.out, prefer_xsl=True)
            self.assertFalse(r['ok'], '数量对不上却判成功了')
            self.assertIn('公式', r['error'])
            self.assertIn('错位', r['error'], '没说清楚为什么不能硬换')
            # 原因里要带上两个数，用户才能判断是不是自己那份书的问题
            self.assertIn('1', r['error'])
        finally:
            todocx._extract_tex_in_order = orig

    def test_没有XSL时直接判失败而不是退回Pandoc(self):
        r"""门口拦了「完全没装 Office」，屋里这条也得拦 ——
        否则装了 Office 但某批公式转不成的人照样静默拿到次等产物。"""
        orig_x, orig_r = tomath.XSL_CANDIDATES, tomath.registry_candidates
        tomath.XSL_CANDIDATES = ['/根本没有/MML2OMML.XSL']
        tomath.registry_candidates = lambda: []
        try:
            r = todocx.md_to_docx(self.md, self.out, prefer_xsl=True)
            self.assertFalse(r['ok'], '没有 XSL 却判成功')
            self.assertIn('Office', r['error'], '没告诉用户该装什么')
        finally:
            tomath.XSL_CANDIDATES = orig_x
            tomath.registry_candidates = orig_r

    def test_明确不要XSL时仍然可以出Word(self):
        r"""prefer_xsl=False 是调用方明确表示「我知道，就要 Pandoc 的结果」，
        那是另一回事，不该被硬性要求拦住 —— 测试和批量脚本都靠这条。"""
        r = todocx.md_to_docx(self.md, self.out, prefer_xsl=False)
        self.assertTrue(r['ok'], '明确要求跳过 XSL 却失败了')
        self.assertEqual(r['math_engine'], 'pandoc')

    def test_失败时不留下半成品Word_没有XSL(self):
        r"""判失败却把 pandoc 已经写出的 docx 留在原地 = 骗人。

        老师看见界面说「失败」，去输出目录一看躺着一份能双击打开、
        里面有内容的 Word，多半就当成功了 —— 而那正是我们判失败要
        拦下的次等品（公式是 Pandoc 转的，∅ 会变成 ⌀）。
        """
        orig_x, orig_r = tomath.XSL_CANDIDATES, tomath.registry_candidates
        tomath.XSL_CANDIDATES = ['/根本没有/MML2OMML.XSL']
        tomath.registry_candidates = lambda: []
        try:
            r = todocx.md_to_docx(self.md, self.out, prefer_xsl=True)
            self.assertFalse(r['ok'])
            self.assertFalse(os.path.exists(self.out),
                             '判失败了却留下一份 Word，用户会当成功')
        finally:
            tomath.XSL_CANDIDATES = orig_x
            tomath.registry_candidates = orig_r

    @unittest.skipUnless(tomath.xsl_available() and tomath.node_available(),
                         '本机没有 Office 的 XSL 或没有 node')
    def test_失败时不留下半成品Word_数量对不上(self):
        r"""这条是实际最常撞上的路径：KaTeX 不认某个命令，产物里的
        公式数对不上，整批不换判失败 —— 此时 docx 早就写出来了。"""
        orig = todocx._extract_tex_in_order
        todocx._extract_tex_in_order = lambda text, cwd=None: ['只有一个']
        try:
            r = todocx.md_to_docx(self.md, self.out, prefer_xsl=True)
            self.assertFalse(r['ok'])
            self.assertFalse(os.path.exists(self.out),
                             '判失败了却留下一份 Word，用户会当成功')
        finally:
            todocx._extract_tex_in_order = orig


if __name__ == '__main__':
    unittest.main()
