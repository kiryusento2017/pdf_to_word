# -*- coding: utf-8 -*-
r"""检查更新 / 下载更新包。

仓库：https://github.com/kiryusento2017/pdf_to_word （public）

## 更新的是发行版，不是源码

**查的是 GitHub Release，不是 commit。** 源码 archive 里有测试、文档、
开发脚本，那些不该发给老师；Release 的 asset 才是打包好的业务代码
（约 0.9 MB）。版本用 Release 的 tag，还能带上 release notes 让用户
看见「这次改了什么」。

分层里只有业务代码需要更新 —— Electron 380 MB、pandoc 223 MB、
torch 4.2 GB、模型 4.6 GB 都不动。改的部分只占万分之一，
所以绝不整包推。

## 为什么查版本和下文件走两条不同的路

2026-09-01 实测（本机）：

    raw 直连          失败    SSL 被切断
    raw + gh-proxy    200     4.54 秒
    raw + ghfast      200     1.38 秒
    api 直连          200     1.14 秒
    api + gh-proxy    403     镜像明确拒绝代理 API

所以：**查版本走 api.github.com 直连**（镜像会 403），
**下文件走镜像**（直连会被切断）。

## 镜像不可信，所以并发测速

六个候选实测当场坏三个（SSL 失败、HTTP 200 但不吐数据）。这类服务
死亡率高（fastgit 当年也是第一名，现在没了）。GitHub 上「镜像可用性
统计」类的仓库 star 都是个位数，靠不住 —— 用一个没人维护的列表去解决
「镜像会挂」，等于把问题换个地方。所以：候选写一串、并发实测、谁快用谁，
复用 `sources.probe_all`。
"""
import io
import json
import os
import urllib.request

import paths
import sources

OWNER = 'kiryusento2017'
REPO = 'pdf_to_word'

API_TIMEOUT = 10
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

# 下文件用的镜像。**每个都可能挂**，所以并发测速谁快用谁。
# prefix 拼在完整的 github.com URL 前面（gh-proxy 那类的用法）。
# 直连也留一条：某些网络下反而是它通。
GH_MIRRORS = [
    {'id': 'ghfast', 'name': 'ghfast.top', 'prefix': 'https://ghfast.top/'},
    {'id': 'gh-proxy', 'name': 'gh-proxy.com', 'prefix': 'https://gh-proxy.com/'},
    {'id': 'ghproxy-net', 'name': 'ghproxy.net', 'prefix': 'https://ghproxy.net/'},
    {'id': 'moeyy', 'name': 'moeyy.xyz', 'prefix': 'https://github.moeyy.xyz/'},
    {'id': 'direct', 'name': 'GitHub 官方', 'prefix': ''},
]

VERSION_FILE = os.path.join(paths.ROOT, 'version.json')


# ── 本地版本 ────────────────────────────────────────────────────────────
def local_version():
    r"""这份发行版是哪个 Release。

    {tag, published_at}。打包脚本负责写进 version.json；
    读不到就是「不知道」，检查更新时会说清楚而不是瞎猜。
    """
    try:
        with io.open(VERSION_FILE, encoding='utf-8') as f:
            d = json.load(f)
        return {'tag': d.get('tag', ''), 'published_at': d.get('published_at', '')}
    except Exception:
        return {'tag': '', 'published_at': ''}


def write_version(tag, published_at=''):
    """打包脚本用：记下这个包是哪个 Release。"""
    with io.open(VERSION_FILE, 'w', encoding='utf-8') as f:
        json.dump({'tag': tag, 'published_at': published_at},
                  f, ensure_ascii=False, indent=2)


# ── 查远端 ──────────────────────────────────────────────────────────────
def _api(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=API_TIMEOUT) as r:
        return json.loads(r.read().decode('utf-8'))


def _pick_asset(rel):
    """从 Release 的附件里挑更新包。约定是那个 .zip。"""
    for a in (rel.get('assets') or []):
        if (a.get('name') or '').lower().endswith('.zip'):
            return {'name': a['name'], 'url': a.get('browser_download_url', ''),
                    'size': a.get('size', 0)}
    return None


def check():
    r"""查有没有新版本。返回 dict，**不抛异常**。

    {ok, has_update, local, latest, notes, published, asset, error}
    """
    out = {'ok': False, 'has_update': False, 'local': '', 'latest': '',
           'notes': '', 'published': '', 'asset': None, 'error': ''}
    loc = local_version()
    out['local'] = loc['tag'] or '(未知)'

    try:
        rel = _api('https://api.github.com/repos/%s/%s/releases/latest'
                   % (OWNER, REPO))
    except Exception as e:
        if '404' in str(e):
            out['ok'] = True
            out['error'] = '仓库里还没有发布任何版本'
            return out
        out['error'] = '连不上 GitHub：%s' % str(e)[:120]
        return out

    out['ok'] = True
    out['latest'] = rel.get('tag_name') or ''
    out['published'] = (rel.get('published_at') or '')[:10]
    out['notes'] = (rel.get('body') or '').strip()[:600]
    out['asset'] = _pick_asset(rel)

    if not loc['tag']:
        out['error'] = '不知道当前是哪个版本（version.json 缺失），没法比较'
        return out
    if loc['tag'] == out['latest']:
        return out                       # 已是最新

    # 🔴 tag 不同**不等于**有更新 —— 也可能本地比远端新（你手动发的测试版）。
    #    比发布时间才不会误报：只有远端更晚才是真的有新版本。
    if loc['published_at'] and rel.get('published_at'):
        if rel['published_at'] <= loc['published_at']:
            out['error'] = ('本地版本（%s）比仓库里的还新，不用更新'
                            % loc['tag'])
            return out

    out['has_update'] = True
    if not out['asset']:
        out['error'] = '有新版本，但那个 Release 没有附更新包'
        out['has_update'] = False
    return out


# ── 下载 ────────────────────────────────────────────────────────────────
def _mirrored(url, prefix):
    return prefix + url if prefix else url


def probe_mirrors(asset_url, seconds=2.0):
    r"""并发实测各镜像，返回按快慢排好的列表。

    探测的就是待会儿要下的那个文件 —— 用它自己测，测出来的才是真带宽。
    （模型源那边栽过：拿几 KB 的 API 接口测，算出来的是延迟，
      界面显示「约 44 小时」。）
    """
    cand = [{'id': m['id'], 'name': m['name'], 'env': {},
             'probe': _mirrored(asset_url, m['prefix'])} for m in GH_MIRRORS]
    return sources.probe_all(cand, seconds=seconds)


def download(asset_url, dest, on_progress=None, seconds=2.0):
    r"""下更新包。返回 (ok, error, 用了哪个源)。

    先并发测速挑最快的再下 —— 候选里当场坏掉的不在少数（实测六个坏三个），
    不测速就可能卡在一个吐不出数据的源上。
    """
    if not asset_url:
        return False, '没有可下载的更新包', ''
    rows = probe_mirrors(asset_url, seconds=seconds)
    best = sources.pick_best(rows)
    if not best:
        return False, '所有下载源都连不上，检查一下网络', ''

    prefix = ''
    for m in GH_MIRRORS:
        if m['id'] == best['id']:
            prefix = m['prefix']
            break

    try:
        req = urllib.request.Request(_mirrored(asset_url, prefix), headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            total = int(r.headers.get('Content-Length') or 0)
            got = 0
            paths.ensure(os.path.dirname(dest) or paths.ROOT)
            with io.open(dest, 'wb') as f:
                while True:
                    b = r.read(65536)
                    if not b:
                        break
                    f.write(b)
                    got += len(b)
                    if on_progress:
                        on_progress(got, total)
    except Exception as e:
        return False, '下载失败：%s' % str(e)[:120], best['name']
    return True, '', best['name']
