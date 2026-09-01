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

    source_env 是选源屏选中的那个源带的变量（MINERU_MODEL_SOURCE 等），
    合并进来。
    """
    env = dict(os.environ)
    ensure(MODELS)
    env['MODELSCOPE_CACHE'] = MODELS
    env['HF_HOME'] = MODELS
    env['MINERU_TOOLS_CONFIG_JSON'] = CONFIG
    if source_env:
        env.update(source_env)
    return env
