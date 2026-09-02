# -*- coding: utf-8 -*-
r"""Markdown → Word。

**两条路的分工**（小蔡 2026-08-31 定：有 XSL 先用 XSL，没有才启用内置 Pandoc）：

    Pandoc 出骨架（段落、表格、图片，以及它自己转的那批公式）
      ↓
    有 XSL → 把骨架里的公式逐个换成 XSL 转出来的
    没 XSL → 就用 Pandoc 转的那批

为什么这么切：Pandoc 一条命令就吐出完整 docx，**不给插入点**。要让 XSL 真正参与，
要么在它的产物上做替换（本文件的做法），要么抛开它自己写段落/样式/表格/合并单元格/
图片嵌入/公式插入的全套生成器（几百行）。前者能达到同样的优先级，代价小一个数量级。

替换靠**顺序一一对应**：源文里第 n 个公式，对应产物里第 n 个 `m:oMath`。
数量对不上就整批不换、保留 Pandoc 的结果，并在报告里写明——
宁可用次优的那条路，也不能张冠李戴把公式换错位置。

Pandoc 是 GPL，我们**当独立程序调用**（起子进程、传文件路径），不触发传染；
分发义务（附协议全文、注明来源）由 `runtime/pandoc/COPYRIGHT.txt` 履行。
"""
import io
import json
import locale
import os
import re
import shutil
import subprocess
import tempfile
import zipfile

from lxml import etree

import tomath

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PANDOC = os.path.join(ROOT, 'runtime', 'pandoc', 'pandoc.exe')

_M_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

_TABLE = re.compile(r'<table.*?</table>', re.S)

# 🔴 **MinerU 切图用 200 DPI**，不是 Pandoc 假定的 96。
#    依据是 MinerU 源码 `mineru/utils/pdf_image_tools.py`：
#        DEFAULT_PDF_IMAGE_DPI = 200
#    实测印证：解不等式那张知识导图 890px，按 200 算 4.45 英寸，
#    在原 PDF 里量出来是 4.46 英寸。
#    不按它换算的后果：Pandoc 按 96 DPI 算出 9.27 英寸、超页宽后压到页宽，
#    一张原本 4.4 英寸的插图占满整页 —— 小蔡看样张第一眼就发现了。
MINERU_IMAGE_DPI = 200

# A4 宽 8.27 英寸，常规页边距各 0.8 英寸左右，正文宽约 6.6 英寸。
# 实测原书里确实有跨整个正文宽的大图（1334px = 6.67 英寸），超了要压。
MAX_IMAGE_INCH = 6.5

# 表格边框。Pandoc 默认的 Table 样式**不画线**（实测 tblPr 和 styles.xml
# 里都没有 tblBorders），于是表格在 Word 里就是几行散着的文字。
# 小蔡看样张说「考情分析的表格没有」—— 表格其实在，只是看不见。
_TBL_BORDERS = (
    '<w:tblBorders %s>'
    '<w:top w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
    '<w:left w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
    '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
    '<w:right w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
    '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
    '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
    '</w:tblBorders>')

_BS_DOLLAR = chr(92) + chr(36)          # 反斜杠 + 美元号
_DOLLAR = chr(36)


def pandoc_available():
    return os.path.isfile(PANDOC)


def _dec(raw):
    r"""解 pandoc 的输出。

    🔴 **pandoc 不是 Python，PYTHONIOENCODING 管不着它。**
    它是 Haskell 程序，Windows 上报错时**用系统本地代码页**（中文机器
    是 GBK），而正文输出是 UTF-8。一律按 UTF-8 硬解，报错里的中文路径
    就变成一片锟斤拷 —— 而 pandoc 报错时最需要看清的恰恰是路径
    （「哪个文件写不进去」）。

    先试 UTF-8（正文走这条），失败再退本地编码。
    """
    for enc in ('utf-8', locale.getpreferredencoding(False), 'gbk'):
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode('utf-8', 'replace')


def _writable_dir(d):
    """这个文件夹能不能写。真写一个探针文件试 —— Windows 上
    os.access(W_OK) 对只读目录会误报成能写。"""
    probe = os.path.join(d, '.p2w_write_probe')
    try:
        with io.open(probe, 'w', encoding='utf-8') as f:
            f.write('x')
        os.remove(probe)
        return True
    except Exception:
        try:
            os.remove(probe)
        except Exception:
            pass
        return False


def _run_pandoc(args, stdin_text=None, cwd=None):
    p = subprocess.run([PANDOC] + args,
                       input=(stdin_text or '').encode('utf-8'),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
    return p.returncode, _dec(p.stdout), _dec(p.stderr)


def _html_tables_to_markdown(text, cwd=None):
    r"""把 MinerU 输出的 HTML 表格转成 markdown 表格。

    不转的话 pandoc 会把它当「原样保留的 HTML」，而 docx 不支持 raw HTML，
    整块丢弃 —— 实测 w:tbl = 0，两张表全没了。

    🔴 **必须开 `html+tex_math_dollars`**：不开的话 html reader 不认识
    美元符号包起来的是数学，输出 markdown 时把 `\Delta` 转义成 `\\Delta`、
    `^` 转义成 `\^`，LaTeX 当场报废。实测：213 个公式掉到 202，残留 11 个源码。
    """
    def one(m):
        # --columns 定得很大：pandoc 默认按 72 列折行，而表格单元格里
        # 一个图片引用光路径就 80+ 字符（MinerU 用 sha256 当文件名），
        # 再加上 {width=...} 属性轻松破百 —— 一折行表格结构就废了，
        # 单元格里的图当场丢失。实测：解不等式的图从 4 张掉到 3 张。
        rc, out, _ = _run_pandoc(['-f', 'html+tex_math_dollars', '-t', 'markdown',
                                  '--columns=9999'],
                                 m.group(0), cwd=cwd)
        if rc != 0 or not out.strip():
            return m.group(0)
        return '\n\n' + out.strip().replace(_BS_DOLLAR, _DOLLAR) + '\n\n'
    return _TABLE.sub(one, text)


def _walk_math(node, out):
    """递归遍历 pandoc AST，按文档顺序收集 Math 节点的 LaTeX 源码。"""
    if isinstance(node, dict):
        if node.get('t') == 'Math':
            c = node.get('c') or []
            if len(c) == 2 and isinstance(c[1], str):
                out.append(c[1])
            return
        for v in node.values():
            _walk_math(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_math(v, out)


# ── 公式：门牌号，不是排队顺序 ──────────────────────────────────────────
#
# 改造前：让 pandoc 照常转公式，出 docx 后按顺序把 m:oMath 一个个换成
# XSL 的结果（zip(found, omml_list)）。这依赖一个 pandoc **自己不保证**
# 的前提：它在 AST 里数出的公式数 == 它渲染进 docx 的 oMath 数。
#
# 2026-09-02 真机上就对不上：613 对 612。一个 OCR 粘连出来的 \gtan
# 让 pandoc 在渲染时把那个公式退化成纯文本，从缺口往后 70 个公式全部
# 张冠李戴 —— 只能整份判失败。11 份真实讲义里 1 份中招。
#
# 改造后：喂给 pandoc 之前就把每个公式换成一个**唯一的占位符**，
# pandoc 全程只当它是普通文字搬运；出 docx 后按占位符精确定位塞回去。
#     · 不依赖数量相等
#     · 某个公式 XSL 转不了，只影响它自己
#     · 报错能说出是第几个、内容是什么
#
# 占位符用 **Code 节点**而不是普通文本：Code 在 docx 里带 VerbatimChar
# 字符样式，**必然独占一个 <w:r>** —— 省掉「占位符被拆进多个 run」
# 这个最容易翻车的处理。
_PH_FMT = '⟦MATH%04d⟧'
_PH_RE = re.compile('⟦MATH([0-9]{4})⟧')


def _ast_swap_math(node, texs):
    """遍历 AST，把每个 Math 节点原地换成占位符，按文档顺序收集 LaTeX。

    返回值就是 texs 被填充的内容；node 是原地改的。
    """
    if isinstance(node, dict):
        if node.get('t') == 'Math':
            c = node.get('c') or []
            if len(c) == 2 and isinstance(c[1], str):
                ph = _PH_FMT % len(texs)
                texs.append(c[1])
                node['t'] = 'Code'
                node['c'] = [['', [], []], ph]
            return
        for v in node.values():
            _ast_swap_math(v, texs)
    elif isinstance(node, list):
        for v in node:
            _ast_swap_math(v, texs)


def _fill_placeholders(docx_path, omml_list, texs):
    """把 docx 里的占位符换成 OMML。

    返回 (换掉几个, 没转成的下标列表, 没找到占位符的下标列表)。

    XSL 没转出来的那些，把 LaTeX 原文写回去 —— 总比留一个
    ⟦MATH0543⟧ 让人莫名其妙强，至少看得出原式是什么。
    """
    with zipfile.ZipFile(docx_path) as z:
        names = z.namelist()
        blobs = {n: z.read(n) for n in names}

    doc = etree.fromstring(blobs['word/document.xml'])
    wt = '{%s}t' % W_NS

    slot = {}
    for t in doc.iter(wt):
        m = _PH_RE.match((t.text or '').strip())
        if m:
            slot[int(m.group(1))] = t

    n = 0
    failed = []
    missing = []
    for i, omml in enumerate(omml_list):
        t = slot.get(i)
        if t is None:
            missing.append(i)
            continue
        if omml is None:
            t.text = texs[i] if i < len(texs) else ''
            failed.append(i)
            continue
        run = t.getparent()
        parent = run.getparent() if run is not None else None
        if parent is None:
            missing.append(i)
            continue
        copy = etree.fromstring(etree.tostring(omml))
        copy.tail = run.tail
        parent.replace(run, copy)
        n += 1

    blobs['word/document.xml'] = etree.tostring(
        doc, xml_declaration=True, encoding='UTF-8', standalone=True)
    tmp = docx_path + '.tmp'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in names:
            z.writestr(name, blobs[name])
    os.replace(tmp, docx_path)
    return n, failed, missing


def px_to_inch(px):
    """图片像素 → 在 Word 里该显示多宽（英寸）。超过正文宽就压到正文宽。"""
    return min(px / float(MINERU_IMAGE_DPI), MAX_IMAGE_INCH)


def _png_jpeg_size(path):
    """读 PNG / JPEG 的像素尺寸。不引 PIL —— 为一个读文件头的活儿加依赖不值。"""
    try:
        with open(path, 'rb') as f:
            head = f.read(32)
            if head[:8] == b'\x89PNG\r\n\x1a\n':
                return (int.from_bytes(head[16:20], 'big'),
                        int.from_bytes(head[20:24], 'big'))
            if head[:2] == b'\xff\xd8':
                f.seek(0)
                d = f.read()
                i = 2
                while i < len(d) - 9:
                    if d[i] != 0xFF:
                        i += 1
                        continue
                    if d[i + 1] in (0xC0, 0xC1, 0xC2, 0xC3):
                        return (int.from_bytes(d[i + 7:i + 9], 'big'),
                                int.from_bytes(d[i + 5:i + 7], 'big'))
                    seg = int.from_bytes(d[i + 2:i + 4], 'big')
                    if seg <= 0:
                        break
                    i += 2 + seg
    except Exception:
        pass
    return 0, 0


def _size_images(text, cwd):
    r"""给 Markdown 里的图片补上显示宽度。

    Pandoc 不知道图在原书里多大，就按「像素 ÷ 96 DPI」算。而 MinerU 切图
    用的是 200 DPI，按 96 算等于凭空放大到 2 倍多，超页宽后再压到页宽 ——
    结果每张插图都占满整页。给它写明宽度就没这回事了。

    已经带了尺寸属性的不动（尊重上游的显式指定）。
    """
    def one(m):
        alt, path, attr = m.group(1), m.group(2), m.group(3) or ''
        if 'width' in attr:
            return m.group(0)
        full = path if os.path.isabs(path) else os.path.join(cwd or '.', path)
        w, _h = _png_jpeg_size(full)
        if not w:
            return m.group(0)
        return '![%s](%s){width="%.2fin"}' % (alt, path, px_to_inch(w))
    return re.sub(r'!\[([^\]]*)\]\(([^)\s]+)\)(\{[^}]*\})?', one, text)


def _add_table_borders(docx_path):
    """给产物里每个表格补上边框。返回补了几个。

    在 XML 上补，不用自定义 reference.docx —— 后者要维护一个二进制文件，
    没法审查、没法 diff，改一条边框颜色都得重新生成一遍。
    """
    with zipfile.ZipFile(docx_path) as z:
        names = z.namelist()
        blobs = {n: z.read(n) for n in names}

    xml = blobs['word/document.xml'].decode('utf-8')
    if '<w:tbl>' not in xml:
        return 0

    ns = 'xmlns:w="%s"' % W_NS
    borders = _TBL_BORDERS % ''

    # tblPr 里已经有 tblBorders 的不动
    def one(m):
        pr = m.group(0)
        if 'tblBorders' in pr:
            return pr
        return pr.replace('</w:tblPr>', borders + '</w:tblPr>', 1)

    new_xml, n = re.subn(r'<w:tblPr>.*?</w:tblPr>', one, xml, flags=re.S)
    if n == 0:
        return 0
    blobs['word/document.xml'] = new_xml.encode('utf-8')

    tmp = docx_path + '.tmp'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in names:
            z.writestr(name, blobs[name])
    os.replace(tmp, docx_path)
    return n


EMU_PER_INCH = 914400


def _img_targets(html, cwd):
    """按出现顺序，算出 HTML 里每张图该显示多大（EMU）。

    顺序即对应关系：docx 里第 n 张图就是 HTML 里第 n 个 <img>。
    """
    out = []
    for m in re.finditer(r'<img\b[^>]*>', html):
        src = re.search(r'src="([^"]+)"', m.group(0))
        if not src:
            out.append(None)
            continue
        p = src.group(1)
        full = p if os.path.isabs(p) else os.path.join(cwd or '.', p)
        w, h = _png_jpeg_size(full)
        if not w or not h:
            out.append(None)
            continue
        inch_w = px_to_inch(w)
        inch_h = inch_w * (h / float(w))          # 保持宽高比
        out.append((int(inch_w * EMU_PER_INCH), int(inch_h * EMU_PER_INCH)))
    return out


def _resize_images(docx_path, targets):
    """按顺序改 docx 里每张图的显示尺寸。返回改了几张。

    OOXML 里图片尺寸有两处要同步：<wp:extent> 和 <a:ext>，
    只改一处 Word 会按另一处显示，等于没改。
    """
    if not targets:
        return 0
    with zipfile.ZipFile(docx_path) as z:
        names = z.namelist()
        blobs = {n: z.read(n) for n in names}
    xml = blobs['word/document.xml'].decode('utf-8')

    n = [0]

    def one(m):
        i = n[0]
        n[0] += 1
        if i >= len(targets) or targets[i] is None:
            return m.group(0)
        cx, cy = targets[i]
        return '<wp:extent cx="%d" cy="%d"' % (cx, cy)

    xml2, cnt = re.subn(r'<wp:extent cx="\d+" cy="\d+"', one, xml)
    if not cnt:
        return 0

    # <a:ext> 跟 <wp:extent> 一一对应，同步改
    n[0] = 0

    def one_ext(m):
        i = n[0]
        n[0] += 1
        if i >= len(targets) or targets[i] is None:
            return m.group(0)
        cx, cy = targets[i]
        return '<a:ext cx="%d" cy="%d"' % (cx, cy)

    xml2, _ = re.subn(r'<a:ext cx="\d+" cy="\d+"', one_ext, xml2)

    blobs['word/document.xml'] = xml2.encode('utf-8')
    tmp = docx_path + '.tmp'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in names:
            z.writestr(name, blobs[name])
    os.replace(tmp, docx_path)
    return sum(1 for t in targets[:cnt] if t is not None)


def _build_docx(md_path, out_path, prefer_xsl=True, resource_path=None):
    r"""把一份 Markdown 转成 Word。返回报告 dict，**不抛异常**。

    报告字段：
      ok / error          成不成、为什么
      formulas_src        源文里有几个公式
      formulas_replaced   有几个被换成了 XSL 的结果
      math_engine         'xsl' 或 'pandoc' —— 这次实际用的哪条路
      math_note           走了哪条路的说明。**只是说明，不是错误** ——
                          2026-09-01 起所有降级都写进 rep['error'] 并判失败，
                          math_note 只在成功时记「公式走 XSL（N/N 成功）」。
                          （它原来的注释写着「必须报给用户」，而 summary_line
                           不读它、前端 0 处引用 —— 说明写进了没人看的字段。）
      tables / images     产物里的表格数、图片数
    """
    rep = {'ok': False, 'error': '', 'formulas_src': 0, 'formulas_replaced': 0,
           'math_engine': 'pandoc', 'math_note': '', 'tables': 0, 'images': 0,
           'tables_bordered': 0, 'images_resized': 0}
    if not pandoc_available():
        rep['error'] = '找不到内置 pandoc：%s' % PANDOC
        return rep
    if not os.path.isfile(md_path):
        rep['error'] = '找不到输入文件：%s' % md_path
        return rep
    # 🔴 输出文件夹能不能写，**在花时间之前**就要知道。
    #    微信、QQ 的下载目录是只读的（文件权限 -r--r--r--），而软件默认
    #    「输出跟原 PDF 放一起」—— 用户直接转微信收到的讲义时必然撞上。
    #    不前置检查的话，要等 pandoc 跑完才炸，而它抛的是
    #    「withBinaryFile: permission denied」加一段 Haskell backtrace，
    #    没人看得懂，更不知道该怎么办。
    dest_dir = os.path.dirname(os.path.abspath(out_path)) or '.'
    if not _writable_dir(dest_dir):
        rep['error'] = ('输出文件夹写不进去：%s' % dest_dir + chr(10)
                        + '微信、QQ 的下载目录通常是只读的。'
                        '点工具条上的「更改」换一个输出位置，再试一次。')
        return rep

    src_dir = resource_path or os.path.dirname(os.path.abspath(md_path))
    try:
        with io.open(md_path, encoding='utf-8') as f:
            src = f.read()
    except Exception as e:
        rep['error'] = '读不了输入文件：%s' % str(e)[:120]
        return rep

    # 🔴 **先把公式换成占位符，再交给 pandoc**。
    #    从 AST 层换，不用猜「pandoc 怎么断句」—— 位置天然精确，
    #    也就不再需要 _extract_tex_in_order 那条「数出来的数必须等于
    #    产物里的 oMath 数」的脆弱前提。
    rc, astjson, err = _run_pandoc(
        ['-f', 'markdown+tex_math_dollars+raw_html', '-t', 'json'],
        src, cwd=src_dir)
    if rc != 0 or not astjson.strip():
        rep['error'] = 'pandoc（md→AST）失败：%s' % (err.strip()[:200] or rc)
        return rep
    try:
        ast = json.loads(astjson)
    except Exception as e:
        rep['error'] = 'pandoc 的 AST 读不了：%s' % str(e)[:120]
        return rep
    texs = []
    if prefer_xsl:
        # 走占位符：pandoc 从此不碰公式，出 docx 后按门牌号填回去。
        _ast_swap_math(ast.get('blocks', ast), texs)
        html_src, html_from = json.dumps(ast), 'json'
    else:
        # 🔴 prefer_xsl=False 是调用方明确说「我就要 pandoc 转的公式」
        #    （测试和批量脚本靠这条）。这时**不能换占位符** ——
        #    换了没人填回去，用户会拿到满屏 ⟦MATH0001⟧。
        #    所以这条路原样走老流程，只借 AST 数一下有几个公式。
        _walk_math(ast.get('blocks', ast), texs)
        html_src, html_from = src, 'markdown+tex_math_dollars+raw_html'
    rep['formulas_src'] = len(texs)

    # 🔴 **走 HTML 中转**，不把表格转成 markdown。
    #    旧流程是 md → 表格转markdown → 给图片加 {width=...} → docx，
    #    结果 markdown 多行表格的列宽在转换那刻就定死了，之后加宽度属性
    #    撑爆列宽、表格解析错乱 —— 实测解不等式的图从 4 张掉到 3 张。
    #    HTML 没有列宽这回事，表格原样带过去，图一张不少。
    tmpdir = tempfile.mkdtemp(prefix='pdf2word_')
    try:
        # 从改过的 AST 出发。公式已经是占位符文本，pandoc 只管搬运。
        rc, html, err = _run_pandoc(
            ['-f', html_from, '-t', 'html', '--mathml'], html_src, cwd=src_dir)
        if rc != 0 or not html.strip():
            rep['error'] = 'pandoc（AST→html）失败：%s' % (err.strip()[:200] or rc)
            return rep
        tmp_html = os.path.join(tmpdir, 'input.html')
        with io.open(tmp_html, 'w', encoding='utf-8') as f:
            f.write(html)
        rc, _out, err = _run_pandoc(
            [tmp_html, '-f', 'html+tex_math_dollars', '-t', 'docx',
             '-o', out_path, '--resource-path', src_dir], cwd=src_dir)
        if rc != 0 or not os.path.isfile(out_path):
            # 🔴 permission denied 翻成人话。pandoc 抛的是
            #    「withBinaryFile: permission denied」加一段 Haskell
            #    backtrace —— 老师看不懂，更猜不到「把 Word 关掉」。
            #    最常见的两种：那份 Word 正开着；输出目录是只读的
            #    （微信/QQ 的下载目录就是）。
            low = (err or '').lower()
            if 'permission denied' in low or 'access is denied' in low:
                rep['error'] = (
                    '写不了这个文件：%s' % out_path + chr(10)
                    + '多半是它正被 Word 打开着 —— 关掉那个 Word 再试。'
                    + '要是没开着，就是这个文件夹不让写（微信、QQ 的下载'
                    + '目录通常是只读的），点工具条上的「更改」换个输出位置。')
            else:
                rep['error'] = ('pandoc（html→docx）失败：%s'
                                % (err.strip()[:200] or rc))
            return rep
        img_targets = _img_targets(html, src_dir)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # 图片尺寸：pandoc 按 96 DPI 算，而 MinerU 切图是 200 DPI，
    # 不改的话每张插图都被放大到两倍多（小蔡一眼就看出来了）。
    rep['images_resized'] = _resize_images(out_path, img_targets)

    # ── 公式必须走 XSL（小蔡 2026-09-01 改定）────────────────────────
    # 🔴 这三条以前都是「记一句话，照常出 Word」。那等于让用户拿到一份
    #    含 ⌀（Pandoc 把空集 ∅ 转错了）的次等产物却毫不知情 —— 而
    #    界面上那句提示挤在 150px 宽的省略号里，多半根本看不见。
    #    门口拦了「完全没有 Office」，屋里这三条也必须拦，否则立论只贯彻了一半。
    if prefer_xsl and texs:
        if not tomath.xsl_available():
            rep['error'] = ('这台电脑没有微软 Office 的 MML2OMML.XSL，'
                            '公式转不成 Word 原生公式。装上 Office 再试。')
            return rep
        elif not tomath.node_available():
            rep['error'] = ('缺少 Node.js —— 公式的第一步转换要用到它，'
                            '而这台电脑上没有。到 nodejs.org 装一个 LTS 版本即可。')
            return rep
        else:
            omml = tomath.batch_to_omml(texs)
            n, failed, missing = _fill_placeholders(out_path, omml, texs)
            rep['math_engine'] = 'xsl'
            rep['formulas_replaced'] = n
            rep['math_note'] = ('公式走 Office 的 MML2OMML.XSL（%d/%d 成功）'
                                % (n, len(texs)))
            # 一个都没换成 = XSL 这条链整个坏了，那才是真失败。
            if n == 0:
                rep['error'] = ('一个公式都没能转成 Word 原生公式（共 %d 个）。'
                                '原因：%s'
                                % (len(texs), tomath.last_error()[:160]))
                return rep
            bad = sorted(failed + missing)
            if bad:
                # 🔴 **少数几个转不成，不再废掉整份**。
                #    改造前这里判整份失败，理由是「按序替换会让其后所有
                #    公式错位」。占位符定位之后错位不可能发生了 ——
                #    坏的那几个只影响它自己，132 张图、18 个表、612 个
                #    已经转好的公式没有理由陪葬。
                #    但必须说清楚是**哪几个**：改造前只说「有 N 个」，
                #    等于让用户在几百个公式里自己猜。
                where = '、'.join(
                    '第 %d 个（%s）' % (i + 1, (texs[i] or '')[:40])
                    for i in bad[:3])
                rep['math_note'] += (
                    '；%s%s 没转成，那几处保留了 LaTeX 原文'
                    % (where, ' 等 %d 个' % len(bad) if len(bad) > 3 else ''))
    elif not prefer_xsl:
        rep['math_note'] = '按调用方要求跳过 XSL，公式由 Pandoc 转换'

    rep['tables_bordered'] = _add_table_borders(out_path)

    try:
        with zipfile.ZipFile(out_path) as z:
            xml = z.read('word/document.xml').decode('utf-8')
            rep['images'] = len([n for n in z.namelist()
                                 if n.startswith('word/media/')])
        rep['tables'] = xml.count('<w:tbl>')
    except Exception:
        pass
    rep['ok'] = True
    return rep


def degraded_path(out_path):
    """判失败的产物换成什么名字。一眼就能跟正品分开。"""
    root, ext = os.path.splitext(out_path)
    return root + '【公式未完全转换】' + ext


def md_to_docx(md_path, out_path, prefer_xsl=True, resource_path=None):
    r"""把一份 Markdown 转成 Word。见 `_build_docx` 的完整说明。

    这层只多做一件事：**判失败就把产物改名，而不是删掉**。

    2026-09-02 查出来的：`_build_docx` 有四条失败路径发生在 pandoc
    已经把 docx 写出来之后（没 XSL、没 node、部分公式转不了、数量对不上），
    以前直接 return，那份文件就留在输出目录里了。界面说「失败」，
    老师去目录一看躺着一份能双击打开、里面有内容的 Word —— 多半就当
    成功了，而那正是我们判失败要拦下的次等品（Pandoc 把空集 ∅ 转成 ⌀）。

    最初的做法是删掉它。**手段超出了目的**：要防的是「被当成正品」，
    不是「这份文件存在」。转一份要四分钟，因为一个公式没转成就把
    132 张图、18 个表、612 个已经转好的公式一起扔掉，代价太大了。
    改名之后谁也不会把它当成品，而它照样能打开、能用。

    🔴 改不了名的时候必须**说出来**。以前那里是 `except OSError: pass`，
       而删不掉的典型场景正是「文件被 Word 打开着」—— 于是原名的次等品
       原地不动，界面只说一句「失败」。静默失守比不拦更糟。
    """
    rep = _build_docx(md_path, out_path, prefer_xsl=prefer_xsl,
                      resource_path=resource_path)
    if not rep.get('ok'):
        rep['degraded'] = ''
        if os.path.isfile(out_path):
            dst = degraded_path(out_path)
            try:
                os.replace(out_path, dst)
                rep['degraded'] = dst
            except OSError as e:
                rep['error'] = (rep.get('error') or '') + (
                    chr(10) + chr(10)
                    + '⚠️ 另外：这份没转好的文件留在了 %s，而且改不成'
                      '带标记的名字（%s）—— 它多半正被 Word 打开着。'
                      '别把它当成品用。' % (out_path, str(e)[:80]))
    return rep
