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

# 只认行内 $...$。与 tomath 同一口径。
_INLINE = re.compile(r'(?<!\$)\$(?!\$)([^\$\n]+?)\$(?!\$)')
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


def _run_pandoc(args, stdin_text=None, cwd=None):
    p = subprocess.run([PANDOC] + args,
                       input=(stdin_text or '').encode('utf-8'),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
    return (p.returncode,
            p.stdout.decode('utf-8', 'replace'),
            p.stderr.decode('utf-8', 'replace'))


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


def _extract_tex_in_order(text, cwd=None):
    r"""按文档顺序取出所有公式的 LaTeX 源码。

    🔴 **问 pandoc 要，不自己用正则数**。第一版用正则数行内 $...$，
    结果跟产物里的 m:oMath 数量对不上（185 对 186、806 对 808、187 对 201），
    11 份里 9 份因此退回 pandoc —— XSL 优先形同虚设。

    根子是**我在猜 pandoc 怎么断句**：我的正则不允许公式跨行（[^$
]），
    pandoc 允许。判据只要有一丝不同，数量就对不上。
    从它自己的 AST 里取，顺序与数量必然一致。
    """
    rc, out, _err = _run_pandoc(
        ['-f', 'markdown+tex_math_dollars+raw_html', '-t', 'json'],
        text, cwd=cwd)
    if rc != 0 or not out.strip():
        return [m.group(1) for m in _INLINE.finditer(text)]   # 退回正则，聊胜于无
    try:
        ast = json.loads(out)
    except Exception:
        return [m.group(1) for m in _INLINE.finditer(text)]
    got = []
    _walk_math(ast.get('blocks', ast), got)
    return got


def count_omath(docx_path):
    """产物里有几个 Word 原生公式对象。给报告和测试用。"""
    try:
        with zipfile.ZipFile(docx_path) as z:
            xml = z.read('word/document.xml').decode('utf-8')
        return xml.count('<m:oMath>') + xml.count('<m:oMath ')
    except Exception:
        return -1


def _replace_omath(docx_path, omml_list):
    r"""把 docx 里的 m:oMath 按顺序换成给定的那批。返回换掉几个。

    只在数量完全一致时才动手 —— 调用方已经校验过，这里再断言一次。
    """
    with zipfile.ZipFile(docx_path) as z:
        names = z.namelist()
        blobs = {n: z.read(n) for n in names}

    doc = etree.fromstring(blobs['word/document.xml'])
    found = doc.findall('.//{%s}oMath' % _M_NS)
    if len(found) != len(omml_list):
        return 0

    n = 0
    for old, new in zip(found, omml_list):
        if new is None:
            continue                     # 这一个 XSL 没转出来，留 pandoc 的
        parent = old.getparent()
        if parent is None:
            continue
        # 新节点可能带自己的命名空间声明，lxml 会处理好；tail 要保住，
        # 丢了会让相邻文字粘在一起
        copy = etree.fromstring(etree.tostring(new))
        copy.tail = old.tail
        parent.replace(old, copy)
        n += 1

    blobs['word/document.xml'] = etree.tostring(
        doc, xml_declaration=True, encoding='UTF-8', standalone=True)

    tmp = docx_path + '.tmp'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in names:               # 保持原顺序，Word 对此不敏感但稳妥
            z.writestr(name, blobs[name])
    os.replace(tmp, docx_path)
    return n


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


def md_to_docx(md_path, out_path, prefer_xsl=True, resource_path=None):
    r"""把一份 Markdown 转成 Word。返回报告 dict，**不抛异常**。

    报告字段：
      ok / error          成不成、为什么
      formulas_src        源文里有几个公式
      formulas_replaced   有几个被换成了 XSL 的结果
      math_engine         'xsl' 或 'pandoc' —— 这次实际用的哪条路
      math_note           路的选择原因 / 降级原因，**必须报给用户**
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

    src_dir = resource_path or os.path.dirname(os.path.abspath(md_path))
    try:
        with io.open(md_path, encoding='utf-8') as f:
            src = f.read()
    except Exception as e:
        rep['error'] = '读不了输入文件：%s' % str(e)[:120]
        return rep

    texs = _extract_tex_in_order(src, cwd=src_dir)
    rep['formulas_src'] = len(texs)

    # 🔴 **走 HTML 中转**，不把表格转成 markdown。
    #    旧流程是 md → 表格转markdown → 给图片加 {width=...} → docx，
    #    结果 markdown 多行表格的列宽在转换那刻就定死了，之后加宽度属性
    #    撑爆列宽、表格解析错乱 —— 实测解不等式的图从 4 张掉到 3 张。
    #    HTML 没有列宽这回事，表格原样带过去，图一张不少。
    tmpdir = tempfile.mkdtemp(prefix='pdf2word_')
    try:
        rc, html, err = _run_pandoc(
            ['-f', 'markdown+tex_math_dollars+raw_html', '-t', 'html', '--mathml'],
            src, cwd=src_dir)
        if rc != 0 or not html.strip():
            rep['error'] = 'pandoc（md→html）失败：%s' % (err.strip()[:200] or rc)
            return rep
        tmp_html = os.path.join(tmpdir, 'input.html')
        with io.open(tmp_html, 'w', encoding='utf-8') as f:
            f.write(html)
        rc, _out, err = _run_pandoc(
            [tmp_html, '-f', 'html+tex_math_dollars', '-t', 'docx',
             '-o', out_path, '--resource-path', src_dir], cwd=src_dir)
        if rc != 0 or not os.path.isfile(out_path):
            rep['error'] = 'pandoc（html→docx）失败：%s' % (err.strip()[:200] or rc)
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
            rep['error'] = ('缺少 Node.js，公式的第一步转换要用到它。'
                            '这是安装包不完整，不是你的问题。')
            return rep
        else:
            omml = tomath.batch_to_omml(texs)
            n_ok = sum(1 for x in omml if x is not None)
            n = _replace_omath(out_path, omml)
            if n:
                rep['math_engine'] = 'xsl'
                rep['formulas_replaced'] = n
                rep['math_note'] = '公式走 Office 的 MML2OMML.XSL（%d/%d 成功）' % (n_ok, len(texs))
                if n_ok < len(texs):
                    rep['math_note'] += '；%d 个 XSL 转不了，保留 Pandoc 的结果：%s' % (
                        len(texs) - n_ok, tomath.last_error()[:160])
            else:
                # 数量对不上：源文数出 N 个，产物里却不是 N 个 m:oMath。
                # 对应关系已不可信，整批不换 —— 强行按序替换会让其后所有
                # 公式张冠李戴，比不换糟得多。
                # 差值几乎总是「有公式 Pandoc 转不出来」造成的：它转不了就
                # 退化成纯文本，产物里少一个 oMath，后面全部错位。实测撞见过
                # \textcircled —— 那是 LaTeX 文本模式命令，不是数学命令。
                #
                # 以前这里保留 Pandoc 的结果并判成功。现在判失败：
                # 用户宁可知道这一份没转好，也不要拿到一份公式可能错位的 Word
                # 却以为它是好的。
                n_out = count_omath(out_path)
                rep['error'] = (
                    '这一份的公式没能转成 Word 原生公式：源文数出 %d 个公式，'
                    '生成的文档里却是 %d 个，对应关系不可信，强行替换会让公式错位。'
                    '通常是某个公式用了特殊写法（比如 \\textcircled 这类文本模式命令）。'
                    % (len(texs), n_out))
                return rep
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
