# -*- coding: utf-8 -*-
r"""上游有没有出新版本。**发版前跑**（docs/RELEASE.md 第一节）。

跑法：.venv\Scripts\python.exe tools\check_upstream.py

跟另外三个检查的分工：

    check_docs      数字对不对、提到的文件在不在   —— 硬事实
    check_claims    文档说的行为跟代码一不一致     —— 行为断言
    check_package   打出来的包干不干净             —— 产物
    check_upstream  上游有没有我们还没跟进的版本   —— 外部世界   ← 本文件

## 为什么要有这个

小蔡 2026-09-05：「每次建立预发行版的时候，必须强制提醒我看 torch 和
min 模型有没有更新。」

不是自动升级 —— 版本稳定性由人说了算。这个脚本只负责**摆出事实并拦住
打包**，让人看一眼再决定。

## 查五件事

  1. mineru 本地版本 vs PyPI 最新正式版（排除 alpha）
  2. torch 各通道本地版本 vs 该通道最新
  3. 钉在 upgrade 段里的版本还在不在源上（PyTorch 删过旧 wheel）
  4. 官方有没有出新的 CUDA 通道（代码里只有三条）
  5. 模型仓库有没有更新（modelscope 的 LastUpdatedTime）

## 没网怎么办

`--offline` 跳过，但**打包日志里要留记录**「本次未做上游检查」。
不能静默跳过 —— 那样这道门禁等于不存在。

## 解析失败按「查不了」处理

🔴 PyPI 或 PyTorch 官网改版导致解析失败时，**报「查不了」而不是
「没有更新」**。这两个混了就是假绿灯。
"""
import io
import json
import os
import re
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
TIMEOUT = 20

# 代码里认的 CUDA 通道。多出来的要提醒人评估。
KNOWN_CHANNELS = ('cu118', 'cu126', 'cu128')

_PY = sys.executable


def say(msg):
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        # Windows 控制台默认 GBK，emoji 打不出来会抛异常，
        # 别把整个检查炸在一个装饰符号上（build_release 栽过）。
        enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
        print(msg.encode(enc, 'replace').decode(enc, 'replace'), flush=True)


def _pip_versions(pkg, index_url=None):
    """(最新, 全部, 错误)。查不到时最新是空串。"""
    argv = [_PY, '-m', 'pip', 'index', 'versions', pkg]
    if index_url:
        argv += ['--index-url', index_url]
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=60)
    except Exception as e:
        return '', [], '%s: %s' % (type(e).__name__, str(e)[:50])
    out = (p.stdout or b'').decode('utf-8', 'replace')
    if p.returncode != 0:
        tail = [x for x in out.strip().splitlines() if x.strip()]
        return '', [], (tail[-1][:90] if tail else '查不到')
    m = re.search(r'^\s*LATEST:\s*(\S+)', out, re.M)
    latest = m.group(1) if m else ''
    m2 = re.search(r'Available versions:\s*(.+)', out)
    avail = [x.strip() for x in m2.group(1).split(',')] if m2 else []
    if not latest and avail:
        latest = avail[0]
    return latest, avail, ''


def _is_pre(v):
    return bool(re.search(r'(a|b|rc)\d+$', v or ''))


def _local(pkg):
    try:
        import importlib.metadata as md
        return md.version(pkg)
    except Exception:
        return ''


def check_mineru(issues):
    latest, avail, err = _pip_versions('mineru')
    local = _local('mineru')
    if err:
        say('  mineru   本地 %-14s 上游 查不了（%s）' % (local or '未装', err))
        issues.append('mineru 的上游查不了 —— **不能当成「没有更新」**')
        return
    if _is_pre(latest):
        stable = [v for v in avail if not _is_pre(v)]
        latest = stable[0] if stable else ''
    same = latest and latest == local
    say('  mineru   本地 %-14s 上游 %-14s %s'
        % (local or '未装', latest or '查不到', '一致' if same else '⚠ 有更新'))
    if latest and not same:
        issues.append('mineru 有新版 %s（本地 %s）' % (latest, local))


def check_torch(issues):
    local = _local('torch')
    for ch in KNOWN_CHANNELS:
        url = 'https://download.pytorch.org/whl/%s/' % ch
        latest, _a, err = _pip_versions('torch', index_url=url)
        if err:
            say('  torch    %-8s 查不了（%s）' % (ch, err))
            issues.append('torch %s 通道查不了 —— **不能当成「没有更新」**' % ch)
            continue
        mark = ''
        if local and local.endswith('+' + ch):
            mark = '  ← 本机走这条'
            if latest != local:
                issues.append('torch %s 有新版 %s（本地 %s）' % (ch, latest, local))
        say('  torch    %-8s 最高 %-18s%s' % (ch, latest or '查不到', mark))


def check_new_channels(issues):
    """官方有没有出新通道。代码里只认三条。"""
    try:
        req = urllib.request.Request('https://download.pytorch.org/whl/',
                                     headers=UA)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            html = r.read().decode('utf-8', 'replace')
    except Exception as e:
        say('  新通道   查不了（%s）' % str(e)[:50])
        issues.append('通道列表查不了 —— **不能当成「没有新通道」**')
        return
    found = sorted(set(re.findall(r'href="(cu\d+)/?"', html)))
    # 🔴 **只报比我们最高那条更新的。** 官方那个页面上挂着 cu75 到
    #    cu132 十几年的通道，全列出来是噪音 —— 而噪音会让人直接忽略
    #    整条提醒，那这道门禁就白做了。
    def num(c):
        try:
            return int(c[2:])
        except ValueError:
            return 0

    top = max(num(c) for c in KNOWN_CHANNELS)
    extra = [c for c in found if c not in KNOWN_CHANNELS and num(c) > top]
    if not extra:
        say('  新通道   没有（代码里认的三条是全的）')
        return
    say('  新通道   官方还有 %s' % ', '.join(extra))
    # 🔴 不是「发现新通道就一定要加」——加了可能让用户拿到更旧的 torch。
    #    cu129 只发到 2.9.0，比 cu128 的 2.11.0 还旧；通道表是从上往下
    #    第一个匹配就返回的，加进去反而是负优化。所以只提醒，不强求。
    issues.append('官方有新通道 %s —— 评估一下要不要加'
                  '（注意 cu 号大不等于版本新）' % ', '.join(extra))


def check_pinned(issues):
    """upgrade 段里钉的版本还在不在源上。PyTorch 删过旧 wheel。"""
    import glob
    files = sorted(glob.glob(os.path.join(ROOT, 'dist', 'requires-*.json')))
    if not files:
        say('  钉的版本 还没发过版，跳过')
        return
    try:
        d = json.load(io.open(files[-1], encoding='utf-8'))
    except Exception as e:
        say('  钉的版本 读不了 %s（%s）' % (os.path.basename(files[-1]), e))
        return
    up = d.get('upgrade') or {}
    pins = []
    for name, node in up.items():
        if not isinstance(node, dict):
            continue
        if 'ok' in node:
            if node.get('to'):
                pins.append((name, '', node['to']))
        else:
            for ch, sub in node.items():
                if isinstance(sub, dict) and sub.get('to'):
                    pins.append((name, ch, sub['to']))
    if not pins:
        say('  钉的版本 没有钉任何版本（upgrade 段全空，正常）')
        return
    for name, ch, ver in pins:
        url = ('https://download.pytorch.org/whl/%s/' % ch) if ch else None
        _l, avail, err = _pip_versions(name, index_url=url)
        if err:
            issues.append('%s%s 钉的 %s 查不了' % (name, ' ' + ch if ch else '', ver))
            continue
        if ver not in avail:
            say('  钉的版本 %s%s = %s  ⚠ 源上没有了' % (name, ' ' + ch if ch else '', ver))
            issues.append('%s%s 钉的 %s 在源上找不到了'
                          % (name, ' ' + ch if ch else '', ver))
        else:
            say('  钉的版本 %s%s = %s  还在' % (name, ' ' + ch if ch else '', ver))


def check_models(issues):
    repos = ['OpenDataLab/PDF-Extract-Kit-1.0',
             'OpenDataLab/MinerU2.5-Pro-2605-1.2B']
    import time
    for repo in repos:
        url = 'https://modelscope.cn/api/v1/models/' + repo
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                d = json.loads(r.read().decode('utf-8'))
            t = (d.get('Data') or {}).get('LastUpdatedTime') or 0
        except Exception as e:
            say('  模型     %-32s 查不了（%s）' % (repo.split('/')[-1], str(e)[:40]))
            issues.append('模型 %s 查不了 —— **不能当成「没有更新」**'
                          % repo.split('/')[-1])
            continue
        say('  模型     %-32s 上游 %s 更新'
            % (repo.split('/')[-1],
               time.strftime('%Y-%m-%d', time.localtime(int(t))) if t else '?'))


def main():
    offline = '--offline' in sys.argv
    say('上游检查')
    say('=' * 60)
    if offline:
        say('')
        say('⚠️ --offline：跳过本次上游检查')
        say('   **这件事要记进打包日志** —— 静默跳过等于这道门禁不存在。')
        return 0

    issues = []
    say('')
    check_mineru(issues)
    say('')
    check_torch(issues)
    say('')
    check_new_channels(issues)
    say('')
    check_pinned(issues)
    say('')
    check_models(issues)

    say('')
    say('=' * 60)
    if not issues:
        say('上游没有需要跟进的东西')
        return 0

    say('')
    say('有 %d 项要你看一眼：' % len(issues))
    for x in issues:
        say('  · ' + x)
    say('')
    say('这不是错误 —— **升不升由你定**。看完之后：')
    say('  · 决定不升 → 在 dist/requires-<版本>.json 的 upgrade 段里')
    say('    写 ok:false 加理由，用户界面上会显示那句话')
    say('  · 决定要升 → 先在本机实测，再写 ok:true 和目标版本')
    say('  · 还没测   → 保持 ok:null，界面显示「我们没测过，你自己定」')
    say('')
    say('确认过了就用 --offline 跳过这道检查继续打包。')
    return 1


if __name__ == '__main__':
    sys.exit(main())
