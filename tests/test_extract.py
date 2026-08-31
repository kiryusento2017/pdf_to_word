# -*- coding: utf-8 -*-
r"""调 MinerU 提取。

**为什么能离线测**：真跑 MinerU 要 GPU 和几分钟，但「命令拼得对不对」
「进度解析得对不对」「产物找不找得到」都是纯逻辑 —— 拦住子进程就能验。
这是工作台那边留下的做法（`test_orchestrator_extract.py`），照抄。

进度靠解析 MinerU 的 tqdm 输出。它源码里是
`tqdm(total=page_count, desc="Processing pages")`（hybrid_analyze.py:1044），
tqdm 默认写 stderr，形如：
    Processing pages:  30%|███       | 3/10 [00:15<00:35,  5.0s/it]
"""
import io
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import extract  # noqa: E402

WORK = os.path.join(ROOT, '_tmp', 'tests', 'extract')


class Test解析进度(unittest.TestCase):
    r"""tqdm 的输出格式是这个模块唯一的进度来源，格式变了界面就瞎了。"""

    def test_解析页码进度(self):
        line = 'Processing pages:  30%|███       | 3/10 [00:15<00:35,  5.0s/it]'
        self.assertEqual(extract.parse_progress(line), ('处理页面', 3, 10))

    def test_刚开始时是0(self):
        line = 'Processing pages:   0%|          | 0/12 [00:00<?, ?it/s]'
        self.assertEqual(extract.parse_progress(line), ('处理页面', 0, 12))

    def test_跑完是满的(self):
        line = 'Processing pages: 100%|██████████| 10/10 [02:31<00:00, 15.1s/it]'
        self.assertEqual(extract.parse_progress(line), ('处理页面', 10, 10))

    def test_别的阶段也要认(self):
        r"""只认「处理页面」的话，实测 237 秒里有 224 秒屏幕一动不动 ——
        那一阶段是最后才跑的。用户会以为死机然后强杀。"""
        self.assertEqual(
            extract.parse_progress('OCR-det:  50%|█████ | 5/10 [00:03<00:03,  1.5it/s]'),
            ('定位文字', 5, 10))
        self.assertEqual(
            extract.parse_progress('MFR Predict: 80%|████ | 8/10 [00:03<00:01,  1.5it/s]'),
            ('识别公式', 8, 10))

    def test_阶段名不许被截断(self):
        r"""正则非贪婪时把 `MFR Predict` 截成了光秃秃的 `Predict`，
        界面上显示「识别中」，人不知道在干嘛。实测端到端时发现的。"""
        self.assertEqual(
            extract.parse_progress('MFR Predict: 50%|## | 5/10 [00:03<00:03]')[0],
            '识别公式')
        self.assertEqual(
            extract.parse_progress('Layout Predict: 10%|# | 1/10 [00:01<00:09]')[0],
            '分析版面')

    def test_没见过的阶段名原样带出来(self):
        r"""MinerU 换版本可能加新阶段。认不出就原样显示，别吞掉 ——
        显示个英文名也比进度条卡住强。"""
        got = extract.parse_progress('Brand New Stage:  10%|█ | 1/10 [00:01<00:09]')
        self.assertEqual(got, ('Brand New Stage', 1, 10))

    def test_普通日志行不误判(self):
        for line in ('', 'INFO loading model', '10/10 是个普通数字'):
            self.assertIsNone(extract.parse_progress(line))


class Test拼命令(unittest.TestCase):

    def setUp(self):
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)
        self.pdf = os.path.join(WORK, 'x.pdf')
        io.open(self.pdf, 'w', encoding='utf-8').write('假 PDF')

    def tearDown(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def test_四个参数一个不少(self):
        r"""漏 --effort 不会报错，只会让 MinerU 用默认的 medium 悄悄降级 ——
        而 medium 会关掉图片分析。这种错最难发现。"""
        argv = extract.build_argv('mineru.exe', self.pdf, WORK)
        for flag in ('-p', '-o', '-b', '-m', '--effort', '-l'):
            self.assertIn(flag, argv, '缺 %s' % flag)

    def test_默认走ocr(self):
        r"""2026-08-31 实测定的：txt/auto 丢 38% 的公式（131 vs 213）。
        依据见 docs/DESIGN.md 第二节。"""
        argv = extract.build_argv('mineru.exe', self.pdf, WORK)
        self.assertEqual(argv[argv.index('-m') + 1], 'ocr')

    def test_effort是high(self):
        argv = extract.build_argv('mineru.exe', self.pdf, WORK)
        self.assertEqual(argv[argv.index('--effort') + 1], 'high')

    def test_参数可以覆盖(self):
        argv = extract.build_argv('mineru.exe', self.pdf, WORK, method='txt')
        self.assertEqual(argv[argv.index('-m') + 1], 'txt')


class Test找产物(unittest.TestCase):

    def setUp(self):
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)

    def tearDown(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def _make(self, stem, sub, with_md=True):
        d = os.path.join(WORK, stem, sub)
        os.makedirs(d, exist_ok=True)
        if with_md:
            io.open(os.path.join(d, stem + '.md'), 'w', encoding='utf-8').write('x')
            io.open(os.path.join(d, stem + '_content_list.json'),
                    'w', encoding='utf-8').write('[]')
        return d

    def test_子目录名不写死(self):
        r"""子目录名由 backend+method 拼出来（hybrid-engine + ocr → hybrid_ocr，
        office backend → office）。工作台那边我写死过 'office'，
        结果 10 份全判成「产物没了」。"""
        d = self._make('某讲义', 'hybrid_ocr')
        got = extract.find_output(WORK, '某讲义')
        self.assertEqual(os.path.normpath(got), os.path.normpath(d))

    def test_换个子目录名照样找得到(self):
        d = self._make('某讲义', 'office')
        got = extract.find_output(WORK, '某讲义')
        self.assertEqual(os.path.normpath(got), os.path.normpath(d))

    def test_没有md的目录不算产物(self):
        self._make('某讲义', 'hybrid_ocr', with_md=False)
        self.assertIsNone(extract.find_output(WORK, '某讲义'))

    def test_找不到返回None(self):
        self.assertIsNone(extract.find_output(WORK, '不存在的书'))


class Test跑一次(unittest.TestCase):
    r"""拦住子进程，只验编排逻辑，不碰 GPU。"""

    def setUp(self):
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)
        self.pdf = os.path.join(WORK, '某讲义.pdf')
        io.open(self.pdf, 'w', encoding='utf-8').write('x')
        self.out = os.path.join(WORK, 'out')
        self.orig = extract._spawn

    def tearDown(self):
        extract._spawn = self.orig
        shutil.rmtree(WORK, ignore_errors=True)

    def _fake(self, lines, make_output=True, rc=0):
        self.seen_env = None

        def fake(argv, on_line, env=None):
            self.seen_env = env
            for ln in lines:
                on_line(ln)
            if make_output:
                d = os.path.join(self.out, '某讲义', 'hybrid_ocr')
                os.makedirs(d, exist_ok=True)
                io.open(os.path.join(d, '某讲义.md'), 'w',
                        encoding='utf-8').write('# x')
                io.open(os.path.join(d, '某讲义_content_list.json'), 'w',
                        encoding='utf-8').write('[]')
            return rc
        extract._spawn = fake

    def test_进度一路吐出来(self):
        self._fake(['Processing pages:   0%|  | 0/3 [00:00<?, ?it/s]',
                    'Processing pages:  33%|█ | 1/3 [00:05<00:10,  5.0s/it]',
                    'Processing pages: 100%|██| 3/3 [00:15<00:00,  5.0s/it]'])
        seen = []
        r = extract.run(self.pdf, self.out, mineru='mineru.exe',
                        on_progress=lambda s, c, t: seen.append((s, c, t)))
        self.assertTrue(r['ok'], r.get('error'))
        self.assertEqual(seen, [('处理页面', 0, 3), ('处理页面', 1, 3),
                                ('处理页面', 3, 3)])

    def test_成功时给出产物目录(self):
        self._fake(['Processing pages: 100%|██| 1/1 [00:01<00:00,  1.0s/it]'])
        r = extract.run(self.pdf, self.out, mineru='mineru.exe')
        self.assertTrue(r['ok'])
        self.assertTrue(os.path.isdir(r['auto_dir']))
        self.assertTrue(r['md'].endswith('.md'))

    def test_跑完没产物要红灯(self):
        r"""最容易踩的：改了 backend/method，MinerU 跑得好好的，
        产物却落在另一个目录名下。"""
        self._fake([], make_output=False)
        r = extract.run(self.pdf, self.out, mineru='mineru.exe')
        self.assertFalse(r['ok'])
        self.assertIn('产物', r['error'])

    def test_子进程失败要把原因带出来(self):
        self._fake(['CUDA out of memory'], make_output=False, rc=1)
        r = extract.run(self.pdf, self.out, mineru='mineru.exe')
        self.assertFalse(r['ok'])
        self.assertIn('CUDA', r['error'] + r.get('tail', ''))

    def test_模型源的环境变量真的传给了子进程(self):
        r"""用户在首启那屏选的下载源，只记在前端等于让人做了个没用的
        选择题，比不给选更糟。这条钉着「选了就真的生效」。"""
        self._fake(['Processing pages: 100%|##| 1/1 [00:01<00:00]'])
        extract.run(self.pdf, self.out, mineru='mineru.exe',
                    env={'MINERU_MODEL_SOURCE': 'modelscope'})
        self.assertEqual(self.seen_env, {'MINERU_MODEL_SOURCE': 'modelscope'})

    def test_不传env时保持原样(self):
        self._fake(['Processing pages: 100%|##| 1/1 [00:01<00:00]'])
        extract.run(self.pdf, self.out, mineru='mineru.exe')
        self.assertIsNone(self.seen_env)

    def test_找不到mineru时说人话(self):
        r = extract.run(self.pdf, self.out, mineru=os.path.join(WORK, '没有.exe'))
        self.assertFalse(r['ok'])
        self.assertIn('找不到', r['error'])


if __name__ == '__main__':
    unittest.main()
