# -*- coding: utf-8 -*-
r"""LaTeX 源码 → Word 原生公式对象（OMML）。

链路：LaTeX --KaTeX(node)--> MathML --MML2OMML.XSL--> OMML

**策略（小蔡 2026-09-01 改定）**：XSL 是**硬性要求**，不再降级到 Pandoc。

    2026-08-31 原本定的是「有 XSL 用 XSL，没有退 Pandoc」。改的原因是
    两条路的产物有实质差异，而不是风格差异 —— Pandoc 会把空集 ∅(U+2205)
    转成直径符号 ⌀(U+2300)，那是错的；括号也不走 OMML 定界符，
    在 Word 里不会随内容伸缩。与其让一部分用户拿到次等产物还不知情，
    不如在门口就说清楚「这软件需要 Office」。

`MML2OMML.XSL` 是微软随 Office 分发的版权文件，**不打包进安装包**——
提取出来再分发是侵权，用户装了 Office 才有。读用户自己机器上那份是
合法的，这里就是这么做。

探测两手都上：先查注册表（准，能找到装在非标准位置的），再扫常见目录
（兜底）。实测本机 0.7 毫秒命中，快到不必缓存。

零新依赖：node + 自带 KaTeX（`vendor/katex/`，MIT）+ lxml。
"""
import json
import os
import subprocess

from lxml import etree

HERE = os.path.dirname(os.path.abspath(__file__))
_JS = os.path.join(HERE, 'tex2mml.js')

# node 只用来跑 KaTeX 把 LaTeX 转成 MathML，**全程不联网**。
# 🔴 以前写死成 'node' 走系统 PATH —— 而老师的电脑上不会有 Node.js，
#    那是开发者工具。XSL 又已经是硬性要求，结果就是老师被自己的
#    拦截屏挡在门外，还被引导去 nodejs.org 下载一个他根本不该关心的东西。
#    发行版必须把 node.exe 打进 runtime/，这里按同一套顺序找。
def _find_node():
    import paths as _p
    return _p.find_exe('node') or 'node'


_NODE = _find_node()

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


def registry_candidates():
    r"""问注册表要 Office 的实际安装路径。

    扫目录只能猜常见位置；注册表里存的是**实际**装到哪，能找到装在
    `E:\SomeFolder\` 这种地方的 Office。小蔡 2026-09-01 定下「必须有
    XSL 才能用」之后，探测的完整性直接决定多少人被误拦在门外，
    所以两种手段都上：先注册表（准），再扫目录（兜底）。

    读 `InstallRoot\Path`，HKLM 和 HKCU 都看（有人是按用户装的）。
    读不到就返回空列表，**绝不抛异常** —— 注册表结构因版本而异，
    为了探测把整个启动自检搞崩不值得。
    """
    out = []
    try:
        import winreg
    except ImportError:
        return out                       # 非 Windows

    def add(p):
        if p and p not in out:
            out.append(p)

    def read(hive, sub, name):
        try:
            with winreg.OpenKey(hive, sub) as k:
                return winreg.QueryValueEx(k, name)[0]
        except Exception:
            return ''

    HKLM, HKCU = winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER

    # ① Click-to-Run。**现代 Office 全走这条** —— 2016 以后和所有
    #    Microsoft 365 都是 C2R 安装，本机实测就是（O365HomePremRetail）。
    #    它的 InstallPath 是「C:\Program Files\Microsoft Office」，
    #    XSL 在下面的 root\Office16\ 里。
    for hive in (HKLM, HKCU):
        for sub in (r'SOFTWARE\Microsoft\Office\ClickToRun',
                    r'SOFTWARE\WOW6432Node\Microsoft\Office\ClickToRun'):
            base = read(hive, sub, 'InstallPath')
            if not base:
                continue
            for office in ('Office16', 'Office15', 'Office14'):
                add(os.path.join(base, 'root', office, 'MML2OMML.XSL'))
                add(os.path.join(base, office, 'MML2OMML.XSL'))

    # ② 传统 MSI 安装（Office 2013 及更早、批量授权版）。
    #    C2R 装的机器上这个键存在但 Path 是空串 —— 本机实测如此，
    #    所以只靠它会一个都找不到。
    for ver in ('16.0', '15.0', '14.0'):
        for hive in (HKLM, HKCU):
            for sub in (r'SOFTWARE\Microsoft\Office\%s\Common\InstallRoot' % ver,
                        r'SOFTWARE\WOW6432Node\Microsoft\Office\%s\Common\InstallRoot' % ver):
                root = read(hive, sub, 'Path')
                if root:
                    add(os.path.join(root, 'MML2OMML.XSL'))
    return out


_last_error = ''


def last_error():
    """上一次失败的原因。成功时为空串。**降级不等于静默**，上层必须报给用户。"""
    return _last_error


def find_xsl():
    """找 MML2OMML.XSL。注册表优先（准），再扫常见目录（兜底）。"""
    for p in registry_candidates():
        if os.path.isfile(p):
            return p
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
        _last_error = ('本机没找到 Office 的 MML2OMML.XSL（注册表 + %d 个候选路径都扫过）。'
                       '2026-09-01 起 XSL 是硬性要求，不再退回 Pandoc。'
                       % len(XSL_CANDIDATES))
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
