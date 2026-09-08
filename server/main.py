# -*- coding: utf-8 -*-
r"""本地 HTTP 服务。Electron 起它，前端跟它说话。

**只绑 127.0.0.1**，端口让系统随机分配，把实际端口打到 stdout 供外壳读 ——
写死端口会在用户同时开着别的软件时撞车，而那种失败现场极难查。

转换是长任务（一份 4 分钟），所以走「提交任务 + 轮询进度」，
不用 WebSocket：本地单机、任务量小，轮询足够，少一套连接状态要维护。

⚠️ **跑同步阻塞代码的路由必须写成 `def`，不能写 `async def`**。
   FastAPI 里 async 处理函数直接在事件循环上跑，同步代码会卡住整个服务；
   普通 def 才会被丢到线程池。/api/scan 扫 456 份 PDF 要 16 秒，
   写成 async 的话这 16 秒里连转换进度的轮询都排队，界面卡住不动。
   只有纯查内存字典的（ping / poll / cancel / download_status）才留 async。
"""
import io
import json
import os
import sys
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import uvicorn                                        # noqa: E402
from fastapi import FastAPI                           # noqa: E402
from fastapi.middleware.cors import CORSMiddleware    # noqa: E402
from fastapi.responses import JSONResponse            # noqa: E402
from pydantic import BaseModel                        # noqa: E402

import convert                                        # noqa: E402
import extract                                        # noqa: E402
import gpu                                            # noqa: E402
import models                                         # noqa: E402
import deps
import maint
import paths
import torchdep
import vcredist                                          # noqa: E402
import probe                                          # noqa: E402
import sources                                      # noqa: E402
import todocx                                         # noqa: E402
import tomath                                         # noqa: E402
import update
import upgrade                                         # noqa: E402

app = FastAPI(title='PDF 转 Word')
app.add_middleware(CORSMiddleware, allow_origins=['*'],
                   allow_methods=['*'], allow_headers=['*'])

# 任务表。单机单用户，内存里放着就行 —— 存盘反而要处理「上次没跑完的任务」
# 这种没人关心的状态。软件关掉任务就没了，符合用户预期。
_TASKS = {}

# 任务表里留几个**已结束**的。
#
# 原来永不清理：转一批留一份，每份带着 results 和最多 120 行日志。以前一批
# 转完就散场，堆几份无所谓；有了待办队列（转换中加的文件攒着、这批转完自动
# 接上）之后一批接一批，这个数涨得快得多，所以补一道上限。
#
# 这纯粹是内存保护，**前端不依赖它**：上一批的报告由前端自己留一份
# results（见 app.js 的 lastResults），不回头来查这张表。所以留几个都不会
# 影响功能，5 个是个够查最近几批、又不至于堆着的数。
_TASKS_KEEP = 5

# 升级下载的状态。跟 _TASKS 一样由 _LOCK 保护。
_UPG = {}
# 装下好的那批。跟 _UPG（下载）分开 —— 下载可以跟转换并行，
# **安装不行**：torch 的 dll 那会儿正被 MinerU 子进程占着。
_UPGI = {'state': 'idle'}
_LOCK = threading.Lock()


# ── 环境自检 ────────────────────────────────────────────────────────────
@app.get('/api/env')
def env():
    """首次启动那一屏要的全部信息，一次给齐 —— 分三个请求只会让首屏闪三次。"""
    g = gpu.detect()
    xsl = tomath.find_xsl() or ''
    node_ok = tomath.node_available()
    return {
        'gpu': {'ok': g['ok'], 'why': g['why'], 'detail': g['gpu']},
        # 公式引擎是硬性要求（小蔡 2026-09-01 定），两个条件缺一不可：
        # XSL 来自用户的 Office，node 用来跑 KaTeX 把 LaTeX 转成 MathML。
        'office': {'ok': bool(xsl), 'path': xsl},
        'node': {'ok': node_ok},
        'formula': {'ok': bool(xsl) and node_ok,
                    'why': _formula_why(bool(xsl), node_ok)},
        'pandoc': {'ok': todocx.pandoc_available(), 'path': todocx.PANDOC},
        'mineru': {'ok': bool(_find_mineru()), 'path': _find_mineru() or ''},
        # GPU 运行库和「有没有显卡」是两件独立的事，报错要分得清：
        # 装的是 CPU 版 torch → 下一份 GPU 版就行；
        # 没有 N 卡 → 换台机器。混成一句「GPU 不可用」谁也不知道该干嘛。
        'cuda_torch': {'ok': torchdep.ready(), 'why': torchdep.why(),
                       'version': torchdep.info().get('version', '')},
        # Visual C++ 运行库。torch 的 c10.dll 依赖它，缺了整个 torch 都
        # import 不了。**在这儿报出来，用户点「现在就装」之前就知道** ——
        # 不然是下完 2.8 GB 才发现前置条件不满足。
        # C++ 运行库。缺了的话 torch 的 c10.dll 加载不了（WinError 1114）。
        # 这里**顺手就补上**（用包里 numpy 自带的那份），不叫用户去装。
        # C++ 运行库。判据是「这个软件装过一次没有」，**不是**
        # 「系统里装没装 VC++」——后者今天判错四次，最后一次是
        # 把我们自己随包带的 vcruntime140* 当成了系统装的，
        # 于是没装 VC 的机器也打勾（小蔡在网吧那台抓到）。
        # 重复安装 vc_redist 无害（微软文档），所以不猜，装一次。
        'vcredist': {'ok': vcredist.already_done()},
        # 磁盘空间。首次要下约 7.4 GB，装完占约 10 GB。
        'space': {'free_gb': round(paths.free_bytes() / 1024.0 ** 3, 1)},
        # 模型和可写性 —— 首启要据此决定是拦住、还是先去下模型
        'models': {'ok': models.ready(), 'dir': models.where() or paths.MODELS,
                   'bytes': paths.models_size()},
        'writable': {'ok': paths.writable(), 'dir': paths.ROOT},
    }


def _formula_why(xsl_ok, node_ok):
    r"""公式引擎为什么不可用。这句话直接显示给老师看，必须是人话，
    而且要说清楚**该去做什么**，不能只报「缺少组件」。"""
    if xsl_ok and node_ok:
        return '公式会转成 Word 原生公式对象，可编辑可搜索。'
    if not xsl_ok:
        return ('这台电脑没有装微软 Office。本软件把公式转成 Word 原生公式，'
                '要用到 Office 自带的一个转换文件（MML2OMML.XSL），'
                '那是微软的文件，不能随本软件分发，只能装了 Office 才有。')
    # ⚠️ 不要说「重装一次应该能解决」。setup_env.py 根本不装 node
    #    （实测提到 node 的次数是 0），node 走的是系统 PATH，
    #    重装我们的软件不会带来它。说一句解决不了问题的话，
    #    比不说更糟 —— 用户会白折腾一遍然后更困惑。
    return ('缺少 Node.js —— 公式的第一步转换要用到它，而这台电脑上没有。'
            '到 nodejs.org 装一个「LTS」版本（一路下一步即可），'
            '装完回来点「重新检查」。')


def _find_mineru():
    r"""MinerU 能不能跑，能的话返回运行它的命令前缀（list）；不能返回空。

    2026-08-31 之前这里有一条退路：找不到就用工作台的那份。
    环境独立之后撤掉了 —— 留着的话，别人机器上装漏了 MinerU 会静默失败，
    而在我这台机器上永远测不出来（因为工作台就在隔壁目录）。
    这种「只在开发机上能跑」的坑，宁可现在红。

    🔴 2026-09-02 改：判据从「mineru.exe 这个文件在不在」换成
       「这个解释器找不找得到 mineru 这个包」。

       旧判据在发行版上是**假绿**：文件确实在，自检一路放行，
       而那个 exe 是 pip 生成的 launcher，尾部硬编码着打包机器上的
       python.exe 路径，在别人机器上根本起不来。网吧实测的
       「测速正常、下载一直失败」就是它 —— 详见 paths.py 的说明。
    """
    return paths.mineru_cmd() if paths.mineru_available() else []


# ── 下载源（B35：点下载时并发实测，不存历史成绩，不用 ping 判优）────────
# 模型总量按 MinerU 实际拉下来的量估：约 4.6 GB。
MODEL_BYTES = 4.6 * 1024 * 1024 * 1024


@app.get('/api/sources')
def list_sources():
    r"""并发测所有源，按快慢排序返回。**每次现测** ——
    下载是低频动作，现测成本可忽略，而旧成绩会过时误导。
    """
    rows = sources.probe_all()
    best = sources.pick_best(rows)
    return {
        'items': [{
            'id': r['id'], 'name': r['name'],
            'ok': r['bps'] > 0,
            'eta': sources.eta_words(MODEL_BYTES, r['bps']),
            'error': r['error'],
        } for r in rows],
        'best': best['id'] if best else '',
        'total_gb': round(MODEL_BYTES / 1024 / 1024 / 1024, 1),
    }


# ── 检查更新 ────────────────────────────────────────────────────────────
# 单例，跟模型下载同理：一台机器同时只可能下一个更新包。
# 'step' 是 state='running' 内部的细分，给界面说清楚「现在到底在等什么」：
#   'checking'  正在跟 GitHub 确认最新版本（最坏 3.8 秒）
#   'probing'   正在并发测各条线路，挑最快的（约 2 秒）
#   'running'   真的在下了，这时候 got/total 才有意义
# 🔴 不叫 phase —— 前端的 u.phase 存的是后端的 state（见 actions.pollUpd），
#    重名会撞车。
_UPD = {'state': 'idle', 'got': 0, 'total': 0, 'error': '',
        'file': '', 'via': '', 'files': 0, 'step': ''}


@app.get('/api/update/check')
def check_update():
    r"""查有没有新版本。**由后端发请求，不是前端**。

    前端页面的 CSP 只放行 connect-src http://127.0.0.1:*；让前端直连
    GitHub 就得放宽 CSP —— 拿安全性换一个小功能不划算。走后端还能
    顺带做超时和错误兜底，以后加镜像轮换也在这一层。

    写成 def 不是 async def：这里面是同步的网络请求，会卡住事件循环。
    """
    return update.check()


class UpdateDlReq(BaseModel):
    # url / name 保留只为兼容老前端 —— 后端**一个都不看**，自己去查
    # （见 _upd_work 的说明）。
    url: str = ''
    name: str = ''
    # 界面上手动指定的线路 id（空 = 自动挑最快的）。**这个是看的** ——
    # 它不是地址，只是一个在 GH_MIRRORS 里查表的键，查不到就回到自动。
    line: str = ''
    # 用户已经知道「拿不到官方校验值」并且选择继续。这是唯一会被读的字段。
    allow_unverified: bool = False


def _upd_work(allow_unverified=False, prefer=''):
    r"""下载 → 校验 → 解压覆盖 → 报「装好了，重启生效」。

    **一口气做完**，不让用户在中间再点一次。小蔡的原话：
    「点了更新按钮，自动下载文件，然后就完成更新」「没有人会去开 github」。

    🔴 **下载地址和校验值都由这里自己去 GitHub 查，不接受前端传**
       （2026-09-02 改）。原来是前端把 `asset.url` POST 过来就照单下载：
       服务只绑 127.0.0.1，但本机任意进程都能 POST 一个自己的 URL，
       让它下载并解压覆盖安装目录 —— zip slip 只挡住了目录外，
       目录内的 .py 照样能被换掉，而那些 .py 下次启动就执行。

       让前端传校验值也没用：能伪造 URL 的进程同样能伪造校验值。
       只有校验值和文件来自两条独立的路（值走 api.github.com 直连、
       文件走镜像），验证才有意义。

       代价是多一次 API 请求（实测 1.14 秒）。顺带还解决了一个真问题：
       用户开着更新面板放了半天再点更新时，拿到的是当下的 Release，
       不是半天前那份。
    """
    def on_prog(got, total):
        with _LOCK:
            _UPD['got'], _UPD['total'] = got, total

    info = update.check()
    asset = info.get('asset') or {}
    if not info.get('ok') or not asset.get('url'):
        with _LOCK:
            _UPD.update({'state': 'error',
                         'error': info.get('error') or '没查到可下载的更新包'})
        return

    # 落点用 _tmp/update：安装目录内、英文路径。
    # （原来建的是中文目录「更新包」—— 而产物目录刚从「PDF转Word」
    #   改成 PDF2Word 就是为了避开中文路径，两处不能各走各的。）
    dest = os.path.join(paths.ensure(os.path.join(paths.TMP, 'update')),
                        asset.get('name') or 'update.zip')
    def on_phase(p):
        with _LOCK:
            _UPD['step'] = p

    # 🔴 **故意不传 size。**
    #
    #    probe_mirrors 有条「小包不值得测速」的捷径（size < 5 MB 就直接
    #    返回 bps 全 0）。更新包只有 0.55 MB，正好落在捷径里 —— 于是
    #    _download_order 的测速排序拿到一堆 0，排了个寂寞，每次都落到
    #    名单第一条。那条要是连不上，_fetch_one 的 urlopen(timeout=30)
    #    得干等满 30 秒才换下一条。
    #
    #    捷径的账在 2026-09-05 已经算翻过一次：代价不是「多等 2 秒测速」，
    #    而是「第一条不通就干等 30 秒」。所以更新包这条路必须真测。
    #    （在这之前是前端点更新时自己先打一次 /api/update/probe 来绕开，
    #      那等于把同一件事拆到前后端两处，删掉一处另一处就静默退化。）
    ok, err, via = update.download(asset['url'], dest, on_progress=on_prog,
                                   digest=asset.get('digest', ''),
                                   allow_unverified=allow_unverified,
                                   prefer=prefer, on_phase=on_phase)
    if not ok and err.startswith('NEED_CONFIRM:'):
        # 拿不到校验值 —— **报警但不阻拦**，把情况透给界面让用户自己决定。
        # 跟显卡那条规矩一样（小蔡：「要报警，但是并不要阻拦用户使用」）。
        with _LOCK:
            _UPD.update({'state': 'need_confirm',
                         'error': ('拿不到 GitHub 给的校验值，没法确认下回来的'
                                   '是不是原件。更新包会覆盖软件里的程序文件，'
                                   '所以这一步有风险。'),
                         'via': via})
        return
    if not ok:
        with _LOCK:
            _UPD.update({'state': 'error', 'error': err, 'via': via})
        return

    with _LOCK:
        _UPD.update({'state': 'installing', 'via': via, 'file': dest})

    ok2, err2, n = update.apply_update(dest)
    with _LOCK:
        if ok2:
            _UPD.update({'state': 'done', 'error': '', 'files': n})
            # 装好了就把下载的包删掉，别在安装目录里留垃圾
            try:
                os.remove(dest)
                os.rmdir(os.path.dirname(dest))
            except Exception:
                pass
        else:
            _UPD.update({'state': 'error',
                         'error': '下载好了但安装失败：%s' % err2,
                         'files': n})


@app.post('/api/update/probe')
def probe_update_mirrors():
    r"""实测各下载镜像的真实带宽。**只有用户点了「测下载速度」才走到这儿。**

    为什么单独一个动作：查更新时顺手拿到的是**响应快慢**（哪条先答话），
    那是延迟；下载要的是**带宽**。两者不是一回事，延迟低的完全可能下得慢。
    但为了后者让每个人每次查更新都多等几秒不划算，所以做成主动触发。

    🔴 **不接受前端传 URL，自己去查。** 跟 `_upd_work` 一个道理：服务虽然
    只绑 127.0.0.1，可本机任意进程都能 POST 过来；接受外部 URL 等于把
    「让这个程序去访问任意地址」的能力递出去。多花 check 那一秒多，换掉这个口子。

    写成 def 不是 async def：里面是同步网络请求，会卡住事件循环。
    """
    info = update.check()
    asset = (info.get('asset') or {}) if info.get('ok') else {}
    url = asset.get('url') or ''
    if not url:
        return {'ok': False, 'lines': [],
                'error': '现在没有可下载的更新包，没什么可测的'}

    # size 不传 —— probe_mirrors 对小包有条「不值得测」的捷径，那是给
    # 自动流程设的；这里是用户明确要求实测，就得真测。
    rows = update.probe_mirrors(url, seconds=2.0)
    best = 0.0
    for r in rows:
        if not r.get('error') and (r.get('bps') or 0) > best:
            best = r['bps']
    lines = [{'id': r['id'], 'name': r['name'],
              'ok': not r.get('error') and (r.get('bps') or 0) > 0,
              'ms': 0, 'bps': r.get('bps') or 0,
              'error': r.get('error') or '',
              'used': bool(best) and r.get('bps') == best}
             for r in rows]
    lines.sort(key=lambda x: (not x['ok'], -x['bps']))
    return {'ok': True, 'lines': lines}


@app.post('/api/update/download')
async def start_update_download(req: UpdateDlReq = UpdateDlReq()):
    r"""开始更新。**不看 req 里的任何东西** —— 见 _upd_work 的说明。

    请求体保留是为了兼容老前端（更新包只覆盖 .py 和 .js，
    用户手上那份 index.html 是旧的还是新的，取决于他更新过几次）。
    """
    with _LOCK:
        if _UPD['state'] == 'running':
            return {'ok': True, 'already': True}
        _UPD.update({'state': 'running', 'got': 0, 'total': 0,
                     'error': '', 'file': '', 'via': '',
                     'step': 'checking'})
    threading.Thread(target=_upd_work, daemon=True,
                     args=(bool(req.allow_unverified),
                           (req.line or '')[:40])).start()
    return {'ok': True}


@app.get('/api/update/download')
async def update_download_status():
    with _LOCK:
        return dict(_UPD)


# ── 下载模型 ────────────────────────────────────────────────────────────
# 单例：一台机器同时只可能下一次。存内存里就够 —— 软件关掉重开会重新
# 判断模型在不在，没必要持久化一个「上次下到一半」的状态。
# 🔴 `lines` 保留最近 200 行，不是只存最后一行。
#
#    小蔡 2026-09-02：「你在下载任何文件的时候，都应该显示一个进度条，
#    并且要弹出背后的命令，这样下载的人才可以知道完整的进度，而不是黑盒。」
#
#    原来只有 `line` 一格，被下一行随时覆盖 —— 用户看到的是一行不断跳变
#    的文字，既看不出下到哪了，出问题也没有上下文。200 行够铺满界面的
#    日志区并往回翻一段；完整的落在 logs/ 下，出问题能直接把文件发过来。
_DL_MAX_LINES = 200

# 转换任务在界面上留多少行日志。比下载那边少一些 —— 转换界面还得放
# 文件表，日志区只占底下一条。全量的那份在 logs/convert.log 里。
_TASK_MAX_LINES = 120

_DL = {'state': 'idle', 'got': 0, 'total': models.learned_total(),
       'error': '', 'line': '', 'lines': [], 'cancel': False,
       # 'gpulib'（装 GPU 运行库）/ 'models'（下模型）/ ''（没在跑）——
       # 界面靠它说清楚现在在等什么，不然用户看着一个不动的进度条
       # 不知道是卡住了还是在装别的东西
       'phase': '',
       # 跑的是哪条命令。日志区第一行就显示它 —— 「弹出背后的命令」
       'cmd': '',
       # 完整日志落在哪，界面上给出来，出问题让用户直接发文件
       'log': ''}


def _dl_log(line):
    """往下载日志里追一行。调用方已经持有 _LOCK 时别再调这个。"""
    with _LOCK:
        _DL['line'] = line[-200:]
        _DL['lines'].append(line[-300:])
        if len(_DL['lines']) > _DL_MAX_LINES:
            del _DL['lines'][0:len(_DL['lines']) - _DL_MAX_LINES]


class DownloadReq(BaseModel):
    source: str = 'modelscope'


def _dl_work(source):
    def on_prog(got, total):
        with _LOCK:
            _DL['got'], _DL['total'] = got, total

    def on_log(line):
        _dl_log(line)

    def stopped():
        with _LOCK:
            return _DL['cancel']

    # 🔴 先装 GPU 运行库，再下模型。
    #
    #    小蔡 2026-09-02 定「只用 GPU」，而发行版里**不带** CUDA 版 torch
    #    （它解压后 4.2 GB，打进安装包会让包从 356 MB 涨到 1.5~2 GB，
    #      逼近 GitHub 单文件 2 GiB 上限，没显卡的人还得跟着下）。
    #    所以放在这里按需装 —— 反正首启本来就要下 4.6 GB 模型，
    #    两件事合成一个流程，用户只等一次。
    #
    #    顺序不能反：模型下完了却发现 torch 是 CPU 版，等于白等半小时。
    if not torchdep.ready():
        with _LOCK:
            _DL['phase'] = 'gpulib'
            _DL['cmd'] = torchdep.install_cmd_text()
            _DL['log'] = torchdep.log_path()
            _DL['got'], _DL['total'] = 0, torchdep.DOWNLOAD_BYTES
            _DL['lines'] = []
        ok, err = torchdep.install(on_log=on_log, stop_flag=stopped,
                                   on_progress=on_prog)
        if not ok:
            with _LOCK:
                _DL.update({'state': 'error', 'error': err, 'phase': ''})
            return
        # 🔴 装完把进度补满再切走。命中 pip 缓存的包不产生 `Progress` 行
        #    （2026-09-05 那次的 setuptools 就是），分子天生差那一截 ——
        #    不补的话进度条停在 9x% 就跳去模型阶段，看着像没下完。
        with _LOCK:
            _DL['got'] = _DL['total']

    with _LOCK:
        _DL['phase'] = 'models'
        _DL['cmd'] = models.download_cmd_text(source)
        _DL['log'] = models.log_path()
        _DL['got'], _DL['total'] = 0, models.learned_total()
        _DL['lines'] = []
    ok, err = models.download(source, on_progress=on_prog, on_log=on_log,
                              stop_flag=stopped)
    with _LOCK:
        _DL['state'] = 'done' if ok else 'error'
        _DL['error'] = err
        _DL['phase'] = ''


@app.post('/api/models/download')
async def start_download(req: DownloadReq):
    r"""下模型。**首次下载和「更新模型」走的是同一条路。**

    2026-09-05 实测确认底层的 `modelscope.snapshot_download` 是
    增量的：原样再跑一次 0.9 秒（全新下载要 21 秒），删掉一个文件
    再跑只补那一个。所以「更新」不需要清空 models/，重跑即可 ——
    中途失败旧模型还在，用户照常能转 PDF。
    """
    with _LOCK:
        if _DL['state'] == 'running':
            return {'ok': True, 'already': True}
        # 🔴 **转换进行中不许下模型。** 首次下载时撞不上（那时还没法
        #    转换），但「更新模型」这个入口会 —— 增量下载虽然不删旧
        #    文件，写 models/ 目录仍然跟正在跑的 MinerU 抢文件锁。
        #    跟「转换中禁用清理 / 检查更新」一个规矩。
        if any(t.get('state') == 'running' for t in _TASKS.values()):
            return JSONResponse({'detail': '正在转换，转完再下模型'},
                                status_code=409)
        _DL.update({'state': 'running', 'got': 0, 'error': '',
                    'line': '', 'lines': [], 'cancel': False,
                    'cmd': '', 'log': ''})
    threading.Thread(target=_dl_work, args=(req.source or 'modelscope',),
                     daemon=True).start()
    return {'ok': True}


@app.get('/api/models/download')
async def download_status():
    with _LOCK:
        d = dict(_DL)
    d['ready'] = models.ready()
    return d


def _gpulib_work():
    def on_log(line):
        _dl_log(line)

    def on_prog(got, total):
        with _LOCK:
            _DL['got'], _DL['total'] = got, total

    def stopped():
        with _LOCK:
            return _DL['cancel']

    with _LOCK:
        _DL['cmd'] = torchdep.install_cmd_text()
        _DL['log'] = torchdep.log_path()
        _DL['got'], _DL['total'] = 0, torchdep.DOWNLOAD_BYTES
        _DL['lines'] = []
    ok, err = torchdep.install(on_log=on_log, stop_flag=stopped,
                               on_progress=on_prog)
    with _LOCK:
        _DL['state'] = 'done' if ok else 'error'
        _DL['error'] = err
        _DL['phase'] = ''
        # 同上：成功了就把进度补满，别让它停在 9x%。
        if ok:
            _DL['got'] = _DL['total']


@app.post('/api/gpulib/install')
async def install_gpulib():
    r"""只装 GPU 运行库，不下模型。

    给模型已经有了、但 torch 还是 CPU 版的人用 —— 典型是从 v0.0.1
    更新上来的老用户：更新包只有 0.4 MB，换不动那 4 GB 的运行库。
    """
    with _LOCK:
        if _DL['state'] == 'running':
            return {'ok': True, 'already': True}
        _DL.update({'state': 'running', 'got': 0, 'error': '',
                    'line': '', 'lines': [], 'cancel': False,
                    'phase': 'gpulib', 'cmd': '', 'log': ''})
    threading.Thread(target=_gpulib_work, daemon=True).start()
    return {'ok': True}


@app.get('/api/gpulib/install')
async def gpulib_status():
    with _LOCK:
        d = dict(_DL)
    d['ready'] = torchdep.ready()
    d['why'] = torchdep.why()
    return d


def _vcredist_work():
    def on_log(line):
        _dl_log(line)

    def on_prog(got, total):
        with _LOCK:
            if got < 0:
                # -1 = 下载完了，开始跑安装程序。进度条到此为止 ——
                # vc_redist 自己有进度界面，我们看不见它的进度。
                _DL['running_installer'] = True
            else:
                _DL['got'], _DL['total'] = got, total

    def stopped():
        with _LOCK:
            return _DL['cancel']

    with _LOCK:
        _DL['running_installer'] = False
        _DL['cmd'] = vcredist.cmd_text()
        _DL['log'] = ''
        _DL['got'], _DL['total'] = 0, vcredist.SIZE_HINT
        _DL['lines'] = []
    ok, err = vcredist.install(on_log=on_log, on_progress=on_prog,
                               stop_flag=stopped)
    with _LOCK:
        _DL['state'] = 'done' if ok else 'error'
        _DL['error'] = err
        _DL['phase'] = ''


@app.post('/api/vcredist/install')
async def install_vcredist():
    r"""下载并运行微软的 vc_redist.x64.exe。

    这是首启的第一步 —— GPU 运行库要靠它才加载得起来，顺序反了
    用户就得白下一趟 2.8 GB（2026-09-02 小蔡真踩了）。
    """
    with _LOCK:
        if _DL['state'] == 'running':
            return {'ok': True, 'already': True}
        _DL.update({'state': 'running', 'got': 0, 'error': '',
                    'line': '', 'lines': [], 'cancel': False,
                    'phase': 'vcredist', 'cmd': '', 'log': ''})
    threading.Thread(target=_vcredist_work, daemon=True).start()
    return {'ok': True}


@app.get('/api/vcredist/install')
async def vcredist_status():
    with _LOCK:
        d = dict(_DL)
    d['ready'] = vcredist.already_done()
    return d


@app.post('/api/models/download/cancel')
async def cancel_download():
    r"""停止当前的下载/安装。

    模型下载和装 GPU 运行库共用一套状态（同一时刻只可能跑一个），
    所以这一个接口两边都管用。
    """
    with _LOCK:
        _DL['cancel'] = True
    return {'ok': True}


class UseLocalReq(BaseModel):
    dir: str = ''


@app.post('/api/models/use-local')
def use_local_models(req: UseLocalReq):
    r"""指向一个已经下好的模型目录。

    给两种人用：一是本来就跑过 MinerU 的（模型已经在硬盘上，没必要
    再下 4.6 GB），二是从别的机器拷了一份过来的。

    写的是**我们自己**那份 mineru.json，不碰用户主目录里的全局配置。
    """
    d = (req.dir or '').strip()
    if not d or not os.path.isdir(d):
        return JSONResponse({'detail': '这个文件夹不存在'}, status_code=400)
    got = models.detect(d)
    if not got['pipeline'] and not got['vlm']:
        return JSONResponse(
            {'detail': '这个文件夹里没找到 MinerU 的模型。'
                       '应该选包含 OpenDataLab--PDF-Extract-Kit 那一层的目录。'},
            status_code=400)
    models.write_config(got['pipeline'], got['vlm'])
    return {'ok': True, 'pipeline': got['pipeline'], 'vlm': got['vlm'],
            'ready': models.ready()}


# ── 选书 ────────────────────────────────────────────────────────────────
class ScanReq(BaseModel):
    paths: list[str] = []


class UpgradeReq(BaseModel):
    # 勾了哪几个包（torch / torchvision / mineru）
    picked: list[str] = []
    # 各自升到哪个版本。空 = 升到最新
    targets: dict = {}


class CleanReq(BaseModel):
    # 要清哪几类（pip_cache / temp_pip / logs / tmp）
    keys: list[str] = []
    # pip 缓存里具体删哪些文件。空 = 只删本软件下的那些，
    # 不碰别的程序的（缓存是按 Windows 用户共用的）。
    paths: list[str] = []


@app.post('/api/scan')
def scan(req: ScanReq):
    """把拖进来的东西（文件或文件夹）摊平成 PDF 清单，并逐份体检。

    **纯读**，不转换、不写任何东西。
    """
    pdfs = []
    for p in req.paths:
        if os.path.isdir(p):
            pdfs.extend(probe.scan_dir(p))
        elif p.lower().endswith('.pdf'):
            pdfs.append(p)
    seen, uniq = set(), []
    for p in pdfs:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return {'items': probe.probe_many(uniq)}


# 每页要多久。**实测值**，不是拍的（2026-08-31，同一份 10 页数学讲义）：
#   有显卡 262 秒 / 10 页 = 26 秒每页
# 用来估「还要等多久」—— 那是用户唯一关心的数，而 MinerU 的阶段进度
# 回答不了它（各阶段耗时差 100 倍，跑满一条也可能只花 1 秒）。
#
# 🔴 只有 GPU 这一个数了。同一次实测里纯 CPU 是 460 秒 / 10 页 = 46 秒每页，
#    这里原来会在显卡不达标时改用那个数 —— 而 2026-09-02 起产品只用 GPU，
#    显卡不行的话转换是**当场失败**（RuntimeError: No CUDA GPUs are
#    available），根本跑不到用这个估值的时候。
#    留着它的后果是：显卡不达标的人点了「仍然继续」，界面先给他算出
#    「还要 8 分钟」，几秒后失败 —— 一个凭空编出来的数。
SEC_PER_PAGE_GPU = 26.0


def _sec_per_page():
    return SEC_PER_PAGE_GPU


# ── 倒计时：开工估一次，之后老实倒数 ──────────────────────────────────
#
# 小蔡 2026-09-06 定：「一开始是多少就老老实实的一点一点倒计时，不用根据
# 真实进度调整，不准就不准。」准不准交给**跨次积累** —— 每转完一份把真实
# 数据记进历史，下次估得更准；而不是在这一次里边跑边改。
#
# 出厂值是 2026-09-06 那次干净环境实测拆出来的（56 页 ok.pdf，1814 秒，
# 613 个公式 / 1100 个元素；第一轮 13 分 17 秒、第二轮 15 分 03 秒）：
#
#     每页 14.2 秒  +  每页 19.6 个元素 x 每个元素 0.82 秒  ≈ 30.3 秒/页
#
# 跟那次的整体 32.4 秒/页对得上。比原来写死的 26.0 准（那个偏低 25%）。
PASS1_SEC_PER_PAGE = 14.2      # 逐页识别：每页几秒
PASS2_SEC_PER_ELEM = 0.82      # 识别公式和文字：每个元素几秒
ELEMS_PER_PAGE = 19.6          # 一页大概有多少个元素（开转之前不知道，只能靠历史）
OTHER_SHARE = 0.05             # 其余几个小阶段 + 出 Word，实测占 5%
LEARN_FROM = 60                # 只看最近这么多条历史


def _median(xs, fallback):
    """中位数。空的就用兜底值。

    🔴 **用中位数不用平均数** —— 一边转一边开别的软件抢显卡的那几次会
    特别慢（实测 >64 秒/页，而干净环境 32.4），平均数会被那几次拖歪，
    中位数不受影响。
    """
    xs = sorted(x for x in xs if x and x > 0)
    if not xs:
        return fallback
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2.0


def _learned_rates():
    """从历史里学这台机器的速度。返回 (每页秒, 每元素秒, 每页元素数)。

    🔴 **只认真跑过 GPU 的记录。** 缓存命中那几份 `pass1_sec` / `pass2_sec`
    都是 0，混进去会把速度学成离谱的快（秒回是没跑显卡，不是显卡快）。
    """
    try:
        rows = maint.runs(LEARN_FROM)
    except Exception:
        rows = []
    p1, p2, ep = [], [], []
    for r in rows:
        pages = r.get('pages') or 0
        elems = r.get('elements') or 0
        a = r.get('pass1_sec') or 0
        b = r.get('pass2_sec') or 0
        if pages > 0 and a > 0:
            p1.append(a / float(pages))
        if pages > 0 and elems > 0:
            ep.append(elems / float(pages))
        if elems > 0 and b > 0:
            p2.append(b / float(elems))
    return (_median(p1, PASS1_SEC_PER_PAGE),
            _median(p2, PASS2_SEC_PER_ELEM),
            _median(ep, ELEMS_PER_PAGE))


def _estimate(pages_list):
    """开工时估一次。返回 (总秒数, 权重字典)。页数不明就返回 (0, 默认权重)。

    权重给顶上那条进度条用：做完的阶段把权重整个加上，正在做的那步按它
    自己的 x/y 折算。**每份文件的权重都不一样** —— 公式密集的讲义第二轮
    自然占得多，这正好绕开「写死 65/30 一定不准」那个死结。
    """
    spp1, spe2, epp = _learned_rates()
    per_page = spp1 + epp * spe2
    total_pages = sum(pages_list or [])
    core = total_pages * per_page
    total = core / (1.0 - OTHER_SHARE) if core > 0 else 0
    w1 = (spp1 / per_page) * (1.0 - OTHER_SHARE) if per_page > 0 else 0.44
    return int(total), {'pass1': round(w1, 4),
                        'pass2': round(1.0 - OTHER_SHARE - w1, 4),
                        'other': OTHER_SHARE}


# ── 转换 ────────────────────────────────────────────────────────────────
class ConvertReq(BaseModel):
    paths: list[str]
    out_dir: str = ''          # 空 = 输出到每份 PDF 自己所在的文件夹
    prefer_xsl: bool = True
    source: str = ''           # 模型下载源的 id，空 = 让 MinerU 自己决定


def _work(task_id, pdf_paths, out_dir, prefer_xsl, source=''):
    r"""后台线程：逐份转。一份失败不影响其余。

    🔴 **整体套一层兜底**。这是后台线程，异常会被 Python 悄悄吞掉，
    任务就永远停在 running —— 界面上是转圈转到天荒地老，用户只会以为
    软件慢，不会知道出了事。实测撞见过：漏了一个 import，
    四条测试全部等到超时才失败。
    """
    try:
        _work_inner(task_id, pdf_paths, out_dir, prefer_xsl, source)
    except Exception as e:
        with _LOCK:
            t = _TASKS.get(task_id)
            if t is not None:
                t['state'] = 'done'
                t['error'] = '%s: %s' % (type(e).__name__, str(e)[:200])


def _work_inner(task_id, pdf_paths, out_dir, prefer_xsl, source=''):
    mineru = _find_mineru()
    tmp = paths.ensure(paths.TMP_EXTRACT)
    # 用户在首启那屏选的源。**必须真的传下去** —— 只记在前端等于让人
    # 做了个没用的选择题，比不给选更糟。
    src_env = {}
    for s in sources.MODEL_SOURCES:
        if s['id'] == source:
            src_env = s['env']
            break
    # child_env 把模型目录和 MinerU 配置也锁进安装目录 —— 这是
    # 「运行中产生的一切都留在安装文件夹内」的实现手段，少传一次就漏一处。
    env = paths.child_env(src_env)
    for i, pdf in enumerate(pdf_paths):
        with _LOCK:
            t = _TASKS[task_id]
            if t['cancel']:
                t['state'] = 'cancelled'
                return
            t['current'] = i
            t['current_name'] = os.path.basename(pdf)
            t['stage'] = '准备'
            t['stage_cur'] = 0
            t['stage_total'] = 0
            t['stages'] = []      # 换一份就重新记

        def on_prog(stage, cur, tot, _tid=task_id):
            with _LOCK:
                s = _TASKS[_tid]
                s['stage'], s['stage_cur'], s['stage_total'] = stage, cur, tot
                # 走过哪几步，按出现顺序，重复的不再记。
                if stage and (not s['stages'] or s['stages'][-1] != stage):
                    s['stages'].append(stage)

        def stopped(_tid=task_id):
            with _LOCK:
                return _TASKS[_tid]['cancel']

        # 🔴 转换也落一份日志。
        #    2026-09-02 小蔡真机转换卡住时，他手上一个字的证据都拿不出来 ——
        #    模型下载和 GPU 运行库都落盘了，唯独最花时间、最容易出问题的
        #    转换这条链没有。落在 logs/convert.log，出问题直接发文件。
        conv_log = os.path.join(paths.ensure(paths.LOGS), 'convert.log')
        with _LOCK:
            _TASKS[task_id]['log'] = conv_log
        try:
            clog = io.open(conv_log, 'a', encoding='utf-8',
                           errors='replace', newline='')
            clog.write(chr(10) + '===== %s =====' % time.strftime(
                '%Y-%m-%d %H:%M:%S') + chr(10) + pdf + chr(10))
            # 🔴 表头必须立刻落盘。MinerU 加载模型要几十秒，这期间
            #    on_conv_log 一次都不会被调用 —— 不 flush 的话表头在
            #    缓冲区里躺着，用户去看是个 0 字节的空文件。
            clog.flush()
        except Exception as e:
            # 🔴 **不许静默**。2026-09-02 真机上转换跑了一个小时，
            #    logs/ 全程是空的，而这段是必经之路 —— 异常被这里吞得
            #    干干净净，事后完全无从查起（最后是靠人工复现 613 个
            #    公式才定位到根因，代价极大）。
            #    现在把原因摆到界面日志区，下次一眼能看见。
            clog = None
            with _LOCK:
                t0 = _TASKS.get(task_id)
                if t0 is not None:
                    t0['lines'].append(
                        '⚠️ 落盘日志打不开，这次没有完整日志：%s: %s（%s）'
                        % (type(e).__name__, str(e)[:120], conv_log))

        def on_conv_log(line, _f=clog, _tid=task_id):
            if _f:
                try:
                    _f.write(line + '\n')
                    _f.flush()      # 卡住时也要能看见已经到哪一步
                except Exception:
                    pass
            # 🔴 tqdm 的进度行**不进界面日志区**。MinerU 每秒刷几十行，
            #    全塞进去会把真正有用的东西（模型加载、警告、报错）
            #    在一秒内挤没。进度另有专门的显示（阶段名 + 比例条）。
            #    落盘的那份是全的 —— 出问题要细看就去 logs/convert.log。
            if extract.parse_progress(line) is not None:
                # 进度行不进日志流（每秒几十行会把报错挤没），改成
                # **钉在日志区最后一行原地刷新** —— MinerU 安静那几十秒
                # 过去之后，这是「还在动」唯一看得见的证据。
                with _LOCK:
                    tp = _TASKS.get(_tid)
                    if tp is not None:
                        tp['progress_line'] = line[-300:]
                return
            with _LOCK:
                t = _TASKS.get(_tid)
                if t is None:
                    return
                t['lines'].append(line[-300:])
                if len(t['lines']) > _TASK_MAX_LINES:
                    del t['lines'][0:len(t['lines']) - _TASK_MAX_LINES]

        _one_started = time.time()
        dest = out_dir or os.path.dirname(pdf)
        name = os.path.splitext(os.path.basename(pdf))[0] + '.docx'
        try:
            rep = convert.pdf_to_word(pdf, os.path.join(dest, name), tmp,
                                      on_progress=on_prog, on_log=on_conv_log,
                                      prefer_xsl=prefer_xsl,
                                      mineru=mineru, env=env,
                                      stop_flag=stopped)
        finally:
            if clog:
                try:
                    clog.close()
                except Exception:
                    pass

        if rep.get('cancelled'):
            # 用户主动停的：不记进结果，直接收尾。
            with _LOCK:
                _TASKS[task_id]['state'] = 'cancelled'
            return

        rep['line'] = convert.summary_line(rep)

        # 🔴 **每转完一份就记一次，不等整批结束。** 整批结束才写的话，
        #    中途崩溃就什么都没有 —— 而那正是最需要看的时刻。这样写，
        #    最后一条记录停在崩溃前那份，正好指向病根。
        #
        #    插在这里而不是 convert.pdf_to_word 里面：那个函数有 5 个
        #    return 点，逐个插就是「散着写、漏一处」—— 这个项目栽过
        #    好几次的形状。这里是唯一的汇合点。
        #
        #    note_run 自己不抛异常（写不进去返回 False），但仍然套
        #    一层：**记日志绝不能把转换搞崩**，用户的 Word 已经转好了。
        try:
            maint.note_run(rep, pdf_name=os.path.basename(pdf),
                           took_sec=time.time() - _one_started)
        except Exception:
            pass
        with _LOCK:
            t = _TASKS[task_id]
            t['results'].append(rep)
            # 记下「已完成的那些一共花了多久」。_remain 要拿它算真实速率——
            # 不能用 elapsed，那里面含着当前这份正在跑的时间，会让估算
            # 随时间单调变大（越等越久）。
            now = time.time() - t['started']
            one = now - (t.get('done_elapsed') or 0)      # 这一份单独花了多久
            t['done_elapsed'] = now
            # 🔴 缓存命中的份不进速率。它两秒转完一整本，混进去算出来
            #    每页 0.09 秒，后面几份的「还要多久」会短得离谱。
            if not rep.get('cached'):
                t['real_elapsed'] = (t.get('real_elapsed') or 0) + one
                t['real_pages'] = (t.get('real_pages') or 0) + (
                    (t.get('pages') or [0])[i] if i < len(t.get('pages') or []) else 0)

    with _LOCK:
        _TASKS[task_id]['state'] = 'done'
        _TASKS[task_id]['current'] = len(pdf_paths)


@app.post('/api/convert')
def start_convert(req: ConvertReq):
    if not req.paths:
        return JSONResponse({'detail': '没有要转换的文件'}, status_code=400)
    if not _find_mineru():
        return JSONResponse({'detail': '找不到 MinerU，还没装好'}, status_code=400)
    tid = uuid.uuid4().hex[:12]
    # 页数在体检时已经知道了，用它估总时长。
    #
    # 🔴 **体检不过的那份按 0 页算，不是按 10 页。** 它在 convert 里同样
    #    过不了体检（convert.py:44 当场 return），一秒都不花 —— 给它记页数
    #    等于凭空往总时长里加一段。
    #
    #    这一行还管着另一件事：**整批都体检不过时 sum 是 0，`_estimate`
    #    返回的 est_total 就是 0，`_remain` 才走得到那套按真实速率反推的
    #    老算法。** 原来这里兜底成 10，sum 恒大于 0、est 恒非 0，那套老算法
    #    连同它守着的几条事故教训整段成了死代码，而它上面的注释还写着
    #    「估不出总时长时才走下面那套」—— 又一个「注释说的事情已经不成立」。
    #    （2026-09-07 审查查出来的。）
    pages = []
    for p in req.paths:
        r = probe.probe_pdf(p)
        pages.append(r['pages'] if r['ok'] and r['pages'] else 0)
    with _LOCK:
        # 🔴 **只淘汰已结束的，正在跑的一个都不能动。**
        #
        #    下模型、清理、依赖升级、安装升级四处都靠
        #    `any(t['state'] == 'running' for t in _TASKS.values())`
        #    判断「是不是在转换」。删掉一个 running 的任务，这四道互斥
        #    当场全部失效 —— 用户能在转换中装 torch，而那会儿 torch 的
        #    dll 正被 MinerU 子进程占着。
        #
        #    按 started 排序取最老的删。
        _done = [(k, v) for k, v in _TASKS.items()
                 if v.get('state') in ('done', 'cancelled')]
        if len(_done) > _TASKS_KEEP:
            _done.sort(key=lambda kv: kv[1].get('started') or 0)
            for _k, _v in _done[:len(_done) - _TASKS_KEEP]:
                del _TASKS[_k]
        _est, _w = _estimate(pages)
        _TASKS[tid] = {'state': 'running', 'total': len(req.paths), 'current': 0,
                       'current_name': '', 'stage': '', 'stage_cur': 0,
                       'stage_total': 0, 'results': [], 'cancel': False,
                       'lines': [], 'log': '',
                       'error': '', 'started': time.time(),
                       'pages': pages, 'sec_per_page': _sec_per_page(),
                       # 只累计**真跑过 GPU** 的那些份。缓存命中的份
                       # 两秒转完 23 页，混进速率会把预估拉到荒谬的低。
                       'real_elapsed': 0.0, 'real_pages': 0,
                       # MinerU 最新的那条 tqdm 原样存着，钉在
                       # 日志区最后一行原地刷新（见 on_conv_log）。
                       'progress_line': '',
                       # 开工估一次，之后老实倒数（见 _remain）。weights 给
                       # 顶上那条进度条按阶段折算用 —— 每份文件都不一样。
                       'est_total': _est, 'weights': _w,
                       'stages': []}
    threading.Thread(target=_work, daemon=True,
                     args=(tid, req.paths, req.out_dir, req.prefer_xsl,
                           req.source)).start()
    return {'task_id': tid}


@app.get('/api/convert/{task_id}')
async def poll(task_id: str):
    with _LOCK:
        t = _TASKS.get(task_id)
        if not t:
            return JSONResponse({'detail': '没有这个任务'}, status_code=404)
        elapsed = time.time() - t['started']
        return dict(t, elapsed=elapsed, remain=_remain(t, elapsed))


def _remain(t, elapsed):
    r"""还要多久（秒）。算不出来就返回 None，**界面上宁可不显示也不瞎猜**。

    做法：没跑的那些按页数 x 每页秒数估；已经跑完的那些用真实耗时
    反推速率 —— 转到第三份时，前两份的真实速度比出厂估值准得多。

    🔴 速率只能用 `done_elapsed`（最后一份转完那一刻的耗时）来算，
       **绝不能用 `elapsed`**。曾经写成 `spp = elapsed / done_pages`，
       而 elapsed 里含着当前这份正在跑的时间，于是：
           转得越久 → spp 越大 → 剩余时间越大
       实测三份书的第二份进行中时，「还要」从 520 秒一路涨到 1400 秒，
       等得越久说要等得越久。同一个式子还让下面那句 cur_spent 恒等于 0
       （done_pages * (elapsed/done_pages) 就是 elapsed），
       「扣掉当前这份已跑时间」整段逻辑是死的。
    """
    # 🔴 **开工时估过就老实倒数，中途不重算**（小蔡 2026-09-06 定）。
    #    边跑边改正是下面那个「转得越久说要等得越久」事故的土壤。
    #    估不出总时长时（体检没拿到页数）才走下面那套按已完成份数反推的
    #    老算法 —— 它守着的那几条事故教训因此还在。
    est = t.get('est_total')
    if est:
        return max(int(est - elapsed), 0)

    pages = t.get('pages') or []
    if not pages:
        return None
    done = len(t.get('results') or [])
    spp = t.get('sec_per_page') or SEC_PER_PAGE_GPU
    done_elapsed = t.get('done_elapsed')
    # 速率只按**真跑过 GPU** 的那些份算。全是缓存命中时这两个都是 0，
    # 退回出厂估值 —— 宁可用个粗的，也不要用缓存那两秒推出来的假速率。
    real_e = t.get('real_elapsed') or 0
    real_p = t.get('real_pages') or 0
    if real_p and real_e:
        spp = real_e / float(real_p)

    left_pages = sum(pages[done:])
    if left_pages <= 0:
        return 0

    left = left_pages * spp
    # 当前这份已经跑掉的时间要扣掉，否则进度看着不动
    if done < len(pages):
        cur_spent = elapsed - (done_elapsed or 0)
        left -= max(cur_spent, 0)
    return max(int(left), 0)


@app.post('/api/convert/{task_id}/cancel')
async def cancel(task_id: str):
    r"""取消。**当场中断，不用等这一份转完**。

    2026-09-02 之前这里只在两份 PDF 之间检查 —— 只转一份的话循环没有
    下一轮，检查点永远走不到，用户点了完全没反应。小蔡真机原话：
    「点击停止还没用，程序一共有几个停止，都有用吗？」

    当时不硬杀的理由写在这儿：「中途硬杀会留下半截产物，比多等一两分钟
    麻烦」。那个理由现在不成立了 —— 退出码非零一律判失败、失败会把
    pandoc 写出的 Word 删掉、中间产物本来就在 _tmp/ 里每次重来。

    真正生效靠的是 extract._spawn 里的 watch 线程：读取循环阻塞在
    p.stdout.read() 上，MinerU 处理一页几十秒可能一个字都不吐，
    检查写在循环里够不着。
    """
    with _LOCK:
        t = _TASKS.get(task_id)
        if not t:
            return JSONResponse({'detail': '没有这个任务'}, status_code=404)
        t['cancel'] = True
    return {'ok': True}


# ── 关于 / 环境检测 ───────────────────────────────────────────────────


@app.get('/api/runs')
def list_runs(limit: int = maint.RUNS_KEEP):
    r"""转换历史。最新的在最前面。

    🔴 **默认给满 RUNS_KEEP 条，别写死一个更小的数。** 存 200、给 50 的话，
       界面上那句「共 N 份」写的是拿回来的条数、不是存了多少 —— 2026-09-07
       小蔡报「但是怎么只有 50 份」就是这么来的。

    小蔡要它干四件事（2026-09-06 问过）：找回转好的 Word 存哪了、确认某份
    转没转过成没成、失败的一键重转、出事时翻当时到底报了什么。

    **只读，不删不改。** 记录本身是每转完一份就写的（`maint.note_run`），
    不等整批结束 —— 中途崩掉时最后一条正好指向病根。
    """
    try:
        return {'ok': True, 'rows': maint.runs(limit)}
    except Exception as e:
        # 历史读不出来不该让这一屏整个废掉，给个空列表加错误说明。
        return {'ok': False, 'rows': [], 'error': '%s: %s' % (type(e).__name__, e)}


@app.get('/api/maint/scan')
def maint_scan():
    r"""各项占用有多大。给「关于 → 环境检测」那一屏用。

    会扫 pip 缓存目录（实测小蔡机器上 1462 个文件），秒级完成 ——
    只读每个 zip 的目录不解压。
    """
    return maint.scan()


@app.post('/api/maint/clean')
def maint_clean(req: CleanReq):
    r"""清理选中的项。

    🔴 **转换进行中不许清** —— 那时 _tmp 里有正在用的中间产物，
    删了当场炸。跟「转换中禁用检查更新」一个规矩。

    🔴 **装东西的时候也不许清。** 三类安装任务各用一个全局字典，
    **一个都不在 `_TASKS` 里** —— `_DL`（模型 / GPU 运行库 /
    vcredist）、`_UPG`（依赖升级）、`_UPD`（软件自更新）。只判
    `_TASKS` 会把它们全放行：pip 正往 `%TEMP%` 的工作目录里解包
    时被清掉，几 GB 白下，而且报错是一串莫名其妙的文件不存在。
    （2026-09-06 加 temp_pip 那一项时查出来的既有缺口。）

    `_UPD` 判两个值：它的状态机有六种（idle / running / installing /
    need_confirm / done / error），**在跑的是 running 和 installing**。
    自更新的 installing 阶段在往安装目录搬文件，它自己的临时目录用的是
    `p2w_upd_` 前缀（`update.py:913`），跟我们只删 `pip-` 前缀的
    temp_pip 不冲突 —— 拦它是为了「安装中不许清」这条规矩本身，
    不是因为会互相删。`need_confirm` 是在等用户点，不算在跑。
    """
    with _LOCK:
        busy = (any(t.get('state') == 'running' for t in _TASKS.values())
                or _DL.get('state') == 'running'
                or _UPG.get('state') == 'running'
                or _UPD.get('state') in ('running', 'installing'))
    if busy:
        return JSONResponse({'detail': '正在转换或安装，完成后再清理'},
                            status_code=409)
    return maint.clean(keys=req.keys, pip_paths=req.paths)


@app.get('/api/deps/check')
def deps_check():
    r"""torch / mineru / 模型的本地版本和上游版本。

    **只在用户主动点「检查上游」时调**，不在打开页面时自动查 ——
    照搬 README 里那条既有规矩：「速度那一列没测过就是空的，
    不拿别的数字顶替」。

    实测约 3.6 秒（两次 pip 子进程 + 一次 HTTP）。
    """
    return deps.check_all()


@app.get('/api/deps/local')
def deps_local():
    """只读本地版本，不联网。打开页面时就能显示。"""
    return {'ok': True, 'versions': deps.local_versions(),
            'models_ready': paths.models_ready(),
            'models_size': paths.models_size()}


# 诊断文件里收多少条转换历史。小蔡 2026-09-08 定 50 —— 200 条太长，
# 最近一次又太少（那是「复制」时代的量）。
DIAG_RUNS = 50


class DiagExportReq(BaseModel):
    r"""前端那一小包 —— **只有它拿得到的东西**，后端够不着。"""
    # 前端已经拼好的那十行摘要（pages.js 的 diagText）。原样放文件开头，
    # 后端**不重拼一遍** —— 重拼就是两处逻辑，改了一处忘另一处，
    # 这个项目最常栽的就是这种。一份逻辑，两个出口。
    summary: str = ''
    errors: list = []       # JS 报错（window.onerror 抓的）
    ua: str = ''            # Electron / Chrome 版本
    screen: str = ''        # 窗口实际尺寸
    task: dict = {}         # 当前任务
    pending: list = []      # 待办队列


def _kv(L, k, fn):
    r"""收一项，**独立兜底**。

    🔴 这份文件恰恰是在机器出问题时才生成的，那时候本来就有东西读不到 ——
    显卡驱动挂了 `gpu.detect()` 就失败，模型目录没了扫描就报错。
    **「读不到」本身就是最值钱的线索**，让整份文件因为它失败等于把线索扔了。
    所以每一行各兜各的，谁也拖不垮谁。
    """
    try:
        v = fn()
    except Exception as e:
        v = '（读不到：%s: %s）' % (type(e).__name__, str(e)[:70])
    L.append('  %-20s %s' % (k, v))


def _tail(path, n):
    """日志的最后 n 行。文件不在、读不了都不抛异常。

    大文件只从尾部读 512 KB —— convert.log 转一天能到几十 MB，
    整个读进来纯属浪费。
    """
    try:
        if not os.path.isfile(path):
            return ['（没有这个文件）']
        size = os.path.getsize(path)
        with io.open(path, 'rb') as f:
            if size > 512 * 1024:
                f.seek(size - 512 * 1024)
                head = f.tell()
                f.readline()          # 丢掉开头那半行残句
                # 🔴 **这 512 KB 里一个换行都没有时，别丢。**
                #    tqdm 刷新用的是 \r 不是 \n，日志里出现「几百 KB 一整行」
                #    很常见。那种情况下 readline 会把整段吃光，最后报
                #    「（空的）」—— 明明有几十 MB，却把人往错方向带。
                if f.tell() >= size:
                    f.seek(head)
            raw = f.read()
        return raw.decode('utf-8', 'replace').splitlines()[-n:] or ['（空的）']
    except Exception as e:
        return ['（读不到：%s: %s）' % (type(e).__name__, str(e)[:70])]


def _slurp(path, limit=4000):
    """读一个小配置文件，压成一行。**用 with** —— 这是全文件唯一一处
    以前裸 open 的地方，CPython 下靠引用计数也能收，但不该赌。
    """
    with io.open(path, encoding='utf-8', errors='replace') as f:
        return f.read(limit).replace(chr(10), ' ')


def _write_diag(text):
    r"""写文件。**三级兜底**，返回 (路径, 错误)。

    小蔡 2026-09-08：「那就不让他失败」。做不到 100%，但可以让它极难失败 ——
    三个位置全军覆没时，这台电脑基本已经没法用了。

    🔴 **utf-8-sig + CRLF**：老师是拿记事本打开的。不带 BOM 中文会乱码，
       不是 CRLF 整个文件会连成一行 —— 那样文件生成了也等于没生成。
    """
    import tempfile
    # 🔴 **先洗一遍孤立代理字符，再动笔。**
    #
    #    Electron 里的 JS 字符串允许孤立代理（\ud800-\udfff），
    #    JSON.stringify 会原样输出，Python 的 json.loads 照单全收 ——
    #    然后写盘时 UnicodeEncodeError，三个位置全部一样失败，一个字都拿不到。
    #    文件名编码坏掉的 PDF（U 盘、网盘同步过来的）真能触发。
    #
    #    更坏的是 TextIOWrapper 是边编码边刷的：炸之前已经刷出去的部分留在盘上，
    #    三个目录各留一个**半截**的诊断文件 —— 用户很可能就把半截那份发出来。
    #    洗在这里，三级兜底才是干净的。
    text = text.encode('utf-8', 'replace').decode('utf-8')
    # 秒级重名会互相截断（两个软件实例同时点）。带上毫秒，一行的事。
    _t = time.time()
    name = '诊断_%s_%03d.txt' % (
        time.strftime('%Y%m%d_%H%M%S', time.localtime(_t)), int(_t * 1000) % 1000)
    last = ''
    # 🔴 **三个位置传的是「怎么拿」，不是「拿到的值」。**
    #
    #    原来写的是 `for d in (paths.LOGS, paths.ROOT, tempfile.gettempdir())` ——
    #    元组在进循环**之前**整体求值，`gettempdir()` 一抛异常，循环一次都
    #    不执行，前两级明明可能是能写的。而它恰恰在 TEMP 被指到不存在的盘、
    #    磁盘满、权限被组策略锁死时才抛 —— **正是这个函数唯一存在的场景**。
    #    三级兜底在最需要它的时候变成零级。
    for get_dir in (lambda: paths.LOGS, lambda: paths.ROOT,
                    tempfile.gettempdir):
        try:
            d = get_dir()
            paths.ensure(d)
            p = os.path.join(d, name)
            with io.open(p, 'w', encoding='utf-8-sig', newline='\r\n',
                         errors='replace') as f:
                f.write(text)
            return p, ''
        except Exception as e:
            last = '%s: %s' % (type(e).__name__, str(e)[:60])
    return '', last


def _diag_text(req):
    """把能拿到的一切拼成人看的文本。**先在内存里收齐，最后一次性写** ——
    中途失败也不会留下半截文件。
    """
    import platform
    L = []
    L.append('=' * 60)
    L.append(req.summary or '（摘要没拿到 —— 环境检测页还没渲染过）')
    L.append('=' * 60)
    L.append('生成于 ' + time.strftime('%Y-%m-%d %H:%M:%S'))

    L.append('')
    L.append('【环境细节】')
    # 🔴 **一律用 lambda 包一层。** 直接传 `paths.python_exe` 的话，属性访问
    #    发生在 `_kv` 的 try **外面** —— 哪天这个符号被改名或挪走，别的行会
    #    老老实实打印「读不到：AttributeError」，这几行却会让整份诊断 500。
    #    兜底必须是均匀的，否则「每一行各兜各的」就是句空话。
    _kv(L, 'python.exe', lambda: paths.python_exe())
    _kv(L, 'mineru 可用', lambda: paths.mineru_available())
    _kv(L, 'CUDA 通道', lambda: '%s（驱动 %s）' % (
        torchdep.pick_channel(torchdep.current_driver())[0],
        torchdep.current_driver()))
    _kv(L, 'GPU 运行库', lambda: torchdep.why())
    _kv(L, 'torch 详情', lambda: json.dumps(torchdep.info(), ensure_ascii=False))
    _kv(L, 'Office XSL', lambda: tomath.find_xsl() or '（找不到，公式转不了）')
    _kv(L, 'node', lambda: '有' if tomath.node_available() else '没有')
    _kv(L, 'pandoc', lambda: todocx.PANDOC if todocx.pandoc_available() else '没有')
    _kv(L, 'C++ 运行库', lambda: '装过' if vcredist.already_done() else '没装过')

    L.append('')
    L.append('【路径与编码】')
    # 🔴 这一组是给「中文路径」那类问题准备的。发行版的中文路径补丁
    #    曾经一次都没生效过，当时要是诊断里有这几行，根本不用查那么久。
    _kv(L, '安装目录', lambda: paths.ROOT)
    _kv(L, '  含非 ASCII', lambda: '是' if any(ord(c) > 127 for c in paths.ROOT) else '否')
    _kv(L, '  含空格', lambda: '是' if ' ' in paths.ROOT else '否')
    _kv(L, '用户名', lambda: os.environ.get('USERNAME', '?'))
    _kv(L, '  含非 ASCII', lambda: '是' if any(
        ord(c) > 127 for c in os.environ.get('USERNAME', '')) else '否')
    _kv(L, 'TEMP(环境变量)', lambda: os.environ.get('TEMP', '?'))
    _kv(L, 'TEMP(系统算的)', lambda: __import__('tempfile').gettempdir())
    _kv(L, '默认编码', lambda: '%s / %s' % (sys.getdefaultencoding(),
                                            sys.getfilesystemencoding()))
    _kv(L, 'CPU', lambda: platform.processor() or '?')
    _kv(L, '逻辑核心', lambda: str(os.cpu_count()))

    L.append('')
    L.append('【模型】')
    _kv(L, '就绪', lambda: '是' if models.ready() else '否')
    # ★ 指到别的项目去了这种事，光看界面发现不了
    _kv(L, '实际位置', lambda: models.where() or '（没找到）')
    _kv(L, '占用', lambda: '%.2f GB' % (paths.models_size() / 1024.0 ** 3))
    _kv(L, '学到的总量', lambda: str(models.learned_total()))
    _kv(L, 'mineru.json', lambda: _slurp(paths.CONFIG))

    L.append('')
    L.append('【占用明细】')
    _kv(L, '各项', lambda: ' / '.join(
        '%s %.2fGB' % (x['label'].split('（')[0], x['size'] / 1024.0 ** 3)
        for x in (maint.scan().get('items') or [])))

    L.append('')
    L.append('【升级状态】')
    _kv(L, '待装的', lambda: json.dumps(upgrade.pending(), ensure_ascii=False))
    _kv(L, '状态机', lambda: json.dumps(upgrade.read_state(), ensure_ascii=False))
    _kv(L, '备份', lambda: ' / '.join(
        '%s %.2fGB' % (b.get('name', '?'), b.get('size', 0) / 1024.0 ** 3)
        for b in upgrade.list_backups()) or '（没有）')

    L.append('')
    L.append('【当前任务】')
    _kv(L, '状态', lambda: json.dumps(
        {k: v for k, v in (req.task or {}).items()
         if k not in ('lines', 'results')}, ensure_ascii=False)[:600])
    _kv(L, '待办队列', lambda: ' / '.join(
        os.path.basename(x.get('path', '')) for x in (req.pending or [])) or '（空）')

    L.append('')
    L.append('【界面】')
    _kv(L, 'UA', lambda: req.ua or '?')
    _kv(L, '窗口', lambda: req.screen or '?')

    L.append('')
    L.append('【JS 报错】最近 %d 条' % len(req.errors or []))
    # 🔴 后端出事有 convert.log 兜着，前端出事以前**一个字都不留** ——
    #    界面就那么卡死，除了让用户重启没别的办法。这一段是补那个窟窿的。
    for e in (req.errors or [])[:20]:
        L.append('  ' + str(e)[:300])
    if not req.errors:
        L.append('  （没有 —— 这是好事）')

    L.append('')
    L.append('【转换历史】最近 %d 条（路径完整保留，路径本身常是病根）' % DIAG_RUNS)
    try:
        for r in maint.runs(DIAG_RUNS):
            L.append('  %s  %s  %s  %s页  公式%s  %s秒' % (
                r.get('time', ''), '✓' if r.get('ok') else '✗',
                r.get('file', ''), r.get('pages', 0),
                r.get('formulas', '?'), r.get('took_sec', 0)))
            L.append('      ' + (r.get('pdf') or ''))
            if not r.get('ok'):
                L.append('      错误：' + (r.get('error_full') or r.get('error') or ''))
    except Exception as e:
        L.append('  （读不到：%s）' % e)

    for fn, n in (('convert.log', 200), ('torch_install.log', 100),
                  ('model_download.log', 100)):
        L.append('')
        L.append('【%s】最后 %d 行' % (fn, n))
        for ln in _tail(os.path.join(paths.LOGS, fn), n):
            L.append('  ' + ln)

    L.append('')
    L.append('（上游版本未查 —— 那要联网，会让这一下从 1 秒变成半分钟）')
    return chr(10).join(L)


@app.post('/api/diag/export')
def export_diag(req: DiagExportReq):
    r"""一键生成诊断文件。返回文件路径，前端拿它弹资源管理器。

    小蔡 2026-09-08 定：**不要复制到剪贴板那条路了，任何情况都生成文件。**
    理由是老师把十行粘进微信还行，粘三百行就没人看了；发个文件是一次拖拽。
    """
    text = _diag_text(req)
    path, err = _write_diag(text)
    if not path:
        # 三个位置都写不进去。**不静默** —— 这个项目吃过静默失败的亏。
        return JSONResponse(
            {'detail': 'logs、安装目录、临时目录都写不进去（%s）' % err},
            status_code=500)
    return {'ok': True, 'path': path,
            'bytes': len(text.encode('utf-8', 'replace')),
            'lines': text.count(chr(10)) + 1}


@app.get('/api/diag')
def diagnostics():
    r"""诊断报告的原始数据。前端拼成一段文本给用户复制。

    老师打电话说「用不了」的时候，这一段能省掉十几轮问答 ——
    显卡驱动、装了什么版本、模型下没下完、最近一次错在哪，全在里面。
    """
    import platform
    loc = update.local_version()
    out = {
        'ok': True,
        'tag': loc.get('tag', ''),
        'sha': (loc.get('sha') or '')[:7],
        'os': platform.platform(),
        'root': paths.ROOT,
        'writable': paths.writable(),
        'free_gb': round(paths.free_bytes() / 1024 ** 3, 1),
        'versions': deps.local_versions(),
        'models_ready': paths.models_ready(),
        'models_size': paths.models_size(),
        'last_run': maint.last_run(),
        'last_error': maint.last_error(),
    }
    try:
        import gpu
        out['gpu'] = gpu.detect()
    except Exception:
        out['gpu'] = None
    # 以管理员身份跑的话，%LOCALAPPDATA% 指向管理员账户，看到的
    # pip 缓存跟用户平时用的不是同一个 —— 排查时这一行能省很多事。
    try:
        import ctypes
        out['admin'] = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        out['admin'] = False
    return out


@app.post('/api/upgrade/plan')
def upgrade_plan(req: UpgradeReq):
    r"""预演：这次升级到底会动哪些包。**不真装。**

    用 pip 自己的 --dry-run --report，所以「该下哪个文件」仍然是
    pip 判断的。发行版的 pip 实测是 26.2.1，支持这两个参数。

    🔴 如果 pip 解不出来（比如只勾 mineru 但新版要求更新的 torch，
    而 torch 被约束文件钉住了），这里会返回 ok=False 加报错 ——
    **那正是约束文件要的效果**：显式暴露冲突，而不是偷偷装出一个
    坏组合。
    """
    with _LOCK:
        busy = any(t.get('state') == 'running' for t in _TASKS.values())
    if busy:
        return JSONResponse({'detail': '正在转换，转完再升级'},
                            status_code=409)
    return upgrade.plan(req.picked, req.targets)


@app.post('/api/upgrade/download')
def upgrade_download(req: UpgradeReq):
    r"""后台下载。**用户可以继续转 PDF，全程不打扰。**

    下完只提示「重启后生效」，不装 —— 安装放在重启时做，那个时刻
    本来就没有转换在跑，天然不打扰任何人。
    """
    with _LOCK:
        if _UPG.get('state') == 'running':
            return JSONResponse({'detail': '已经在下了'}, status_code=409)
        _UPG.clear()
        _UPG.update({'state': 'running', 'lines': [], 'error': '',
                     # 下了多少 / 一共多少。**两个数都来自 pip 自己吐的
                     # 字节**，不是估的（见 upgrade.download 的注释）。
                     'got': 0, 'total': 0,
                     'picked': req.picked})

    def work():
        def on_prog(got, total):
            with _LOCK:
                _UPG['got'], _UPG['total'] = got, total

        def on_log(line):
            with _LOCK:
                _UPG['lines'].append(line[-300:])
                if len(_UPG['lines']) > 400:
                    del _UPG['lines'][0:len(_UPG['lines']) - 400]
        try:
            r = upgrade.download(req.picked, req.targets,
                                 on_log=on_log, on_progress=on_prog)
        except Exception as e:
            r = {'ok': False, 'error': '%s: %s' % (type(e).__name__, e)}
        with _LOCK:
            _UPG['state'] = 'done'
            _UPG['ok'] = bool(r.get('ok'))
            _UPG['error'] = r.get('error', '')

    threading.Thread(target=work, daemon=True).start()
    return {'ok': True}


@app.get('/api/upgrade/download')
def upgrade_download_status():
    with _LOCK:
        return dict(_UPG)


@app.post('/api/upgrade/install')
def upgrade_install():
    r"""装下好的那批。**这个接口以前不存在。**

    🔴 2026-09-07 小蔡实测：torch 2.14 下好了、界面说「重启后生效」，
       重启之后什么都没发生。查下来 `upgrade.install()` 逻辑完整、
       `--dry-run` 实测能装、`/api/upgrade/pending` 也写好了 ——
       **但没有任何地方去调 install**。整条链在「谁按下那个装」断了，
       而界面还理直气壮说「重启后生效」。

    **转换中拒绝**：torch 的 dll 那会儿正被 MinerU 子进程占着，
    这时候装必然出事。服务进程自己不 import torch（只 import
    torchdep，纯逻辑），所以只要没有转换在跑，文件就是干净的。
    """
    with _LOCK:
        if _UPGI.get('state') == 'running':
            return JSONResponse({'detail': '已经在装了'}, status_code=409)
        busy = (any(t.get('state') == 'running' for t in _TASKS.values())
                or _DL.get('state') == 'running'
                or _UPG.get('state') == 'running'
                or _UPD.get('state') in ('running', 'installing'))
        if busy:
            return JSONResponse({'detail': '正在转换或下载，完成后再安装'},
                                status_code=409)
        _UPGI.clear()
        _UPGI.update({'state': 'running', 'lines': [], 'error': '', 'ok': False})

    def work():
        def on_log(line):
            with _LOCK:
                _UPGI['lines'].append(line[-300:])
                if len(_UPGI['lines']) > 400:
                    del _UPGI['lines'][0:len(_UPGI['lines']) - 400]
        try:
            r = upgrade.install(on_log=on_log)
        except Exception as e:
            # 🔴 线程里抛异常没人接的话，state 会永远停在 running，
            #    界面就一直转圈。宁可把原因原样带回去。
            r = {'ok': False, 'error': '%s: %s' % (type(e).__name__, e)}
        with _LOCK:
            _UPGI['state'] = 'done'
            _UPGI['ok'] = bool(r.get('ok'))
            _UPGI['error'] = r.get('error', '')
            _UPGI['rolled_back'] = bool(r.get('rolled_back'))

    threading.Thread(target=work, daemon=True).start()
    return {'ok': True}


@app.get('/api/upgrade/install')
def upgrade_install_status():
    with _LOCK:
        return dict(_UPGI)


@app.get('/api/upgrade/pending')
def upgrade_pending():
    r"""开机时问一次：有没有没做完的升级。

    🔴 下载中断电**不算事**（环境没坏，旧的还能用），正常进主界面。
    只有装到一半才必须处理 —— 那时 import torch 可能已经失败。
    """
    return upgrade.pending()


@app.post('/api/upgrade/rollback')
def upgrade_rollback():
    """回滚到升级前。**无条件** —— 不检查坏没坏。"""
    return upgrade.rollback()


@app.get('/api/upgrade/backups')
def upgrade_backups():
    """有哪些备份。给环境检测那一屏列出来让用户自己清。"""
    return {'ok': True, 'items': upgrade.list_backups()}


@app.get('/api/ping')
async def ping():
    return {'ok': True}


def main():
    cfg = uvicorn.Config(app, host='127.0.0.1', port=0, log_level='warning')
    server = uvicorn.Server(cfg)

    # 端口是系统分配的，得等它真绑上才知道。外壳在等 stdout 那一行。
    def announce():
        while not getattr(server, 'started', False):
            time.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        print('PDF2WORD_PORT=%d' % port, flush=True)

    threading.Thread(target=announce, daemon=True).start()
    server.run()


if __name__ == '__main__':
    main()
