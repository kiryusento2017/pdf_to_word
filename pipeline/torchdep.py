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
import sys

import paths

# pip 拿 CUDA 版 torch 的官方源。cu128 对应 CUDA 12.8，
# 跟开发环境实测的那份一致（torch 2.11.0+cu128）。
TORCH_INDEX = 'https://download.pytorch.org/whl/cu128'
PACKAGES = ['torch', 'torchvision']

# 下载量按开发环境实测：torch 目录解压后 4.2 GB，wheel 压缩后约 2.5 GB
DOWNLOAD_BYTES = int(2.5 * 1024 * 1024 * 1024)


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


def install(on_log=None, stop_flag=None):
    r"""下载并装上 CUDA 版 torch。返回 (ok, error)。

    用 pip 装进当前这个解释器 —— 它就是待会儿要跑 MinerU 的那个
    （见 paths.python_exe 的说明）。装到别处等于没装。

    `--upgrade` 是必需的：机器上可能已经有 CPU 版（发行版曾经打包过），
    不加的话 pip 认为「torch 已安装」直接跳过，装完还是用不了显卡。
    """
    log = os.path.join(paths.ensure(paths.LOGS), 'torch_install.log')
    argv = [paths.python_exe(), '-m', 'pip', 'install', '--upgrade',
            '--no-warn-script-location'] + PACKAGES + [
        '--index-url', TORCH_INDEX]

    try:
        fp = io.open(log, 'w', encoding='utf-8', errors='replace')
        fp.write('# %s\n\n' % ' '.join(argv))
    except Exception:
        fp = None

    tail = []
    try:
        p = subprocess.Popen(argv, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, cwd=paths.ROOT,
                             env=paths.child_env())
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
            tail.append(line)
            if len(tail) > 40:
                del tail[0]
            if on_log:
                on_log(line)
            if stop_flag and stop_flag():
                try:
                    subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'],
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
                except Exception:
                    pass
                return False, '已取消'
        rc = p.wait()
    except Exception as e:
        return False, '%s: %s' % (type(e).__name__, str(e)[:200])
    finally:
        if fp:
            try:
                fp.close()
            except Exception:
                pass

    if rc != 0:
        return False, ('装 GPU 运行库失败（退出码 %s）。%s完整日志：%s'
                       % (rc, ('pip 说：%s。' % ' / '.join(tail[-3:])[:300])
                          if tail else '', log))
    if not ready():
        return False, ('装完了，但检查发现还是 CPU 版 —— '
                       'pip 可能装到别的地方去了。完整日志：%s' % log)
    return True, ''
