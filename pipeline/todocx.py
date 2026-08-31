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

# 只认行内 $...$。与 tomath 同一口径。
_INLINE = re.compile(r'(?<!\$)\$(?!\$)([^\$\n]+?)\$(?!\$)')
_TABLE = re.compile(r'<table.*?</table>', re.S)

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
        rc, out, _ = _run_pandoc(['-f', 'html+tex_math_dollars', '-t', 'markdown'],
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
           'math_engine': 'pandoc', 'math_note': '', 'tables': 0, 'images': 0}
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

    fixed = _html_tables_to_markdown(src, cwd=src_dir)
    texs = _extract_tex_in_order(fixed, cwd=src_dir)
    rep['formulas_src'] = len(texs)

    tmpdir = tempfile.mkdtemp(prefix='pdf2word_')
    try:
        tmp_md = os.path.join(tmpdir, 'input.md')
        with io.open(tmp_md, 'w', encoding='utf-8') as f:
            f.write(fixed)
        rc, _out, err = _run_pandoc(
            [tmp_md, '-f', 'markdown+tex_math_dollars+raw_html',
             '-t', 'docx', '-o', out_path, '--resource-path', src_dir],
            cwd=src_dir)
        if rc != 0 or not os.path.isfile(out_path):
            rep['error'] = 'pandoc 转换失败：%s' % (err.strip()[:200] or '退出码 %s' % rc)
            return rep
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # ── 公式改走 XSL（小蔡定的优先级）────────────────────────────────
    if prefer_xsl and texs:
        if not tomath.xsl_available():
            rep['math_note'] = '本机没有 Office 的 MML2OMML.XSL，公式由 Pandoc 转换'
        elif not tomath.node_available():
            rep['math_note'] = '本机没有 node，KaTeX 跑不起来，公式由 Pandoc 转换'
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
                # 对应关系已不可信，整批不换。
                # 差值几乎总是「有公式 Pandoc 转不出来」造成的：它转不了就退化成
                # 纯文本，产物里少一个 oMath，后面全部错位。实测撞见过
                # 	extcircled —— 那是 LaTeX 文本模式命令，不是数学命令。
                n_out = count_omath(out_path)
                rep['math_note'] = (
                    '公式数量对不上（源文 %d 个、产物 %d 个），整批保留 Pandoc 的结果。'
                    '差值通常是个别公式 Pandoc 转不出来、退化成了纯文本；'
                    '强行按序替换会让其后所有公式错位' % (len(texs), n_out))
    elif not prefer_xsl:
        rep['math_note'] = '按调用方要求跳过 XSL，公式由 Pandoc 转换'

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
