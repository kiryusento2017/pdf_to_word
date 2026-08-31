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
    return {
        'gpu': {'ok': g['ok'], 'why': g['why'], 'detail': g['gpu']},
        'office': {'ok': tomath.xsl_available(), 'path': tomath.find_xsl() or ''},
        'node': {'ok': tomath.node_available()},
        'pandoc': {'ok': todocx.pandoc_available(), 'path': todocx.PANDOC},
        'mineru': {'ok': bool(_find_mineru()), 'path': _find_mineru() or ''},
    }


def _find_mineru():
    """找 MinerU。装在自己 venv 里最好，找不到就退回工作台那份（开发期用）。"""
    here = os.path.join(ROOT, '.venv', 'Scripts', 'mineru.exe')
    if os.path.isfile(here):
        return here
    dev = os.path.join(os.path.dirname(ROOT), 'edu_book_generator',
                       '.venv', 'Scripts', 'mineru.exe')
    return dev if os.path.isfile(dev) else ''


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


# ── 转换 ────────────────────────────────────────────────────────────────
class ConvertReq(BaseModel):
    paths: list[str]
    out_dir: str = ''          # 空 = 输出到每份 PDF 自己所在的文件夹
    prefer_xsl: bool = True
    source: str = ''           # 模型下载源的 id，空 = 让 MinerU 自己决定


def _work(task_id, paths, out_dir, prefer_xsl, source=''):
    r"""后台线程：逐份转。一份失败不影响其余。

    🔴 **整体套一层兜底**。这是后台线程，异常会被 Python 悄悄吞掉，
    任务就永远停在 running —— 界面上是转圈转到天荒地老，用户只会以为
    软件慢，不会知道出了事。实测撞见过：漏了一个 import，
    四条测试全部等到超时才失败。
    """
    try:
        _work_inner(task_id, paths, out_dir, prefer_xsl, source)
    except Exception as e:
        with _LOCK:
            t = _TASKS.get(task_id)
            if t is not None:
                t['state'] = 'done'
                t['error'] = '%s: %s' % (type(e).__name__, str(e)[:200])


def _work_inner(task_id, paths, out_dir, prefer_xsl, source=''):
    mineru = _find_mineru()
    tmp = os.path.join(ROOT, '_tmp', 'extract')
    # 用户在首启那屏选的源。**必须真的传下去** —— 只记在前端等于让人
    # 做了个没用的选择题，比不给选更糟。
    env = {}
    for s in sources.MODEL_SOURCES:
        if s['id'] == source:
            env = s['env']
            break
    for i, pdf in enumerate(paths):
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

    with _LOCK:
        _TASKS[task_id]['state'] = 'done'
        _TASKS[task_id]['current'] = len(paths)


@app.post('/api/convert')
async def start_convert(req: ConvertReq):
    if not req.paths:
        return JSONResponse({'detail': '没有要转换的文件'}, status_code=400)
    if not _find_mineru():
        return JSONResponse({'detail': '找不到 MinerU，还没装好'}, status_code=400)
    tid = uuid.uuid4().hex[:12]
    with _LOCK:
        _TASKS[tid] = {'state': 'running', 'total': len(req.paths), 'current': 0,
                       'current_name': '', 'stage': '', 'stage_cur': 0,
                       'stage_total': 0, 'results': [], 'cancel': False,
                       'error': '', 'started': time.time()}
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
        return dict(t, elapsed=time.time() - t['started'])


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
