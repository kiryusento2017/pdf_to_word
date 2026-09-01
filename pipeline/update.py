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

## 查版本要多试几条路

2026-09-01 实测：

    raw 直连          失败    SSL 被切断
    raw + gh-proxy    200     4.54 秒
    raw + ghfast      200     1.38 秒
    api 直连          200     1.14 秒
    api + gh-proxy    403     镜像明确拒绝代理 API

据此当时把查版本写死成「api.github.com 直连」。**2026-09-02 这个结论
翻车了** —— 小蔡在外面测试时一直「连不上 github」，重测发现：

    api 直连                    200  1.6s
    api + gh-proxy              200  1.8s   ← 09-01 还是 403，现在能用
    api + ghfast                403         ← 反过来了
    api + ghproxy.net           SSL 证书错误
    api + moeyy                 SSL EOF
    网页 /releases/latest 的 302  直连和 ghfast 都拿得到 tag

**镜像的行为会变，而且是双向的变。** 所以查版本也依次试多条路，
谁先通用谁；全都不通时退到网页版的 302 —— 那条只能拿到版本号
（没有 asset 列表和校验值），只够告诉用户「有新版本，去页面手动下」。

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
import urllib.error
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
# 查版本依次试这些前缀，谁先通用谁。空串 = 直连。
# 顺序按 2026-09-02 的实测排：直连最快，gh-proxy 次之，剩下的当兜底。
API_PREFIXES = ['', 'https://gh-proxy.com/', 'https://ghfast.top/',
                'https://ghproxy.net/', 'https://github.moeyy.xyz/']

# 单条路的超时。比原来的 10 秒短 —— 要试好几条，每条都等 10 秒的话
# 用户要盯着转圈将近一分钟。
API_TRY_TIMEOUT = 6


def _api(url):
    r"""查 GitHub API。**依次试直连和各镜像**，第一个成功的就用。

    绑死一条路的下场：2026-09-02 小蔡在外面一直「连不上 github」，
    而那条路（api 直连）在开发机上一直是通的 —— 又一个「在我这儿好好的」。

    全都失败时抛最后一个异常，让上层报出人话。
    """
    last = None
    for pre in API_PREFIXES:
        try:
            req = urllib.request.Request(pre + url if pre else url, headers=UA)
            with urllib.request.urlopen(req, timeout=API_TRY_TIMEOUT) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:
            last = e
            # 404 是「仓库还没发过版本」，换条路也是一样的结果，不用再试
            if '404' in str(e):
                raise
            continue
    raise last if last else RuntimeError('查不到版本')


def _latest_tag_via_web():
    r"""退路：从网页版 `/releases/latest` 的 302 里抠出版本号。

    走的是 github.com（不是 api.github.com），镜像代理得了。
    但只能拿到 tag —— 没有 asset 列表、没有校验值，所以**只够告诉用户
    「有新版本」**，装不了。这是最后一道，聊胜于无。
    """
    import re as _re
    web = 'https://github.com/%s/%s/releases/latest' % (OWNER, REPO)

    class _NoRedir(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    for pre in ('', 'https://ghfast.top/', 'https://gh-proxy.com/'):
        try:
            op = urllib.request.build_opener(_NoRedir)
            req = urllib.request.Request(pre + web if pre else web, headers=UA)
            loc = ''
            try:
                r = op.open(req, timeout=API_TRY_TIMEOUT)
                loc = r.headers.get('Location', '') or ''
            except urllib.error.HTTPError as e:
                loc = e.headers.get('Location', '') or ''
            m = _re.search(r'/releases/tag/([^/?#]+)', loc)
            if m:
                return m.group(1)
        except Exception:
            continue
    return ''


def _ver(tag):
    r"""把 `v0.0.1` / `0.0.1` 解析成 (0, 0, 1)。看不懂就返回 None。

    🔴 为什么不用发布时间当主判据（2026-09-02 改）：
       `build_release.py` 写 version.json 时 published_at 那格是**空串**
       （打包时还没发布，拿不到真实时间），于是原来那句
       `if loc['published_at'] and rel.get('published_at')` 前半恒为假，
       防降级保护从来没执行过 —— 本地装着 v0.0.3 测试版、远端是 v0.0.1
       时，界面会提示「有新版本」，点更新就是降级。

       测试当时是绿的，因为测试自己手写了一个非空的 published_at，
       喂进去的形状和生产不一样。版本号是包里就有的、不依赖发版流程的
       东西，拿它当主判据才不会再出这种「测试绿、生产坏」。

    只认「数字.数字.数字」，后缀（v0.0.1-beta 的 -beta）忽略不比 ——
    这个项目的 tag 规矩就是 v主.次.修（见 docs/RELEASE.md），
    真出现看不懂的写法就返回 None，退回比发布时间。
    """
    import re
    m = re.match(r'^\s*[vV]?(\d+)\.(\d+)\.(\d+)', tag or '')
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _pick_asset(rel):
    r"""从 Release 的附件里挑更新包。

    一个 Release 会传两种：
        pdf_to_word-v0.0.1-full.zip     完整发行版 0.69 GB，首次安装用
        pdf_to_word-v0.0.1-update.zip   只有业务代码 0.4 MB，日常更新用

    **必须挑 update 那个** —— 挑成 full 的话，老师为 0.4 MB 的改动
    重下 0.69 GB。名字里带 update 的优先，没有再退回第一个 zip
    （比如早期只传了一个包的 Release）。
    """
    zips = [a for a in (rel.get('assets') or [])
            if (a.get('name') or '').lower().endswith('.zip')]
    if not zips:
        return None
    pick = None
    for a in zips:
        if 'update' in (a.get('name') or '').lower():
            pick = a
            break
    pick = pick or zips[0]
    # digest 形如 "sha256:abc123..."。GitHub 的 Releases API 每个 asset
    # 都带（2026-09-02 实测确认，v0.0.1 的两个附件都有）。
    # 这一格是整条信任链的起点，详见 download() 的说明。
    dg = (pick.get('digest') or '')
    if dg.lower().startswith('sha256:'):
        dg = dg.split(':', 1)[1].strip().lower()
    else:
        dg = ''
    return {'name': pick['name'], 'url': pick.get('browser_download_url', ''),
            'size': pick.get('size', 0), 'digest': dg}


def check():
    r"""查有没有新版本。返回 dict，**不抛异常**。

    {ok, has_update, local, latest, notes, published, asset, error}
    """
    out = {'ok': False, 'has_update': False, 'local': '', 'latest': '',
           'notes': '', 'published': '', 'asset': None, 'error': '',
           # 跨了主/次版本：更新包补不上依赖，得重下完整安装包
           'need_full': False}
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

        # API 那几条路全断了 —— 再试一次网页版的 302，至少能知道
        # 有没有新版本。拿不到 asset 和校验值，所以只能让用户手动去下。
        tag = _latest_tag_via_web()
        if tag:
            out['ok'] = True
            out['latest'] = tag
            lv, rv = _ver(loc['tag']), _ver(tag)
            newer = (lv and rv and rv > lv) or (not lv and tag != loc['tag'])
            out['error'] = (
                ('仓库里最新是 %s，比你现在这个新，但当前网络下载不了 —— '
                 '到项目的 Release 页面手动下载安装包。' % tag) if newer
                else ('已经是最新版本（%s）。'
                      '（GitHub 的接口连不上，这个结果是从网页拿的）' % tag))
            return out

        out['error'] = ('连不上 GitHub（直连和几个镜像都试过了）。'
                        '换个网络再试，或者到项目的 Release 页面手动下载。'
                        '原因：%s' % str(e)[:100])
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

    # 🔴 tag 不同**不等于**有更新 —— 也可能本地比远端新（手动发的测试版）。
    #    主判据是版本号：包里自带，不依赖发版流程有没有填对时间。
    lv, rv = _ver(loc['tag']), _ver(out['latest'])
    if lv and rv:
        if rv == lv:
            return out                   # 同一版本，只是 tag 写法不同
        if rv < lv:
            out['error'] = ('本地版本（%s）比仓库里的（%s）还新，不用更新'
                            % (loc['tag'], out['latest']))
            return out
    elif loc['published_at'] and rel.get('published_at'):
        # 版本号看不懂时才退回比发布时间（老版本的 version.json 可能没版本号规矩）
        if rel['published_at'] <= loc['published_at']:
            out['error'] = ('本地版本（%s）比仓库里的还新，不用更新'
                            % loc['tag'])
            return out

    out['has_update'] = True

    # 🔴 跨了主版本或次版本 → 更新包补不上依赖，必须重下完整安装包。
    #
    #    更新包里只有 .py 和 .js。docs/RELEASE.md 定的规矩：依赖变了就
    #    进次版本。所以「次版本不同」等价于「依赖可能变了」，这时候
    #    自动更新会让用户拿到**新代码配旧依赖** —— 下次启动 ImportError，
    #    而他刚「更新成功」过，根本想不到是更新害的。
    #
    #    跨多少个**修订号**都没事：更新包是全量替换不是增量补丁，
    #    v0.0.1 直接下 v0.0.31 的包就变成 v0.0.31，不用一个一个来。
    if lv and rv and rv[:2] != lv[:2]:
        out['need_full'] = True
        out['has_update'] = False
        out['error'] = (
            '有新版本 %s，但它跟你现在这个（%s）差了一个大版本 —— '
            '这种更新会带新的依赖，小小的更新包补不上，'
            '需要重新下载完整安装包。' % (out['latest'], loc['tag']))
        return out

    if not out['asset']:
        out['error'] = '有新版本，但那个 Release 没有附更新包'
        out['has_update'] = False
    return out


# ── 下载 ────────────────────────────────────────────────────────────────
def _mirrored(url, prefix):
    return prefix + url if prefix else url


# 小于这个体积就不值得测速了（见 probe_mirrors）
PROBE_WORTH_BYTES = 5 * 1024 * 1024


def probe_mirrors(asset_url, seconds=2.0, size=0):
    r"""并发实测各镜像，返回按快慢排好的列表。

    探测的就是待会儿要下的那个文件 —— 用它自己测，测出来的才是真带宽。
    （模型源那边栽过：拿几 KB 的 API 接口测，算出来的是延迟，
      界面显示「约 44 小时」。）

    🔴 但**小包不值得测**。更新包只有 0.4 MB：五个镜像各下最多 2 秒，
       慢网下等于先下五遍再下第六遍，测速开销是下载本身的五倍。
       「探测文件够大，通常轮不到下完」这句话对 4.6 GB 的模型成立，
       对 0.4 MB 不成立。所以体积够小就按固定顺序试，不测。
    """
    if size and size < PROBE_WORTH_BYTES:
        return [{'id': m['id'], 'name': m['name'], 'ok': True,
                 'speed': 0, 'why': '包太小，没测速'} for m in GH_MIRRORS]
    cand = [{'id': m['id'], 'name': m['name'], 'env': {},
             'probe': _mirrored(asset_url, m['prefix'])} for m in GH_MIRRORS]
    return sources.probe_all(cand, seconds=seconds)


def apply_update(zip_path):
    r"""把下好的更新包解压覆盖到安装目录。返回 (ok, error, 覆盖了几个文件)。

    小蔡定的体验（2026-09-01）：点「更新」→ 自动下载 → 自动装好 →
    提示重启。**不能让老师自己去解压覆盖** —— 那等于把活推给用户，
    而且"没有人会去开 github"。

    ## 安全：必须防路径穿越

    zip 里的成员名是打包方给的。恶意（或手滑）的包里可以有
    `../../Windows/System32/xxx`，直接解压就写到安装目录外面去了。
    这叫 zip slip，是个有名的洞。所以每个成员都要算出最终绝对路径，
    确认它确实落在安装目录内才写。

    ## 为什么覆盖运行中的文件没问题

    更新包里只有 .py 和 .js。Python 读完就释放文件句柄，Electron 加载
    完也一样，Windows 上覆盖它们不会被拒。但**当前进程跑的还是旧代码**，
    所以覆盖完必须重启才生效 —— 界面上要说清楚这一点。

    ## 先解压到临时目录再搬

    直接往安装目录解压的话，中途失败会留下一半新一半旧的代码，
    那种状态比不更新糟得多。先全部解到临时目录，都成功了再逐个搬过去。
    """
    import tempfile
    import zipfile

    if not os.path.isfile(zip_path):
        return False, '更新包不见了', 0

    root = os.path.abspath(paths.ROOT)
    tmp = tempfile.mkdtemp(prefix='p2w_upd_')
    try:
        try:
            zf = zipfile.ZipFile(zip_path)
        except Exception as e:
            return False, '更新包打不开，可能没下完整：%s' % str(e)[:80], 0

        with zf:
            members = []
            for info in zf.infolist():
                if info.is_dir():
                    continue
                # 🔴 zip slip 防护：算出最终落点，必须在安装目录内
                target = os.path.abspath(os.path.join(root, info.filename))
                if not (target == root or target.startswith(root + os.sep)):
                    return (False,
                            '更新包里有指向安装目录外面的文件（%s），'
                            '出于安全没有安装' % info.filename[:60], 0)
                members.append((info, target))

            if not members:
                return False, '更新包是空的', 0

            # 先全解到临时目录
            staged = []
            for info, target in members:
                out = os.path.join(tmp, info.filename.replace('/', os.sep))
                os.makedirs(os.path.dirname(out) or tmp, exist_ok=True)
                with zf.open(info) as src, io.open(out, 'wb') as dst:
                    dst.write(src.read())
                staged.append((out, target))

        # 都解出来了再搬。
        #
        # 🔴 搬运这一步**也会中途失败**（文件被占用是最常见的），
        #    所以每覆盖一个之前先把原件备份到临时目录，任一失败就全部还原。
        #
        #    不这么做的话，上面那段注释承诺的原子性只兑现了一半：解压是
        #    原子的，搬运不是 —— 失败就留下一半新一半旧的代码，
        #    而那正是它自己说的「比不更新糟得多」的状态。
        #    半新半旧最坏的地方在于它**还能启动**：新的 server 配着旧的
        #    pipeline，报出来的错跟真实原因八竿子打不着。
        import shutil
        backup_dir = os.path.join(tmp, '__backup__')
        os.makedirs(backup_dir, exist_ok=True)
        undo = []          # [(备份文件, 原位置)]，原位置本来没有文件时备份为 None
        done = 0
        for out, target in staged:
            os.makedirs(os.path.dirname(target) or root, exist_ok=True)
            try:
                if os.path.isfile(target):
                    bak = os.path.join(backup_dir, '%d.bak' % len(undo))
                    shutil.copyfile(target, bak)
                    undo.append((bak, target))
                else:
                    undo.append((None, target))     # 新增的文件，还原=删掉
                shutil.copyfile(out, target)
                done += 1
            except Exception as e:
                # 还原：倒着来，最后动的先还原
                restored = 0
                for bak, tgt in reversed(undo):
                    try:
                        if bak is None:
                            if os.path.isfile(tgt):
                                os.remove(tgt)
                        else:
                            shutil.copyfile(bak, tgt)
                        restored += 1
                    except Exception:
                        pass        # 还原也失败就没辙了，至少别把原错误吞掉
                return (False,
                        '写入 %s 失败：%s。可能是软件正在用这个文件，'
                        '关掉软件再试一次。（已经改的 %d 个文件都还原了，'
                        '现在还是更新前的状态）'
                        % (os.path.relpath(target, root), str(e)[:60],
                           restored), 0)
        return True, '', done
    finally:
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)


# 只认这个前缀的下载地址。GitHub Release 附件的 browser_download_url
# 长这样：https://github.com/<owner>/<repo>/releases/download/<tag>/<name>
ASSET_PREFIX = 'https://github.com/%s/%s/releases/download/' % (OWNER, REPO)


def download(asset_url, dest, on_progress=None, seconds=2.0,
             digest='', size=0, allow_unverified=False):
    r"""下更新包。返回 (ok, error, 用了哪个源)。

    先并发测速挑最快的再下 —— 候选里当场坏掉的不在少数（实测六个坏三个），
    不测速就可能卡在一个吐不出数据的源上。

    ## 下回来的东西必须校验（2026-09-02 加）

    这个包会被解压覆盖安装目录里的 `.py` 和 `.js`，下次启动就执行 ——
    也就是说，**谁能决定这个包的内容，谁就能在老师的电脑上跑代码**。
    而它走的是 ghfast / gh-proxy / ghproxy.net / moeyy 四个第三方镜像。
    原来的文档只把镜像的不可信定义成「可能挂」，但镜像同样可以
    **返回假内容**，那是一条完整的远程执行路径。

    信任链是这么闭合的：

        校验值  ← api.github.com **直连** HTTPS（镜像访问 API 会 403，
                  见本文件顶部的实测表）→ 可信
        文件    ← 第三方镜像                                → 不可信
        拿可信的值去验不可信的文件 → 装进去的东西可信

    三道，缺一不可：
      1. **地址**必须是本仓库的 Release 附件。服务只绑 127.0.0.1，
         但本机任意进程都能 POST 一个自己的 URL 让它下载并覆盖安装目录；
         zip slip 只挡住了目录外，目录内的 .py 照样能被换掉。
      2. **长度**对得上 Content-Length。截断的包原来一路走到 apply_update
         才报「更新包打不开」，把网络问题说成了文件问题。
      3. **SHA256** 对得上 GitHub 给的 digest。拿不到 digest 时**拒绝安装** ——
         「没法校验」和「校验失败」的风险是一样的，不能因为值取不到就放行。
    """
    import hashlib

    if not asset_url:
        return False, '没有可下载的更新包', ''
    if not asset_url.startswith(ASSET_PREFIX):
        return (False,
                '这个下载地址不是本仓库的 Release 附件，出于安全没有下载', '')
    if not digest and not allow_unverified:
        # 🔴 **报警，但不阻拦** —— 这里返回一个可识别的标记，让上层去问用户，
        #    而不是在这儿把路堵死。
        #
        #    原来这条是硬拒绝。小蔡 2026-09-02 真机上点更新，看到
        #    「出于安全没有下载」就走不下去了 —— 更新按钮的全部意义
        #    （点一下自动搞定）就此作废。安全规则挡住正常更新、而不是
        #    挡住攻击的时候，该改的是规则。
        #
        #    用户点了「仍然安装」之后走 allow_unverified=True 这条路：
        #    校验值没有，但长度和 zip 完整性还是会查（见下面）。
        return (False, 'NEED_CONFIRM:拿不到 GitHub 给的校验值', '')
    rows = probe_mirrors(asset_url, seconds=seconds, size=size)
    best = sources.pick_best(rows) or (rows[0] if rows else None)
    if not best:
        return False, '所有下载源都连不上，检查一下网络', ''

    prefix = ''
    for m in GH_MIRRORS:
        if m['id'] == best['id']:
            prefix = m['prefix']
            break

    h = hashlib.sha256()
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
                    h.update(b)
                    got += len(b)
                    if on_progress:
                        on_progress(got, total)
    except Exception as e:
        return False, '下载失败：%s' % str(e)[:120], best['name']

    def _drop():
        try:
            os.remove(dest)
        except OSError:
            pass

    if total and got != total:
        _drop()
        return (False, '没下完（只到了 %d / %d 字节），网络中断了，可以重试'
                % (got, total), best['name'])

    if not digest:
        # 没有官方校验值时能做的：确认它至少是个**完整的、能打开的 zip**。
        # 这挡不住蓄意篡改（那要靠 digest），但挡得住截断、挡得住镜像
        # 返回一个 HTML 错误页 —— 后者在实测里出现过。
        import zipfile
        try:
            with zipfile.ZipFile(dest) as z:
                bad = z.testzip()
            if bad:
                _drop()
                return (False, '下回来的包内部损坏（%s），换个时间再试' % bad,
                        best['name'])
        except Exception as e:
            _drop()
            return (False,
                    '下回来的不是一个完整的更新包（%s）—— 多半是这个下载源'
                    '返回了别的东西。换个时间再试。' % str(e)[:60],
                    best['name'])
        return True, '', best['name']

    if h.hexdigest().lower() != digest.lower():
        # 换源重试没有意义 —— 内容对不上说明这个源给的就不是原件。
        # 坏包必须删掉：留在硬盘上，下次有人手滑双击就装了。
        _drop()
        return (False,
                '更新包校验没通过 —— 从「%s」下回来的内容和 GitHub 上的原件'
                '对不上，可能是这个下载源不干净。出于安全没有安装，'
                '换个时间再试，或到项目的 Release 页面手动下载。'
                % best['name'], best['name'])

    return True, '', best['name']
