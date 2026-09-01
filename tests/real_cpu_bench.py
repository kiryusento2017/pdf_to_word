# -*- coding: utf-8 -*-
r"""纯 CPU 到底多慢。**要跑很久**，只在需要这个数时手动跑。

为什么要这个数：GPU 检测已经按小蔡定的方案实现了 —— 显卡不满足时
让用户自己选「退出」还是「仍然继续」。但「仍然继续」到底多难受，
没有数就只能说「会慢很多」，那是废话。有了数才能在界面上说
「这台电脑预计要 X 分钟一份」，让人自己判断划不划算。

做法：CUDA_VISIBLE_DEVICES 设成空，torch 就看不见显卡，
MinerU 自动退回 CPU。不用重装 CPU 版 torch（那会把 CUDA 版覆盖掉）。

跑法：
    .venv\Scripts\python.exe tests\real_cpu_bench.py [超时秒数]


⚠️ 2026-09-02 起产品**只用 GPU**（小蔡定的规矩，见 docs/DESIGN.md）。
   这个脚本留着是为了保住那组对照数据（GPU 262 秒 / CPU 460 秒），
   它不代表软件支持 CPU —— 正常流程里 MINERU_DEVICE_MODE 写死成 cuda，
   跑不到 CPU 那条路上去。要删的话先问一句，那组数是当初改产品判断的依据。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import extract  # noqa: E402
import gpu      # noqa: E402

PDF = (r'D:\工作\高1数学秋季课~腾飞版\高1秋季课~腾飞版'
       r'\4【腾飞】解不等式(学生版).pdf')
GPU_SECONDS = 235.0        # 同一份在 GPU 上的实测值

limit = float(sys.argv[1]) if len(sys.argv) > 1 else 2400

mineru = os.path.join(ROOT, '.venv', 'Scripts', 'mineru.exe')
if not os.path.isfile(mineru):
    print('这个项目自己的 mineru 还没装：%s' % mineru)
    sys.exit(1)

g = gpu.detect()
print('本机显卡：%s' % g['why'])
print('原书：%s' % os.path.basename(PDF))
print('对照：同一份在 GPU 上 %.0f 秒' % GPU_SECONDS)
print('上限：超过 %.0f 分钟就掐掉，结论按「慢到不可用」记' % (limit / 60))
print('')

out = os.path.join(ROOT, '_tmp', 'cpu_bench')
t0 = time.time()
last = [None]
stalled = [False]


def on_prog(stage, cur, tot):
    key = (stage, cur == 0 or cur == tot)
    if key == last[0]:
        return
    last[0] = key
    if cur != 0 and cur != tot:
        return
    print('  %-8s %4d/%-4d %6.0f 秒' % (stage, cur, tot, time.time() - t0))
    sys.stdout.flush()
    if time.time() - t0 > limit:
        stalled[0] = True


# CUDA_VISIBLE_DEVICES 设成空串 = 一张卡都看不见。
# 传给子进程即可，不影响本进程和别的软件。
env = {'CUDA_VISIBLE_DEVICES': ''}

r = extract.run(PDF, out, mineru=mineru, env=env, on_progress=on_prog)
dt = time.time() - t0

print('')
print('用时 %.0f 秒（%.1f 分钟）' % (dt, dt / 60))
if r['ok']:
    print('成功。比 GPU 慢 %.1f 倍' % (dt / GPU_SECONDS))
    print('')
    print('按这个速度，一份 10 页的讲义要 %.0f 分钟；' % (dt / 60))
    print('一份 30 页的教师版大约 %.0f 分钟。' % (dt / 60 * 3))
else:
    print('失败：%s' % r['error'][:400])
