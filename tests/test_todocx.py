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


def _theme_fonts(docx_path):
    """读出产物主题里的字体。返回 {'major_latin', 'major_hans',
    'minor_latin', 'minor_hans'}，取不到的键就没有。"""
    import re as _re
    z = zipfile.ZipFile(docx_path)
    try:
        names = [n for n in z.namelist() if n.startswith('word/theme/')]
        if not names:
            return {}
        xml = z.read(names[0]).decode('utf-8')
    finally:
        z.close()
    out = {}
    for kind in ('major', 'minor'):
        m = _re.search(r'<a:%sFont>(.*?)</a:%sFont>' % (kind, kind), xml, _re.S)
        if not m:
            continue
        blk = m.group(1)
        lat = _re.search(r'<a:latin typeface="([^"]*)"', blk)
        if lat:
            out[kind + '_latin'] = lat.group(1)
        hans = _re.search(r'<a:font script="Hans" typeface="([^"]*)"', blk)
        if hans:
            out[kind + '_hans'] = hans.group(1)
    return out


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
class Test字体(unittest.TestCase):
    r"""转出来的 Word 用什么字体。

    2026-09-05 之前是 pandoc 内置 reference.docx 的主题字体：
    西文 Aptos、简体中文「等线 Light」。两个都不合适 ——

      · Aptos 是无衬线，而公式是 Word 强制的 Cambria Math（衬线），
        一行字里插个公式风格打架
      · 等线只有 Win10 才自带，Win7/8 上会回退成别的字体
      · 讲义要打印，宋体是为印刷设计的

    正文和标题**用同一套字体**，不给标题单独设 —— MinerU 认标题会认错，
    字体一跳就出现半页宋体半页黑体的花脸。标题靠字号加粗区分就够了。
    """

    def setUp(self):
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)

    def tearDown(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def _out(self, text):
        md = os.path.join(WORK, 'in.md')
        io.open(md, 'w', encoding='utf-8').write(text)
        out = os.path.join(WORK, 'out.docx')
        r = todocx.md_to_docx(md, out)
        self.assertTrue(r['ok'], r.get('error'))
        return out

    def test_西文用TimesNewRoman不是Aptos(self):
        f = _theme_fonts(self._out('# 标题\n\n正文 abc 123。\n'))
        self.assertEqual(f.get('minor_latin'), 'Times New Roman')
        self.assertNotIn('Aptos', f.get('minor_latin', ''))

    def test_简体中文用宋体(self):
        f = _theme_fonts(self._out('# 标题\n\n这是一段中文正文。\n'))
        self.assertEqual(f.get('minor_hans'), '宋体')

    def test_标题和正文同一套字体(self):
        r"""不给标题单独设字体：MinerU 认标题会认错，认错的地方字体
        就会跳。统一之后最多是字号不对，不会花脸。"""
        f = _theme_fonts(self._out('# 标题\n\n正文。\n'))
        self.assertEqual(f.get('major_latin'), f.get('minor_latin'))
        self.assertEqual(f.get('major_hans'), f.get('minor_hans'))

    def test_产物仍然是能打开的docx(self):
        r"""改的是 docx 内部的 XML，改坏了 Word 会报「文件已损坏」，
        而这是**每一份产物**都会经过的路径。"""
        out = self._out('# 标题\n\n正文 $x^2$ 和表格。\n')
        self.assertTrue(zipfile.is_zipfile(out), '产物不是合法的 docx')
        z = zipfile.ZipFile(out)
        try:
            self.assertIsNone(z.testzip(), 'zip 内部有损坏的成员')
            self.assertIn('word/document.xml', z.namelist())
        finally:
            z.close()

    def test_字体改失败也不能毁掉整份转换(self):
        r"""走到改字体那一步时，公式、表格、图片都已经转好了 ——
        字体只是锦上添花。磁盘满了、文件被杀软锁了这类意外，宁可让
        用户拿到一份 Aptos 字体的 Word，也不能让他一无所有。"""
        md = os.path.join(WORK, 'in.md')
        io.open(md, 'w', encoding='utf-8').write('正文一段。' + chr(10))
        out = os.path.join(WORK, 'out.docx')

        real = todocx._set_theme_fonts

        def boom(_path):
            raise OSError('磁盘满了')

        todocx._set_theme_fonts = boom
        try:
            r = todocx.md_to_docx(md, out)
        finally:
            todocx._set_theme_fonts = real

        self.assertTrue(r['ok'], '字体那一步炸了不该让整份转换失败')
        self.assertTrue(os.path.isfile(out), '产物应该还在')
        self.assertEqual(r.get('theme_fonts'), 0)
        self.assertIn('磁盘满了', r.get('theme_fonts_error', ''),
                      '失败原因要记在报告里，不能静默吞掉')

    def test_改字体不影响公式和表格(self):
        r"""改主题字体跟 _add_table_borders 是两道独立的后处理，
        谁也不能把对方的成果覆盖掉。"""
        md = os.path.join(WORK, 'in.md')
        io.open(md, 'w', encoding='utf-8').write(
            '设 $x^2 + y^2 = z^2$。\n\n'
            '<table><tr><td>年份</td><td>分值</td></tr></table>\n')
        out = os.path.join(WORK, 'out.docx')
        r = todocx.md_to_docx(md, out)
        self.assertTrue(r['ok'], r.get('error'))
        self.assertEqual(_omath_count(out), 1, '公式丢了')
        self.assertEqual(_tbl_count(out), 1, '表格丢了')
        z = zipfile.ZipFile(out)
        try:
            xml = z.read('word/document.xml').decode('utf-8')
        finally:
            z.close()
        self.assertIn('tblBorders', xml, '表格边框被字体那一步覆盖掉了')


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
    def test_少数公式转不成不再废掉整份(self):
        r"""改造前：pandoc 在 AST 里数出 N 个公式、渲染进 docx 却只有 N-1 个，
        于是「整批不换 + 判失败」。那个理由是成立的 —— 按顺序替换会让缺口
        之后的公式全部张冠李戴。

        但代价是：一个 OCR 粘连出来的坏公式，能让 612 个转好的公式、
        132 张图、18 个表一起陪葬（2026-09-02 真机，11 份里 1 份中招）。

        占位符定位之后**错位不可能发生**，坏的那几个只影响它自己。
        所以现在照常出 Word，并在报告里点名是第几个。
        """
        orig = tomath.batch_to_omml

        def half(texs):
            out = orig(texs)
            if out:
                out[0] = None          # 第一个故意转不成
            return out
        tomath.batch_to_omml = half
        try:
            r = todocx.md_to_docx(self.md, self.out, prefer_xsl=True)
            self.assertTrue(r['ok'],
                            '一个公式没转成就废掉整份：%s' % r.get('error'))
            self.assertTrue(os.path.isfile(self.out), '产物没了，白转一场')
            self.assertIn('第 1 个', r['math_note'],
                          '没说清是哪个公式没转成，等于让人猜')
        finally:
            tomath.batch_to_omml = orig

    def test_一个都转不成才算真失败(self):
        r"""少数几个转不成不算失败，但一个都没换成说明 XSL 这条链整个坏了。"""
        orig = tomath.batch_to_omml
        tomath.batch_to_omml = lambda texs: [None] * len(texs)
        try:
            r = todocx.md_to_docx(self.md, self.out, prefer_xsl=True)
            self.assertFalse(r['ok'], '一个都没转成却判成功')
            self.assertIn('一个公式都没能', r['error'])
        finally:
            tomath.batch_to_omml = orig

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
        orig = tomath.batch_to_omml
        tomath.batch_to_omml = lambda texs: [None] * len(texs)
        try:
            r = todocx.md_to_docx(self.md, self.out, prefer_xsl=True)
            self.assertFalse(r['ok'])
            self.assertFalse(os.path.exists(self.out),
                             '判失败了却留下原名的 Word，用户会当成功')
        finally:
            tomath.batch_to_omml = orig


class Test判失败也要留下产物(unittest.TestCase):
    r"""判失败**不等于销毁产物**。

    要防的是「次品被当成正品」，不是「次品存在」。转一份四分钟，因为一个
    公式没转成就把 132 张图、18 个表、612 个已转好的公式一起扔掉，
    代价太大。改名之后谁也不会认错，而它照样能打开、能用。
    """

    def setUp(self):
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)
        self.md = os.path.join(WORK, 'in.md')
        io.open(self.md, 'w', encoding='utf-8').write(
            '第一个 $a + b$ 第二个 $c ' + chr(92) + 'times d$ 完。' + chr(10))
        self.out = os.path.join(WORK, 'out.docx')

    def tearDown(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def _fail_on_count(self):
        """造一个「一个公式都没转成」的失败。

        占位符改造之后「数量对不上」这条路径不存在了（那正是改造要消灭的），
        现在唯一的整份失败是「XSL 一个都没转出来」。
        """
        orig = tomath.batch_to_omml
        tomath.batch_to_omml = lambda texs: [None] * len(texs)
        self.addCleanup(lambda: setattr(tomath, 'batch_to_omml', orig))

    @unittest.skipUnless(todocx.pandoc_available(), '本机没有 pandoc')
    def test_失败时产物改名留下而不是删掉(self):
        self._fail_on_count()
        r = todocx.md_to_docx(self.md, self.out, prefer_xsl=True)
        self.assertFalse(r['ok'], '这一步本来就该判失败')
        self.assertFalse(os.path.isfile(self.out), '原名的次品还在，会被当成正品')
        dst = todocx.degraded_path(self.out)
        self.assertTrue(os.path.isfile(dst), '产物被删了，四分钟白等')
        self.assertEqual(r.get('degraded'), dst, '没把次品路径告诉调用方')

    @unittest.skipUnless(todocx.pandoc_available(), '本机没有 pandoc')
    def test_次品名字一眼能认出来(self):
        self._fail_on_count()
        r = todocx.md_to_docx(self.md, self.out, prefer_xsl=True)
        self.assertIn('公式未完全转换', os.path.basename(r['degraded']),
                      '名字看不出是次品，等于没改')
        self.assertTrue(r['degraded'].endswith('.docx'), '扩展名丢了就打不开')

    @unittest.skipUnless(todocx.pandoc_available(), '本机没有 pandoc')
    def test_改不了名必须说出来不能静默(self):
        r"""🔴 改名失败的典型场景是「文件正被 Word 打开着」——
        那时原名的次品原地不动，用户会把它当成品。
        以前这里是 except OSError: pass，静默失守比不拦更糟。
        """
        self._fail_on_count()
        # 只拦「改成带标记的名字」那一次 —— _fill_placeholders 内部也用
        # os.replace 把改好的 docx 写回去，一刀切会误伤它。
        orig = os.replace

        def picky(a, b):
            if '公式未完全转换' in str(b):
                raise OSError('被占用')
            return orig(a, b)
        os.replace = picky
        self.addCleanup(lambda: setattr(os, 'replace', orig))
        r = todocx.md_to_docx(self.md, self.out, prefer_xsl=True)
        self.assertFalse(r['ok'])
        self.assertIn('别把它当成品用', r['error'], '改名失败却没警告')
        self.assertEqual(r.get('degraded'), '', '没改成却报了个路径')

    @unittest.skipUnless(todocx.pandoc_available(), '本机没有 pandoc')
    def test_成功时不改名(self):
        r = todocx.md_to_docx(self.md, self.out, prefer_xsl=False)
        self.assertTrue(r['ok'], r.get('error'))
        self.assertTrue(os.path.isfile(self.out), '成功的产物被改名了')
        self.assertFalse(os.path.isfile(todocx.degraded_path(self.out)))


class Testpandoc的输出不能变乱码(unittest.TestCase):
    r"""pandoc 不是 Python，PYTHONIOENCODING 管不着它。

    它是 Haskell 程序，Windows 上**报错时用系统本地代码页**（中文机器
    是 GBK），正文输出才是 UTF-8。一律按 UTF-8 硬解，报错里的中文路径
    就变成一片锟斤拷 —— 而 pandoc 报错时最需要看清的恰恰是路径。

    2026-09-02 真机上就是这么撞见的：转微信收到的讲义写不进去，
    界面上只有一堆问号，完全看不出是哪个文件、为什么。
    """

    def test_GBK编码的报错要解得出来(self):
        path = 'D:' + chr(92) + '微信wechat' + chr(92) + '讲义.docx'
        msg = 'pandoc.exe: ' + path + ': permission denied'
        self.assertEqual(todocx._dec(msg.encode('gbk')), msg)

    def test_UTF8的正常输出照旧(self):
        msg = '公式 613 个，表格 18 个'
        self.assertEqual(todocx._dec(msg.encode('utf-8')), msg)

    def test_两种编码都解不了也不抛异常(self):
        self.assertIsInstance(todocx._dec(bytes([0xff, 0xfe, 0x80, 0x81])), str)


class Test写不进去要说人话(unittest.TestCase):
    def setUp(self):
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)
        self.md = os.path.join(WORK, 'in.md')
        io.open(self.md, 'w', encoding='utf-8').write('只有文字，没有公式。')

    def tearDown(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def test_能写的目录判成能写(self):
        self.assertTrue(todocx._writable_dir(WORK))

    def test_不存在的目录判成不能写(self):
        self.assertFalse(todocx._writable_dir(os.path.join(WORK, '没有这个')))

    def test_输出目录写不进去时给出路(self):
        r"""微信、QQ 的下载目录是只读的，而软件默认「输出跟原 PDF 放一起」。
        不前置检查的话要等 pandoc 跑完才炸，抛的还是一段 Haskell backtrace。
        """
        orig = todocx._writable_dir
        todocx._writable_dir = lambda d: False
        self.addCleanup(lambda: setattr(todocx, '_writable_dir', orig))
        r = todocx.md_to_docx(self.md, os.path.join(WORK, 'out.docx'))
        self.assertFalse(r['ok'])
        self.assertIn('写不进去', r['error'])
        self.assertIn('更改', r['error'], '没告诉用户怎么办')


if __name__ == '__main__':
    unittest.main()
