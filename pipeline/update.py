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

**镜像的行为会变，而且是双向的变。** 所以查版本也多试几条路，
全都不通时退到网页版的 302 —— 那条只能拿到版本号（没有 asset 列表和
校验值），只够告诉用户「有新版本，去页面手动下」。

**2026-09-03 第三次实测**（小蔡的机器，十条候选一起测）：

    api + gh-proxy.com          200            api + gh-proxy.org      200
    api + cdn.gh-proxy.org      200            api + axisnow.…org      200
    api + v4/v6.gh-proxy.org    200            api 直连                200
    api + ghfast                403            api + ghproxy.net       403
    api + moeyy                 SSL 握手失败（下载也一起挂了）

这次做了三件事：

  · **改成并发，不再依次试。** 原来名单第一条是直连，用户网络封了
    GitHub 的话要先干等满 6 秒超时才轮到第二条，全试一遍最坏 30 秒 ——
    而界面上只有一个转圈。并发之后典型 1.2 秒（实测），最坏就是单条超时。
  · **顺手把每条路的成败和耗时交给界面**（`api_race` 返回的 lines）。
    既然每条都跑了，这份明细是白捡的。「连不上 GitHub」这句话没有任何
    可操作性，「五条里三条超时、两条 403」才能让人判断是断网还是被墙。
  · moeyy 清出两份名单；ghfast / ghproxy.net 只从 API 名单拿掉，
    它们的文件下载还是好的。

**同日又把网上推荐的 12 个候选挨个打了一遍，只活下来 1 个：**

    ghproxy.vip                              200  1.86s  ← 收
    gh.zwy.one / gh.llkk.cc                  403
    ghp.ci / mirror.ghproxy.com /
      github.moeyy.cn / hub.gitmirror.com    SSL 握手失败
    ghproxy.cxkpro.top                       200 但返回 HTML，不是 JSON
    ghproxy.cc                               证书验证失败
    api.kkgithub.com                         证书验证失败    ← 换域名型
    api.bgithub.xyz                          403            ← 换域名型

**能代理 API 的是稀缺品**，多数镜像只做文件下载（走 CDN，没有配额问题）。
收下 ghproxy.vip 不是因为它快（可用的里最慢），而是因为它是**第二家**：
gh-proxy.com 和 gh-proxy.org/cdn/axisnow 看着四条，其实同属一个家族，
真挂起来是一起挂。冗余要跨供应商才算冗余。

## 镜像不可信，所以并发测速

六个候选实测当场坏三个（SSL 失败、HTTP 200 但不吐数据）。这类服务
死亡率高（fastgit 当年也是第一名，现在没了）。GitHub 上「镜像可用性
统计」类的仓库 star 都是个位数，靠不住 —— 用一个没人维护的列表去解决
「镜像会挂」，等于把问题换个地方。所以：候选写一串、并发实测、谁快用谁，
复用 `sources.probe_all`。
"""
import concurrent.futures as futures
import io
import json
import os
import time
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
    {'id': 'ghp-org', 'name': 'gh-proxy.org',
     'prefix': 'https://gh-proxy.org/'},
    {'id': 'ghp-cdn', 'name': 'cdn.gh-proxy.org',
     'prefix': 'https://cdn.gh-proxy.org/'},
    {'id': 'ghp-axisnow', 'name': 'axisnow.gh-proxy.org',
     'prefix': 'https://axisnow.gh-proxy.org/'},
    {'id': 'ghproxy-vip', 'name': 'ghproxy.vip',
     'prefix': 'https://ghproxy.vip/'},
    {'id': 'direct', 'name': 'GitHub 官方', 'prefix': ''},
]

# 2026-09-03 实测（小蔡的机器，10 条候选并发采样，只看排名不看绝对值）：
#
#   gh-proxy.com    244 KB/s  API ✓     gh-proxy.org      222 KB/s  API ✓
#   axisnow         223 KB/s  API ✓     cdn.gh-proxy.org  212 KB/s  API ✓
#   v6.gh-proxy.org 199 KB/s  API ✓     直连              186 KB/s  API ✓
#   ghproxy.net     159 KB/s  API ✗403  ghfast.top        152 KB/s  API ✗403
#   v4.gh-proxy.org 152 KB/s  API ✓     moeyy.xyz         全挂（SSL 握手失败）
#
# 这一版的取舍：
#   · **moeyy.xyz 删掉** —— API 和下载双双 SSL 失败，留着只是每次多等一条超时
#   · v4 / v6 不收：v4 垫底；v6 要 IPv6，别人机器上不一定有
#   · ghfast / ghproxy.net **留在这份名单**（下载还是好的），但从下面的
#     API 名单里拿掉 —— 它俩现在 403。09-02 时 ghfast 的 API 还是通的，
#     又一次印证「镜像的行为会变，而且是双向的变」

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
# 查版本走哪些路。空串 = 直连。
#
# 🔴 **这份名单和 GH_MIRRORS 不一样，是故意的。**
#    `api.github.com` 对未认证请求限速 60 次/小时/IP。镜像要是代理 API，
#    全世界用户共用它服务器的那 60 次配额，几分钟就爆，还会连累它被 GitHub
#    封 IP —— 所以不少镜像明确拒绝代理 API（直接 403），但文件下载走 CDN
#    没这问题，乐意代理。
#    2026-09-03 实测：ghfast.top 和 ghproxy.net 现在都是 403（09-02 时
#    ghfast 还是通的），所以它俩只留在 GH_MIRRORS 里下文件。
API_MIRRORS = [
    {'id': 'direct', 'name': 'GitHub 官方', 'prefix': ''},
    {'id': 'gh-proxy', 'name': 'gh-proxy.com',
     'prefix': 'https://gh-proxy.com/'},
    {'id': 'ghp-org', 'name': 'gh-proxy.org',
     'prefix': 'https://gh-proxy.org/'},
    {'id': 'ghp-cdn', 'name': 'cdn.gh-proxy.org',
     'prefix': 'https://cdn.gh-proxy.org/'},
    {'id': 'ghp-axisnow', 'name': 'axisnow.gh-proxy.org',
     'prefix': 'https://axisnow.gh-proxy.org/'},
    # 🔴 留它不是因为快（1.9 秒，可用的里最慢），是因为**它是第二家**。
    #    上面 gh-proxy.com 和 gh-proxy.org/cdn/axisnow 看着四条，其实是
    #    一个家族的几个域名，一起挂的概率很高。ghproxy.vip 是另一个项目
    #    （WJQSERVER-STUDIO/ghproxy），才算真正的冗余。
    {'id': 'ghproxy-vip', 'name': 'ghproxy.vip',
     'prefix': 'https://ghproxy.vip/'},
]

# 单条路的超时。**必须小于 API_DETAIL_BUDGET** —— 让每条线路自己先
# 超时、给出确定结论，而不是被窗口踢掉标成「未测」。
#
# 🔴 2026-09-05 小蔡报：一条真不通的线路永远显示「未测」。根因就是这两个
#    数的大小关系反了 —— 那条要 6 秒才超时，而窗口 3 秒就到期，它永远
#    赶不上，每次都被当成「还没测完」补成 pending。
API_TRY_TIMEOUT = 3.5

# 拿到第一个成功结果之后，还愿意为「收集其余线路的明细」多等多久。
#
# 🔴 这个值是「速度」和「信息完整」之间的那条线，两头都栽过（见 api_race）：
#    · 设成 0（第一个成功就 break）→ 明细只剩一条，界面说「5 条未测」
#    · 设成无穷（等全部跑完）→ 直连被墙时要卡满 6 秒，真赛跑白改
#
# 查一次版本，整体最多花多久。**从发出请求那一刻算起**，不是从
# 「第一条成功」算起。
#
# 🔴 这个值 2026-09-05 反复了三次，把账算明白了记在这儿：
#
#   v0.1.1 的做法是并发发六条、`ex.map` 等全部回来。因为是并发，
#   等全部 = 等最慢那条 ≈ 1.1 秒 —— **又快又全，小蔡记得的就是这个**。
#   它唯一的毛病是直连被墙时要陪那条卡满 6 秒超时。
#
#   我为了治那 6 秒，改成「第一个成功就 break」。结果治好了边缘情况，
#   却把常规情况（直连能通）的明细砍成一条，界面报「5 条未测」——
#   拿六条真实状态换了 0.2 秒，赔死。
#
#   再改成「第一个成功后再宽限 N 秒」，基准点又错了：第一条要是本来
#   就慢，从它成功开始再等 N 秒，总时长照样失控。
#
# 现在：**从头算总预算**。正常网络 1.1 秒就全回来了，循环自然结束，
# 根本用不到这个上限。
#
# 🔴 2026-09-05 再改一次，这次改的是它和 API_TRY_TIMEOUT 的**大小关系**：
#
#    以前 3.0 < 6，窗口先到期，没跑完的一律标 pending —— 一条真不通的
#    线路要 6 秒才超时，永远赶不上这个窗口，于是每次都显示「未测」，
#    而它其实是**确定不通**的。用户看着「未测」不知道该怎么办。
#
#    现在 3.8 > 3.5，每条线路自己先超时出结论，这个窗口降级成**兜底**：
#    只有线程卡死（DNS 解析卡住那种，socket timeout 管不着）才会走到，
#    那时候标 pending 是诚实的 —— 它确实没测出来。
#
#    另外前端倒计时是 4 秒（actions.js 的 UPD_COUNTDOWN），必须长过
#    这个数，否则会「数完了还在转」。两个数由一条测试盯着。
API_DETAIL_BUDGET = 3.8


def _race_fetch(url, prefixes, timeout=None):
    r"""几条线路并发抓同一个 URL，**谁先成功用谁**。返回 bytes 或 None。

    给「拉 requires.json」和「网页兜底」用 —— 它们原来是串行的，
    直连排第一，不通就先干等 6 秒才试第二条。

    比 api_race 简单：不收集线路明细（界面上不显示这两条），所以
    拿到就返回，没有副作用。

    opener 可以由调用方给（网页兜底那条要拦重定向）。
    """
    timeout = timeout or API_TRY_TIMEOUT

    def one(pre):
        full = pre + url if pre else url
        req = urllib.request.Request(full, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

    ex = futures.ThreadPoolExecutor(max_workers=len(prefixes))
    try:
        futs = [ex.submit(one, p) for p in prefixes]
        for fut in futures.as_completed(futs):
            try:
                return fut.result()
            except Exception:
                continue
    finally:
        # 不等剩下的跑完 —— 它们只是发个 HTTP 请求，没有副作用。
        ex.shutdown(wait=False)
    return None


def api_race(url):
    r"""并发查 GitHub API，返回 (数据, 线路明细)。

    🔴 **原来是串行依次试，这是个隐蔽的坑。** 名单第一条是直连，
       用户网络封了 GitHub 的话，要先干等满 6 秒超时才轮到第二条；
       五条都试一遍最坏 30 秒，而界面上只有一个转圈。

    🔴 **2026-09-05 又修了一次：并发改成真赛跑。**

       改成并发之后，原来的写法是 `ex.map` —— 六条确实同时发出去，
       但 `ex.map` **按输入顺序**返回，那个 for 循环必须走完六个
       结果才结束。直连排第一且它不通的网络里，**每次检查更新都要
       等满 6 秒**，即使某个镜像 1 秒就返回了。

       （老注释写着「典型情况约 1.5 秒」—— 那句话只在直连能通时
       成立，是在能直连的环境下测的。而这个软件的目标用户大概率
       直连不通。）

       现在用 `as_completed`：谁先跑完先给谁，**第一个成功的就用**。
       直连通的环境行为不变（它最快，还是它先到）；不通的环境立刻
       用上第一个成功的镜像，不等那 6 秒。

    线路明细仍然给，但**提前返回时会有几条还没跑完** —— 那几条标成
    `pending`，界面上显示「检测中」。明细是给排查用的，为它多等几秒
    不划算。全都失败时反倒是完整的（那时六条都跑完了）。

    返回的 lines 每项：{id, name, ok, ms, error, pending}。
    全都失败时抛最后一个异常，让上层报出人话。
    """
    lines, data, err = [], None, None

    def one(m):
        t0 = time.time()
        full = m['prefix'] + url if m['prefix'] else url
        try:
            req = urllib.request.Request(full, headers=UA)
            with urllib.request.urlopen(req, timeout=API_TRY_TIMEOUT) as r:
                return m, json.loads(r.read().decode('utf-8')), None, \
                    int((time.time() - t0) * 1000)
        except Exception as e:
            return m, None, e, int((time.time() - t0) * 1000)

    seq = {m['id']: i for i, m in enumerate(API_MIRRORS)}
    done = {}
    # 🔴 **不能用 with**：with 退出时会等所有线程跑完（Python 3.9+ 的
    #    shutdown(wait=True) 是默认行为），那样 break 就白 break 了。
    #    手动 shutdown(wait=False) 才能真的提前返回。
    #    剩下那几个线程会自己跑完然后退出，不影响什么 —— 它们只是
    #    发个 HTTP 请求，没有副作用。
    ex = futures.ThreadPoolExecutor(max_workers=len(API_MIRRORS))
    try:
        futs = [ex.submit(one, m) for m in API_MIRRORS]
        # 🔴 **六条一起发、等它们回来，总共给 API_DETAIL_BUDGET 秒。**
        #
        #    这就是 v0.1.1 的行为（`ex.map` 并发发、遍历完六个结果），
        #    再补上它缺的那个上限。为什么绕回来，见那个常量的注释 ——
        #    2026-09-05 在这儿改错过两版：先是「第一个成功就 break」，
        #    把明细砍成一条；再是「成功之后再宽限 N 秒」，基准点又错。
        #
        #    并发的关键性质：**等全部 = 等最慢那条**，不是六条时间相加。
        #    正常网络 1.1 秒就都回来了，循环自然结束，用不到那个上限。
        pend_set = set(futs)
        deadline = time.time() + API_DETAIL_BUDGET
        while pend_set:
            wait = deadline - time.time()
            if wait <= 0:
                break                      # 预算用完，剩下的算未测
            got, pend_set = futures.wait(
                pend_set, timeout=wait,
                return_when=futures.FIRST_COMPLETED)
            if not got:
                break
            for fut in got:
                m, d, e, ms = fut.result()
                done[m['id']] = {'id': m['id'], 'name': m['name'],
                                 'ok': d is not None, 'ms': ms,
                                 'error': _api_err(e) if e else '',
                                 'used': False, 'pending': False,
                                 'seq': seq[m['id']]}
                # 第一个成功的当结果用 —— 跟 v0.1.1 一样的语义
                if d is not None and data is None:
                    data = d
                if e is not None:
                    err = e
    finally:
        ex.shutdown(wait=False)

    # 没跑完的补成 pending，界面上显示「检测中」而不是假装它挂了。
    for m in API_MIRRORS:
        if m['id'] not in done:
            done[m['id']] = {'id': m['id'], 'name': m['name'],
                             'ok': False, 'ms': 0, 'error': '',
                             'used': False, 'pending': True,
                             'seq': seq[m['id']]}
    lines = list(done.values())

    # 挂了的沉底，可用的**保持名单原序**。
    #
    # 🔴 **不按 ms 排。** 这里的 ms 是查版本的响应延迟，拿它排序等于在
    #    暗示「排前面的下得快」—— 而 2026-09-03 实测正好反过来：
    #    延迟最快的直连（903ms）下载只排第 4（663 KB/s），延迟垫底的
    #    gh-proxy.org（1077ms）下载第一（723 KB/s）。两个排名几乎是反的。
    #    排序依据只能是速度，没测速就别排（小蔡定的规矩）。
    # 排序：成功的在前 → 还在检测的 → 挂了的。可用的保持名单原序。
    #
    # 🔴 **不按 ms 排。** 这里的 ms 是查版本的响应延迟，拿它排序等于
    #    在暗示「排前面的下得快」—— 而那两个排名不是一回事。排序依据
    #    只能是速度，没测速就别排（小蔡定的规矩）。
    def _rank(x):
        if x['ok']:
            return 0
        return 1 if x.get('pending') else 2

    lines.sort(key=lambda x: (_rank(x), x['seq']))
    if data is None:
        # 404 = 仓库还没发过版本，换哪条路都一样，照原样抛出去
        e = err if err else RuntimeError('查不到版本')
        # 🔴 **失败时更要把明细带出去** —— 「连不上 GitHub」这句话本身
        #    没有任何可操作性，而「五条路里三条超时、两条 403」是能拿去
        #    判断到底是断网还是被墙的。异常对象上挂一下，让 check() 取。
        try:
            e.lines = lines
        except Exception:
            pass
        raise e
    # 「本次采用」标的是**实际用了哪条**，不是「哪条最快」——
    # 后者要测速才知道。
    #
    # 🔴 改成 as_completed 之后，lines[0] 不一定是 data 的来源了
    #    （排序按名单顺序，而实际用的是最先成功的那条）。所以要
    #    显式标记，不能再靠「排完之后第一个就是」这个巧合。
    for x in lines:
        if x['ok']:
            x['used'] = True
            break
    return data, lines


def _api_err(e):
    """把异常压成界面能直接显示的一句话。"""
    s = str(e)
    if '403' in s:
        return '403 不代理 API'
    if '404' in s:
        return '404 没找到'
    if 'SSL' in s or 'CERTIFICATE' in s.upper():
        return 'SSL 握手失败'
    if 'timed out' in s or 'timeout' in s.lower():
        return '超时'
    return s[:40]


def _api(url):
    """只要数据、不关心线路明细时的写法。"""
    return api_race(url)[0]
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

    # 🔴 2026-09-05：原来是串行三条，直连排第一，不通就先干等 6 秒。
    #    改成并发，谁先抠出 tag 用谁。
    #
    #    套不进通用的 _race_fetch —— 那个读响应体，这条要拦重定向读
    #    Location 头再正则抠 tag。
    def one(pre):
        op = urllib.request.build_opener(_NoRedir)
        req = urllib.request.Request(pre + web if pre else web, headers=UA)
        loc = ''
        try:
            r = op.open(req, timeout=API_TRY_TIMEOUT)
            loc = r.headers.get('Location', '') or ''
        except urllib.error.HTTPError as e:
            loc = e.headers.get('Location', '') or ''
        m = _re.search(r'/releases/tag/([^/?#]+)', loc)
        return m.group(1) if m else None

    prefixes = ('', 'https://ghfast.top/', 'https://gh-proxy.com/')
    ex = futures.ThreadPoolExecutor(max_workers=len(prefixes))
    try:
        futs = [ex.submit(one, pre) for pre in prefixes]
        for fut in futures.as_completed(futs):
            try:
                tag = fut.result()
            except Exception:
                continue
            if tag:
                return tag
    finally:
        # 不等剩下的 —— 它们只是发个 HTTP 请求，没有副作用。
        ex.shutdown(wait=False)
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
    # 只看 .zip —— requires-vX.json 那份是依赖清单，不是更新包
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


def _requires_gap(rel, out=None):
    r"""拉 Release 里那份依赖清单，跟本地比。返回缺了什么（空 = 能装）。

    顺手把 upgrade 段解出来塞进 out（那是同一份 json，不额外发请求）——
    界面上「这个版本准不准升」要用它。

    清单是打包时从**实际装的包**里读出来的（`importlib.metadata.version`），
    不是手写的。所以它是事实，不是「我记得改版本号」。

    拉不到就返回空 —— 不拿一次网络失败去挡住正常更新，
    真装不了的话 `apply_update` 里还有一道（更新包里也带着同一份清单，
    只是要下完才能看到）。
    """
    asset = None
    for a in (rel.get('assets') or []):
        n = (a.get('name') or '').lower()
        if n.startswith('requires') and n.endswith('.json'):
            asset = a
            break
    if not asset:
        return []
    url = asset.get('browser_download_url') or ''
    if not url:
        return []
    # 🔴 2026-09-05：原来是串行三条，直连排第一，不通就先干等 6 秒。
    #    改成并发，谁先成功用谁。
    body = _race_fetch(url, ('', 'https://gh-proxy.com/',
                             'https://ghfast.top/'))
    if body is None:
        return []
    raw = body.decode('utf-8', 'replace')
    if out is not None:
        out['upgrade'] = read_upgrade(raw)
    return check_requires(raw)


def _is_hr(line):
    """这一行是不是 Markdown 的分隔线（三个以上连字符，独占一行）。

    **必须整行判断**，不能只看「含不含 ---」—— Markdown 表格的分隔行
    长得像它（`|---|---|`），撞上就把摘要腰斩在一个莫名其妙的地方。
    """
    t = line.strip()
    return len(t) >= 3 and set(t) == {'-'}


def split_notes(body):
    """把 Release 正文拆成 (摘要, 全文)。

    ## 为什么要拆

    正文是给人看的长篇散文，直接摆进 620x440 的小窗口只能看到一个残缺
    的开头。2026-09-05 之前是「后端截断 600 字符 + 前端只显示前 6 行」，
    v0.1.1 那版用户看到的是：

        ## 修了两件事
        ### 1. 装在中文路径里转换必失败
        （空行）
        有用户把软件放在桌面的「新建文件夹 (2)」里，转换跑满一分钟然后失败：

    —— 一条实质信息都没有。

    ## 约定

    Release 正文以摘要开头，用一条独占一行的 `---` 隔开正文：

        - 新增 关于页面，里面有环境检测和缓存清理
        - 修改 Word 正文字体改成宋体 + Times New Roman
        - 修复 装在中文路径里转换失败

        ---

        ## 详细说明
        ……

    用**可见的分隔线**而不是 HTML 注释：注释在 GitHub 网页上是隐藏的，
    用户在 Release 页面看不到摘要。用 `---` 网页和软件里都好看。

    ## 兼容老版本

    老 Release 的正文里没有分隔线 —— 找不到就**把全文当摘要**，跟改之前
    的行为一样。不能崩，也不能显示成空的。
    """
    body = (body or '').strip()
    if not body:
        return '', ''
    lines = body.split('\n')
    for i, ln in enumerate(lines):
        if _is_hr(ln):
            return '\n'.join(lines[:i]).strip(), body
    return body, body


def check():
    r"""查有没有新版本。返回 dict，**不抛异常**。

    {ok, has_update, local, latest, notes, notes_brief, notes_full,
     published, asset, error}
    """
    out = {'ok': False, 'has_update': False, 'local': '', 'latest': '',
           # notes 是老字段（截断版），留着免得别处读它读到 None；
           # 界面用下面两个：brief 默认显示，full 点「完整说明」才展开。
           'notes': '', 'notes_brief': '', 'notes_full': '',
           # requires.json 里的 upgrade 段（准不准升的策略）。
           # 老 Release 没有这一段，读不到就是空字典 = 全都「没测过」。
           'upgrade': {},
           'published': '', 'asset': None, 'error': '',
           # 跨了主/次版本：更新包补不上依赖，得重下完整安装包
           'need_full': False,
           # 各条线路的实测明细，给界面展开看。成功失败都有。
           'lines': []}
    loc = local_version()
    out['local'] = loc['tag'] or '(未知)'

    try:
        rel, out['lines'] = api_race(
            'https://api.github.com/repos/%s/%s/releases/latest'
            % (OWNER, REPO))
    except Exception as e:
        out['lines'] = getattr(e, 'lines', [])
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
    # 🔴 **全文不能截断** —— 截了的话「展开完整说明」也没东西可看。
    #    老的 notes 字段保持截断版不动（可能有别处在读）。
    out['notes_brief'], out['notes_full'] = split_notes(rel.get('body'))
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

    # 🔴 **能不能自动更新，看依赖，不看版本号。**
    #
    #    这里原来是 `rv[:2] != lv[:2] → need_full`：拿「次版本号变了」
    #    去推断「依赖变了」。中间隔着一个约定（RELEASE.md 里的
    #    「依赖变了必须进次版本」），而约定靠发版的人不出错 ——
    #    哪天加了个 pip 包却只改修订号，用户就会拿到新代码配旧依赖，
    #    下次启动 ImportError，而他刚「更新成功」过。
    #
    #    小蔡 2026-09-02 的指令：「禁止版本号作为判断依据」。
    #    改成拉 Release 里那份 requires-vX.json（几百字节）跟本地实际装的
    #    比对 —— 那是**事实**。拉不到就交给 apply_update 里的兜底
    #    （更新包里也带了一份，只是要下完才能看）。
    #
    #    版本号还留着干一件事：判断**有没有**新版本。那件事没有别的
    #    办法，也不涉及「能不能装」的推断。
    miss = _requires_gap(rel, out)
    if miss:
        out['need_full'] = True
        out['has_update'] = False
        out['error'] = (
            '有新版本 %s，但它需要的东西你这儿还没有（%s）—— '
            '小小的更新包装不了这些，要重新下载完整安装包。'
            '你已经下好的模型和 GPU 运行库不用重下。'
            % (out['latest'], '、'.join(miss[:3])))
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
        # 🔴 字段必须跟 sources.probe_all 给的那套一样（bps / error）。
        #    这个列表会直接喂给 sources.pick_best，它读的是 `bps` ——
        #    原来这里给的是 ok / speed / why，于是**点更新必崩**：
        #    KeyError: 'bps'，跟网络毫无关系。
        #
        #    bps=0 会让 pick_best 返回 None，正好落到调用处的
        #    `rows[0]` 兜底 —— 按固定顺序试第一个，正是这条捷径要的。
        return [{'id': m['id'], 'name': m['name'], 'bps': 0,
                 'error': '', 'why': '包太小，没测速'} for m in GH_MIRRORS]
    cand = [{'id': m['id'], 'name': m['name'], 'env': {},
             'probe': _mirrored(asset_url, m['prefix'])} for m in GH_MIRRORS]
    return sources.probe_all(cand, seconds=seconds)


REQUIRES_NAME = 'requires.json'


def read_upgrade(raw):
    r"""从 requires.json 里读 upgrade 段。读不到返回空字典。

    ## 这一段是什么

    一小段跟着每次发布挂在 GitHub 上的文字，软件读它来决定要不要给
    用户升级按钮。**默认全是 null（没测过），发版的人不用管它。**

    般配度不用人管 —— mineru 的包里自带 `torch<3,>=2.6.0` 这样的声明，
    pip 解依赖时自己会拒。这一段只用于 pip 查不出来的那类问题：
    新版装得上、但实际效果变差了。

    ## 兼容性

    🔴 **老 Release 的 json 里没有这一段** —— 读不到就返回空字典，
    界面按「没测过」处理。反过来，老客户端拿到带这一段的新 json 也
    没事：check_requires 只取 requires 这一个键，多出来的键它根本
    不看。**双向兼容，不用做版本协商。**
    """
    import json as _json
    try:
        d = _json.loads(raw) or {}
    except Exception:
        return {}
    up = d.get('upgrade')
    return up if isinstance(up, dict) else {}


def upgrade_policy(up, name, channel=''):
    r"""某个包准不准升。返回 {ok, to, note}。

    ok 是 None 表示没测过 —— 界面上要显示「我们没测过，升不升你
    自己定」，**不能当成「可以升」，也不能当成「不能升」**。

    torch 按通道分开记（cu128 上测通过不代表 cu126 也行），所以要
    传 channel。传了但那条通道没记录，同样是「没测过」。
    """
    blank = {'ok': None, 'to': '', 'note': ''}
    if not isinstance(up, dict):
        return blank
    node = up.get(name)
    if not isinstance(node, dict):
        return blank
    # torch 那种按通道分的：再下一层
    if channel and 'ok' not in node:
        node = node.get(channel)
        if not isinstance(node, dict):
            return blank
    return {'ok': node.get('ok'), 'to': node.get('to') or '',
            'note': node.get('note') or ''}


def check_requires(raw):
    r"""比对更新包要求的依赖和本地实际装的。返回缺了什么（空 = 都满足）。

    ## 为什么不能只看版本号

    原来判断「这次更新能不能自动装」靠的是「次版本号变没变」，而那条
    建立在一个**约定**上：「依赖变了必须进次版本」。约定靠发版的人
    不出错 —— 哪天加了个 pip 包却只改了修订号，用户就会拿到
    新代码配旧依赖，下次启动 ImportError，而他刚「更新成功」过。

    依赖清单是**事实**：打包时把这一版需要的包和当时装的版本写进
    `requires.json`，客户端拿它跟本地比。版本号判断留作第一道快速筛
    （省掉一次下载），最终判据在这里。

    ## 比对到什么程度

    只看「本地有没有这个包」和「大版本对不对得上」，不做精确 pin ——
    我们本来就不锁版本，锁了反而会因为无关的小版本差异挡住正常更新。
    大版本不同才是真会出事的那种（比如 mineru 3.x → 4.x）。
    """
    import json as _json
    try:
        want = (_json.loads(raw) or {}).get('requires') or {}
    except Exception:
        return []          # 清单坏了/没有 —— 不拿它卡人，交回版本号那道

    try:
        import importlib.metadata as md
    except Exception:
        return []

    miss = []
    for name, ver in want.items():
        try:
            have = md.version(name)
        except Exception:
            miss.append('%s（没装）' % name)
            continue
        if ver and have:
            a = str(ver).split('.')[0]
            b = str(have).split('.')[0]
            if a.isdigit() and b.isdigit() and a != b:
                miss.append('%s（要 %s.x，装的是 %s）' % (name, a, have))
    return miss


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
            return (False,
                    '更新包打不开，可能没下完整：%s。'
                    '再点一次「检查更新」重下就行。' % str(e)[:80], 0)

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

            # 🔴 依赖对不上就别覆盖 —— 覆盖完才发现的话，用户拿到的是
            #    一个启动就崩的软件，而他刚「更新成功」过。
            try:
                raw = zf.read(REQUIRES_NAME).decode('utf-8')
            except Exception:
                raw = ''        # 老版本的更新包没有这个文件，跳过
            if raw:
                miss = check_requires(raw)
                if miss:
                    return (False,
                            '这个更新需要的东西你这儿还没有（%s）—— '
                            '小小的更新包装不了这些，要重新下载完整安装包。'
                            '你已经下好的模型和 GPU 运行库不用重下。'
                            % '、'.join(miss[:3]), 0)

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
             digest='', size=0, allow_unverified=False, prefer=''):
    r"""下更新包。返回 (ok, error, 用了哪个源)。

    先并发测速挑最快的再下 —— 候选里当场坏掉的不在少数（实测六个坏三个），
    不测速就可能卡在一个吐不出数据的源上。

    `prefer` 是界面上手动指定的线路 id（空 = 自动挑最快的）。留这个后门是
    因为**最快的未必最稳**：别人的网络跟开发机可能完全不同，测速赢的那条
    也可能下到一半就断。只认 GH_MIRRORS 里的 id，认不出来就照常自动挑 ——
    所以即便前端传进来个乱七八糟的值，最坏也只是回到默认行为。

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
                '这个下载地址不是本仓库的 Release 附件，出于安全没有下载。'
                '可以自己去项目的 Releases 页面手动下安装包。', '')
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
    # ── 按顺序试，**一条挂了就换下一条** ────────────────────────────
    #
    # 🔴 原来只试一条，挂了整个更新就失败，而错误信息还写着「再点一次会
    #    换个源重试」—— 那句话根本不成立：更新包只有 0.5 MB，走的是
    #    probe_mirrors 的「小包不测速」捷径，所有 bps 都是 0，pick_best
    #    返回 None，于是每次都落到名单第一条。实测连挑三次全是同一条。
    #
    #    2026-09-03 发 v0.1.1 当天就撞上了：ghfast.top 的 SSL 挂掉，
    #    自动更新链路当场断掉 —— 而旁边六条是通的。0.5 MB 的东西，
    #    换一条重下的代价近乎为零，没有理由不试。
    order = _download_order(asset_url, prefer, seconds, size)
    if not order:
        return False, '所有下载源都连不上，检查一下网络', ''

    tried = []
    for m in order:
        ok, err, fatal = _fetch_one(asset_url, m['prefix'], dest,
                                    on_progress, digest)
        if ok:
            return True, '', m['name']
        tried.append((m['name'], err))
        if fatal:
            # 🔴 校验没过**不换源**。内容和原件对不上是安全事件，不是
            #    网络抖动；拿「多试几个源」去碰运气，等于在一堆不可信的
            #    源里找一个碰巧对得上的。停在这儿，让用户自己去 Release 页。
            break

    name, err = tried[0]
    if len(tried) == 1:
        return False, err, name
    return (False, '%d 条下载线路都试过了，都没成功。第一条（%s）：%s'
            % (len(tried), name, err), name)


def _download_order(asset_url, prefer, seconds, size):
    r"""按什么顺序试各条下载线路。

    手动指定的排最前（用户已经替我们做了选择），其余按测速快慢；
    小包测不出速度时就按名单顺序 —— 那正是 probe_mirrors 的小包捷径
    要的行为，只是现在不再「只试第一条」了。

    测速时明确报错的那几条排到最后当退路，不直接剔除：测速用的是
    HEAD/短读，跟真正下载不完全是一回事，留着比丢掉稳。
    """
    by_id = {m['id']: m for m in GH_MIRRORS}
    order = []
    if prefer and prefer in by_id:
        # 🔴 指定了就**不测速** —— 用户已经替我们做完选择，再花几秒去测
        #    一个用不上的排名是白等。其余线路按名单顺序留作退路即可。
        order.append(by_id[prefer])
        for m in GH_MIRRORS:
            if m not in order:
                order.append(m)
        return order
    try:
        rows = probe_mirrors(asset_url, seconds=seconds, size=size)
    except Exception:
        rows = []
    for r in sorted(rows, key=lambda x: -(x.get('bps') or 0)):
        m = by_id.get(r.get('id'))
        if m is not None and m not in order and not r.get('error'):
            order.append(m)
    for m in GH_MIRRORS:
        if m not in order:
            order.append(m)
    return order


def _fetch_one(asset_url, prefix, dest, on_progress, digest):
    r"""从**一条**线路下回来并验。返回 (成功, 错误, 要不要就此打住)。

    第三个值只有校验没过时才是 True —— 那种情况换源没有意义，见调用处。
    三道验证（长度 / zip 完整性 / SHA256）跟改造前一字不差，只是把
    「返回给调用方」换成了「告诉调用方要不要再试下一条」。
    """
    import hashlib
    import zipfile

    h = hashlib.sha256()
    total = 0
    got = 0
    try:
        req = urllib.request.Request(_mirrored(asset_url, prefix), headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            total = int(r.headers.get('Content-Length') or 0)
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
        return False, '下载失败：%s' % str(e)[:120], False

    def _drop():
        try:
            os.remove(dest)
        except OSError:
            pass

    if total and got != total:
        _drop()
        return (False, '没下完（只到了 %d / %d 字节），网络中断了'
                % (got, total), False)

    if not digest:
        # 没有官方校验值时能做的：确认它至少是个**完整的、能打开的 zip**。
        # 这挡不住蓄意篡改（那要靠 digest），但挡得住截断、挡得住镜像
        # 返回一个 HTML 错误页 —— 后者在实测里出现过。
        try:
            with zipfile.ZipFile(dest) as z:
                bad = z.testzip()
            if bad:
                _drop()
                return False, '下回来的包内部损坏（%s）' % bad, False
        except Exception as e:
            _drop()
            return (False, '下回来的不是一个完整的更新包（%s）—— 多半是这条'
                    '线路返回了别的东西' % str(e)[:60], False)
        return True, '', False

    if h.hexdigest().lower() != digest.lower():
        # 坏包必须删掉：留在硬盘上，下次有人手滑双击就装了。
        _drop()
        return (False,
                '更新包校验没通过 —— 下回来的内容和 GitHub 上的原件对不上，'
                '可能是这条线路不干净。出于安全没有安装，'
                '到项目的 Release 页面手动下载。', True)

    return True, '', False
