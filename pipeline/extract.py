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
import codecs
import hashlib
import io
import json
import os
import shutil
import time

import paths
import re
import subprocess
import threading

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
    # mineru 可以是一个字符串（老写法，一个可执行文件），也可以是
    # 命令前缀列表 —— 现在走的是后者：[python.exe, '-m', 'mineru.cli.client']。
    # 见 paths.py：pip 生成的 mineru.exe 里硬编码了打包机器的解释器路径。
    head = list(mineru) if isinstance(mineru, (list, tuple)) else [mineru]
    return head + ['-p', pdf, '-o', out_dir,
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


# ── 产物复用 ────────────────────────────────────────────────────────────
#
# 同一份 PDF 用同一组参数转第二次，没道理再等四分钟。产物按**指纹分桶**：
#
#     <out_dir>/<指纹16位>/<文件名>/hybrid_ocr/...
#                        └─ .fingerprint.json
#
# 指纹 = sha256(PDF 内容) + 四个提取参数 + MinerU 版本。
#
# 🔴 **不能按文件名分桶**（这是改造前的做法）。两份不同内容的
#    `讲义.pdf` 会落在同一个位置互相覆盖；你把 PDF 改了重新导出、名字没变，
#    也会拿到旧产物 —— 而那种错最难查，因为界面上一切正常。
#
# 参数或 MinerU 版本变了 → 指纹不同 → 换个桶 → 自动重跑，
# 不需要另写一套失效逻辑。
FP_NAME = '.fingerprint.json'
CACHE_DAYS = 10


def mineru_version():
    """MinerU 版本号。读元数据，**不 import mineru** —— import 要好几秒。

    读不到就返回 'unknown'，照常参与指纹（小蔡 2026-09-02 定）。
    代价是「既读不到版本、又升级了 MinerU」时旧缓存不会失效；
    反过来（读不到就停用缓存）会让人每次白等四分钟还不知道为什么，
    那更糟。真读不到时 run() 会往日志里记一句，留个线索。
    """
    try:
        import importlib.metadata as md
        return md.version('mineru')
    except Exception:
        return 'unknown'


def fingerprint(pdf, backend=None, method=None, effort=None, lang=None):
    """这份 PDF + 这组参数 + 这个 MinerU 版本的唯一标识（16 位十六进制）。

    分块读文件，几十 MB 的 PDF 也只占 1 MB 内存、几十毫秒 ——
    相对于四分钟的提取可以忽略。
    """
    h = hashlib.sha256()
    with open(pdf, 'rb') as f:
        while True:
            blk = f.read(1024 * 1024)
            if not blk:
                break
            h.update(blk)
    tail = '|%s|%s|%s|%s|%s' % (backend or BACKEND, method or METHOD,
                                effort or EFFORT, lang or LANG,
                                mineru_version())
    h.update(tail.encode('utf-8'))
    return h.hexdigest()[:16]


def _fp_write(bucket, fp, pdf, argv):
    """把指纹和它的来龙去脉写进桶里。写不成不影响转换，只是下次不认这个桶。"""
    try:
        with io.open(os.path.join(bucket, FP_NAME), 'w', encoding='utf-8') as f:
            json.dump({'fp': fp, 'pdf': os.path.basename(pdf),
                       'mineru': mineru_version(),
                       'argv': [str(x) for x in (argv or [])],
                       'at': time.strftime('%Y-%m-%d %H:%M:%S')},
                      f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _fp_matches(bucket, fp):
    """桶里那份指纹对不对得上。读不到或对不上都算不命中。"""
    try:
        with io.open(os.path.join(bucket, FP_NAME), encoding='utf-8') as f:
            return (json.load(f) or {}).get('fp') == fp
    except Exception:
        return False


def find_any_output(bucket):
    r"""桶里任意一份产物。找不到返回 None。

    🔴 **不按文件名找**。桶是按「PDF 内容 + 参数 + MinerU 版本」分的，
    里面的产物必然等价 —— 用户把 PDF 改个名、换个目录，内容没变，
    就不该让他重等四分钟。

    改造初版这里写的是 find_output(bucket, stem)，stem 取自当前文件名：
    指纹明明命中了，却因为桶里的子目录还叫旧名字而判成不命中。
    2026-09-02 小蔡改了个文件名就撞上了。
    """
    if not os.path.isdir(bucket):
        return None
    for name in sorted(os.listdir(bucket)):
        if not os.path.isdir(os.path.join(bucket, name)):
            continue
        got = find_output(bucket, name)
        if got:
            return got
    return None


def purge_old(root, days=CACHE_DAYS):
    """清掉超过 days 天没动过的桶。返回清掉几个。

    只清 root 下的一级子目录 —— 那是软件自己的临时区，里面只该有桶。
    清不掉（被占用之类）就跳过，绝不让清理失败挡住转换。
    """
    if not os.path.isdir(root):
        return 0
    cutoff = time.time() - days * 86400
    n = 0
    for name in os.listdir(root):
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        try:
            if os.path.getmtime(d) >= cutoff:
                continue
            shutil.rmtree(d, ignore_errors=True)
            if not os.path.isdir(d):
                n += 1
        except OSError:
            pass
    return n


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


def _spawn(argv, on_line, env=None, stop_flag=None):
    r"""起子进程，逐行回调。抽出来是为了测试能拦住它，不必真跑 GPU。

    🔴 **不能用 readline**。tqdm 刷新进度用的是**回车符、不换行**，
    按行读会一直阻塞到整个进度条结束才吐出一大坨 —— 端到端第一次跑通时
    进度一条都没回调，就是栽在这儿。这里按块读、回车换行都当行尾。

    （原来还写过 bufsize=1，二进制模式根本不支持行缓冲，Python 自己会警告。）
    """
    # env 用来指定模型下载源（MINERU_MODEL_SOURCE / HF_ENDPOINT）。
    # **合并进现有环境而不是替换** —— 替换会丢掉 PATH，子进程直接起不来。
    # 🔴 强制子进程用 UTF-8 输出（paths.utf8_env 里写了为什么）。
    #    先铺 UTF-8 再合并调用方的 env —— 调用方给的是 child_env()，
    #    它本身也是从 utf8_env 起头的，值一样，谁先谁后都不影响。
    real_env = paths.utf8_env()
    if env:
        real_env.update(env)

    p = subprocess.Popen(argv, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, env=real_env)

    # 🔴 「停止」的检查放在独立线程里，不放读取循环。
    #
    #    读取循环阻塞在 p.stdout.read() 上，MinerU 处理一页要几十秒、
    #    期间可能一个字都不吐 —— 检查写在循环里的话，用户点了停止得等到
    #    下一次有输出才生效，而「半天没动静」恰恰是他最想停的时候。
    #
    #    这是这个项目第三次栽在同一个模式上（models.download、
    #    torchdep.install 是前两次），三处现在用的是同一套解法。
    killed = []
    stop_watch = threading.Event()

    def watch():
        while not stop_watch.is_set():
            if stop_flag and stop_flag():
                killed.append(True)
                try:
                    subprocess.run(
                        ['taskkill', '/PID', str(p.pid), '/T', '/F'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass
                try:
                    p.terminate()
                except Exception:
                    pass
                return
            stop_watch.wait(0.5)

    watcher = None
    if stop_flag:
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()

    # 🔴 增量解码器，不是每块各自 decode。
    #    一个中文字 3 字节，按固定 256 字节切块的话，字符会横跨两块 ——
    #    前半在这块末尾、后半在下块开头，两边 decode 各吐一个 U+FFFD，
    #    **每 256 字节就吃掉一个字**。增量解码器会把不完整的字节序列
    #    留着，等下一块补齐了再吐出来。
    dec = codecs.getincrementaldecoder('utf-8')('replace')
    buf = ''
    try:
        while True:
            chunk = p.stdout.read(256)
            if not chunk:
                break
            buf += dec.decode(chunk)
            lines, buf = _split_lines(buf)
            for ln in lines:
                on_line(ln)
        buf += dec.decode(b'', True)      # 收尾，吐出残留
        if buf.strip():
            on_line(buf)
    finally:
        stop_watch.set()
        p.stdout.close()
    return p.wait()


def run(pdf, out_dir, mineru=None, on_progress=None, on_log=None,
        env=None, stop_flag=None, **kw):
    r"""提取一份 PDF。返回报告 dict，**不抛异常**。

    on_progress(阶段中文名, 当前, 总数) —— 真进度，来自 MinerU 的 tqdm
    on_log(line) —— 原始输出，给「查看日志」用

    报告字段：ok / error / auto_dir / md / pages / tail
    """
    rep = {'ok': False, 'error': '', 'auto_dir': '', 'md': '',
           'pages': 0, 'tail': '', 'stage': '', 'cancelled': False,
           'cached': False}
    mineru = mineru or paths.mineru_cmd()
    if isinstance(mineru, str):
        # 老写法：给的是一个可执行文件路径
        if os.path.sep in mineru and not os.path.isfile(mineru):
            rep['error'] = '找不到 mineru：%s' % mineru
            return rep
    elif not mineru:
        rep['error'] = '没给 MinerU 的运行命令'
        return rep
    if not os.path.isfile(pdf):
        rep['error'] = '找不到 PDF：%s' % pdf
        return rep
    os.makedirs(out_dir, exist_ok=True)

    # ── 缓存 ────────────────────────────────────────────────────────────
    # 顺手清掉十天没碰过的桶。清理失败不影响转换（purge_old 自己吞掉）。
    purge_old(out_dir)

    stem = os.path.splitext(os.path.basename(pdf))[0]
    fp = fingerprint(pdf, **kw)
    if on_log and mineru_version() == 'unknown':
        # 留个线索：这种情况下升级 MinerU 不会让旧缓存失效。
        on_log('读不到 MinerU 版本号，缓存指纹用 unknown 代替')
    bucket = os.path.join(out_dir, fp)

    # 按桶找，不按文件名找 —— 改个名、换个目录不该让缓存失效。
    hit = find_any_output(bucket) if _fp_matches(bucket, fp) else None
    if hit:
        mds = [f for f in os.listdir(hit) if f.endswith('.md')]
        if mds:
            # 直接用上次的产物。**不跑 MinerU，也就没有 GPU 开销**。
            if on_log:
                on_log('这份和参数都没变，直接用上次的识别结果（%s）' % fp)
            rep['auto_dir'] = hit
            rep['md'] = os.path.join(hit, mds[0])
            rep['cached'] = True
            rep['ok'] = True
            return rep

    # 没命中：产物落进这个桶，后面 build_argv / find_output 都跟着走。
    out_dir = bucket
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

    argv = build_argv(mineru, pdf, out_dir, **kw)
    # 🔴 把命令原样摆出来，跟下载面板一个待遇。出问题时这一行往往
    #    比任何解释都有用（参数漏了、路径不对，一眼就看见）。
    if on_log:
        on_log('$ ' + ' '.join(str(x) for x in argv))
    # MinerU 起来到第一条 tqdm 之间有几十秒在加载模型，**一个字都不吐**。
    # 不说一声的话界面上阶段名停在「准备」、进度条不动、日志区不加行 ——
    # 三样死在一起，用户只能判断为卡死。
    if on_progress:
        on_progress('正在加载识别模型', 0, 0)
    try:
        rc = _spawn(argv, on_line, env=env, stop_flag=stop_flag)
    except Exception as e:
        rep['error'] = '起 mineru 失败：%s: %s' % (type(e).__name__, str(e)[:120])
        return rep

    if stop_flag and stop_flag():
        # 用户主动停的，不是故障 —— 别报一堆退出码和最后几行输出，
        # 那会让人以为出错了。
        rep['error'] = '已停止'
        rep['cancelled'] = True
        return rep

    auto = find_output(out_dir, stem)
    rep['tail'] = chr(10).join(lines[-25:])
    if auto is None:
        # 跑完却没产物：最容易踩的是改了 backend/method 但产物落在别的目录名下。
        # 把尾巴带出来，不然人只看到「失败」两个字无从查起。
        rep['error'] = ('提取跑完了但没找到产物（退出码 %s）。'
                        '最后几行输出：' + chr(10) + '%s') % (rc, rep['tail'][-400:])
        return rep
    # 🔴 **有产物不等于成功。**
    #
    #    原来的判据只有「找不找得到 .md」，rc 只在没产物时露个脸。
    #    可 MinerU 是边处理边写的：十页处理到第七页崩掉（OOM / CUDA 错），
    #    前六页已经落进 .md —— 找得到产物、判成功、老师拿到一份
    #    **只有前六页**的 Word，而软件说「转好了」。
    #
    #    转换这件事，「少了几页」比「失败」严重得多：失败会重来，
    #    残缺会被当成成品直接发给学生。
    if rc != 0:
        rep['error'] = ('提取没有正常结束（退出码 %s），产物可能是残缺的，'
                        '不能当成功用。最后几行输出：' + chr(10) + '%s'
                        ) % (rc, rep['tail'][-400:])
        return rep

    mds = [f for f in os.listdir(auto) if f.endswith('.md')]
    # 认领这个桶。写在最后 —— 中途失败的桶没有指纹文件，下次不会被当成缓存。
    _fp_write(out_dir, fp, pdf, argv)
    rep['auto_dir'] = auto
    rep['md'] = os.path.join(auto, mds[0])
    rep['ok'] = True
    return rep
