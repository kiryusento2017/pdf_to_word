# -*- coding: utf-8 -*-
r"""GPU 运行库（CUDA 版 torch）在不在、怎么装。

## 为什么单独一个模块

小蔡 2026-09-02 定的规矩：**这个软件只用 GPU，不用 CPU**。显卡不达标
要报警，但不阻拦用户去点 —— 点了就让它当场报错，别让人白等半小时。

而「能不能用 GPU」有两个独立的前提，缺一不可，报错时也必须分清楚：

    torch 是 CUDA 版编译的  ← 这个模块管
    机器上有 N 卡且驱动正常  ← gpu.py 管

两者混在一起报「GPU 不可用」的话，用户根本不知道该装驱动还是该下运行库。

## 为什么发行版里不带它

CUDA 版 torch 解压后 4.2 GB（CPU 版只有 486 MB）。打进安装包会让它从
356 MB 涨到 1.5~2 GB，逼近 GitHub 单文件 2 GiB 的上限，而且没有显卡的人
也得跟着下这 4 GB —— 他反正也用不了。

所以改成首次启动时按需下载，跟那 4.6 GB 模型走同一个流程。

## 怎么判断装的是哪个版本

**读 `site-packages/torch/version.py`，不 import torch** —— import 一次要
好几秒，启动自检里做这个会让软件看起来卡住。

    CPU 版   __version__ = '2.13.0+cpu'    cuda: Optional[str] = None
    CUDA 版  __version__ = '2.11.0+cu128'  cuda: Optional[str] = '12.8'

判据是 `cuda` 那一行不是 None。

⚠️ **不要用 dist-info 的目录名判断**：发行版里 CPU 版 torch 的目录叫
   `torch-2.13.0.dist-info`，`+cpu` 后缀被 pip 吃掉了，认不出来。
   （开发环境倒是保留了 `torch-2.11.0+cu128.dist-info`，
     只测开发环境的话会以为这条判据管用。）
"""
import io
import os
import re
import subprocess
import threading
import sys

import paths

# ── 按驱动版本挑 CUDA 版本 ──────────────────────────────────────────
#
# 🔴 别写死最新的那个。CUDA 版 torch 的 c10.dll 要求驱动够新，不够就
#    **整个 torch import 不了**（OSError WinError 1114），而 modelscope
#    的 import 链里有 import torch —— 于是连模型下载都做不了，
#    用户看到的是「下载器崩了」，完全猜不到跟显卡驱动有关。
#
# 驱动门槛查证自 NVIDIA CUDA DL Release Notes（2026-09-02）：
#   CUDA 12.8 → 驱动 570+（消费级显卡）
#   CUDA 12.x → 驱动 525+（minor version compatibility）
#   CUDA 11.8 → 驱动 452+
#
# 各源实际有哪些 cp312/win 的 torch（2026-09-02 实测各 index 页面）：
#   cu128  2.7.1 ~ 2.9.1
#   cu126  2.7.1 ~ 2.9.1   ← 版本跟 cu128 一样新，驱动门槛低一档
#   cu121  2.4.0 ~ 2.5.1   ← **出局**：MinerU 要 torch>=2.6.0
#   cu118  2.5.1 ~ 2.7.1
_BASE = 'https://download.pytorch.org/whl/'

# 下 GPU 运行库的候选源。**都试一遍，谁快用谁** —— 跟模型下载一套思路。
#
# 2026-09-02 实测（本机，下同一个 wheel 各 6 秒）：
#     pytorch 官方   4.7 MB/s     上海交大  3.0 MB/s     阿里云  1.9 MB/s
#     清华 403、中科大 404（这两个的 pytorch-wheels 路径不通）
#
# **官方在开发机上反而最快**，所以不能反过来写死一个国内源。
# 网吧、家里、学校的排序完全可能相反 —— 这正是 sources.py 那套
# 「不存历史成绩、点下载时现测」的理由。
#
# 这些镜像是**扁平文件列表**，不是 PEP 503 的 /simple/ 结构，
# 所以 pip 得用 --find-links 而不是 --index-url。
TORCH_SOURCES = [
    {'id': 'pytorch', 'name': 'PyTorch 官方',
     'base': 'https://download.pytorch.org/whl/'},
    {'id': 'sjtu', 'name': '上海交大',
     'base': 'https://mirror.sjtu.edu.cn/pytorch-wheels/'},
    {'id': 'aliyun', 'name': '阿里云',
     'base': 'https://mirrors.aliyun.com/pytorch-wheels/'},
]

# 测速拿哪个文件当探针。要用**真的要下的那个大文件** —— 拿几 KB 的索引页
# 测出来的是延迟不是带宽，这个坑在模型源那边栽过（界面显示「约 44 小时」）。
#
# 🔴 **每条通道各配一个，不能写死一个 cu128 的文件名。** 那样一换通道
#    URL 就必然打不开（实测 `cu126/torch-2.9.1+cu128-...whl` 返回 403），
#    而 `pick_source` 是 try/except pass —— **静默**退回官方源，用户看不到
#    任何报错，只是下载变慢，极难查。
#
# ⚠️ 版本号是快照。PyTorch 不删历史 wheel，所以就算过期，最坏也只是
#    测速失效退回官方源，不影响能不能装上。加新通道时**必须同时补这里**，
#    test_torchdep 有一条钉着这件事。
PROBE_WHEELS = {
    'cu118': 'torch-2.7.1%2Bcu118-cp312-cp312-win_amd64.whl',
    'cu126': 'torch-2.14.0%2Bcu126-cp312-cp312-win_amd64.whl',
    'cu128': 'torch-2.11.0%2Bcu128-cp312-cp312-win_amd64.whl',
    'cu129': 'torch-2.9.0%2Bcu129-cp312-cp312-win_amd64.whl',
    'cu130': 'torch-2.14.0%2Bcu130-cp312-cp312-win_amd64.whl',
    'cu132': 'torch-2.14.0%2Bcu132-cp312-cp312-win_amd64.whl',
}


def probe_sources(tag='cu128', seconds=3.0):
    """并发实测各源，返回按快慢排好的列表。跟模型下载共用一套测速。"""
    import sources
    cand = []
    for m in TORCH_SOURCES:
        base = m['base'] + tag + '/'
        # 配了探针就测真文件；没配（新通道忘了补）就退而用索引页 ——
        # 那测出来的是延迟不是带宽，但总比用一个打不开的 URL 强。
        _w = PROBE_WHEELS.get(tag)
        cand.append({'id': m['id'], 'name': m['name'], 'env': {},
                     'probe': base + _w if _w else base, 'base': base})
    return sources.probe_all(cand, seconds=seconds)


def pick_source(tag='cu128', seconds=3.0):
    """挑最快的源，返回它的 base URL。测不出来就用官方。"""
    try:
        import sources
        rows = probe_sources(tag, seconds=seconds)
        best = sources.pick_best(rows)
        if best:
            for m in TORCH_SOURCES:
                if m['id'] == best['id']:
                    return m['base'] + tag + '/', best.get('name', '')
    except Exception:
        pass
    return _BASE + tag + '/', 'PyTorch 官方'

# 各通道里**编进去了哪些显卡的机器码**。
# 来源：pytorch/pytorch 的 `.ci/pytorch/windows/build_env_setup.py`
# （main 与 v2.14.0 tag 一致）。本机装 cu128 / cu126 各实测一次
# `get_arch_list()`，跟这张表逐项对得上。
#
# 🔴 **别去看 `.ci/manywheel/` 那份** —— 那是 Linux 的表，12.6 那行少一个
#    6.1。这个坑第一次查就踩了。
_ARCH_126 = ((5, 0), (6, 0), (6, 1), (7, 0), (7, 5), (8, 0), (8, 6), (9, 0))
_ARCH_13X = ((7, 5), (8, 0), (8, 6), (9, 0), (10, 0), (12, 0))

# 驱动门槛抄 `light-the-torch` 的表（它标着来源是 NVIDIA release notes
# 的 Table 2，逐条有出处，v0.8.1 还在维护）。
#
# 🔴 **同一个 CUDA 大版本共用一个门槛** —— wheel 自带 CUDA runtime，走的是
#    minor version compatibility，不需要各 minor 的 GA 门槛。这一格的说法
#    换过三次，前两次都错：525 是 **Linux** 的数（Windows 该是 528.33）；
#    560.76 是 CUDA 12.6 的 **GA** 门槛，wheel 用不着那么高。
#
# 表的顺序就是优先级：**官方还在维护的排前面，停更的排后面。**
# 🔴 不是「CUDA 号越大越好」。实测各通道当时的最新 torch：
#    cu118=2.7.1（停）、cu126=2.14.0（在更）、cu128=2.11.0（**停了**）、
#    cu129=2.9.0（废档）、cu130=2.14.0、cu132=2.14.0。
#    原来那条「从大到小第一个够得着就用」会让驱动 572 的用户拿到 cu128 的
#    2.11.0 —— 比低一档的 cu126 还旧三个次版本。
#
# ⚠️ 这是一张**快照**，官方停更/新增通道时会过期。`check_upstream.py`
#    在打包前会提醒，别指望它自己跟上。
TORCH_CHANNELS = [
    # (index 后缀, 最低驱动, 编进去的显卡算力, 说明)
    ('cu132', 580.0, _ARCH_13X, 'CUDA 13.2'),
    ('cu130', 580.0, _ARCH_13X, 'CUDA 13.0'),
    ('cu126', 528.33, _ARCH_126, 'CUDA 12.6'),
    ('cu128', 528.33, _ARCH_13X, 'CUDA 12.8（官方已停更）'),
    ('cu129', 528.33, _ARCH_13X, 'CUDA 12.9（官方已停更）'),
    # cu118 没有可靠的架构表（那个 tag 的构建脚本里找不到），只按驱动兜底
    ('cu118', 452.39, None, 'CUDA 11.8'),
]
# 驱动版本读不到时用哪个 —— 挑最保守的那档，宁可慢一点也别 import 不了
TORCH_FALLBACK = 'cu118'

TORCH_INDEX = _BASE + 'cu128'      # 兼容老调用方；实际用 pick_index()
PACKAGES = ['torch', 'torchvision']


def driver_num(ver):
    """把 '572.83' 解析成 572.83。读不出来返回 0。

    门槛是带小数的（528.33 / 580.0 / 452.39），只比主版本号会把
    528.0~528.32 那一小段也放进来。
    """
    try:
        parts = str(ver or '').strip().split('.')
        return float('.'.join(parts[:2])) if parts[0] else 0.0
    except Exception:
        return 0.0


_CAP_CACHE = []


def current_cap():
    """这台机器显卡的算力，比如 8.9。读不到返回 0。跑一次记住。"""
    if _CAP_CACHE:
        return _CAP_CACHE[0]
    val = 0.0
    try:
        import gpu
        g = (gpu.detect() or {}).get('gpu') or {}
        val = float(g.get('compute_cap') or 0)
    except Exception:
        val = 0.0
    # 🔴 **读失败不进缓存。** nvidia-smi 被占用、驱动刚装完还没就绪、
    #    超时 —— 都是一次性的。缓存住 0 的话，这个常驻进程在用户重启
    #    软件之前会一直认为「没有显卡」，除 cu118 外每条线路都被挡掉，
    #    而且界面上没有任何提示。宁可下次多跑一次 smi。
    if val > 0:
        _CAP_CACHE.append(val)
    return val


def arch_fits(archs, cap):
    """这个通道里有没有这张卡跑得动的机器码。

    规则：**同一个 major 之内，编进去的 minor <= 设备的 minor 就能跑**，
    跨 major 完全不通。本机 RTX 4060 是 sm_8.9，cu126 和 cu128 的
    arch_list 里都没有 8.9，但都跑得动 —— 靠的就是这条。

    ⚠️ 验这条时**不能用矩阵乘**：`a @ a` 走的是 cuBLAS，那是独立的库、
    有自己的 fatbin，证明不了 torch 自编的 kernel 能跑。当天补验了
    relu / add_ / sum / where 四个不走 cuBLAS 的算子，全部通过。

    🔴 **读不到显卡信息就算不合格**（小蔡 2026-09-06 定）。以前是读不到
    就走最保守那档，那等于让一台可能根本没有 N 卡的机器也去下 2.6 GB。
    """
    if archs is None:
        return True          # cu118 没有可靠的架构表，只按驱动兜底
    if not cap:
        return False
    major = int(cap)
    minor = int(round((cap - major) * 10))
    return any(a == major and b <= minor for a, b in archs)


def pick_channel(driver=None, cap=None):
    """挑一条 torch 下载线路。返回 (后缀, 说明, 用到的驱动号)。

    判据两维，缺一不可：

      驱动够得着   驱动版本 >= 这条线路要求的最低驱动
      机器码对得上 这条线路里编进了这张卡能跑的 SASS

    🔴 **只看驱动会出人命**：1080Ti（sm_6.1）配新驱动，按老规矩命中 cu128，
    而 cu128 根本没编 6.1 的机器码 —— `import torch` 能过、`is_available()`
    也可能是 True，**只有真跑起 kernel 才报 no kernel image is available**。
    正式版 wheel 是 SASS-only，不带 PTX，没有 JIT 兜底。
    """
    n = driver_num(driver if driver is not None else current_driver())
    if cap is None:
        cap = current_cap()
    if n:
        for tag, need, archs, note in TORCH_CHANNELS:
            if n >= need and arch_fits(archs, cap):
                return tag, note, n
    return TORCH_FALLBACK, 'CUDA 11.8', n


def pick_index(driver=None):
    """挑好的那档对应的 pip index URL。"""
    return _BASE + pick_channel(driver)[0]


def current_driver():
    """当前机器的显卡驱动版本号。读不到返回空串。"""
    try:
        import gpu
        return ((gpu.detect() or {}).get('gpu') or {}).get('driver', '') or ''
    except Exception:
        return ''

# 下载量的**兜底值**。只在第一个包开始下之前顶着 —— 之后分母由 pip 报的
# 真实字节接管（见 ProgressAcc.total）。
#
# 🔴 **这个数必须是裸字节，不许拿 pip 打印的「2753.2 MB」换算。**
#    2026-09-05 的 92% 事故就是这么来的：pip 的 format_size 用十进制
#    （1 MB = 10^6），这里却当成 MiB 写了 2.8 * 1024^3 = 3006477107，
#    比真实总量大 8.8%，于是下完了进度条只走到 92% 就停住。
#    分子来自 `Progress N of M` 的裸字节，分母也必须同源、同进制。
#
# 2026-09-05 小蔡真机日志里的实数（取 Progress 行的 of，不是打印的 MB）：
#   torch-2.11.0+cu128-cp312-cp312-win_amd64.whl   2753189216
#   torchvision-0.26.0+cu128                          9585013
#   setuptools-78.1.0（这次命中 pip 缓存，没有 Progress 行）  约 1300000
DOWNLOAD_BYTES = 2775000000


# pip 的机器可读进度行：`Progress 262144 of 12464674`
# （2026-09-02 实测 `pip download numpy --no-cache-dir --progress-bar raw`
#   的真实输出，不是照文档抄的。）
_PROGRESS = re.compile(r'^\s*Progress\s+(\d+)\s+of\s+(\d+)\s*$')


def parse_progress(line):
    r"""认 pip 的 raw 进度行。不是进度行就返回 None。

    为什么用 `--progress-bar raw` 而不是默认那个：

        默认   ---------------------------- 12.5/12.5 MB 5.0 MB/s  0:00:02
        raw    Progress 262144 of 12464674

    默认那行是给人看的 —— 宽度随终端变、单位随大小变（kB/MB/GB）、
    还混着速度和 eta，正则得考虑一堆情况。raw 是 pip 专门为非终端环境
    做的机器格式，两个纯数字，解析零歧义。

    这个项目在「凭印象写解析」上栽过：测速那次 probe URL 指向几 KB 的
    接口，算出来的是延迟不是带宽，界面显示「约 44 小时」。所以这次
    先跑一遍真命令、看真输出，再写正则。
    """
    m = _PROGRESS.match(line or '')
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)))


def is_noise(line):
    """这一行该不该进日志区。进度行不该 —— 2.4 GB 会刷出几千行，
    把 Collecting / Downloading 这些真正有用的行全淹掉。"""
    return parse_progress(line) is not None


class ProgressAcc(object):
    r"""把「一个个包各自的进度」累成「总共下了多少」。

    pip 装 torch 会连着下好几个包（torch、torchvision、还有依赖），
    每个都从 0 开始报。直接拿当前包的数当总进度的话，**进度条会一次次
    退回去** —— 那比没有进度条还糟，用户会以为卡住了或者出错重来了。
    """

    def __init__(self, floor=None):
        self.done = 0        # 已经下完的那些包，加起来多少
        self._cur = 0        # 当前这个包下到哪了
        self._tot = 0        # 当前这个包多大
        self._seen = 0       # 见过的每个包的大小之和 —— 分母的真实来源
        self._floor = DOWNLOAD_BYTES if floor is None else floor

    def feed(self, cur, tot):
        """喂一行进度，返回累计已下字节。"""
        if tot != self._tot or cur < self._cur:
            # 换包了：把上一个包的总量结算进 done，新包的大小计进分母
            self.done += self._tot
            self._tot = tot
            self._seen += tot
        self._cur = cur
        return self.done + cur

    def total(self):
        r"""分母。**只增不减**，所以进度条不会倒退。

        · 一个包都还没开始下：用兜底值顶着，界面上先有个像样的百分比
        · 下到一半：已见之和还不含后面没开始下的包，比真实总量小 ——
          取 max 让兜底值继续顶着，免得进度条冲到 99% 又被后面的包拉回来
        · 哪天 torch 变胖超过兜底值：已见之和接管，不会冲过 100%
          （注释里记着的那次「估 2.5 GB 实际下 2.77 GB，冲到 110%」，
            这一层就是防它的）

        命中 pip 缓存的包不产生 Progress 行（2026-09-05 那次的
        setuptools 就是），它既不进分子也不进分母 —— 那点尾巴由
        「装完把 got 补成 total」兜住，见 server/main.py。
        """
        return max(self._floor, self._seen)


def _site_packages():
    """当前解释器的 site-packages 在哪。找不到返回空串。"""
    for p in sys.path:
        if p and p.lower().endswith('site-packages') and os.path.isdir(p):
            return p
    return ''


def version_file():
    """torch/version.py 的路径。torch 没装的话返回空串。"""
    sp = _site_packages()
    if not sp:
        return ''
    p = os.path.join(sp, 'torch', 'version.py')
    return p if os.path.isfile(p) else ''


def info():
    r"""torch 的情况。返回 {installed, cuda, version}。

    不 import torch —— 那要好几秒，启动自检拖不起。
    """
    out = {'installed': False, 'cuda': '', 'version': ''}
    vf = version_file()
    if not vf:
        return out
    out['installed'] = True
    try:
        with io.open(vf, encoding='utf-8', errors='replace') as f:
            txt = f.read()
    except Exception:
        return out
    m = re.search(r"^__version__\s*=\s*['\"]([^'\"]+)", txt, re.M)
    if m:
        out['version'] = m.group(1)
    # cuda: Optional[str] = '12.8'   /   cuda: Optional[str] = None
    m = re.search(r"^cuda\s*(?::[^=]*)?=\s*['\"]([^'\"]+)['\"]", txt, re.M)
    if m:
        out['cuda'] = m.group(1)
    return out


def ready():
    """装的是不是 CUDA 版 torch。"""
    return bool(info()['cuda'])


def why():
    """一句人话，说清楚现在是什么状况。"""
    d = info()
    if not d['installed']:
        return '还没装 GPU 运行库（PyTorch）。'
    if not d['cuda']:
        return ('装的是 CPU 版 PyTorch（%s），用不了显卡。'
                '要下一份 GPU 版才能转换。' % (d['version'] or '版本未知'))
    return 'GPU 运行库就绪（PyTorch %s，CUDA %s）。' % (d['version'], d['cuda'])


# torch 加载失败时，Windows 给的原始错误。翻成人话用。
_DLL_HINTS = (
    ('1114', 'dll'),         # 动态链接库初始化例程失败
    ('c10.dll', ''),         # torch 的核心 dll
    ('error loading', ''),
    ('cuda', 'dll'),
)


# torch 的 c10.dll 依赖的 MSVC 运行库。缺哪个都会让整个 torch import 不了。
# 装在 C:\Windows\System32 下，随 Visual C++ Redistributable 一起来。
# torch 加载时会用到的 MSVC 运行库。
#
# 🔴 2026-09-02 实测：小蔡那台缺这些里的东西，装 vc_redist 之后
#    GPU 库一次就过。而在那之前，软件「自动补一份 msvcp140」的做法
#    根本不够 —— vc_redist 装 13 个，我们包里只有 1 个。
#
#    尤其是 msvcp140_1.dll：PyTorch 用 C++17，一部分符号在这个文件里，
#    而 numpy/pandas/shapely 的 wheel 都不带它。
#
#    所以这份清单的作用是**判断要不要拦**，不是「补齐它就行」。
VCRUNTIME_DLLS = ('vcruntime140.dll', 'vcruntime140_1.dll',
                  'msvcp140.dll', 'msvcp140_1.dll')
# 🔴 这里原来还有一份 VCREDIST_URL，跟 vcredist.URL 一模一样，而且
#    **没有任何地方在用它**（2026-09-05 复查发现）。同一个 URL 写两遍，
#    哪天要改（换地址、加兜底、改参数）漏一处就是「改了但没生效」——
#    这个项目栽过好几次这种形状（-x! 和 -xr! 差一个字母毁掉一整版；
#    中文路径补丁散着写导致在发行版上一次都没生效过）。
#    删掉了。要用就 import vcredist 拿 vcredist.URL。


def vcruntime_missing():
    r"""查 MSVC 运行库缺了哪几个。返回缺失的文件名列表。

    为什么要自己查：c10.dll 是 PyTorch 的**基础**库，它本身不碰 CUDA，
    但依赖 MSVC 运行库。缺了的话 Windows 报的是
    「[WinError 1114] 动态链接库(DLL)初始化例程失败」—— 这句话既没说
    是哪个 dll 缺了，也没说该装什么，老师看了完全不知道下一步做什么。

    软件自己查一下就能把这句话变成「缺 Visual C++ 运行库，点这里装」。

    ⚠️ 查得到不代表一定没问题（还可能是版本太旧、或者杀软动过），
       所以这只是**加一条更具体的线索**，不是唯一判据。
    """
    sysdir = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                          'System32')
    # python.exe 旁边也算 —— DLL 搜索顺序里「应用程序目录」排在
    # System32 前面，Python embeddable 自带的 vcruntime140* 就在那儿，
    # 只看 System32 会把它们误报成缺失。
    beside = os.path.dirname(paths.python_exe())
    miss = []
    for dll in VCRUNTIME_DLLS:
        if not (os.path.isfile(os.path.join(sysdir, dll))
                or os.path.isfile(os.path.join(beside, dll))):
            miss.append(dll)
    return miss


def _msvcp_beside_python():
    """python.exe 旁边那个 msvcp140.dll 的路径（不管在不在）。"""
    return os.path.join(os.path.dirname(paths.python_exe()), 'msvcp140.dll')


def _bundled_msvcp():
    r"""包里现成的 msvcp140.dll 在哪。找不到返回空串。

    numpy / pandas / shapely 的 Windows wheel 都用 delvewheel 打包，
    会把依赖的 MSVC 运行库改名（加内容 hash）塞进 `<包名>.libs/`：

        numpy.libs/msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll

    改名是为了避免几个包各带一份互相覆盖。我们要的就是这个文件，
    复制出来改回原名即可。
    """
    import glob
    import importlib.util
    for pkg in ('numpy', 'pandas', 'shapely'):
        # 直接问包自己在哪，别拿 python.exe 的位置去拼 —— 发行版是
        # runtime/python/Lib/site-packages，而 venv 的 python.exe 在
        # Scripts/ 下、site-packages 在上一层，拼法对不上。
        try:
            spec = importlib.util.find_spec(pkg)
        except Exception:
            continue
        if not spec or not spec.origin:
            continue
        site = os.path.dirname(os.path.dirname(spec.origin))
        hits = sorted(glob.glob(os.path.join(site, pkg + '.libs',
                                             'msvcp140*.dll')))
        if hits:
            return hits[0]
    return ''


def ensure_msvcp():
    r"""系统缺 msvcp140.dll 时，从包里复制一份放到 python.exe 旁边。

    返回 (搞定了没, 说明)。

    ## 为什么不让用户去装 vc_redist

    小蔡 2026-09-02：「那你为什么让我安装 vc」。因为**没必要** ——
    包里本来就有（numpy 带的），复制一下就行，不用下载、不用动系统、
    删文件夹照样卸载干净。让用户去微软官网下一个装上，是我上一版
    偷懒的写法。

    ## 为什么只在系统缺失时才动

    实测版本（2026-09-02）：numpy 自带的是 14.40，小蔡这台系统里的
    是 14.50。而 `runtime/python/` 的 DLL 搜索优先级**高于**
    System32 —— 无脑复制等于把这台机器降级到 14.40。

    MSVC 运行库的向后兼容是单向的：新版能跑旧代码，旧版跑不了新代码。
    torch 若用 14.5x 工具链编译、引用了 14.40 里没有的符号，
    这一手会把本来正常的机器搞坏。

    所以顺序是：系统有 → 什么都不做；系统没有 → 才补自己那份。
    """
    sysdir = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                          'System32')
    if os.path.isfile(os.path.join(sysdir, 'msvcp140.dll')):
        return True, ''                      # 系统有，用系统的，别插手

    dst = _msvcp_beside_python()
    if os.path.isfile(dst):
        return True, ''                      # 之前补过了

    src = _bundled_msvcp()
    if not src:
        return False, ('这台电脑缺少 C++ 运行库（msvcp140.dll），'
                       '而安装包里也没找到可用的备份。'
                       '到微软官网下一个 vc_redist.x64.exe 装上就好。')
    try:
        import shutil
        shutil.copyfile(src, dst)
        # 只是「补了一个」，不是「齐了」。齐没齐由 vcruntime_missing()
        # 按完整清单说了算 —— 2026-09-02 就是在这儿把「复制过去了」
        # 当成了「能用了」，害小蔡白下一趟 2.8 GB。
        return True, '补上了包里自带的 msvcp140.dll（还要再查其余几个）'
    except Exception as e:
        return False, ('这台电脑缺少 C++ 运行库，想自动补上但没成功：%s。'
                       '到微软官网下一个 vc_redist.x64.exe 装上就好。' % e)


def explain_load_error(err):
    r"""把 torch 加载失败的原始报错翻成人话。翻不了就返回空串。

    原始错误长这样，老师看不懂，也猜不到跟显卡有关：

        OSError: [WinError 1114] 动态链接库(DLL)初始化例程失败。
          Error loading "...\torch\lib\c10.dll" or one of its dependencies.

    而这条错误的实际含义几乎总是「这台机器的显卡环境撑不起 CUDA 版
    PyTorch」—— 要么没有 N 卡，要么驱动太旧。
    """
    low = (err or '').lower()
    hit = ('1114' in low or 'c10.dll' in low
           or ('error loading' in low and '.dll' in low))
    if not hit:
        return ''
    # 先查最具体、也最好解决的那个原因
    miss = vcruntime_missing()
    if miss:
        return ('缺少 Visual C++ 运行库（少了 %s），GPU 运行库加载不了。'
                '到微软官网下一个 vc_redist.x64.exe 装上就行，'
                '很小、几分钟，装完回来重新装一次 GPU 运行库。'
                % '、'.join(miss))

    drv = current_driver()
    # 🔴 原来这里硬编码「需要 570 以上」，而 cu118 只要 452.39 ——
    #    驱动 530 的用户会被告知一个跟他无关的数字。
    #
    #    现在取的是**所有线路里最低的那个门槛**，不是实际选中那条：
    #    这一段是加载失败之后的兜底解释，只想拦住「驱动低到没有任何
    #    线路能用」这种确定的情况。用选中线路的门槛会把「驱动够 cu118
    #    但不够 cu126」也说成驱动太低，而那种机器本来就该走 cu118 ——
    #    宁可少说一句，不要说错。
    n = driver_num(drv)
    _need = min(c[1] for c in TORCH_CHANNELS)
    if n and n < _need:
        return ('显卡驱动是 %s，撑不起 GPU 运行库（最低要 %s）。'
                '到 nvidia.com 更新一下驱动，然后回来重新装一次。'
                % (drv, _need))

    # 运行库在、驱动也够新 —— 剩下的可能性得让人往下查，
    # 所以要把原始报错也带出去，不能只说「失败了」。
    return ('GPU 运行库装上了，但这台电脑加载不了它'
            '（Windows 报「动态链接库初始化失败」）。%s'
            '可以试试这两条：① 装一个 Visual C++ 运行库'
            '（微软官网的 vc_redist.x64.exe）；'
            '② 把杀毒软件关掉再重装一次 GPU 运行库 —— '
            '有些杀软会动 torch 的 dll。'
            % (('这台电脑的显卡驱动是 %s。' % drv) if drv else ''))


def can_load():
    r"""torch 是不是**真的能加载**。返回 (ok, 原始报错)。

    真起一个子进程 import 一次 —— 读 version.py 只能证明文件在，
    证明不了 c10.dll 加载得起来。这两件事在小蔡那台机器上就是分开的：
    version.py 好好的，`import torch` 直接 OSError。

    要几秒钟，所以**只在装完之后验一次**，不放进每次启动的自检里。
    """
    try:
        p = subprocess.run(
            [paths.python_exe(), '-c', 'import torch; print(torch.__version__)'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            # 加载失败时抛的是中文异常（WinError 1114），不设 UTF-8
            # 就是一片乱码 —— 而下面 explain_load_error 要靠它翻译。
            cwd=paths.ROOT, timeout=180, env=paths.utf8_env())
    except Exception as e:
        return False, '%s: %s' % (type(e).__name__, str(e)[:200])
    out = (p.stdout or b'').decode('utf-8', 'replace').strip()
    return (p.returncode == 0), out


def uninstall():
    r"""把 torch 卸掉，退回「干净的没装」状态。返回 (ok, 输出)。

    什么时候用：装完发现加载不了。**留着比没装更糟** ——
    modelscope 用 `find_spec('torch')` 判断 torch 在不在（只看文件），
    找得到就直接 import，没有 try/except。所以一个「在、但加载不了」的
    torch 会让模型下载一起崩，而「压根没装」反而一切正常。

    卸掉走 pip 自己的元数据，不是删目录 —— 删目录会留下 dist-info，
    下次装的时候 pip 认为「已经装过了」直接跳过。
    """
    try:
        p = subprocess.run(
            [paths.python_exe(), '-m', 'pip', 'uninstall', '-y', '-q']
            + PACKAGES,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=paths.ROOT, timeout=300, env=paths.utf8_env())
    except Exception as e:
        return False, '%s: %s' % (type(e).__name__, str(e)[:200])
    return (p.returncode == 0), (p.stdout or b'').decode('utf-8', 'replace')


def install_argv():
    """装 CUDA 版 torch 的完整命令。

    `--upgrade` 是必需的：机器上可能已经有 CPU 版（发行版曾经打包过），
    不加的话 pip 认为「torch 已安装」直接跳过，装完还是用不了显卡。

    `--progress-bar raw` 让 pip 吐机器可读的 `Progress N of M`，
    用来驱动界面上的进度条（见 parse_progress）。
    """
    tag = pick_channel(current_driver())[0]
    base, _name = pick_source(tag)
    return ([paths.python_exe(), '-m', 'pip', 'install', '--upgrade',
             '--no-warn-script-location', '--progress-bar', 'raw']
            + PACKAGES + ['--index-url', _BASE + tag + '/',
                          '--find-links', base])


def install_cmd_text():
    """命令的可读形式，显示在日志区第一行。"""
    return ' '.join(install_argv())


def log_path():
    """完整日志落在哪。界面上把这个路径给用户，出问题直接发文件。"""
    return os.path.join(paths.LOGS, 'torch_install.log')


def install(on_log=None, stop_flag=None, on_progress=None):
    r"""下载并装上 CUDA 版 torch。返回 (ok, error)。

    用 pip 装进当前这个解释器 —— 它就是待会儿要跑 MinerU 的那个
    （见 paths.python_exe 的说明）。装到别处等于没装。

    `--upgrade` 是必需的：机器上可能已经有 CPU 版（发行版曾经打包过），
    不加的话 pip 认为「torch 已安装」直接跳过，装完还是用不了显卡。
    """
    # 🔴 **下载之前**先把零成本能查的前置条件查完。
    #
    #    小蔡 2026-09-02：「顺序是不是不太对，是不是应该先装 VC」。
    #    他说得对 —— 原来的流程是下 2.8 GB（十几分钟）、装上、
    #    发现加载不了、卸掉、让用户去装 VC++ 运行库、**重新下 2.8 GB**。
    #    而 vcruntime140.dll 在不在是一次本地文件检查，零成本。
    #
    #    通则：任何耗时操作之前，先查能零成本查的前置条件。
    #    C++ 运行库缺了的话 torch 装上也加载不了。但**不用让用户去装** ——
    #    包里带着 numpy 那份，复制一下改回原名就行（小蔡：「那你为什么
    #    让我安装 vc」）。只在系统真缺的时候补，系统有就一个字不动。
    #    判据是「这个软件装过 vc_redist 没有」，不是数 DLL 文件 ——
    #    数文件今天判错四次（最后一次把我们自己带的 vcruntime140*
    #    当成系统装的，没装 VC 的机器也放行）。
    import vcredist
    if not vcredist.already_done():
        return False, ('还没装 Microsoft Visual C++ 运行库。'
                       'GPU 运行库要靠它才加载得起来，'
                       '所以那 2.8 GB 先别下 —— 回首页把它装上再来。')

    # 2.8 GB 下载 + 解压成 4.2 GB，中途还要放 wheel，留出余量
    need = int(7.5 * 1024 * 1024 * 1024)
    okspace, free = paths.enough_space(need)
    if not okspace:
        return False, ('磁盘空间不够 —— GPU 运行库要下 2.8 GB、解压后占 '
                       '4.2 GB，这个盘只剩 %.1f GB。'
                       '腾出空间再来，或者把软件装到别的盘。'
                       % (free / 1024.0 ** 3))

    paths.ensure(paths.LOGS)
    log = log_path()
    argv = install_argv()

    try:
        fp = io.open(log, 'w', encoding='utf-8', errors='replace')
        fp.write('# %s\n\n' % ' '.join(argv))
    except Exception:
        fp = None

    drv = current_driver()
    tag, note, n = pick_channel(drv)
    if fp:
        try:
            fp.write('# 显卡驱动 %s → 选 %s（%s）\n\n'
                     % (drv or '(读不到)', tag, note))
        except Exception:
            pass
    if on_log:
        on_log('显卡驱动 %s，选用 %s（%s）版本的运行库'
               % (drv or '(读不到)', tag, note))

    tail = []
    acc = ProgressAcc()
    proc_box = []
    killed = []
    watch_stop = threading.Event()

    def _kill(p):
        try:
            subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        except Exception:
            pass
        try:
            p.terminate()
        except Exception:
            pass

    def watch():
        r"""盯着「用户点没点停止」。

        🔴 这个检查**不能**放在读取循环里，两个原因，两个都实际发生过：

          1. `readline()` 会阻塞。pip 卡住不吐东西时（网络断了最常见），
             代码就停在那儿 —— 而「卡住不动」正是用户最想点停止的时候。
          2. 就算不阻塞，进度行是 `continue` 掉的，而下 2.5 GB 时几乎
             每一行都是进度行 —— 检查写在 continue 后面等于没写。
             （models.download 刚因为第 1 条修过，这里转头又踩了第 2 条。）
        """
        while not watch_stop.is_set():
            if stop_flag and stop_flag() and proc_box:
                killed.append(True)
                _kill(proc_box[0])
                return
            watch_stop.wait(0.5)

    t = threading.Thread(target=watch, daemon=True)
    t.start()
    try:
        p = subprocess.Popen(argv, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, cwd=paths.ROOT,
                             env=paths.child_env())
        proc_box.append(p)
        while True:
            raw = p.stdout.readline()
            if not raw:
                break
            line = raw.decode('utf-8', 'replace').rstrip()
            if not line:
                continue
            if fp:
                try:
                    fp.write(line + '\n')
                    fp.flush()
                except Exception:
                    pass

            pg = parse_progress(line)
            if pg is not None:
                # 进度行只驱动进度条，不进日志区（几千行会把有用的淹掉）
                if on_progress:
                    on_progress(acc.feed(pg[0], pg[1]), acc.total())
                continue

            tail.append(line)
            if len(tail) > 40:
                del tail[0]
            if on_log:
                on_log(line)
        rc = p.wait()
    except Exception as e:
        return False, '%s: %s' % (type(e).__name__, str(e)[:200])
    finally:
        watch_stop.set()
        if fp:
            try:
                fp.close()
            except Exception:
                pass

    if killed:
        # 用户主动停的。半截的 wheel 留在 pip 缓存里，下次装会接着用，
        # 不用清 —— 清了反而白下一遍。
        return False, '已取消'

    if rc != 0:
        return False, ('装 GPU 运行库失败（退出码 %s）。%s完整日志：%s'
                       % (rc, ('pip 说：%s。' % ' / '.join(tail[-3:])[:300])
                          if tail else '', log))
    if not ready():
        return False, ('装完了，但检查发现还是 CPU 版 —— '
                       'pip 可能装到别的地方去了。完整日志：%s' % log)

    # 🔴 装上了 ≠ 能用。真 import 一次再说「装好了」。
    #    小蔡 2026-09-02 那台机器上，version.py 好好的、ready() 说就绪，
    #    而 `import torch` 直接 OSError（c10.dll 加载失败）—— 于是
    #    modelscope 也 import 不了，**连模型下载都做不了**，
    #    用户看到的是「下载器崩了」，完全猜不到跟显卡有关。
    if on_log:
        on_log('检查 GPU 运行库能不能真的加载…')
    ok2, raw = can_load()
    if not ok2:
        human = explain_load_error(raw)

        # 🔴 加载不了就**卸掉**，别留着。
        #    留着的话，modelscope 的 `find_spec('torch')` 会找到它、
        #    直接 import、然后崩 —— 模型下载跟着一起废，而用户看到的
        #    错误跟模型和网络毫无关系，根本无从查起。
        #    卸干净之后至少能下模型，转换时会明确说「缺 GPU 运行库」。
        if on_log:
            on_log('加载不了，把它卸掉，免得连模型下载也一起崩…')
        uninstall()

        return False, ((human or ('GPU 运行库装上了但加载失败：%s'
                                  % raw[-300:]))
                       + '（已经把装坏的那份卸掉了，环境修好后可以重装；'
                       + '安装包还在缓存里，不用重新下。完整日志：%s）' % log)
    return True, ''
