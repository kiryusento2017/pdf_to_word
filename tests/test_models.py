# -*- coding: utf-8 -*-
r"""模型的认领与配置。

要盯住的两件事：
  1. **绝不碰用户主目录里的全局 mineru.json** —— 用户机器上可能装着
     别的用 MinerU 的东西，改它等于动别人的家当。
  2. models-dir 要指到 snapshots/master 那一层。指错一层 MinerU 不报错，
     只在推理时说找不到权重，那种错误很难查到根因上。
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import models  # noqa: E402
import paths   # noqa: E402


def _fake_repo(root, name, weight_mb=2):
    r"""造一个假的模型仓库，结构照着 MinerU 真实的来：
        <root>/models/<name>/snapshots/master/<大文件>
    """
    d = os.path.join(root, 'models', name, 'snapshots', 'master')
    os.makedirs(d)
    with io.open(os.path.join(d, 'weights.bin'), 'wb') as f:
        f.write(b'\0' * (weight_mb * 1024 * 1024))
    return d


class Test认已有的模型(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_认出两个仓库并指到snapshots那一层(self):
        want_p = _fake_repo(self.tmp, 'OpenDataLab--PDF-Extract-Kit-1.0')
        want_v = _fake_repo(self.tmp, 'OpenDataLab--MinerU2.5-Pro-2605-1.2B')
        got = models.detect(self.tmp)
        self.assertEqual(got['pipeline'], want_p)
        self.assertEqual(got['vlm'], want_v)
        # 指到 snapshots/master，不是仓库根 —— 差一层 MinerU 就找不到权重
        self.assertTrue(got['pipeline'].endswith(os.path.join('snapshots', 'master')))

    def test_版本号变了也认得出来(self):
        r"""目录名里带版本（MinerU2.5-Pro-2605-1.2B），MinerU 升级就会变。
        写死整个目录名的话，用户换个版本就认不出来了。"""
        _fake_repo(self.tmp, 'OpenDataLab--PDF-Extract-Kit-9.9')
        got = models.detect(self.tmp)
        self.assertTrue(got['pipeline'], '换了版本号就认不出来了')

    def test_空壳目录不算数(self):
        r"""下载中断会留下有结构但没权重的目录。认成「有模型」的话，
        用户会卡在一个永远缺文件的转换里。"""
        d = os.path.join(self.tmp, 'models',
                         'OpenDataLab--PDF-Extract-Kit-1.0', 'snapshots', 'master')
        os.makedirs(d)
        with io.open(os.path.join(d, 'config.json'), 'w') as f:
            f.write('{}')          # 小文件，不是权重
        got = models.detect(self.tmp)
        self.assertFalse(got['pipeline'], '空壳目录被当成了有模型')

    def test_不存在的目录不抛异常(self):
        got = models.detect(os.path.join(self.tmp, '没这个'))
        self.assertEqual(got, {'pipeline': '', 'vlm': ''})

    def test_无关目录返回空(self):
        os.makedirs(os.path.join(self.tmp, '照片', '2026'))
        got = models.detect(self.tmp)
        self.assertEqual(got['pipeline'], '')


class Test写配置(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = paths.CONFIG
        paths.CONFIG = os.path.join(self.tmp, 'mineru.json')

    def tearDown(self):
        paths.CONFIG = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_写出来的结构是MinerU认的(self):
        models.write_config('D:/a/pipeline', 'D:/a/vlm', 'modelscope')
        with io.open(paths.CONFIG, encoding='utf-8') as f:
            cfg = json.load(f)
        self.assertEqual(cfg['models-dir']['pipeline'], 'D:/a/pipeline')
        self.assertEqual(cfg['models-dir']['vlm'], 'D:/a/vlm')
        self.assertEqual(cfg['model-source'], 'modelscope')
        self.assertIn('config_version', cfg)

    def test_保留已有的其他键(self):
        r"""MinerU 升级后配置里可能多出新字段，整个覆盖会把它们抹掉。"""
        with io.open(paths.CONFIG, 'w', encoding='utf-8') as f:
            json.dump({'latex-delimiter-config': {'inline': {'left': '$'}},
                       'models-dir': {}}, f)
        models.write_config('D:/a/pipeline')
        with io.open(paths.CONFIG, encoding='utf-8') as f:
            cfg = json.load(f)
        self.assertIn('latex-delimiter-config', cfg, '把别的配置项抹掉了')

    def test_只更新传进来的那一项(self):
        models.write_config('D:/a/pipeline', 'D:/a/vlm')
        models.write_config(pipeline_dir='D:/b/pipeline')      # 只改 pipeline
        p, v = models.configured_dirs()
        self.assertEqual(p, 'D:/b/pipeline')
        self.assertEqual(v, 'D:/a/vlm', 'vlm 被清掉了')

    def test_坏的配置文件不抛异常(self):
        with io.open(paths.CONFIG, 'w', encoding='utf-8') as f:
            f.write('这不是 json{{{')
        self.assertEqual(models.read_config(), {})
        models.write_config('D:/a/pipeline')                   # 应当能覆盖掉
        self.assertEqual(models.configured_dirs()[0], 'D:/a/pipeline')


class Test下载失败要留住原因(unittest.TestCase):
    r"""2026-09-02 网吧实测：测速正常、模型下载一直失败，而我们这边
    只拿到一句「下载器退出码 N，多半是网络断了」——**那句话是猜的**。

    下载器自己打印的真实原因（磁盘满 / SSL / 代理 / 权限……）当时被
    丢得一干二净：server 的 on_log 只把最后一行塞进一个随时被覆盖的
    变量，一个字节都没落盘。隔着几百公里，这等于没法查。

    所以钉两件事：日志必须落盘，失败信息里必须带上下载器说的话。
    """

    def setUp(self):
        # 🔴 用 addCleanup 而不是 tearDown：setUp 中途抛异常时 unittest
        #    **不会**调 tearDown，改到一半的全局状态就留在那儿污染后面的
        #    用例。这次就是这么翻车的 —— 这里一句 AttributeError，
        #    连累 test_paths 的两条跟着红，而它们跟这个改动毫无关系。
        self.work = tempfile.mkdtemp(prefix='p2w_dl_log_')
        self.addCleanup(shutil.rmtree, self.work, True)
        for name, val in (('ROOT', self.work),
                          ('MODELS', os.path.join(self.work, 'models')),
                          ('LOGS', os.path.join(self.work, 'logs')),
                          ('CONFIG', os.path.join(self.work, 'mineru.json'))):
            self.addCleanup(setattr, paths, name, getattr(paths, name))
            setattr(paths, name, val)
        # 不真去起解释器：命令前缀换成一个不存在的假 exe，
        # Popen 反正也被换掉了
        self.addCleanup(setattr, paths, 'models_download_cmd',
                        paths.models_download_cmd)
        self.addCleanup(setattr, paths, 'mineru_available',
                        paths.mineru_available)
        paths.models_download_cmd = lambda: [os.path.join(self.work, 'py.exe'),
                                             '-m', 'mineru.cli.models_download']
        paths.mineru_available = lambda: True

    def _fake_downloader(self, lines, rc):
        """让 subprocess.Popen 返回一个吐出这些行、然后以 rc 退出的假进程。"""
        import subprocess

        body = b''.join(l.encode('utf-8') + b'\n' for l in lines)

        class _P(object):
            def __init__(self):
                self.returncode = rc
                self.stdout = io.BytesIO(body)

            def wait(self):
                return rc

            def terminate(self):
                pass

        orig = subprocess.Popen
        subprocess.Popen = lambda *a, **k: _P()
        self.addCleanup(lambda: setattr(subprocess, 'Popen', orig))

    def test_失败时把下载器说的话带出来(self):
        self._fake_downloader(
            ['Downloading models...',
             'OSError: [Errno 28] No space left on device'], rc=1)
        ok, err = models.download('modelscope')
        self.assertFalse(ok)
        self.assertIn('No space left', err,
                      '只报了退出码，没把真实原因带出来：%s' % err)
        self.assertNotIn('多半是网络断了', err, '还在猜原因')

    def test_日志落盘且在安装目录内(self):
        self._fake_downloader(['line one', 'line two'], rc=1)
        ok, err = models.download('modelscope')
        log = os.path.join(paths.LOGS, 'model_download.log')
        self.assertTrue(os.path.isfile(log), '日志没落盘，下次照样查不了')
        with io.open(log, encoding='utf-8') as f:
            txt = f.read()
        self.assertIn('line two', txt)
        self.assertIn(log, err, '错误里没给日志路径，用户不知道该发什么给我')
        # 落点必须在安装目录内 —— 删掉文件夹 = 卸载干净
        self.assertTrue(os.path.abspath(log).startswith(
            os.path.abspath(paths.ROOT)))

    def test_最后一行没有换行符也不能丢(self):
        r"""进程崩掉时最后那行往往没有换行 —— 而它恰恰是最重要的一行。"""
        import subprocess

        class _P(object):
            def __init__(self):
                self.returncode = 1
                self.stdout = io.BytesIO(
                    'ok\nRuntimeError: 证书验证失败'.encode('utf-8'))

            def wait(self):
                return 1

            def terminate(self):
                pass

        orig = subprocess.Popen
        subprocess.Popen = lambda *a, **k: _P()
        self.addCleanup(lambda: setattr(subprocess, 'Popen', orig))
        ok, err = models.download('modelscope')
        self.assertFalse(ok)
        self.assertIn('证书验证失败', err, '最后一行被丢了')


class Test停止下载在卡住时也要管用(unittest.TestCase):
    r"""原来的停止检查写在主循环里：

        while True:
            if stop_flag and stop_flag(): p.terminate()
            chunk = p.stdout.read(256)      # ← 阻塞在这

    下载器不吐东西（网络卡死）时，代码就停在 read 上，**永远回不到
    检查那行**。而「卡住不动」恰恰是用户最想点停止的时候 ——
    这个检查在最需要它的场景里必然失灵。
    """

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix='p2w_stop_')
        self.addCleanup(shutil.rmtree, self.work, True)
        for name, val in (('ROOT', self.work),
                          ('MODELS', os.path.join(self.work, 'models')),
                          ('LOGS', os.path.join(self.work, 'logs')),
                          ('CONFIG', os.path.join(self.work, 'mineru.json'))):
            self.addCleanup(setattr, paths, name, getattr(paths, name))
            setattr(paths, name, val)
        self.addCleanup(setattr, paths, 'models_download_cmd',
                        paths.models_download_cmd)
        self.addCleanup(setattr, paths, 'mineru_available',
                        paths.mineru_available)
        paths.models_download_cmd = lambda: ['fake.exe', '-m', 'x']
        paths.mineru_available = lambda: True

    def test_下载器一声不吭时也能停下来(self):
        import subprocess
        import threading
        import time

        class _Stuck(object):
            """一个吐不出任何东西的下载器 —— 就卡在那儿。"""

            def __init__(self):
                self.pid = 0x7FFFFFFF        # 不存在的 pid，taskkill 会失败
                self.returncode = None
                self._dead = threading.Event()
                self.stdout = self

            def read(self, n):
                # 最多阻塞 10 秒：真卡死的话测试也不该挂在这
                self._dead.wait(10)
                return b''

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
        ok, err = models.download('modelscope', stop_flag=lambda: True)
        used = time.time() - t0

        self.assertFalse(ok)
        self.assertIn('取消', err, '停下来了却不是「已取消」：%s' % err)
        self.assertLess(used, 8,
                        '点了停止还等了 %.1f 秒 —— 说明检查又被阻塞挡住了' % used)


class Test绝不碰用户的全局配置(unittest.TestCase):
    r"""这一条是给别人机器上的东西上保险。"""

    def test_配置路径不在用户主目录(self):
        home = os.path.abspath(os.path.expanduser('~'))
        self.assertFalse(os.path.abspath(paths.CONFIG).startswith(home + os.sep),
                         '写到用户主目录去了：%s' % paths.CONFIG)

    def test_配置路径就是安装目录里那份(self):
        self.assertEqual(os.path.abspath(paths.CONFIG),
                         os.path.abspath(os.path.join(paths.ROOT, 'mineru.json')))


if __name__ == '__main__':
    unittest.main()
