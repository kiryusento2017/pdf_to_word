# -*- coding: utf-8 -*-
r"""本地 HTTP 服务。Electron 起它，前端跟它说话。

**只绑 127.0.0.1**，端口让系统随机分配，把实际端口打到 stdout 供外壳读 ——
写死端口会在用户同时开着别的软件时撞车，而那种失败现场极难查。

转换是长任务（一份 4 分钟），所以走「提交任务 + 轮询进度」，
不用 WebSocket：本地单机、任务量小，轮询足够，少一套连接状态要维护。
"""
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
import gpu                                            # noqa: E402
import models                                         # noqa: E402
import paths                                          # noqa: E402
import probe                                          # noqa: E402
import sources                                      # noqa: E402
import todocx                                         # noqa: E402
import tomath                                         # noqa: E402

app = FastAPI(title='PDF 转 Word')
app.add_middleware(CORSMiddleware, allow_origins=['*'],
                   allow_methods=['*'], allow_headers=['*'])

# 任务表。单机单用户，内存里放着就行 —— 存盘反而要处理「上次没跑完的任务」
# 这种没人关心的状态。软件关掉任务就没了，符合用户预期。
_TASKS = {}
_LOCK = threading.Lock()


# ── 环境自检 ────────────────────────────────────────────────────────────
@app.get('/api/env')
async def env():
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
    r"""找 MinerU。**只认自己 venv 里那份**。

    2026-08-31 之前这里有一条退路：找不到就用工作台的那份。
    环境独立之后撤掉了 —— 留着的话，别人机器上装漏了 MinerU 会静默失败，
    而在我这台机器上永远测不出来（因为工作台就在隔壁目录）。
    这种「只在开发机上能跑」的坑，宁可现在红。
    """
    here = os.path.join(ROOT, '.venv', 'Scripts', 'mineru.exe')
    return here if os.path.isfile(here) else ''


# ── 下载源（B35：点下载时并发实测，不存历史成绩，不用 ping 判优）────────
# 模型总量按 MinerU 实际拉下来的量估：约 4.6 GB。
MODEL_BYTES = 4.6 * 1024 * 1024 * 1024


@app.get('/api/sources')
async def list_sources():
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


# ── 下载模型 ────────────────────────────────────────────────────────────
# 单例：一台机器同时只可能下一次。存内存里就够 —— 软件关掉重开会重新
# 判断模型在不在，没必要持久化一个「上次下到一半」的状态。
_DL = {'state': 'idle', 'got': 0, 'total': models.TOTAL_BYTES,
       'error': '', 'line': '', 'cancel': False}


class DownloadReq(BaseModel):
    source: str = 'modelscope'


def _dl_work(source):
    def on_prog(got, total):
        with _LOCK:
            _DL['got'], _DL['total'] = got, total

    def on_log(line):
        with _LOCK:
            _DL['line'] = line[-200:]

    def stopped():
        with _LOCK:
            return _DL['cancel']

    ok, err = models.download(source, on_progress=on_prog, on_log=on_log,
                              stop_flag=stopped)
    with _LOCK:
        _DL['state'] = 'done' if ok else 'error'
        _DL['error'] = err


@app.post('/api/models/download')
async def start_download(req: DownloadReq):
    with _LOCK:
        if _DL['state'] == 'running':
            return {'ok': True, 'already': True}
        _DL.update({'state': 'running', 'got': 0, 'error': '',
                    'line': '', 'cancel': False})
    threading.Thread(target=_dl_work, args=(req.source or 'modelscope',),
                     daemon=True).start()
    return {'ok': True}


@app.get('/api/models/download')
async def download_status():
    with _LOCK:
        d = dict(_DL)
    d['ready'] = models.ready()
    return d


@app.post('/api/models/download/cancel')
async def cancel_download():
    with _LOCK:
        _DL['cancel'] = True
    return {'ok': True}


class UseLocalReq(BaseModel):
    dir: str = ''


@app.post('/api/models/use-local')
async def use_local_models(req: UseLocalReq):
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
async def scan(req: ScanReq):
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
#   纯 CPU 460 秒 / 10 页 = 46 秒每页
# 用来估「还要等多久」—— 那是用户唯一关心的数，而 MinerU 的阶段进度
# 回答不了它（各阶段耗时差 100 倍，跑满一条也可能只花 1 秒）。
SEC_PER_PAGE_GPU = 26.0
SEC_PER_PAGE_CPU = 46.0


def _sec_per_page():
    return SEC_PER_PAGE_GPU if gpu.detect()['ok'] else SEC_PER_PAGE_CPU


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

        dest = out_dir or os.path.dirname(pdf)
        name = os.path.splitext(os.path.basename(pdf))[0] + '.docx'
        rep = convert.pdf_to_word(pdf, os.path.join(dest, name), tmp,
                                  on_progress=on_prog, prefer_xsl=prefer_xsl,
                                  mineru=mineru, env=env)
        rep['line'] = convert.summary_line(rep)
        with _LOCK:
            _TASKS[task_id]['results'].append(rep)
            # 记下「已完成的那些一共花了多久」。_remain 要拿它算真实速率——
            # 不能用 elapsed，那里面含着当前这份正在跑的时间，会让估算
            # 随时间单调变大（越等越久）。
            _TASKS[task_id]['done_elapsed'] = time.time() - _TASKS[task_id]['started']

    with _LOCK:
        _TASKS[task_id]['state'] = 'done'
        _TASKS[task_id]['current'] = len(pdf_paths)


@app.post('/api/convert')
async def start_convert(req: ConvertReq):
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
                       'error': '', 'started': time.time(),
                       'pages': pages, 'sec_per_page': _sec_per_page()}
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
    if done and done_elapsed:
        done_pages = sum(pages[:done]) or 1
        spp = done_elapsed / float(done_pages)     # 只用已完成部分的真实耗时

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
    r"""取消。**只在两份之间生效** —— MinerU 那步是子进程，
    中途硬杀会留下半截产物，比多等一两分钟麻烦。
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
