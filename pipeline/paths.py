# -*- coding: utf-8 -*-
r"""所有落点集中在这里。

小蔡定的规矩（2026-09-01）：**运行过程中产生的一切文件都留在安装文件夹内，
只有导出的 Word 例外**。删掉这个文件夹 = 卸载干净，不在用户的 C 盘、
注册表、AppData 里留任何东西。

这条规矩有个直接后果：**这个软件不能装进 `C:\Program Files\`**。
那目录普通用户没有写权限，模型和临时文件都写不进去，一转换就失败。
它必须是绿色软件 —— 解压到 D:\软件\ 之类的地方用。

具体落点：

    <安装目录>/
      models/          模型 4.6 GB（MODELSCOPE_CACHE / HF_HOME 指过来）
      mineru.json      我们自己的 MinerU 配置（MINERU_TOOLS_CONFIG_JSON 指过来）
      _tmp/extract/    每次转换的中间产物，转完即可删
      appdata/         Electron 的缓存、GPU 缓存、日志（app.setPath 挪进来）
      logs/            我们自己的日志

为什么不用 %LOCALAPPDATA%（Windows 的常规做法）：常规做法会在用户目录里
留下几个 GB，卸载时多数人找不到、也想不起来删。小蔡明确要求全留在
安装目录内，这是刻意的取舍，不是疏忽。

⚠️ 不碰用户的全局 `~/mineru.json`。那是 MinerU 的全局配置，用户机器上
   可能装着别的用 MinerU 的东西，改它等于动别人的配置。我们通过
   MINERU_TOOLS_CONFIG_JSON 指向自己那份。
"""
import os

# pipeline/paths.py → 上一层就是安装目录
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MODELS = os.path.join(ROOT, 'models')
TMP = os.path.join(ROOT, '_tmp')
TMP_EXTRACT = os.path.join(TMP, 'extract')
CONFIG = os.path.join(ROOT, 'mineru.json')
APPDATA = os.path.join(ROOT, 'appdata')
LOGS = os.path.join(ROOT, 'logs')

RUNTIME = os.path.join(ROOT, 'runtime')
PANDOC = os.path.join(RUNTIME, 'pandoc', 'pandoc.exe')


def find_exe(name, subdirs=()):
    r"""找一个可执行文件。**发行版和开发环境用同一套查找顺序**。

    顺序（先找到先用）：
      1. `<安装目录>/runtime/<name>.exe`             发行版：直接打包的（node）
      2. `<安装目录>/runtime/<子目录>/<name>.exe`
      3. `<安装目录>/runtime/python/Scripts/<name>.exe`  发行版：pip 装出来的
      4. `<安装目录>/.venv/Scripts/<name>.exe`      开发环境
      5. 系统 PATH                                  最后的退路

    第 3 条是发行版实测才发现要加的（`mineru.exe` 落在 Python 的
    `Scripts/` 下，而发行版的 Python 在 `runtime/python/`）。
    ⚠️ 但那次「找到了」其实是**假绿**：文件是在，可它根本跑不起来 ——
       见下面那条警告。这两条候选如今只对 node 之类的真 exe 有意义，
       留着是因为它们无害，删掉反而少一层退路。

    为什么要有这个函数：`_find_mineru()`、`download_exe()`、
    `tomath._NODE` 三处各写各的路径，全都写死在 `.venv\\Scripts\\` ——
    而发行版里根本没有 .venv（那目录不能打包分发：`.venv/Lib/` 下只有
    site-packages，没有 stdlib，`os.__file__` 指向开发机上的 Python
    安装目录，换台机器第一句 import 就死）。散着写的话，发行版要改三处，
    改漏一处就是「在我这儿好好的」。

    ⚠️ **不要拿它找 pip 生成的 `Scripts/*.exe`**（mineru、
       mineru-models-download 这类 console_scripts）。那种 exe 里
       硬编码了打包机器上的 python.exe 路径，换台机器就废，
       而这个函数只查文件在不在，查不出来。跑 MinerU 走
       `mineru_cmd()` / `models_download_cmd()`。
       这里现在只用于 node、pandoc 这类真正的独立可执行文件。

    返回绝对路径，找不到返回空串。
    """
    import shutil
    names = [name] if name.lower().endswith('.exe') else [name + '.exe', name]
    roots = [RUNTIME]
    roots += [os.path.join(RUNTIME, d) for d in subdirs]
    roots.append(os.path.join(RUNTIME, 'python', 'Scripts'))   # 发行版 pip 装的
    roots.append(os.path.join(ROOT, '.venv', 'Scripts'))       # 开发环境
    for r in roots:
        for n in names:
            p = os.path.join(r, n)
            if os.path.isfile(p):
                return p
    hit = shutil.which(name)
    return hit or ''


# ── 跑 MinerU ───────────────────────────────────────────────────────────
# 🔴 绝不调 `runtime/python/Scripts/mineru*.exe`。
#
#    那些 exe 是 pip 装包时给 console_scripts 生成的 launcher，
#    **尾部硬编码了生成它那一刻的 python.exe 绝对路径**。
#    v0.0.1 的字节实测：
#
#        #!D:\claude_code_workspace\pdf_to_word\dist\PDF2Word\runtime\python\python.exe
#
#    那是打包机器上的路径。用户解压到 D:\PDF2Word，它就找不到解释器 ——
#    模型下载和 PDF 转换全废，而开发机上永远是好的（包就是在那儿打的）。
#
#    2026-09-02 网吧实测的现象是「测速正常、下载一直失败」：测速走的是
#    我们自己的 Python 代码，不经过 launcher，所以只有那一半是好的。
#
#    改法：解释器 + `-m 模块`，路径全部运行时算。两个入口点是从
#    launcher 内嵌的 __main__.py 里读出来的，跟它调的完全一样。

MINERU_CLI = 'mineru.cli.client'                   # 转换
MINERU_DOWNLOAD_CLI = 'mineru.cli.models_download'  # 下模型


def python_exe():
    r"""跑 MinerU 用哪个解释器。

    **就用正在跑我们自己的这个** —— 它一定装着 mineru：后端服务能起来，
    说明 fastapi 在，而 fastapi 和 mineru 装在同一个环境里。
    再去别处找解释器，等于又给「找错那个」留了空间。

    兜底才按发行版 / 开发环境的固定位置找（比如有人直接 import 这个
    模块来做脚本，sys.executable 指向别处）。
    """
    import sys
    if sys.executable and os.path.isfile(sys.executable):
        return sys.executable
    for p in (os.path.join(RUNTIME, 'python', 'python.exe'),
              os.path.join(ROOT, '.venv', 'Scripts', 'python.exe')):
        if os.path.isfile(p):
            return p
    return 'python'


def mineru_cmd():
    """转换用的命令前缀。后面接 -p / -o 等参数。"""
    return [python_exe(), '-m', MINERU_CLI]


def models_download_cmd():
    """下模型用的命令前缀。后面接 -s / -m 等参数。"""
    return [python_exe(), '-m', MINERU_DOWNLOAD_CLI]


def mineru_available():
    r"""MinerU 能不能跑。

    判据是**这个解释器找不找得到 mineru 这个包**，不是「哪个文件在不在」。
    原来的自检写成 `bool(find_exe('mineru'))`：文件确实在，于是一路绿灯，
    而它根本起不来。用 find_spec 只查不加载 —— 真 import 会把 torch
    一起拖进来，那要好几秒。
    """
    try:
        import importlib.util
        return importlib.util.find_spec('mineru') is not None
    except Exception:
        return False


def ensure(path):
    """建目录，已存在也不报错。返回它本身，方便串着写。"""
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


def writable():
    r"""安装目录能不能写。

    装进 Program Files 的话这里会是 False —— 那种情况下软件根本没法用，
    要在启动自检里拦下来，明确告诉用户「换个地方解压」，
    而不是等他拖了一堆 PDF、点了开始转换才报一个看不懂的权限错误。
    """
    try:
        ensure(TMP)
        probe = os.path.join(TMP, '.write_test')
        with open(probe, 'w') as f:
            f.write('1')
        os.remove(probe)
        return True
    except Exception:
        return False


def free_bytes():
    """安装目录所在盘还剩多少空间。读不到返回 0（读不到就不拿它拦人）。"""
    try:
        import shutil
        return shutil.disk_usage(ensure(ROOT)).free
    except Exception:
        return 0


def enough_space(need_bytes):
    r"""空间够不够。返回 (够不够, 还剩多少字节)。

    读不到就当够 —— 一个读不出磁盘信息的环境不该被这条拦住，
    真不够的话下载会自己失败，那时错误信息里有真实原因。
    """
    free = free_bytes()
    if not free:
        return True, 0
    return free >= need_bytes, free


def models_ready():
    r"""模型在不在。

    判据是 models/ 下有没有实际内容 —— 只看目录存在不行，
    下载中断会留下一个空壳目录，那种情况必须判成「没有」，
    否则用户会卡在一个永远缺文件的转换里。
    """
    if not os.path.isdir(MODELS):
        return False
    for _dirpath, _dirnames, filenames in os.walk(MODELS):
        for fn in filenames:
            # 模型权重都是大文件，拿 1 MB 当门槛能滤掉残留的配置/锁文件
            try:
                if os.path.getsize(os.path.join(_dirpath, fn)) > 1024 * 1024:
                    return True
            except OSError:
                continue
    return False


def models_size():
    """models/ 有多大（字节）。给界面显示用，算不出来就返回 0。"""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(MODELS):
        for fn in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                continue
    return total


def child_env(source_env=None):
    r"""给 MinerU 子进程用的环境变量。

    这三个变量是「全留在安装目录」的全部实现手段：

      MODELSCOPE_CACHE          模型下到我们的 models/
      HF_HOME                   换 HuggingFace 源时同理
      MINERU_TOOLS_CONFIG_JSON  配置写我们自己那份，不碰 ~/mineru.json

    还有第四个，管的是另一件事：

      MINERU_DEVICE_MODE=cuda   **强制走显卡，不许悄悄用 CPU**

    小蔡 2026-09-02 定的规矩：这个软件只用 GPU。不设这个变量的话，
    MinerU 的 get_device() 会自己探测（cuda → mps → npu → gcu → musa → cpu），
    显卡用不了就**默默换成 CPU 跑** —— 慢两倍，而用户完全不知道
    自己在等一件本可以快一倍的事，只觉得「这软件真慢」。
    宁可当场报错说清楚，也不要静默降级。
    （变量名是从 mineru/utils/config_reader.py:106 读出来的，
      它的优先级最高：设了就直接返回，根本不走自动探测。）

    source_env 是选源屏选中的那个源带的变量（MINERU_MODEL_SOURCE 等），
    合并进来。
    """
    env = dict(os.environ)
    ensure(MODELS)
    env['MODELSCOPE_CACHE'] = MODELS
    env['HF_HOME'] = MODELS
    env['MINERU_TOOLS_CONFIG_JSON'] = CONFIG
    env['MINERU_DEVICE_MODE'] = 'cuda'
    if source_env:
        env.update(source_env)
    return env
