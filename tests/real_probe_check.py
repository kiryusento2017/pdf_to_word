# -*- coding: utf-8 -*-
r"""拿真实讲义验 probe。**不是单元测试** —— 它依赖本机的真实文件，
换台机器就跑不了，所以不放进 discover 的自动套件里，手动跑。

存在的理由：单元测试用的是脚本造的 PDF，只能证明逻辑自洽，
不能证明它在真书上判得对。工作台那边的教训是「测试全绿但实际没生效」，
这一步就是防这个。

跑法：
    .venv\Scripts\python.exe tests\real_probe_check.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import probe  # noqa: E402

DIRS = [
    r'D:\工作\高1数学秋季课~腾飞版\高1秋季课~腾飞版',
    r'D:\工作\学er思高中物理人教版四季讲义',
]

found = []
for d in DIRS:
    if os.path.isdir(d):
        found.extend(probe.scan_dir(d))

if not found:
    print('这几个目录下没找到 PDF，检查路径：')
    for d in DIRS:
        print('  %s  %s' % ('在' if os.path.isdir(d) else '不在', d))
    sys.exit(1)

print('扫到 %d 份 PDF，逐份探测：' % len(found))
print('')
n_text = n_scan = n_bad = 0
for r in probe.probe_many(found[:40]):
    name = os.path.basename(r['path'])
    if len(name) > 42:
        name = name[:39] + '...'
    if not r['ok']:
        n_bad += 1
        print('  [坏]  %-42s %s' % (name, r['error']))
        continue
    # 只报事实，不下「好 / 差」的结论 —— has_text 不是质量保证
    # （那个「己知」错字在原 PDF 文字层里就是错的，走哪种模式都消不掉）。
    if r['kind'] == 'text':
        n_text += 1
    else:
        n_scan += 1
    note = ''
    if r['scan_pages']:
        sp = r['scan_pages']
        if len(sp) == r['pages']:
            note = '整份无文字层'
        else:
            note = '第 %s 页无文字层' % ','.join(str(x) for x in sp[:6])
            if len(sp) > 6:
                note += ' 等 %d 页' % len(sp)
    print('  %-44s %2d 页  %s' % (name, r['pages'], note))

print('')
print('能取到文字的 %d ｜ 整份取不到的 %d ｜ 读不了 %d' % (n_text, n_scan, n_bad))
print('')
print('注意：「能取到文字」不等于「文字是对的」：实测那个「己知」错字')
print('   在原 PDF 的文字层里就是错的，txt / auto / ocr 三种模式都消不掉。')
print('   标出来的「无文字层」页只是提示那几页整页当图识别、出错概率更高。')
