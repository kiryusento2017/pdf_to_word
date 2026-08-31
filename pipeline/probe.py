# -*- coding: utf-8 -*-
r"""PDF 探测：有没有文字层、几页、能不能打开。

转换之前先看一眼：几页、能不能打开、每页取不取得到文字。

⚠️ **`has_text` 不是质量保证**（2026-08-31 实测纠正）。
初版以为「有文字层 = 正文不会出错」，据此想让 MinerU 走 txt 模式。
实测把这个前提打掉了：

  · 那个「己知」错字**在原 PDF 的文字层里就是错的**（原书排版打错的字），
    txt / auto / ocr 三种模式都是 1 处，谁也消不掉
  · txt 模式反倒丢了 38% 的公式（131 vs 213）—— 文字层里没有公式

所以 `has_text` 只说明「这份能取到文字」，**不说明取到的文字是对的**。
界面上因此不显示「文字版 / 扫描版」这类质量暗示（那会让人以为标了文字版
就不用校对），只报客观事实：几页、哪几页没有文字层 —— 那些页整页当图识别，
出错概率更高，值得单独看一眼。

**不抛异常**：用户会拖进来加密的、损坏的、根本不是 PDF 的文件，
一个坏文件不能让整批失败。所有失败都折成 `{'ok': False, 'error': '人话'}`。
"""
import os

import pymupdf

# 一页有多少个字符才算「这页有文字层」。定 1 不行 —— 扫描件常被
# 塞进一两个水印字符；定太高又会漏掉真的很短的页（比如只有一道题的页）。
MIN_CHARS_PER_PAGE = 10

PDF_EXTS = ('.pdf',)


def probe_pdf(path):
    r"""探一份 PDF。返回 dict，永不抛异常。

    返回字段：
      ok          能不能读
      error       读不了的原因（人话，直接显示给用户）
      pages       页数
      page_chars  每页文字层字符数
      has_text    整份有没有文字层
      kind        'text' / 'scan'
      scan_pages  哪几页没有文字层（1 起数，给界面显示用）
    """
    if not os.path.isfile(path):
        return {'ok': False, 'error': '找不到这个文件：%s' % path,
                'path': path, 'pages': 0, 'page_chars': [],
                'has_text': False, 'kind': 'unknown', 'scan_pages': []}
    try:
        doc = pymupdf.open(path)
    except Exception as e:
        return {'ok': False, 'error': '打不开，可能不是 PDF 或文件已损坏（%s）'
                                     % str(e)[:80],
                'path': path, 'pages': 0, 'page_chars': [],
                'has_text': False, 'kind': 'unknown', 'scan_pages': []}
    try:
        if doc.needs_pass:
            return {'ok': False, 'error': '这份 PDF 有密码，请先解密',
                    'path': path, 'pages': doc.page_count, 'page_chars': [],
                    'has_text': False, 'kind': 'unknown', 'scan_pages': []}
        chars = []
        for i in range(doc.page_count):
            try:
                chars.append(len((doc[i].get_text() or '').strip()))
            except Exception:
                chars.append(0)          # 单页读不了就当没文字，不整份失败
    finally:
        doc.close()

    # 🔴 **只要有一页有文字层，整份就算文字版**。
    #    真实讲义的第 1 页常是整版封面图（实测：第5讲物理第 1 页文字层 0 字符），
    #    按「每页都要有」判会把整份误判成扫描版，白白走一遍 OCR 还引入错字。
    scan_pages = [i + 1 for i, n in enumerate(chars) if n < MIN_CHARS_PER_PAGE]
    has_text = any(n >= MIN_CHARS_PER_PAGE for n in chars)
    return {'ok': True, 'error': '', 'path': path,
            'pages': len(chars), 'page_chars': chars,
            'has_text': has_text, 'kind': 'text' if has_text else 'scan',
            'scan_pages': scan_pages}


def probe_many(paths):
    """批量探测。一个坏文件不影响其余 —— 用户拖进来的文件夹里什么都可能有。"""
    return [probe_pdf(p) for p in paths]


def scan_dir(root):
    """递归找出目录下所有 PDF。

    **递归**：老师习惯按章节建子文件夹，只扫一层会漏掉大半。
    """
    out = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in sorted(filenames):
            if fn.lower().endswith(PDF_EXTS):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)
