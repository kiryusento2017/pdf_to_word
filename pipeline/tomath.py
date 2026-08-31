# -*- coding: utf-8 -*-
r"""LaTeX 源码 → Word 原生公式对象（OMML）。

链路：LaTeX --KaTeX(node)--> MathML --MML2OMML.XSL--> OMML

**优先级（小蔡 2026-08-31 定）**：有 XSL 先用 XSL，没有才启用内置的 Pandoc。
本模块只管 XSL 这一条；拿不到就明确返回 None 并把原因记进 `last_error()`，
由上层决定退到 Pandoc。**降级可以，静默不行。**

`MML2OMML.XSL` 是微软随 Office 分发的版权文件，**不打包进安装包**——
提取出来再分发是侵权。读用户自己机器上那份是合法的，这里就是这么做。
没装 Office 的用户由 Pandoc 接管，不受影响。

零新依赖：node + 自带 KaTeX（`vendor/katex/`，MIT）+ lxml。
"""
import json
import os
import subprocess

from lxml import etree

HERE = os.path.dirname(os.path.abspath(__file__))
_JS = os.path.join(HERE, 'tex2mml.js')
_NODE = 'node'

# 🔴 **路径不写死**。工作台那边只写了 Office16 一条，换台机器
#    （Office 2013 是 Office15、32 位版落在 Program Files (x86)、
#     还有人装在 D 盘）就直接失效，而且无从察觉 —— 公式会静默退回源码。
#    这里按「新版在前、64 位在前」的顺序扫。
def _candidates():
    roots = []
    for env in ('ProgramFiles', 'ProgramFiles(x86)', 'ProgramW6432'):
        v = os.environ.get(env)
        if v and v not in roots:
            roots.append(v)
    for extra in (r'C:\Program Files', r'C:\Program Files (x86)',
                  r'D:\Program Files', r'D:\Program Files (x86)'):
        if extra not in roots:
            roots.append(extra)
    out = []
    for r in roots:
        for office in ('Office16', 'Office15', 'Office14'):
            out.append(os.path.join(r, 'Microsoft Office', 'root',
                                    office, 'MML2OMML.XSL'))
            out.append(os.path.join(r, 'Microsoft Office',
                                    office, 'MML2OMML.XSL'))
    return out


XSL_CANDIDATES = _candidates()

_last_error = ''


def last_error():
    """上一次失败的原因。成功时为空串。**降级不等于静默**，上层必须报给用户。"""
    return _last_error


def find_xsl():
    """按候选顺序找 MML2OMML.XSL，找不到返回 None。"""
    for p in XSL_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def xsl_available():
    return find_xsl() is not None


def node_available():
    """node 在不在。KaTeX 跑在 node 上，没有 node 这条路整条断。"""
    try:
        p = subprocess.run([_NODE, '--version'], capture_output=True, timeout=30)
        return p.returncode == 0
    except Exception:
        return False


def batch_to_omml(texs, display=False):
    r"""批量把 LaTeX 转成 OMML 元素。返回与输入**等长**的列表，转不了的位置是 None。

    等长是硬契约：上层靠下标把结果对回原公式。长度对不上就既没法退回源码，
    也没法知道是第几个失败的。

    **批量**：一份讲义几百个公式（实测解不等式 213 个），逐个起 node 子进程
    会慢到不可用。这里一次子进程转完一整批，XSL 每批只 parse 一次。
    """
    global _last_error
    _last_error = ''
    texs = list(texs or [])
    if not texs:
        return []

    xsl = find_xsl()
    if not xsl:
        _last_error = ('本机没找到 Office 的 MML2OMML.XSL（扫了 %d 个候选路径），'
                       '公式将改由 Pandoc 转换' % len(XSL_CANDIDATES))
        return [None] * len(texs)

    try:
        p = subprocess.run(
            [_NODE, _JS],
            input=json.dumps([{'tex': t, 'display': display} for t in texs]),
            capture_output=True, text=True, encoding='utf-8', cwd=HERE)
        got = json.loads(p.stdout)
    except Exception as e:
        _last_error = '调 node/KaTeX 失败：%s: %s' % (type(e).__name__, str(e)[:160])
        return [None] * len(texs)

    if not isinstance(got, list) or len(got) != len(texs):
        _last_error = ('tex2mml.js 返回 %d 条，与输入 %d 条不符'
                       % (len(got) if isinstance(got, list) else -1, len(texs)))
        return [None] * len(texs)

    try:
        xslt = etree.XSLT(etree.parse(xsl))          # 每批只 parse 一次
    except Exception as e:
        _last_error = '读不了 %s：%s' % (xsl, str(e)[:120])
        return [None] * len(texs)

    out, errs = [], []
    for i, g in enumerate(got):
        if not g.get('ok'):
            errs.append('第 %d 个：KaTeX %s' % (i + 1, g.get('err', '')))
            out.append(None)
            continue
        try:
            out.append(xslt(etree.fromstring(g['mml'])).getroot())
        except Exception as e:
            errs.append('第 %d 个：%s %s' % (i + 1, type(e).__name__, str(e)[:100]))
            out.append(None)
    if errs:
        _last_error = ' ｜ '.join(errs[:5])
        if len(errs) > 5:
            _last_error += ' ｜ 另有 %d 个失败' % (len(errs) - 5)
    return out


def tex_to_omml(tex, display=False):
    """转一段 LaTeX。转不了返回 None，原因见 last_error()。"""
    return batch_to_omml([tex], display)[0]


def omml_to_string(el):
    """OMML 元素转字符串，调试与测试断言用。"""
    if el is None:
        return ''
    return etree.tostring(el, encoding='unicode')
