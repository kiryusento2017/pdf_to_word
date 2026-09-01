# -*- coding: utf-8 -*-
r"""拿真实讲义的 MinerU 产物跑端到端。**不是单元测试**，依赖本机真实文件。

存在的理由：单元测试用的是几行的假 md，只能证明逻辑自洽。
工作台那边的教训是「测试全绿但实际没生效」—— 这一步就是防这个。

跑法：
    .venv\Scripts\python.exe tests\real_convert_check.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import todocx  # noqa: E402
import tomath  # noqa: E402

# 拿终末诗篇工作台那边现成的 MinerU 产物当**输入数据**（不是借它的环境）——
# 省掉每次跑都要先花几分钟 GPU 提取一遍。工作台目录改名或搬走的话，
# 这行要跟着改；跑不起来会直接打印提示，不会静默跳过。
EXTRACT = r'D:\claude_code_workspace\edu_book_generator\data\extract'
OUTDIR = os.path.join(ROOT, '_tmp', 'real_convert')

print('XSL：%s' % (tomath.find_xsl() or '没找到'))
print('node：%s' % ('可用' if tomath.node_available() else '不可用'))
print('pandoc：%s' % ('在' if todocx.pandoc_available() else '不在'))
print('')

if not os.path.isdir(EXTRACT):
    print('找不到 MinerU 产物目录：%s' % EXTRACT)
    sys.exit(1)
os.makedirs(OUTDIR, exist_ok=True)

jobs = []
for name in sorted(os.listdir(EXTRACT)):
    base = os.path.join(EXTRACT, name)
    if not os.path.isdir(base):
        continue
    for sub in sorted(os.listdir(base)):
        d = os.path.join(base, sub)
        if not os.path.isdir(d):
            continue
        mds = [f for f in os.listdir(d) if f.endswith('.md')]
        if mds:
            jobs.append((name, os.path.join(d, mds[0])))
            break

print('找到 %d 份产物，逐份转 Word：' % len(jobs))
print('')
print('  %-34s %6s %6s %5s %5s  %s' % ('书名', '公式', '换XSL', '表格', '图', '引擎'))
print('  ' + '-' * 78)

t0 = time.time()
n_ok = n_fail = 0
tot_f = tot_r = 0
notes = []
for name, md in jobs:
    out = os.path.join(OUTDIR, name[:40] + '.docx')
    r = todocx.md_to_docx(md, out)
    short = name if len(name) <= 34 else name[:31] + '...'
    if not r['ok']:
        n_fail += 1
        print('  %-34s  失败：%s' % (short, r['error'][:40]))
        continue
    n_ok += 1
    tot_f += r['formulas_src']
    tot_r += r['formulas_replaced']
    print('  %-34s %6d %6d %5d %5d  %s'
          % (short, r['formulas_src'], r['formulas_replaced'],
             r['tables'], r['images'], r['math_engine']))
    if r['math_engine'] != 'xsl' and r['math_note']:
        notes.append('%s：%s' % (short, r['math_note'][:90]))

print('')
print('成功 %d ｜ 失败 %d ｜ 用时 %.1f 秒' % (n_ok, n_fail, time.time() - t0))
print('公式合计 %d 个，其中 %d 个走了 Office 的 XSL' % (tot_f, tot_r))
if notes:
    print('')
    print('没走 XSL 的：')
    for x in notes:
        print('  - %s' % x)
print('')
print('产物在：%s' % OUTDIR)
