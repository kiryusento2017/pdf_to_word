# -*- coding: utf-8 -*-
r"""调 MinerU 把 PDF 提取成 Markdown + 图片。

这是整条链里**唯一需要 GPU** 的一步。其余模块（probe / tomath / todocx）
都能离线跑，所以这里的编排逻辑要尽量薄，重的判断都放在别处。

**进度按页走，是真的**：MinerU 源码 `hybrid_analyze.py:1044` 有
`tqdm(total=page_count, desc="Processing pages")`，形如
`Processing pages: 30%|███| 3/10 [...]`。解析它就有真进度，
不必画假进度条骗人。

**默认 method=ocr**：2026-08-31 同一份 PDF 实测三种模式 ——
文字准确度完全一样（那个「己知」错字在原 PDF 文字层里就是错的，谁也消不掉），
但 txt / auto 丢了 38% 的公式（131 vs 213），因为文字层里没有公式。
依据见 docs/DESIGN.md 第二节。
"""
import os
import re
import subprocess

# 提取参数。改这里之前先看 docs/DESIGN.md 第二节的实测表。
BACKEND = 'hybrid-engine'
METHOD = 'ocr'
EFFORT = 'high'        # medium 会关掉图片分析，而讲义一半是「如图所示」
LANG = 'ch'

# MinerU 有七八个 tqdm 阶段，**全都要认**。
# 实测：端到端 237 秒里，「Processing pages」到第 224 秒才第一次出现 ——
# 只认它的话，94% 的时间屏幕上一动不动，用户会以为死机然后强杀进程。
# 阶段之间进度会跳回 0，但「跳一跳」远好过「静止四分钟」。
# 阶段名用**贪婪**匹配：非贪婪会把 `MFR Predict` 截成光秃秃的 `Predict`，
#   人看着不知道在干嘛。
_ANY_BAR = re.compile(r'([A-Za-z][A-Za-z0-9\-/ ]*):\s*\d+%\|.*?\|\s*(\d+)/(\d+)')

# 阶段名说人话 —— 这行字直接显示给老师看，不能是 MFR Predict 这种。
STAGE_CN = {
    # 下面这串是 2026-08-31 端到端实测抓到的真实序列（235 秒 / 10 页），
    # 不是照着源码猜的 —— 源码里的 desc 常量和实际打出来的不完全一样。
    'layout predict': '分析版面',
    'layout preparation': '准备版面',
    'layout output parsing': '整理版面',
    'extract preparation': '准备提取',
    'post processing': '收尾整理',
    'processing pages': '处理页面',
    'ocr-det': '定位文字',
    'ocr-det predict': '定位文字',
    'ocr-rec predict': '识别文字',
    'mfr predict': '识别公式',
    'table-ocr det': '识别表格',
    'table-wired predict': '识别表格',
    'table-wireless predict': '识别表格',
    'table-wired/wireless cls predict': '识别表格',
    'seal predict': '识别印章',
    'predict': '识别中',          # 光秃秃的 Predict，认不出细分阶段时的兜底
}


def stage_cn(name):
    """英文阶段名 → 人话。认不出来的原样返回，不吞掉。"""
    return STAGE_CN.get((name or '').strip().lower(), (name or '').strip())

# 行尾符。tqdm 刷新进度用回车不换行，所以两个都得当行尾。
_CR = chr(13)
_LF = chr(10)


def parse_progress(line):
    """从一行 MinerU 输出里解析 (阶段中文名, 当前, 总数)。不是进度行返回 None。"""
    m = _ANY_BAR.search(line or '')
    if not m:
        return None
    return stage_cn(m.group(1)), int(m.group(2)), int(m.group(3))


def build_argv(mineru, pdf, out_dir, backend=None, method=None,
               effort=None, lang=None):
    r"""拼 MinerU 的命令行。

    四个参数一个都不能漏 —— 漏了不会报错，MinerU 会用它自己的默认值
    悄悄降级（比如 effort 默认 medium 会关掉图片分析），事后极难发现。
    """
    return [mineru, '-p', pdf, '-o', out_dir,
            '-b', backend or BACKEND,
            '-m', method or METHOD,
            '--effort', effort or EFFORT,
            '-l', lang or LANG]


def find_output(out_dir, stem):
    r"""找 MinerU 产出的目录。找不到返回 None。

    🔴 **子目录名不写死**。它由 backend + method 拼出来
    （hybrid-engine + ocr → `hybrid_ocr`，office backend → `office`）。
    工作台那边我写死过 'office'，结果 10 份书全判成「产物没了」。
    判据改成「哪个子目录里有 .md，哪个就是」。
    """
    base = os.path.join(out_dir, stem)
    if not os.path.isdir(base):
        return None
    for sub in sorted(os.listdir(base)):
        d = os.path.join(base, sub)
        if not os.path.isdir(d):
            continue
        if any(f.endswith('.md') for f in os.listdir(d)):
            return d
    return None


def _split_lines(buf):
    """按回车或换行切行。返回 (完整的行列表, 剩下的半截)。"""
    out = []
    while True:
        i = -1
        for ch in (_CR, _LF):
            j = buf.find(ch)
            if j >= 0 and (i < 0 or j < i):
                i = j
        if i < 0:
            break
        line, buf = buf[:i], buf[i + 1:]
        if line.strip():
            out.append(line)
    return out, buf


def _spawn(argv, on_line):
    r"""起子进程，逐行回调。抽出来是为了测试能拦住它，不必真跑 GPU。

    🔴 **不能用 readline**。tqdm 刷新进度用的是**回车符、不换行**，
    按行读会一直阻塞到整个进度条结束才吐出一大坨 —— 端到端第一次跑通时
    进度一条都没回调，就是栽在这儿。这里按块读、回车换行都当行尾。

    （原来还写过 bufsize=1，二进制模式根本不支持行缓冲，Python 自己会警告。）
    """
    p = subprocess.Popen(argv, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT)
    buf = ''
    try:
        while True:
            chunk = p.stdout.read(256)
            if not chunk:
                break
            buf += chunk.decode('utf-8', 'replace')
            lines, buf = _split_lines(buf)
            for ln in lines:
                on_line(ln)
        if buf.strip():
            on_line(buf)
    finally:
        p.stdout.close()
    return p.wait()


def run(pdf, out_dir, mineru=None, on_progress=None, on_log=None, **kw):
    r"""提取一份 PDF。返回报告 dict，**不抛异常**。

    on_progress(阶段中文名, 当前, 总数) —— 真进度，来自 MinerU 的 tqdm
    on_log(line) —— 原始输出，给「查看日志」用

    报告字段：ok / error / auto_dir / md / pages / tail
    """
    rep = {'ok': False, 'error': '', 'auto_dir': '', 'md': '',
           'pages': 0, 'tail': '', 'stage': ''}
    mineru = mineru or 'mineru'
    if os.path.sep in mineru and not os.path.isfile(mineru):
        rep['error'] = '找不到 mineru：%s' % mineru
        return rep
    if not os.path.isfile(pdf):
        rep['error'] = '找不到 PDF：%s' % pdf
        return rep
    os.makedirs(out_dir, exist_ok=True)

    lines = []

    def on_line(ln):
        lines.append(ln)
        if len(lines) > 400:                 # 只留尾巴，日志可能上万行
            del lines[:200]
        if on_log:
            on_log(ln)
        pg = parse_progress(ln)
        if pg:
            stage, cur, tot = pg
            if stage == '处理页面':
                rep['pages'] = tot
            rep['stage'] = stage
            if on_progress:
                on_progress(stage, cur, tot)

    try:
        rc = _spawn(build_argv(mineru, pdf, out_dir, **kw), on_line)
    except Exception as e:
        rep['error'] = '起 mineru 失败：%s: %s' % (type(e).__name__, str(e)[:120])
        return rep

    stem = os.path.splitext(os.path.basename(pdf))[0]
    auto = find_output(out_dir, stem)
    rep['tail'] = chr(10).join(lines[-25:])
    if auto is None:
        # 跑完却没产物：最容易踩的是改了 backend/method 但产物落在别的目录名下。
        # 把尾巴带出来，不然人只看到「失败」两个字无从查起。
        rep['error'] = ('提取跑完了但没找到产物（退出码 %s）。'
                        '最后几行输出：' + chr(10) + '%s') % (rc, rep['tail'][-400:])
        return rep
    mds = [f for f in os.listdir(auto) if f.endswith('.md')]
    rep['auto_dir'] = auto
    rep['md'] = os.path.join(auto, mds[0])
    rep['ok'] = True
    return rep
