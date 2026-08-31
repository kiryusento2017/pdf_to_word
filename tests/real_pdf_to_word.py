# -*- coding: utf-8 -*-
r"""端到端真跑：一份 PDF 直接出 Word。**要 GPU，要几分钟。**

这是阶段 1 唯一算数的验证 —— 前面 70 条单元测试都拦住了子进程，
只证明了「命令拼得对、进度解析得对、产物找得到」，没有一条证明
真的 MinerU 跑出来是这样。工作台那边的教训就是「测试全绿但实际没生效」。

跑法：
    .venv\Scripts\python.exe tests\real_pdf_to_word.py [PDF路径]
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import convert  # noqa: E402
import gpu      # noqa: E402

DEFAULT_PDF = (r'D:\工作\高1数学秋季课~腾飞版\高1秋季课~腾飞版'
               r'\4【腾飞】解不等式(学生版).pdf')
MINERU = os.path.join(r'D:\claude_code_workspace\edu_book_generator',
                      '.venv', 'Scripts', 'mineru.exe')

pdf = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PDF
work = os.path.join(ROOT, '_tmp', 'e2e')
out = os.path.join(ROOT, '_tmp', 'e2e_out.docx')

g = gpu.detect()
print('显卡：%s' % g['why'])
if not os.path.isfile(MINERU):
    print('找不到 mineru：%s' % MINERU)
    sys.exit(1)
print('原书：%s' % os.path.basename(pdf))
print('')
print('  %-8s %-22s %8s %8s' % ('阶段', '进度', '计数', '秒'))
print('  ' + '-' * 52)

seen = [None]
t0 = time.time()


def on_prog(stage, cur, tot):
    """每个阶段只打首尾两次，中间不刷屏 —— 这里是文本日志，不是界面。"""
    key = (stage, cur == 0 or cur == tot)
    if key == seen[0]:
        return
    seen[0] = key
    if cur != 0 and cur != tot:
        return
    bar = '#' * int(22.0 * cur / max(tot, 1))
    print('  %-8s %-22s %4d/%-4d %6.0f' % (stage, bar, cur, tot,
                                           time.time() - t0))


rep = convert.pdf_to_word(pdf, out, work, on_progress=on_prog, mineru=MINERU)
print('')
print('用时 %.0f 秒' % (time.time() - t0))
print(convert.summary_line(rep))
if rep['math_note']:
    print('公式：%s' % rep['math_note'])
if rep['ok']:
    print('产物：%s（%.0f KB）' % (out, os.path.getsize(out) / 1024.0))
else:
    print('失败：%s' % rep['error'][:300])
