# -*- coding: utf-8 -*-
r"""GPU 检测：这台机器跑不跑得动 MinerU。

小蔡定的方案（2026-08-31）：**首次打开时强制检测**，满足就继续，
不满足让用户自己选「退出」还是「硬来」—— 不猜、不替用户做主。

判据：compute_cap ≥ 7.5（Turing 架构）且显存 ≥ 6 GB，这是 MinerU 的硬件要求。

数据来源是 `nvidia-smi`（装了 N 卡驱动就有），本机实测：
    NVIDIA GeForce RTX 4060 Laptop GPU, 8188 MiB, 8.9, 572.83

**所有理由都得是人话** —— 那句话会原样显示给老师看，
不能是「compute_capability < 7.5」这种。
"""
import re
import subprocess

# MinerU 的硬件门槛
MIN_COMPUTE_CAP = 7.5          # Turing。1080Ti 是 6.1，不够
MIN_VRAM_MB = 6 * 1024         # pipeline 后端 6G；VLM 后端要 8G

_SMI_QUERY = 'name,memory.total,compute_cap,driver_version'

# WMI 里那些不是真显卡的东西：远程桌面、录屏软件装的虚拟显示器。
# 本机就列出来一堆 Todesk / 向日葵的，别把它们当独显。
_FAKE_GPU = ('virtual', 'idd', 'oray', 'todesk', 'gameviewer',
             'remote', 'basic display', 'microsoft basic')


def _run(argv, timeout=30):
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout)
        return p.returncode, p.stdout.decode('utf-8', 'replace')
    except Exception:
        return -1, ''


def parse_smi(out):
    """解析 nvidia-smi 的 CSV 输出。拿不到就返回 None，不抛异常。"""
    for line in (out or '').splitlines():
        line = line.strip()
        if not line or line.lower().startswith('name'):
            continue                       # 表头
        parts = [x.strip() for x in line.split(',')]
        if len(parts) < 4:
            continue
        m = re.match(r'(\d+)', parts[1])
        if not m:
            continue
        try:
            cap = float(parts[2])
        except ValueError:
            continue
        return {'name': parts[0], 'vram_mb': int(m.group(1)),
                'compute_cap': cap, 'driver': parts[3]}
    return None


def query_smi():
    """问 nvidia-smi 要显卡信息。没装驱动 / 没有 N 卡都会返回 None。"""
    rc, out = _run(['nvidia-smi',
                    '--query-gpu=' + _SMI_QUERY, '--format=csv'])
    return parse_smi(out) if rc == 0 else None


def query_wmi():
    """没有 nvidia-smi 时的兜底：问 Windows 要显卡名单。

    用途是**区分两种失败**：「有 N 卡但驱动没装」和「压根没独显」——
    前者装个驱动就能用，后者只能换机器，给用户的话术完全不同。
    """
    rc, out = _run(['powershell.exe', '-NoProfile', '-Command',
                    'Get-CimInstance Win32_VideoController | '
                    'Select-Object -ExpandProperty Name'])
    if rc != 0:
        return []
    return [x.strip() for x in out.splitlines() if x.strip()]


def _real_nvidia(names):
    """名单里有没有真正的 N 卡（排除虚拟显示器）。"""
    for n in names or []:
        low = n.lower()
        if any(f in low for f in _FAKE_GPU):
            continue
        if 'nvidia' in low or 'geforce' in low or 'quadro' in low or 'tesla' in low:
            return n
    return None


def judge(g, wmi_names=None):
    """够不够用。返回 {ok, why, gpu}。why 直接显示给用户，必须是人话。"""
    if g:
        if g['compute_cap'] < MIN_COMPUTE_CAP:
            return {'ok': False, 'gpu': g,
                    'why': ('显卡是「%s」，架构太老（需要 2018 年后的 RTX / GTX 16 系'
                            '及更新的型号），会退回 CPU 转换 —— 实测比显卡慢约 2 倍：'
                            '10 页的讲义约 8 分钟。能用，就是得等。' % g['name'])}
        if g['vram_mb'] < MIN_VRAM_MB:
            return {'ok': False, 'gpu': g,
                    'why': ('显卡「%s」的显存只有 %.1f GB，不足 6 GB，'
                            '页数多的书可能中途失败。失败的话会退回 CPU，'
                            '实测慢约 2 倍：10 页的讲义约 8 分钟。'
                            % (g['name'], g['vram_mb'] / 1024.0))}
        return {'ok': True, 'gpu': g,
                'why': '显卡「%s」，显存 %.1f GB，满足要求。'
                       % (g['name'], g['vram_mb'] / 1024.0)}

    card = _real_nvidia(wmi_names)
    if card:
        return {'ok': False, 'gpu': None,
                'why': ('检测到显卡「%s」，但没能读到它的信息 —— '
                        '多半是显卡驱动没装或版本太旧。装好驱动再打开就能用。'
                        % card)}
    # 「慢很多」是废话。2026-08-31 实测同一份 10 页讲义：
    # GPU 262 秒、纯 CPU 460 秒 —— 慢 2.0 倍，不是十几倍。没显卡是能用的。
    # 给具体分钟数，让人自己判断划不划算。
    return {'ok': False, 'gpu': None,
            'why': ('这台电脑没有独立显卡，会用 CPU 转换 —— 实测比显卡慢约 2 倍：'
                    '10 页的讲义约 8 分钟，30 页的约 23 分钟。能用，就是得等。')}


def detect():
    """完整检测。**不抛异常** —— 首次启动就崩是最糟的第一印象。"""
    g = query_smi()
    return judge(g, wmi_names=None if g else query_wmi())
