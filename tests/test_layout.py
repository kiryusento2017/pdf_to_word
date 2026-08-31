# -*- coding: utf-8 -*-
r"""版面两件事：图片尺寸、表格边框。

都是小蔡看了样张当场指出来的（2026-08-31）：
「为什么表格里面的图片转出来那么大」「为什么考情分析的表格没有」。

**图片太大的根因**：Pandoc 不知道图在原书里多大，就按「像素 ÷ 96 DPI」算，
890px 算出 9.27 英寸，超页宽后再压到页宽 —— 于是一张原本 4.4 英寸的插图
占满了整页。而 MinerU 切图用的是 200 DPI（源码 `DEFAULT_PDF_IMAGE_DPI = 200`），
按 200 换算才是原书里的真实大小。实测那张知识导图：
890 ÷ 200 = 4.45 英寸，PDF 里量出来 4.46 英寸。

**表格看不见的根因**：Pandoc 默认的 Table 样式**不画边框**（实测 tblBorders
在 tblPr 和 styles.xml 里都没有）。表格在、内容对、合并单元格也对，
但在 Word 里就是几行散着的文字 —— 用户说「没有」是对的，能看见才叫有。
"""
import io
import os
import shutil
import sys
import unittest
import zipfile

from lxml import etree

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import todocx  # noqa: E402

W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
WORK = os.path.join(ROOT, '_tmp', 'tests', 'layout')
EMU_PER_INCH = 914400.0


def _make_png(path, w, h):
    """造一张指定像素尺寸的 PNG，不依赖 PIL。"""
    import struct
    import zlib

    def chunk(tag, data):
        c = tag + data
        return (struct.pack('>I', len(data)) + c
                + struct.pack('>I', zlib.crc32(c) & 0xFFFFFFFF))

    raw = b''.join(b'\x00' + b'\xff\xff\xff' * w for _ in range(h))
    png = (b'\x89PNG\r\n\x1a\n'
           + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(raw))
           + chunk(b'IEND', b''))
    with open(path, 'wb') as f:
        f.write(png)


class Test图片按MinerU的DPI还原尺寸(unittest.TestCase):

    def test_DPI取的是MinerU的官方默认值(self):
        r"""不是拍脑袋定的：mineru/utils/pdf_image_tools.py 里
        `DEFAULT_PDF_IMAGE_DPI = 200`。实测那张知识导图 890px，
        按 200 算 4.45 英寸，PDF 里量出来 4.46 英寸。"""
        self.assertEqual(todocx.MINERU_IMAGE_DPI, 200)

    def test_按DPI算出英寸(self):
        self.assertAlmostEqual(todocx.px_to_inch(890), 4.45, places=2)
        self.assertAlmostEqual(todocx.px_to_inch(400), 2.0, places=2)

    def test_超过正文宽度就压到正文宽(self):
        r"""偶尔有跨整页的大图（实测有 1334px = 6.67 英寸的）。
        超了要压，不然 Word 里会溢出到页边距外。"""
        self.assertLessEqual(todocx.px_to_inch(4000), todocx.MAX_IMAGE_INCH)

    def test_小图标不会被放大(self):
        r"""原书里那些 15x15pt 的小图标（MinerU 切出来约 42px），
        按 200 DPI 算是 0.21 英寸 —— 必须保持小，不能拉成一整行。"""
        self.assertLess(todocx.px_to_inch(42), 0.3)


@unittest.skipUnless(todocx.pandoc_available(), '没有 pandoc')
class Test产物里的图片尺寸(unittest.TestCase):

    def setUp(self):
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)

    def tearDown(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def _extents(self, docx_path):
        with zipfile.ZipFile(docx_path) as z:
            xml = z.read('word/document.xml').decode('utf-8')
        import re
        return [(int(a) / EMU_PER_INCH, int(b) / EMU_PER_INCH)
                for a, b in re.findall(r'<wp:extent cx="(\d+)" cy="(\d+)"', xml)]

    def test_890像素的图显示成4点45英寸(self):
        r"""这是小蔡报的那张知识导图的真实尺寸。修之前是 5.83 英寸。"""
        _make_png(os.path.join(WORK, 'big.png'), 890, 904)
        md = os.path.join(WORK, 'in.md')
        io.open(md, 'w', encoding='utf-8').write('![](big.png)\n')
        out = os.path.join(WORK, 'out.docx')
        r = todocx.md_to_docx(md, out)
        self.assertTrue(r['ok'], r.get('error'))
        ex = self._extents(out)
        self.assertEqual(len(ex), 1)
        self.assertAlmostEqual(ex[0][0], 4.45, places=1,
                               msg='宽度不对，实际 %.2f 英寸' % ex[0][0])

    def test_小图标保持小(self):
        _make_png(os.path.join(WORK, 'icon.png'), 42, 42)
        md = os.path.join(WORK, 'in.md')
        io.open(md, 'w', encoding='utf-8').write('文字 ![](icon.png) 文字\n')
        out = os.path.join(WORK, 'out.docx')
        r = todocx.md_to_docx(md, out)
        self.assertTrue(r['ok'], r.get('error'))
        ex = self._extents(out)
        self.assertLess(ex[0][0], 0.3, '小图标被放大了：%.2f 英寸' % ex[0][0])


@unittest.skipUnless(todocx.pandoc_available(), '没有 pandoc')
class Test表格有边框(unittest.TestCase):

    def setUp(self):
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)
        self.md = os.path.join(WORK, 'in.md')
        io.open(self.md, 'w', encoding='utf-8').write(
            '<table><tr><td>年份</td><td>分值</td></tr>'
            '<tr><td>2024</td><td>5</td></tr></table>\n')
        self.out = os.path.join(WORK, 'out.docx')

    def tearDown(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def test_表格画得出线(self):
        r"""小蔡看样张说「考情分析的表格没有」—— 表格其实在，
        只是 Pandoc 默认样式不画边框，看着就不像表格。"""
        r = todocx.md_to_docx(self.md, self.out)
        self.assertTrue(r['ok'], r.get('error'))
        with zipfile.ZipFile(self.out) as z:
            doc = etree.fromstring(z.read('word/document.xml'))
        tbl = doc.find('.//{%s}tbl' % W_NS)
        self.assertIsNotNone(tbl, '表格没了')
        borders = tbl.find('.//{%s}tblBorders' % W_NS)
        self.assertIsNotNone(borders, '表格没有边框，在 Word 里看不出是表格')
        kinds = {etree.QName(c).localname for c in borders}
        for need in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
            self.assertIn(need, kinds, '缺 %s 边框' % need)

    def test_内容没被边框搞坏(self):
        r = todocx.md_to_docx(self.md, self.out)
        with zipfile.ZipFile(self.out) as z:
            xml = z.read('word/document.xml').decode('utf-8')
        self.assertIn('年份', xml)
        self.assertIn('2024', xml)
        self.assertEqual(xml.count('<w:tbl>'), 1)


if __name__ == '__main__':
    unittest.main()
