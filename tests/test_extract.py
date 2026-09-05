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
import tempfile
import time
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

        def fake(argv, on_line, env=None, stop_flag=None):
            self.seen_env = env
            for ln in lines:
                on_line(ln)
            if make_output:
                # 真 MinerU 按 argv 里的 -o 写产物，fake 也得这样。
                # 写死 self.out 的话，产物按指纹分桶之后就对不上了。
                o = argv[argv.index('-o') + 1]
                d = os.path.join(o, '某讲义', 'hybrid_ocr')
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
        # 第一条是「加载模型」—— MinerU 起来到第一条 tqdm 之间有几十秒
        # 一个字都不吐，不说一声的话界面三样（阶段名/进度条/日志）一起死。
        self.assertEqual(seen[0], ('正在加载识别模型', 0, 0))
        self.assertEqual(seen[1:], [('处理页面', 0, 3), ('处理页面', 1, 3),
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


class Test不抛异常是硬契约(unittest.TestCase):
    r"""run() 的 docstring 写着「不抛异常」，那就得真的做到。

    2026-09-05 全量审查发现它原来是漏的：`fingerprint()` 里的
    `open(pdf,'rb')`、`os.makedirs`、`os.listdir` 全裸着。PDF 在
    isfile 检查之后被移走（U 盘拔了）、磁盘满、输出目录只读，异常
    都会穿出去，最后被 server._work 的总兜底接住 —— 那是**整批中止**，
    而 convert.py 的设计意图写得很清楚：「一份书失败不能带倒整批」。
    """

    def setUp(self):
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)
        self.pdf = os.path.join(WORK, '某讲义.pdf')
        io.open(self.pdf, 'w', encoding='utf-8').write('x')
        self.out = os.path.join(WORK, 'out')
        self.orig_fp = extract.fingerprint

    def tearDown(self):
        extract.fingerprint = self.orig_fp
        shutil.rmtree(WORK, ignore_errors=True)

    def _boom(self, exc):
        def fake(*a, **k):
            raise exc
        extract.fingerprint = fake

    def test_算指纹时文件没了_返回失败而不是抛出去(self):
        self._boom(OSError(2, '文件不见了'))
        rep = extract.run(self.pdf, self.out, mineru='mineru.exe')
        self.assertFalse(rep['ok'])
        self.assertIn('出错', rep['error'])

    def test_兜底返回的报告结构要完整(self):
        r"""convert.pdf_to_word 会直接读 e['ok'] / e['error'] /
        e.get('tail')，兜底那份少一个字段就换个地方炸。"""
        self._boom(RuntimeError('随便什么错'))
        rep = extract.run(self.pdf, self.out, mineru='mineru.exe')
        for k in ('ok', 'error', 'auto_dir', 'md', 'pages', 'tail',
                  'stage', 'cancelled', 'cached'):
            self.assertIn(k, rep, '兜底报告缺字段 %s' % k)


class Test退出码不能不看(unittest.TestCase):
    r"""🔴 原来的判据是「找不找得到 .md」，退出码 rc 只在**没有产物**时
    才出现在错误信息里露个脸。

    后果：MinerU 处理 10 页，第 7 页崩了（OOM / CUDA 错），前 6 页已经
    写进 .md —— 找得到产物 → 判成功 → 老师拿到一份**只有前 6 页**的 Word，
    而软件说「转好了」。少掉的四页没有任何地方会发现。

    转换这件事，「少了几页」比「失败」严重得多：失败会重来，
    残缺会被当成成品直接发给学生。
    """

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix='p2w_rc_')
        self.addCleanup(shutil.rmtree, self.work, True)
        self.pdf = os.path.join(self.work, 'x.pdf')
        with io.open(self.pdf, 'wb') as f:
            f.write(b'%PDF-1.4 fake')
        self.out = os.path.join(self.work, 'out')
        # 造一份「产物在，但进程是崩掉的」的现场。
        # 要造进指纹桶里 —— run() 现在按桶找产物。桶里故意不写
        # .fingerprint.json，所以不会被当成缓存命中，正是这组要测的场景。
        auto = os.path.join(self.out, extract.fingerprint(self.pdf),
                            'x', 'hybrid_ocr')
        os.makedirs(auto)
        with io.open(os.path.join(auto, 'x.md'), 'w', encoding='utf-8') as f:
            f.write('# 只转了前 6 页就崩了\n')

    def _spawn_rc(self, rc, lines=()):
        orig = extract._spawn

        def fake(argv, on_line, env=None, stop_flag=None):
            for ln in lines:
                on_line(ln)
            return rc
        extract._spawn = fake
        self.addCleanup(lambda: setattr(extract, '_spawn', orig))

    def test_有产物但退出码非零要判失败(self):
        self._spawn_rc(1, ['torch.cuda.OutOfMemoryError: CUDA out of memory'])
        rep = extract.run(self.pdf, self.out, mineru=['fake'])
        self.assertFalse(rep['ok'],
                         '进程崩了却判成功 —— 用户会拿到一份残缺的 Word')
        self.assertIn('1', rep['error'], '没说退出码是多少')

    def test_失败信息里要带上最后几行输出(self):
        self._spawn_rc(1, ['torch.cuda.OutOfMemoryError: CUDA out of memory'])
        rep = extract.run(self.pdf, self.out, mineru=['fake'])
        self.assertIn('OutOfMemory', rep['error'] + rep['tail'],
                      '没把原因带出来，只说「失败」谁也查不了')

    def test_退出码为零且有产物才算成功(self):
        self._spawn_rc(0, ['处理页面: 100%|##| 6/6'])
        rep = extract.run(self.pdf, self.out, mineru=['fake'])
        self.assertTrue(rep['ok'], rep.get('error'))


class Test停止要当场生效(unittest.TestCase):
    r"""🔴 小蔡 2026-09-02 真机：「点击停止还没用，程序一共有几个停止，
    都有用吗？」

    当时转换的取消**只在两份 PDF 之间检查**：

        for i, pdf in enumerate(pdf_paths):
            if t['cancel']: return        ← 只有这一个检查点
            convert.pdf_to_word(...)      ← 这一步几分钟，期间够不着

    只转一份的话循环没有下一轮，那个检查点永远走不到 —— 用户点了完全
    没反应，只能干等或者强杀软件。

    当时不硬杀的理由（写在路由的 docstring 里）：「中途硬杀会留下半截
    产物」。那个理由现在不成立了：退出码非零一律判失败、失败会把 pandoc
    写出的 Word 删掉、中间产物本来就在 _tmp/ 里每次重来。

    ⚠️ 这是同一个错误模式在本项目的**第三处**（models.download、
       torchdep.install 是前两次）。三处的共同点：检查写在一个会阻塞
       的读取循环里，而「半天没动静」恰恰是用户最想停的时候。
       解法也统一：独立的 watch 线程 + taskkill /T。
    """

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix='p2w_stop_')
        self.addCleanup(shutil.rmtree, self.work, True)
        self.pdf = os.path.join(self.work, 'x.pdf')
        with io.open(self.pdf, 'wb') as f:
            f.write(b'%PDF-1.4')
        self.out = os.path.join(self.work, 'out')

    def test_一份都还没转完就能停下来(self):
        r"""关键点：**一份 PDF、没有「下一轮循环」**。
        旧实现在这种情况下 100% 停不下来。"""
        import subprocess
        import threading
        import time

        class _Stuck(object):
            """一个装死的 MinerU：不吐输出、也不退出。"""

            def __init__(self):
                self.pid = 0x7FFFFFFE
                self.returncode = None
                self._dead = threading.Event()
                self.stdout = self

            def read(self, n):
                self._dead.wait(10)      # 真卡死时测试也不该挂在这
                return b''

            def close(self):
                pass

            def terminate(self):
                self.returncode = 1
                self._dead.set()

            def wait(self):
                self._dead.wait(10)
                return self.returncode or 1

        orig = subprocess.Popen
        subprocess.Popen = lambda *a, **k: _Stuck()
        self.addCleanup(lambda: setattr(subprocess, 'Popen', orig))

        t0 = time.time()
        rep = extract.run(self.pdf, self.out, mineru=['fake'],
                          stop_flag=lambda: True)
        used = time.time() - t0

        self.assertTrue(rep.get('cancelled'), '停了却没标成「已取消」')
        self.assertIn('已停止', rep['error'])
        self.assertLess(used, 8,
                        '点了停止还等了 %.1f 秒 —— 检查又被阻塞的读取挡住了'
                        % used)

    def test_没点停止就不许自己停(self):
        import subprocess

        class _Quick(object):
            def __init__(self):
                self.pid = 1
                self.returncode = 0
                self.stdout = io.BytesIO(b'Processing pages: 100%|##| 1/1\n')

            def wait(self):
                return 0

            def terminate(self):
                pass

        orig = subprocess.Popen
        subprocess.Popen = lambda *a, **k: _Quick()
        self.addCleanup(lambda: setattr(subprocess, 'Popen', orig))

        d = os.path.join(self.out, extract.fingerprint(self.pdf),
                         'x', 'hybrid_ocr')
        os.makedirs(d)
        with io.open(os.path.join(d, 'x.md'), 'w', encoding='utf-8') as f:
            f.write('# x')
        rep = extract.run(self.pdf, self.out, mineru=['fake'],
                          stop_flag=lambda: False)
        self.assertFalse(rep.get('cancelled'))
        self.assertTrue(rep['ok'], rep.get('error'))


class Test缓存复用(unittest.TestCase):
    r"""同一份 PDF 同一组参数转第二次，不该再等四分钟。

    判据是**指纹**（PDF 内容 + 四个参数 + MinerU 版本），不是文件名 ——
    按文件名会让两份不同内容的「讲义.pdf」互相覆盖，那种错最难查。
    """

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix='p2w_cache_')
        self.addCleanup(shutil.rmtree, self.work, True)
        self.pdf = os.path.join(self.work, '某讲义.pdf')
        with io.open(self.pdf, 'wb') as f:
            f.write(b'%PDF-1.4 content-A')
        self.out = os.path.join(self.work, 'out')
        self.calls = []
        orig = extract._spawn
        self.addCleanup(lambda: setattr(extract, '_spawn', orig))

        def fake(argv, on_line, env=None, stop_flag=None):
            self.calls.append(argv)
            o = argv[argv.index('-o') + 1]
            d = os.path.join(o, '某讲义', 'hybrid_ocr')
            os.makedirs(d, exist_ok=True)
            with io.open(os.path.join(d, '某讲义.md'), 'w',
                         encoding='utf-8') as f:
                f.write('# x')
            return 0
        extract._spawn = fake

    def test_第二次直接用缓存不再跑mineru(self):
        r1 = extract.run(self.pdf, self.out, mineru='m.exe')
        self.assertTrue(r1['ok'], r1.get('error'))
        self.assertFalse(r1['cached'])
        r2 = extract.run(self.pdf, self.out, mineru='m.exe')
        self.assertTrue(r2['ok'], r2.get('error'))
        self.assertTrue(r2['cached'])
        self.assertEqual(len(self.calls), 1, '第二次不该再跑 MinerU')
        self.assertEqual(r1['md'], r2['md'])

    def test_PDF内容变了就不算同一份(self):
        extract.run(self.pdf, self.out, mineru='m.exe')
        with io.open(self.pdf, 'wb') as f:
            f.write(b'%PDF-1.4 content-B')      # 名字没变，内容变了
        r = extract.run(self.pdf, self.out, mineru='m.exe')
        self.assertFalse(r['cached'], '改了内容还敢用旧产物')
        self.assertEqual(len(self.calls), 2)

    def test_提取参数变了就不算同一份(self):
        extract.run(self.pdf, self.out, mineru='m.exe')
        r = extract.run(self.pdf, self.out, mineru='m.exe', effort='medium')
        self.assertFalse(r['cached'], 'effort 变了还用旧产物')
        self.assertEqual(len(self.calls), 2)

    def test_mineru升级了就不算同一份(self):
        extract.run(self.pdf, self.out, mineru='m.exe')
        orig = extract.mineru_version
        extract.mineru_version = lambda: '9.9.9'
        self.addCleanup(lambda: setattr(extract, 'mineru_version', orig))
        r = extract.run(self.pdf, self.out, mineru='m.exe')
        self.assertFalse(r['cached'], '换了模型版本还用旧识别结果')
        self.assertEqual(len(self.calls), 2)

    def test_没有指纹文件的桶不算缓存(self):
        r1 = extract.run(self.pdf, self.out, mineru='m.exe')
        fp = extract.fingerprint(self.pdf)
        os.remove(os.path.join(self.out, fp, extract.FP_NAME))
        r2 = extract.run(self.pdf, self.out, mineru='m.exe')
        self.assertFalse(r2['cached'], '没认领过的桶不能当缓存')
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(r2['ok'], r2.get('error'))
        self.assertEqual(r1['md'], r2['md'])

    def test_改了文件名换了目录照样命中(self):
        r"""指纹按**内容**算，文件名不参与 —— 用户把 PDF 改个名、
        挪个位置，内容没变，不该让他重等四分钟。

        初版这里用 find_output(桶, 当前文件名) 判命中：指纹明明对上了，
        却因为桶里的子目录还叫旧名字而判成不命中。2026-09-02 小蔡改了个
        文件名就撞上了。
        """
        extract.run(self.pdf, self.out, mineru='m.exe')
        other = os.path.join(self.work, '换个名字.pdf')
        shutil.copy2(self.pdf, other)
        r = extract.run(other, self.out, mineru='m.exe')
        self.assertTrue(r['cached'], '改个文件名就重跑了')
        self.assertEqual(len(self.calls), 1, '不该再起一次 MinerU')

    def test_中途失败的桶不会被当成缓存(self):
        # 跑一半崩了：产物在，但没写指纹（_fp_write 在最后一步）
        fp = extract.fingerprint(self.pdf)
        d = os.path.join(self.out, fp, '某讲义', 'hybrid_ocr')
        os.makedirs(d)
        with io.open(os.path.join(d, '某讲义.md'), 'w', encoding='utf-8') as f:
            f.write('# 只转了一半')
        r = extract.run(self.pdf, self.out, mineru='m.exe')
        self.assertFalse(r['cached'])
        self.assertEqual(len(self.calls), 1, '该重跑')


class Test清理过期缓存(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='p2w_purge_')
        self.addCleanup(shutil.rmtree, self.root, True)

    def _bucket(self, name, days_ago):
        d = os.path.join(self.root, name)
        os.makedirs(d)
        t = time.time() - days_ago * 86400
        os.utime(d, (t, t))
        return d

    def test_超过十天的清掉(self):
        old = self._bucket('old', 11)
        self.assertEqual(extract.purge_old(self.root), 1)
        self.assertFalse(os.path.isdir(old))

    def test_没超期的留着(self):
        new = self._bucket('new', 3)
        self.assertEqual(extract.purge_old(self.root), 0)
        self.assertTrue(os.path.isdir(new))

    def test_目录不存在也不报错(self):
        self.assertEqual(extract.purge_old(os.path.join(self.root, '没有')), 0)


if __name__ == '__main__':
    unittest.main()
