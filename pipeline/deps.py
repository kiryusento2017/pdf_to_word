# -*- coding: utf-8 -*-
r"""依赖状态：torch / mineru / 模型，本地是什么版本、上游有没有新的。

## 三样东西的情况完全不同

    torch    site-packages   4.2 GB   pip 装      有版本号
    mineru   site-packages   7.3 MB   pip 装      有版本号
             （加 62 个依赖约 0.9 GB）
    模型     models/         4.6 GB   mineru CLI  **没有版本号**

模型那个是实测确认的：`models/` 下面只有
`OpenDataLab--PDF-Extract-Kit-1.0/snapshots/master/`，快照目录名是
`master` 不是 commit hash，没有 refs、没有 .msc 元数据、没有 version
文件。`configuration.json` 里只有 `{"framework":"Pytorch",...}`。

**但能判断新旧** —— modelscope 的 API 给得出 `LastUpdatedTime`。

## 用哪个源查

🔴 **用哪个源下载，就用哪个源查版本。**

原来打算写死查官方，但那会造出一种很难查的故障：官方说有 2.14.0，
用户点了升级，而他实际用的那个镜像上根本没有这个版本。

所以 torch 直接复用 `torchdep.TORCH_SOURCES`、模型复用
`sources.MODEL_SOURCES` —— 查得到的版本 = 下得到的版本。

## 为什么用 pip 而不是自己解析网页

`pip index versions` 一条命令给出可用版本列表、已装版本、最新版本，
而且**该下哪个文件是 pip 判断的**（它自己认得出这台机器是 cp312 +
win_amd64）。自己解析 HTML 的话，PyTorch 改版就崩。

## 不自动查

打开环境检测那一屏时，「上游」那一列是空的（显示 —），点了按钮才
发请求。这是照搬 README 里已有的规矩：**「速度那一列没测过就是
空的，不拿别的数字顶替」**。

查不到就显示「查不到」，**绝不显示「已是最新」** —— 这两个意思差
很远，混了就是假绿灯。
"""
import json
import os
import re
import subprocess
import time
import urllib.request

import paths

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

# 查上游的超时。比更新检查那边短 —— 这是用户主动点的，等太久不如
# 早点告诉他查不到。
TIMEOUT = 12

# 模型仓库。跟 mineru 下的那两个对应（mineru.json 里写着路径）。
MODEL_REPOS = [
    ('PDF-Extract-Kit-1.0', 'OpenDataLab/PDF-Extract-Kit-1.0'),
    ('MinerU2.5-Pro-2605-1.2B', 'OpenDataLab/MinerU2.5-Pro-2605-1.2B'),
]


def local_versions():
    """本地装的版本。取不到的键值是空串，不抛异常。"""
    out = {}
    try:
        import importlib.metadata as md
    except Exception:
        return out
    for name in ('torch', 'torchvision', 'mineru'):
        try:
            out[name] = md.version(name)
        except Exception:
            out[name] = ''
    return out


def _pip_index_versions(pkg, index_url=None, find_links=None):
    r"""问 pip：这个包在这个源上有哪些版本。

    返回 (最新版本, 全部版本列表, 错误)。查不到时最新版本是空串 ——
    **调用方必须把空串显示成「查不到」而不是「已是最新」**。
    """
    argv = [paths.python_exe(), '-m', 'pip', 'index', 'versions', pkg]
    if index_url:
        argv += ['--index-url', index_url]
    if find_links:
        argv += ['--find-links', find_links]
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=TIMEOUT * 2,
                           env=paths.utf8_env(), cwd=paths.ROOT)
    except subprocess.TimeoutExpired:
        return '', [], '超时'
    except Exception as e:
        return '', [], '%s: %s' % (type(e).__name__, str(e)[:60])

    out = (p.stdout or b'').decode('utf-8', 'replace')
    if p.returncode != 0:
        # pip 的报错最后一行通常最有用
        tail = [ln for ln in out.strip().splitlines() if ln.strip()]
        return '', [], (tail[-1][:120] if tail else '查不到')

    # LATEST: 那一行最准；没有的话退回 Available versions 的第一个
    m = re.search(r'^\s*LATEST:\s*(\S+)', out, re.M)
    latest = m.group(1) if m else ''
    m2 = re.search(r'Available versions:\s*(.+)', out)
    avail = [x.strip() for x in m2.group(1).split(',')] if m2 else []
    if not latest and avail:
        latest = avail[0]
    return latest, avail, ''


def check_mineru():
    r"""mineru 上游最新的**正式版**。

    🔴 **要排除预览版。** PyPI 上现在正式版是 3.4.5，另有 4.0.0a6 ——
    alpha 不能当成「有新版本」推给用户。
    """
    local = local_versions().get('mineru', '')
    latest, avail, err = _pip_index_versions('mineru')
    if err:
        return {'name': 'mineru', 'local': local, 'latest': '',
                'error': err, 'source': 'PyPI'}
    # pip index versions 默认就不给预览版（除非加 --pre），这里再拦一道
    if latest and re.search(r'(a|b|rc)\d+$', latest):
        stable = [v for v in avail if not re.search(r'(a|b|rc)\d+$', v)]
        latest = stable[0] if stable else ''
    return {'name': 'mineru', 'local': local, 'latest': latest,
            'error': '', 'source': 'PyPI'}


def check_torch():
    r"""torch 上游最新版。**用用户实际会下的那个源查。**

    通道由驱动决定（torchdep.pick_channel），源由测速决定 —— 这里
    不测速（那要几秒），直接用官方源查。查不到就报错，不猜。
    """
    local = local_versions().get('torch', '')
    try:
        import torchdep
        tag = torchdep.pick_channel(torchdep.current_driver())[0]
    except Exception:
        tag = 'cu128'
    base = 'https://download.pytorch.org/whl/' + tag + '/'
    latest, _avail, err = _pip_index_versions('torch', index_url=base)
    return {'name': 'torch', 'local': local, 'latest': latest,
            'error': err, 'source': 'PyTorch 官方 · ' + tag, 'channel': tag}


def check_models():
    r"""模型有没有更新。

    模型**没有版本号**（实测：快照目录叫 master，没有 refs 和元数据），
    所以比的是时间：本地文件的最后修改时间 vs 上游仓库的
    LastUpdatedTime。

    ⚠️ 这依赖 modelscope 的私有 API（/api/v1/models/<org>/<repo>），
    它改版这个功能就失效。取不到就说「查不到」，不假装最新。
    """
    ready = paths.models_ready()
    size = paths.models_size()
    local_mtime = 0
    if ready:
        for dp, _dn, fns in os.walk(paths.MODELS):
            for fn in fns:
                try:
                    m = os.path.getmtime(os.path.join(dp, fn))
                except OSError:
                    continue
                if m > local_mtime:
                    local_mtime = m

    out = {'name': '模型', 'ready': ready, 'size': size,
           'local_time': (time.strftime('%Y-%m-%d', time.localtime(local_mtime))
                          if local_mtime else ''),
           'upstream_time': '', 'error': '', 'source': 'ModelScope'}

    times = []
    for _label, repo in MODEL_REPOS:
        url = 'https://modelscope.cn/api/v1/models/' + repo
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                d = json.loads(r.read().decode('utf-8'))
            t = ((d.get('Data') or {}).get('LastUpdatedTime') or 0)
            if t:
                times.append(int(t))
        except Exception as e:
            out['error'] = '%s: %s' % (type(e).__name__, str(e)[:60])
    if times:
        out['upstream_time'] = time.strftime('%Y-%m-%d',
                                             time.localtime(max(times)))
        out['error'] = ''
    elif not out['error']:
        out['error'] = '查不到上游更新时间'
    return out


def check_all():
    r"""三样一起查。串行 —— 一共也就几秒，而且 pip 那两条要起子进程，
    并发起三个 Python 会跟 MinerU 抢内存（8 GB 显存那台尤其）。
    """
    return {'ok': True,
            'torch': check_torch(),
            'mineru': check_mineru(),
            'models': check_models()}
