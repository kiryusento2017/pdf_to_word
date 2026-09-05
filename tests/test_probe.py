# -*- coding: utf-8 -*-
r"""PDF 探测：有没有文字层、几页、能不能打开。

**为什么这是整个软件最先做的模块**：它决定界面上那句「文字版 / 扫描版」——
在转换之前就告诉用户「这份几乎不会出错」还是「这份会有错字」，
而不是等他打开 Word 才发现。

判据的来源是实测（2026-08-31）：这批讲义 PDF 都带文字层，
原文写的是「已知」，而工作台那边配了 method=ocr，把现成的文字层扔掉
当图片重新识别，才变成「己知」。走文字层就没有这个问题。

跑法：
    .venv\Scripts\python.exe -m unittest discover -s tests
"""
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import probe  # noqa: E402

WORK = os.path.join(ROOT, '_tmp', 'tests', 'probe')


def _make_pdf(path, pages, text=None):
    """造一份最小 PDF。text 为 None 则整页空白（当扫描版用）。"""
    import pymupdf
    doc = pymupdf.open()
    for _ in range(pages):
        pg = doc.new_page()
        if text:
            pg.insert_text((72, 100), text, fontsize=12)
    doc.save(path)
    doc.close()


class Test判文字层(unittest.TestCase):

    def setUp(self):
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)

    def tearDown(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def test_有文字层的判成文字版(self):
        p = os.path.join(WORK, 'a.pdf')
        _make_pdf(p, 3, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ 0123456789')
        r = probe.probe_pdf(p)
        self.assertTrue(r['ok'], r.get('error'))
        self.assertEqual(r['pages'], 3)
        self.assertTrue(r['has_text'], '有文字却判成了扫描版')
        self.assertEqual(r['kind'], 'text')

    def test_空白页判成扫描版(self):
        r"""没有文字层 = 只能走 OCR = 会有错字，界面上要提前标出来。"""
        p = os.path.join(WORK, 'b.pdf')
        _make_pdf(p, 2, None)
        r = probe.probe_pdf(p)
        self.assertTrue(r['ok'])
        self.assertFalse(r['has_text'])
        self.assertEqual(r['kind'], 'scan')

    def test_只有个别页有文字也算文字版(self):
        r"""真实讲义的第 1 页常是整版封面图（实测：第5讲物理第 1 页文字层 0 字符），
        不能因为第一页没字就把整份判成扫描版。"""
        import pymupdf
        p = os.path.join(WORK, 'c.pdf')
        doc = pymupdf.open()
        doc.new_page()                                  # 空白封面
        pg = doc.new_page()
        pg.insert_text((72, 100), 'real content here and then some more', fontsize=12)
        doc.save(p)
        doc.close()
        r = probe.probe_pdf(p)
        self.assertTrue(r['has_text'], '只有封面没字就被判成扫描版了')

    def test_文件不存在时给明白话而不是抛异常(self):
        r = probe.probe_pdf(os.path.join(WORK, '没有这个文件.pdf'))
        self.assertFalse(r['ok'])
        self.assertIn('找不到', r['error'])

    def test_不是PDF时也不炸(self):
        p = os.path.join(WORK, 'fake.pdf')
        with open(p, 'w', encoding='utf-8') as f:
            f.write('这不是 PDF')
        r = probe.probe_pdf(p)
        self.assertFalse(r['ok'])
        self.assertTrue(r['error'], '失败了却没给原因')

    def test_报告每页的字符数(self):
        r"""界面要能显示「第 3 页没有文字层」这种粒度，不能只给整份的结论。"""
        import pymupdf
        p = os.path.join(WORK, 'd.pdf')
        doc = pymupdf.open()
        pg = doc.new_page()
        pg.insert_text((72, 100), 'page one has plenty of characters', fontsize=12)
        doc.new_page()                                  # 第二页空白
        doc.save(p)
        doc.close()
        r = probe.probe_pdf(p)
        self.assertEqual(len(r['page_chars']), 2)
        self.assertGreater(r['page_chars'][0], 0)
        self.assertEqual(r['page_chars'][1], 0)
        self.assertEqual(r['scan_pages'], [2], '没能指出哪一页需要 OCR')


class Test阈值的依据(unittest.TestCase):
    r"""MIN_CHARS_PER_PAGE 不是拍脑袋定的。

    实测（2026-08-31）真实讲义：
        解不等式  第1页 190 字符、第2页 145、第3页 214
        第5讲物理 第1页 0（整版封面图）、第2页 340、第3页 867

    真实页要么 0、要么上百，**中间地带不存在**。所以阈值只要落在
    (0, 145) 区间内都等价；定 10 是为了容忍扫描件里偶尔混进的
    一两个水印字符，不让它们把整份误判成文字版。
    """

    def test_阈值落在实测的安全区间内(self):
        self.assertGreater(probe.MIN_CHARS_PER_PAGE, 2,
                           '太低会把扫描件的水印字符当成文字层')
        self.assertLess(probe.MIN_CHARS_PER_PAGE, 145,
                        '太高会把真实的正文页判成扫描版（实测最少的一页 145 字符）')


class Test批量探测(unittest.TestCase):

    def setUp(self):
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)

    def tearDown(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def test_一个坏文件不影响其余(self):
        r"""用户拖进来一个文件夹，里面混着一个坏 PDF —— 不能整批失败。"""
        good = os.path.join(WORK, 'good.pdf')
        _make_pdf(good, 1, 'good file with enough characters')
        bad = os.path.join(WORK, 'bad.pdf')
        with open(bad, 'w', encoding='utf-8') as f:
            f.write('坏的')
        rs = probe.probe_many([good, bad])
        self.assertEqual(len(rs), 2)
        self.assertTrue(rs[0]['ok'])
        self.assertFalse(rs[1]['ok'])

    def test_扫目录只收PDF(self):
        _make_pdf(os.path.join(WORK, 'a.pdf'), 1, 'enough characters here')
        with open(os.path.join(WORK, 'note.txt'), 'w', encoding='utf-8') as f:
            f.write('不是 PDF')
        got = probe.scan_dir(WORK)
        self.assertEqual([os.path.basename(p) for p in got], ['a.pdf'])

    def test_扫目录是递归的(self):
        r"""老师习惯按章节建子文件夹，只扫一层会漏掉大半。"""
        sub = os.path.join(WORK, '第一章')
        os.makedirs(sub)
        _make_pdf(os.path.join(sub, 'b.pdf'), 1, 'enough characters here')
        got = probe.scan_dir(WORK)
        self.assertEqual(len(got), 1)

    def test_太深了就不再往下走(self):
        r"""🔴 护栏，不是功能限制。

        用户可能把整个 C 盘拖进来 —— 那会让 os.walk 跑遍全盘、再逐份
        pymupdf.open 取文字，界面假死几分钟。默认 12 层是「正常用法
        永远碰不到」的量（按学科/年级/章节建最多三四层）。
        （2026-09-05 复查加的：models._find_snapshot 早就有这道护栏，
          这边一直没有。）
        """
        d = WORK
        for i in range(5):
            d = os.path.join(d, 'lv%d' % i)
            os.makedirs(d)
            _make_pdf(os.path.join(d, 'a%d.pdf' % i), 1,
                      'enough characters here')

        # 卡到 2 层：只看得见 lv0 和 lv1 里那两份
        self.assertEqual(len(probe.scan_dir(WORK, max_depth=2)), 2)
        # 默认深度足够宽松，五层全都找得到 —— 护栏不该漏掉真实文件
        self.assertEqual(len(probe.scan_dir(WORK)), 5)


if __name__ == '__main__':
    unittest.main()
