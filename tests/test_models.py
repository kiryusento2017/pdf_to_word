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
