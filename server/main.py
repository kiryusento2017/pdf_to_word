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
import paths
import torchdep
import vcredist                                          # noqa: E402
import probe                                          # noqa: E402
import sources                                      # noqa: E402
import todocx                                         # noqa: E402
import tomath                                         # noqa: E402
import update                                         # noqa: E402

app = FastAPI(title='PDF 转 Word')
app.add_middleware(CORSMiddleware, allow_origins=['*'],
                   allow_methods=['*'], allow_headers=['*'])

# 任务表。单机单用户，内存里放着就行 —— 存盘反而要处理「上次没跑完的任务」
# 这种没人关心的状态。软件关掉任务就没了，符合用户预期。
_TASKS = {}
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
_UPD = {'state': 'idle', 'got': 0, 'total': 0, 'error': '',
        'file': '', 'via': '', 'files': 0}


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
    # 用户已经知道「拿不到官方校验值」并且选择继续。这是唯一会被读的字段。
    allow_unverified: bool = False


def _upd_work(allow_unverified=False):
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
    ok, err, via = update.download(asset['url'], dest, on_progress=on_prog,
                                   digest=asset.get('digest', ''),
                                   size=asset.get('size', 0),
                                   allow_unverified=allow_unverified)
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
                     'error': '', 'file': '', 'via': ''})
    threading.Thread(target=_upd_work, daemon=True,
                     args=(bool(req.allow_unverified),)).start()
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

_DL = {'state': 'idle', 'got': 0, 'total': models.TOTAL_BYTES,
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

    with _LOCK:
        _DL['phase'] = 'models'
        _DL['cmd'] = models.download_cmd_text(source)
        _DL['log'] = models.log_path()
        _DL['got'], _DL['total'] = 0, models.TOTAL_BYTES
        _DL['lines'] = []
    ok, err = models.download(source, on_progress=on_prog, on_log=on_log,
                              stop_flag=stopped)
    with _LOCK:
        _DL['state'] = 'done' if ok else 'error'
        _DL['error'] = err
        _DL['phase'] = ''


@app.post('/api/models/download')
async def start_download(req: DownloadReq):
    with _LOCK:
        if _DL['state'] == 'running':
            return {'ok': True, 'already': True}
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

        def on_prog(stage, cur, tot, _tid=task_id):
            with _LOCK:
                s = _TASKS[_tid]
                s['stage'], s['stage_cur'], s['stage_total'] = stage, cur, tot

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
    # 页数在体检时已经知道了，用它估总时长。一份读不了就按 10 页算，
    # 不让一个坏文件把整批的预计搞成 0。
    pages = []
    for p in req.paths:
        r = probe.probe_pdf(p)
        pages.append(r['pages'] if r['ok'] and r['pages'] else 10)
    with _LOCK:
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
                       'progress_line': ''}
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
