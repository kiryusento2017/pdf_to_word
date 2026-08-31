# -*- coding: utf-8 -*-
r"""多源并发测速 + 选源 + 断点续传下载。

照搬金石工作台 BUILD_PLAN §12-26 / BACKLOG B35 的设计，一条不改：

  · 每类源内置多个候选
  · **点下载时并发对所有源各测 2-3 秒实速**，按预计耗时排序，最快的默认选中
  · 途中掉速自动切次优源 + 断点续传
  · **不存历史成绩** —— 下载是低频动作，现测成本可忽略，旧成绩反而过时误导
  · **不用 ping 判优** —— 很多 CDN 屏蔽 ICMP，且低延迟 != 高带宽
  · 界面显示「预计几分钟」而不是 MB/s，电脑盲直接点开始即可

B35 的立论依据是实测：Chromium 走 npmmirror 比 Google 官方快 22 倍
（15.04 vs 0.68 MB/s），torch 却是官方源更快 —— **没有哪个源普遍最优**，
写死必然坑一批人。
"""
import os
import threading
import time
import urllib.request

# 测速时长。太短测不出稳定带宽（TCP 还在慢启动），太长让人干等。
PROBE_SECONDS = 2.5
PROBE_TIMEOUT = 6
CHUNK = 64 * 1024

# 模型源。每个源给一个**小而真实**的探测文件 URL —— 不能拿首页测，
# 首页是 HTML 且常被 CDN 缓存在边缘节点，测出来的速度跟真实下载无关。
MODEL_SOURCES = [
    {'id': 'modelscope', 'name': 'ModelScope（阿里，国内快）',
     'env': {'MINERU_MODEL_SOURCE': 'modelscope'},
     'probe': 'https://modelscope.cn/api/v1/models/OpenDataLab/PDF-Extract-Kit-1.0'},
    {'id': 'hf-mirror', 'name': 'HF-Mirror（国内镜像）',
     'env': {'MINERU_MODEL_SOURCE': 'huggingface', 'HF_ENDPOINT': 'https://hf-mirror.com'},
     'probe': 'https://hf-mirror.com/api/models/opendatalab/PDF-Extract-Kit-1.0'},
    {'id': 'huggingface', 'name': 'HuggingFace 官方（海外快）',
     'env': {'MINERU_MODEL_SOURCE': 'huggingface'},
     'probe': 'https://huggingface.co/api/models/opendatalab/PDF-Extract-Kit-1.0'},
]


def _probe_one(src, out, seconds=PROBE_SECONDS):
    """测一个源的实速（字节/秒）。测不通就是 0，不抛异常。"""
    got, t0 = 0, time.time()
    try:
        req = urllib.request.Request(src['probe'], headers={'User-Agent': 'pdf2word/0.1'})
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as r:
            while time.time() - t0 < seconds:
                b = r.read(CHUNK)
                if not b:
                    break
                got += len(b)
    except Exception as e:
        out[src['id']] = {'bps': 0, 'error': str(e)[:80]}
        return
    dt = max(time.time() - t0, 0.001)
    out[src['id']] = {'bps': got / dt, 'error': ''}


def probe_all(sources=None, seconds=PROBE_SECONDS):
    r"""并发测所有源。返回 [{id, name, bps, error, ...}]，按快慢排序。

    **并发**：串行测三个源要 7 秒以上，人会以为卡住了。
    """
    sources = sources or MODEL_SOURCES
    out, threads = {}, []
    for s in sources:
        t = threading.Thread(target=_probe_one, args=(s, out, seconds), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=PROBE_TIMEOUT + 2)

    rows = []
    for s in sources:
        r = out.get(s['id'], {'bps': 0, 'error': '测速没跑完'})
        rows.append(dict(s, bps=r['bps'], error=r['error']))
    # 快的在前；测不通的沉底
    rows.sort(key=lambda x: -x['bps'])
    return rows


def eta_words(total_bytes, bps):
    r"""预计还要多久，**说成人话**。

    B35 的验收标准之一：界面展示「预计几分钟」而不是 MB/s ——
    老师看得懂前者。
    """
    if not bps or bps <= 0:
        return '连不上'
    sec = total_bytes / float(bps)
    if sec < 90:
        return '不到 2 分钟'
    m = int(round(sec / 60.0))
    if m < 60:
        return '约 %d 分钟' % m
    h = m // 60
    return '约 %d 小时 %d 分钟' % (h, m % 60) if m % 60 else '约 %d 小时' % h


def pick_best(rows):
    """选最快的那个。全都连不上时返回 None，由调用方决定怎么说。"""
    for r in rows:
        if r['bps'] > 0:
            return r
    return None


def download(url, dest, on_progress=None, timeout=30, retries=3):
    r"""断点续传下载。返回 {ok, error, bytes}。**不抛异常**。

    断点续传是必需的，不是锦上添花：模型 4.6 GB，家用网络断一次就得从头来，
    那种体验会让人直接卸载。
    """
    tmp = dest + '.part'
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or '.', exist_ok=True)
    total = 0

    for attempt in range(retries):
        have = os.path.getsize(tmp) if os.path.isfile(tmp) else 0
        headers = {'User-Agent': 'pdf2word/0.1'}
        if have:
            headers['Range'] = 'bytes=%d-' % have
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                # 服务器不支持 Range 时会返回 200 + 完整内容，这时得从头写
                if have and r.status != 206:
                    have = 0
                    mode = 'wb'
                else:
                    mode = 'ab' if have else 'wb'
                clen = r.headers.get('Content-Length')
                total = (int(clen) + have) if clen else 0
                with open(tmp, mode) as f:
                    while True:
                        b = r.read(CHUNK)
                        if not b:
                            break
                        f.write(b)
                        have += len(b)
                        if on_progress:
                            on_progress(have, total)
            os.replace(tmp, dest)
            return {'ok': True, 'error': '', 'bytes': have}
        except Exception as e:
            if attempt == retries - 1:
                return {'ok': False, 'error': '%s: %s' % (type(e).__name__, str(e)[:120]),
                        'bytes': have}
            time.sleep(1.5 * (attempt + 1))
    return {'ok': False, 'error': '重试用尽', 'bytes': 0}
