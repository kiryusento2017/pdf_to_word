# -*- coding: utf-8 -*-
r"""编排层：三步串起来之后，报告里该说的话有没有说出来。

这个文件是 2026-09-02 占位符改造时补的 —— 那次发现 `math_note`
（「第几个公式没转成」写在这里）**没有任何地方读**：summary_line 不读、
前端 0 处引用。信息写进了没人看的字段，等于没写。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import convert  # noqa: E402


def _rep(**kw):
    r = {'ok': True, 'error': '', 'pages': 10, 'formulas': 613,
         'formulas_xsl': 613, 'tables': 18, 'images': 132,
         'math_engine': 'xsl', 'math_note': '', 'scan_pages': []}
    r.update(kw)
    return r


class Test一行人话(unittest.TestCase):
    def test_全转成了就不提没转成的事(self):
        line = convert.summary_line(_rep())
        self.assertIn('公式 613', line)
        self.assertNotIn('没转成', line)

    def test_有公式没转成必须说出来(self):
        r"""占位符改造之后，少数公式转不成不再废掉整份 —— 那就更要说清楚
        这一份有几个没转成，否则用户会以为拿到的是完整的。"""
        line = convert.summary_line(_rep(formulas_xsl=612))
        self.assertIn('1 个公式没转成', line)

    def test_失败时给的是失败原因(self):
        line = convert.summary_line(_rep(ok=False, error='一个公式都没能转成'))
        self.assertIn('失败', line)
        self.assertIn('一个公式都没能转成', line)


class Test两轮识别分得开(unittest.TestCase):
    r"""转换里最耗时的两段（实测 13 分 17 秒 + 15 分 03 秒）在界面上都只
    显示兜底的「识别中」—— 换了识别后端之后，MinerU 打出来的就是光秃秃的
    `Predict:`，不再带细分名字。

    🔴 **靠分母分辨**：第一轮的总数等于页数（逐页过 VLM），第二轮是元素数
    （公式、文字块，跟页数无关）。页数在体检那步就填好了。
    """

    def setUp(self):
        self._probe = convert.probe.probe_pdf
        self._run = convert.extract.run

    def tearDown(self):
        convert.probe.probe_pdf = self._probe
        convert.extract.run = self._run

    def _go(self, rows, pages=56):
        convert.probe.probe_pdf = lambda p: {
            'ok': True, 'pages': pages, 'scan_pages': [], 'error': ''}
        seen = []

        def fake_run(pdf, work_dir, **kw):
            cb = kw.get('on_progress')
            for stage, cur, tot in rows:
                cb(stage, cur, tot)
                seen.append((stage, cur, tot))
            return {'ok': False, 'error': '到此为止（测试用）'}

        convert.extract.run = fake_run
        got = []
        rep = convert.pdf_to_word(
            'x.pdf', 'x.docx', 'work',
            on_progress=lambda s, c, t: got.append((s, c, t)))
        return rep, got

    def test_分母等于页数的那轮叫逐页识别(self):
        _rep, got = self._go([('识别中', 4, 56)])
        self.assertEqual(got[0][0], '逐页识别')

    def test_分母不等于页数的那轮叫识别公式和文字(self):
        _rep, got = self._go([('识别中', 45, 1100)])
        self.assertEqual(got[0][0], '识别公式和文字')

    def test_有名字的阶段原样传不许改(self):
        r"""只有兜底的「识别中」才需要靠分母猜，别的阶段 MinerU 给了名字，
        动它就是画蛇添足。"""
        _rep, got = self._go([('分析版面', 3, 8), ('识别公式', 2, 5)])
        self.assertEqual([g[0] for g in got], ['分析版面', '识别公式'])

    def test_元素数记下来了(self):
        rep, _got = self._go([('识别中', 4, 56), ('识别中', 45, 1100)])
        self.assertEqual(rep['elements'], 1100)

    def test_两轮耗时都记下来了(self):
        rep, _got = self._go([('识别中', 4, 56), ('识别中', 45, 1100)])
        self.assertIsInstance(rep['pass1_sec'], int)
        self.assertIsInstance(rep['pass2_sec'], int)
        self.assertGreaterEqual(rep['pass1_sec'], 0)

    def test_缓存命中时三个数都是零(self):
        r"""🔴 秒回是因为没跑 GPU，不是 GPU 快。这种份数**不能**拿去学速度，
        混进去会把速度学成离谱的快。"""
        rep, _got = self._go([])
        self.assertEqual((rep['pass1_sec'], rep['pass2_sec'], rep['elements']),
                         (0, 0, 0))

    def test_只有第一轮没第二轮时不乱记(self):
        r"""第一轮跑到一半被停掉 —— 没有第二轮，就不该编出耗时来。"""
        rep, _got = self._go([('识别中', 4, 56)])
        self.assertEqual((rep['pass1_sec'], rep['pass2_sec'], rep['elements']),
                         (0, 0, 0))

    def test_页数为零时一轮都不认(self):
        r"""🔴 分母判据全靠页数当参照，页数是 0 就分辨不了 —— 此时第一轮
        （分母 = 页数 ≠ 0）会掉进 else 被当成第二轮，pass1_sec≈0、
        pass2_sec≈全程、elements=页数 三个错值一起写进 runs.json。
        那是「越用越准」的样本池，脏数据会跟着最近 60 条窗口一直影响
        后面每一次估算，**不是只错这一次**。

        ⚠️ 这不是假想的输入：2026-09-07 实测手工造一个 `/Count 0` 的 PDF，
        `probe.probe_pdf` 返回 ok=True、pages=0（pymupdf 自己不肯保存 0 页
        文档，但它打得开别人造的）。认不出来就保持兜底的「识别中」，
        这一份不学速度 —— 少学一份远好过学一份错的。
        """
        rep, got = self._go([('识别中', 4, 56), ('识别中', 9, 1100)], pages=0)
        self.assertEqual([g[0] for g in got], ['识别中', '识别中'],
                         '页数是 0 还硬认出了轮次：%s' % [g[0] for g in got])
        self.assertEqual((rep['pass1_sec'], rep['pass2_sec'], rep['elements']),
                         (0, 0, 0), '页数是 0 却学到了速度')
