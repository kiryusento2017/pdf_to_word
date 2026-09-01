# -*- coding: utf-8 -*-
r"""一份 PDF → 一份 Word。把三步串起来的编排层。

    probe   看一眼：几页、能不能打开、哪几页没有文字层
      ↓
    extract 调 MinerU（**唯一需要 GPU 的一步**），按页吐进度
      ↓
    todocx  出 Word：公式优先走 Office 的 XSL，没有才用内置 Pandoc

**编排层不做判断**，只负责串和汇总。每一步该报什么、降级到哪，
都由那一步自己决定 —— 这样每步都能单独测，编排层也能整体 mock 掉三步来测。

**不抛异常**：一份书失败不能带倒整批（用户常常一次拖进来一整个文件夹）。
"""
import os

import extract
import torchdep
import probe
import todocx


def pdf_to_word(pdf, out_docx, work_dir, on_progress=None, on_log=None,
                prefer_xsl=True, mineru=None, env=None, stop_flag=None, **kw):
    r"""转一份。返回汇总报告，**不抛异常**。

    on_progress(阶段中文名, 当前, 总数) —— 提取那步的真进度，
        阶段名来自 MinerU 自己的 tqdm（定位文字 / 识别公式 / 处理页面…）。
        探测和出 Word 两步是瞬时的，不发假进度。
    """
    rep = {'ok': False, 'error': '', 'cancelled': False,
           'pdf': pdf, 'docx': out_docx,
           'pages': 0, 'scan_pages': [], 'formulas': 0, 'formulas_xsl': 0,
           'tables': 0, 'images': 0, 'math_engine': '', 'math_note': '',
           'auto_dir': ''}

    # ── ① 体检 ────────────────────────────────────────────────────────
    p = probe.probe_pdf(pdf)
    if not p['ok']:
        rep['error'] = p['error']
        return rep
    rep['pages'] = p['pages']
    rep['scan_pages'] = p['scan_pages']

    # ── ② 提取（GPU）──────────────────────────────────────────────────
    def _prog(stage, cur, tot):
        if on_progress:
            on_progress(stage, cur, tot)

    e = extract.run(pdf, work_dir, mineru=mineru, env=env,
                    on_progress=_prog, on_log=on_log,
                    stop_flag=stop_flag, **kw)
    if e.get('cancelled'):
        rep['error'] = '已停止'
        rep['cancelled'] = True
        return rep
    if not e['ok']:
        # 🔴 MinerU 崩在 `import torch` 的话，用户看到的是
        #    「[WinError 1114] 动态链接库(DLL)初始化例程失败」——
        #    既没说缺什么，也没说该干什么。翻成人话，并且**指出具体
        #    该装哪个东西**。
        #
        #    为什么这里也要翻一遍（装 GPU 运行库那步已经验过一次）：
        #    环境是会变的 —— 从别的机器拷过来、杀软事后删了 dll、
        #    系统更新动了运行库。装的那一刻好好的，不代表用的时候还好。
        human = torchdep.explain_load_error(
            (e['error'] or '') + ' ' + (e.get('tail') or ''))
        rep['error'] = ('%s\n\n（原始报错：%s）' % (human, e['error'][:300])
                        if human else e['error'])
        return rep
    rep['auto_dir'] = e['auto_dir']

    # ── ③ 出 Word ─────────────────────────────────────────────────────
    d = todocx.md_to_docx(e['md'], out_docx, prefer_xsl=prefer_xsl,
                          resource_path=e['auto_dir'])
    if not d['ok']:
        rep['error'] = d['error']
        return rep

    rep['formulas'] = d['formulas_src']
    rep['formulas_xsl'] = d['formulas_replaced']
    rep['tables'] = d['tables']
    rep['images'] = d['images']
    rep['math_engine'] = d['math_engine']
    rep['math_note'] = d['math_note']
    rep['ok'] = True
    return rep


def summary_line(rep):
    """一行人话，给界面和命令行共用。"""
    if not rep['ok']:
        return '失败：%s' % rep['error'][:120]
    bits = ['%d 页' % rep['pages'],
            '公式 %d' % rep['formulas'],
            '表格 %d' % rep['tables'],
            '图 %d' % rep['images']]
    if rep['math_engine'] == 'xsl':
        bits.append('公式走 Office')
    else:
        bits.append('公式走 Pandoc')
    if rep['scan_pages']:
        n = len(rep['scan_pages'])
        bits.append('第 %s 页无文字层%s'
                    % (','.join(str(x) for x in rep['scan_pages'][:4]),
                       ' 等 %d 页' % n if n > 4 else ''))
    return ' ｜ '.join(bits)
