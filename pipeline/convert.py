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
import time

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
           'auto_dir': '', 'cached': False, 'degraded': '',
           # 两轮识别各花了多久、认出多少个元素 —— 给倒计时学速度用。
           # 缓存命中时这三个都是 0（没真跑 GPU，不能拿来学）。
           'pass1_sec': 0, 'pass2_sec': 0, 'elements': 0,
           # 这一份走过哪几步，按出现顺序。给界面展开看用。
           'stages': [], 'details_dropped': 0}

    # ── ① 体检 ────────────────────────────────────────────────────────
    p = probe.probe_pdf(pdf)
    if not p['ok']:
        rep['error'] = p['error']
        return rep
    rep['pages'] = p['pages']
    rep['scan_pages'] = p['scan_pages']

    # ── ② 提取（GPU）──────────────────────────────────────────────────
    #
    # 🔴 **两轮 Predict 靠分母分辨，不靠名字。** 换了识别后端之后 MinerU
    #    打出来的就是光秃秃的 `Predict:`，两轮都翻译成兜底的「识别中」——
    #    而那两轮恰恰是最耗时的（实测 56 页那份：13 分 17 秒 + 15 分 03 秒，
    #    占掉几乎全部时间），有名字的那几个阶段加起来才几秒。
    #
    #    分母不一样：第一轮的总数 == 页数（逐页过一遍 VLM），第二轮是
    #    元素数（公式、文字块，跟页数无关，那次是 1100）。页数在体检那步
    #    0.07 秒就填好了，这个闭包直接读得到，不用绕路。
    _t0 = time.time()
    _sw = {'pass2_at': None, 'elements': 0, 'stages': []}

    def _prog(stage, cur, tot):
        if stage == '识别中' and tot:
            if tot == rep['pages']:
                stage = '逐页识别'
            else:
                stage = '识别公式和文字'
                if _sw['pass2_at'] is None:
                    _sw['pass2_at'] = time.time()
                _sw['elements'] = tot
        if stage and (not _sw['stages'] or _sw['stages'][-1] != stage):
            _sw['stages'].append(stage)
        if on_progress:
            on_progress(stage, cur, tot)

    e = extract.run(pdf, work_dir, mineru=mineru, env=env,
                    on_progress=_prog, on_log=on_log,
                    stop_flag=stop_flag, **kw)
    # 两轮各花了多久 —— 第一轮从提取开始算到第二轮开头（中间那几个
    # 小阶段各几秒，并进第一轮不影响学出来的速度），第二轮到提取结束。
    if _sw['pass2_at']:
        rep['pass1_sec'] = int(_sw['pass2_at'] - _t0)
        rep['pass2_sec'] = int(time.time() - _sw['pass2_at'])
        rep['elements'] = _sw['elements']
    rep['stages'] = list(_sw['stages'])
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
    # 这一份是直接用了上次的识别结果，还是真跑了一遍 GPU。
    # 界面要标出来，剩余时间的速率也要把它排除掉。
    rep['cached'] = e.get('cached', False)

    # ── ③ 出 Word ─────────────────────────────────────────────────────
    d = todocx.md_to_docx(e['md'], out_docx, prefer_xsl=prefer_xsl,
                          resource_path=e['auto_dir'])
    if not d['ok']:
        rep['error'] = d['error']
        # 判失败不等于没有产物。次品改了名留在原地，路径带给界面 ——
        # 转一份四分钟，不该因为一个公式没转成就让人两手空空。
        rep['degraded'] = d.get('degraded', '')
        return rep

    rep['formulas'] = d['formulas_src']
    rep['formulas_xsl'] = d['formulas_replaced']
    rep['tables'] = d['tables']
    rep['images'] = d['images']
    rep['math_engine'] = d['math_engine']
    rep['math_note'] = d['math_note']
    # 图里的文字被拦下了几处 —— 转换报告要照实说一句，不能悄悄删。
    rep['details_dropped'] = d.get('details_dropped', 0)
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
    # 有公式没转成就说出来。以前这个事实只写进 math_note，而
    # summary_line 不读它、前端 0 处引用 —— 等于写进了没人看的字段。
    miss = (rep.get('formulas') or 0) - (rep.get('formulas_xsl') or 0)
    if miss > 0:
        bits.append('%d 个公式没转成' % miss)
    if rep['scan_pages']:
        n = len(rep['scan_pages'])
        bits.append('第 %s 页无文字层%s'
                    % (','.join(str(x) for x in rep['scan_pages'][:4]),
                       ' 等 %d 页' % n if n > 4 else ''))
    return ' ｜ '.join(bits)
