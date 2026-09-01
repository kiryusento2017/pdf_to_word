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
PROBE_WHEEL = 'torch-2.9.1%2Bcu128-cp312-cp312-win_amd64.whl'


def probe_sources(tag='cu128', seconds=3.0):
    """并发实测各源，返回按快慢排好的列表。跟模型下载共用一套测速。"""
    import sources
    cand = []
    for m in TORCH_SOURCES:
        base = m['base'] + tag + '/'
        cand.append({'id': m['id'], 'name': m['name'], 'env': {},
                     'probe': base + PROBE_WHEEL, 'base': base})
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

TORCH_CHANNELS = [
    # (最低驱动主版本, index 后缀, 说明)
    (570, 'cu128', 'CUDA 12.8'),
    (525, 'cu126', 'CUDA 12.6'),
    (452, 'cu118', 'CUDA 11.8'),
]
# 驱动版本读不到时用哪个 —— 挑最保守的那档，宁可慢一点也别 import 不了
TORCH_FALLBACK = 'cu118'

TORCH_INDEX = _BASE + 'cu128'      # 兼容老调用方；实际用 pick_index()
PACKAGES = ['torch', 'torchvision']


def driver_major(ver):
    """把 '572.83' 解析成 572。读不出来返回 0。"""
    try:
        return int(str(ver or '').strip().split('.')[0])
    except Exception:
        return 0


def pick_channel(driver=None):
    """按驱动版本挑一档。返回 (后缀, 说明, 用到的驱动号)。

    驱动读不到（没装 nvidia-smi、没有 N 卡）就走最保守那档 —— 那种机器
    本来也转不了，但至少别让 import torch 崩掉，否则模型下载一起废。
    """
    n = driver_major(driver)
    if n:
        for need, tag, note in TORCH_CHANNELS:
            if n >= need:
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

# 下载量。2026-09-02 小蔡真机日志里的实数：
#   torch-2.11.0+cu128-cp312-cp312-win_amd64.whl   2753.2 MB
#   torchvision-0.26.0+cu128                          9.6 MB
#   setuptools                                         1.3 MB
# 估小了进度条会冲过 100%（估 2.5 GB 时实际下 2.77 GB，冲到 110%），
# 所以按实测值取整往上留一点。
DOWNLOAD_BYTES = int(2.8 * 1024 * 1024 * 1024)


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

    def __init__(self):
        self.done = 0        # 已经下完的那些包，加起来多少
        self._cur = 0        # 当前这个包下到哪了
        self._tot = 0        # 当前这个包多大

    def feed(self, cur, tot):
        """喂一行进度，返回累计已下字节。"""
        if tot != self._tot or cur < self._cur:
            # 换包了：把上一个包的总量结算进 done
            self.done += self._tot
            self._tot = tot
        self._cur = cur
        return self.done + cur


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
VCRUNTIME_DLLS = ('vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll')
VCREDIST_URL = 'https://aka.ms/vs/17/release/vc_redist.x64.exe'


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
    miss = []
    for dll in VCRUNTIME_DLLS:
        if not os.path.isfile(os.path.join(sysdir, dll)):
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
        return True, '已补上 C++ 运行库（用的是安装包自带的那份）'
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
    n = driver_major(drv)
    if n and n < 570:
        return ('显卡驱动是 %s，撑不起这版 GPU 运行库（需要 570 以上）。'
                '到 nvidia.com 更新一下驱动，然后回来重新装一次。' % drv)

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
            cwd=paths.ROOT, timeout=180)
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
            cwd=paths.ROOT, timeout=300)
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
    okvc, vcmsg = ensure_msvcp()
    if not okvc:
        return False, vcmsg + ' 装好之后回来点一次，那 2.8 GB 现在先别下。'

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
                    on_progress(acc.feed(pg[0], pg[1]), DOWNLOAD_BYTES)
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
