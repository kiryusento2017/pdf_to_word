# -*- coding: utf-8 -*-
r"""依赖升级。

最要紧的三条性质：

  · 只升勾了的，没勾的钉住（约束文件）
  · 装到一半断电 → **无条件回滚**，不判断坏没坏
  · 下载中断电 → 不算事，正常进主界面
"""
import io
import json
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import upgrade  # noqa: E402

WORK = os.path.join(ROOT, '_tmp', 'tests', 'upgrade')


class Test约束文件(unittest.TestCase):
    r"""「只升 mineru 不动 torch」的实现手段。

    mineru 的包里自带 `torch<3,>=2.6.0` —— 只勾 mineru 的话，pip 解
    依赖完全可能顺手把 4.2 GB 的 torch 也换掉。约束文件把没勾的钉住，
    **让冲突显式报错，而不是偷偷装出一个坏组合**。"""

    def test_没勾的包被钉在当前版本(self):
        c = upgrade.constraints_for(['mineru'])
        self.assertIn('torch==', c, 'torch 没被钉住，可能被顺手换掉')

    def test_勾了的包不出现在约束里(self):
        c = upgrade.constraints_for(['mineru'])
        for line in c.splitlines():
            self.assertFalse(line.startswith('mineru=='),
                             '勾了的包不该被钉住，那样就升不了')

    def test_全勾上时约束为空(self):
        c = upgrade.constraints_for(['torch', 'torchvision', 'mineru'])
        self.assertEqual(c.strip(), '')

    def test_没装的包不写进约束(self):
        old = upgrade.local_version
        upgrade.local_version = lambda p: ''
        try:
            c = upgrade.constraints_for(['mineru'])
        finally:
            upgrade.local_version = old
        self.assertEqual(c.strip(), '', '没装的包钉不了版本')


class Test只允许升白名单里的包(unittest.TestCase):
    r"""用户没有理由在这个界面里装任意包。"""

    def test_不在白名单的包被忽略(self):
        r = upgrade.plan(['requests', 'flask'])
        self.assertFalse(r['ok'])
        self.assertIn('没选', r['error'])

    def test_白名单就这三个(self):
        self.assertEqual(set(upgrade.ALLOWED),
                         {'torch', 'torchvision', 'mineru'})


class Test开机时怎么办(unittest.TestCase):
    r"""🔴 下载中断电和安装中断电，处理方式完全不同。"""

    def setUp(self):
        self._state = upgrade.STATE
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)
        upgrade.STATE = os.path.join(WORK, 'state.json')

    def tearDown(self):
        upgrade.STATE = self._state
        shutil.rmtree(WORK, ignore_errors=True)

    def _state_is(self, d):
        io.open(upgrade.STATE, 'w', encoding='utf-8').write(
            json.dumps(d, ensure_ascii=False))

    def test_什么都没发生时正常进主界面(self):
        self.assertEqual(upgrade.pending()['action'], 'none')

    def test_下载中断电不算事(self):
        r"""环境没坏，旧的还能用 —— **正常进主界面**，不打扰用户。
        半截文件留着，下次接着下。"""
        self._state_is({'phase': 'downloading', 'picked': ['mineru']})
        self.assertEqual(upgrade.pending()['action'], 'none')

    def test_下好了没装就提示装(self):
        self._state_is({'phase': 'downloaded', 'picked': ['mineru']})
        r = upgrade.pending()
        self.assertEqual(r['action'], 'install')
        self.assertEqual(r['picked'], ['mineru'])

    def test_装到一半断电必须回滚(self):
        r"""🔴 那时 import torch 可能已经失败，让用户进主界面点转换
        只会得到一个看不懂的报错。"""
        self._state_is({'phase': 'installing', 'picked': ['torch'],
                        'backup': 'D:/x/backup/20260905'})
        r = upgrade.pending()
        self.assertEqual(r['action'], 'rollback')
        self.assertEqual(r['backup'], 'D:/x/backup/20260905')

    def test_装完了就没事了(self):
        self._state_is({'phase': 'done', 'picked': ['mineru']})
        self.assertEqual(upgrade.pending()['action'], 'none')

    def test_状态文件坏了当没事(self):
        io.open(upgrade.STATE, 'w', encoding='utf-8').write('不是 json')
        self.assertEqual(upgrade.pending()['action'], 'none')


class Test备份与回滚(unittest.TestCase):

    def setUp(self):
        self._state, self._backup = upgrade.STATE, upgrade.BACKUP
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)
        upgrade.STATE = os.path.join(WORK, 'state.json')
        upgrade.BACKUP = os.path.join(WORK, 'backup')

    def tearDown(self):
        upgrade.STATE, upgrade.BACKUP = self._state, self._backup
        shutil.rmtree(WORK, ignore_errors=True)

    def test_回滚是无条件的不检查坏没坏(self):
        r"""🔴 这是整块设计的核心。pip 没有事务，断在卸载那步会留下
        新旧混合的残骸，而 `import torch` 可能照样成功 —— 直到用户转到
        某一页才崩。所以**不判断**：删干净再拷回去。"""
        site = os.path.join(WORK, 'site')
        bak = os.path.join(WORK, 'backup', '20260905')
        os.makedirs(os.path.join(site, 'fakepkg'))
        os.makedirs(os.path.join(bak, 'fakepkg'))
        # 现场：一个「新旧混合」的残骸
        io.open(os.path.join(site, 'fakepkg', 'new.py'), 'w').write('新的')
        io.open(os.path.join(site, 'fakepkg', 'stale.py'), 'w').write('旧残留')
        # 备份：干净的旧版
        io.open(os.path.join(bak, 'fakepkg', 'old.py'), 'w').write('旧的')

        old_site = upgrade._site_dir
        upgrade._site_dir = lambda: site
        io.open(upgrade.STATE, 'w', encoding='utf-8').write(
            json.dumps({'phase': 'installing', 'backup': bak,
                        'picked': ['fakepkg']}))
        try:
            r = upgrade.rollback()
        finally:
            upgrade._site_dir = old_site

        self.assertTrue(r['ok'])
        files = os.listdir(os.path.join(site, 'fakepkg'))
        self.assertIn('old.py', files, '备份没拷回来')
        self.assertNotIn('new.py', files, '新文件没删干净')
        self.assertNotIn('stale.py', files, '残留没清掉')

    def test_回滚之后状态清空(self):
        r"""不清的话下次开机又要回滚一遍。"""
        site = os.path.join(WORK, 'site')
        bak = os.path.join(WORK, 'backup', 'x')
        os.makedirs(site)
        os.makedirs(bak)
        io.open(os.path.join(bak, 'a.py'), 'w').write('x')
        old_site = upgrade._site_dir
        upgrade._site_dir = lambda: site
        io.open(upgrade.STATE, 'w', encoding='utf-8').write(
            json.dumps({'phase': 'installing', 'backup': bak}))
        try:
            upgrade.rollback()
        finally:
            upgrade._site_dir = old_site
        self.assertIsNone(upgrade.read_state())

    def test_回滚可以重复做结果一样(self):
        r"""回滚本身再断电也不怕 —— 删加拷这个动作是幂等的。"""
        site = os.path.join(WORK, 'site')
        bak = os.path.join(WORK, 'backup', 'x')
        os.makedirs(site)
        os.makedirs(os.path.join(bak, 'p'))
        io.open(os.path.join(bak, 'p', 'a.py'), 'w').write('旧的')
        old_site = upgrade._site_dir
        upgrade._site_dir = lambda: site
        try:
            for _ in range(3):
                io.open(upgrade.STATE, 'w', encoding='utf-8').write(
                    json.dumps({'phase': 'installing', 'backup': bak}))
                upgrade.rollback()
            got = io.open(os.path.join(site, 'p', 'a.py')).read()
        finally:
            upgrade._site_dir = old_site
        self.assertEqual(got, '旧的')

    def test_没有备份时不装作成功(self):
        io.open(upgrade.STATE, 'w', encoding='utf-8').write(
            json.dumps({'phase': 'installing', 'backup': 'D:/没有这个目录'}))
        r = upgrade.rollback()
        self.assertFalse(r['ok'])
        self.assertIn('找不到', r['error'])

    def test_备份列表能列出来给用户清(self):
        r"""硬链接不占额外空间，但用户要看得见能删 —— 小蔡定的：
        「备份的东西要加入到环境监测，方便用户清理，我们不自动清理」。"""
        d = os.path.join(WORK, 'backup', '20260905_120000')
        os.makedirs(d)
        io.open(os.path.join(d, 'a.bin'), 'wb').write(b'x' * 1000)
        io.open(os.path.join(d, 'backup.json'), 'w', encoding='utf-8').write(
            json.dumps({'picked': ['torch'], 'versions': {'torch': '2.11.0'}}))
        rows = upgrade.list_backups()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['picked'], ['torch'])
        self.assertGreater(rows[0]['size'], 900)

    def test_没有备份目录时返回空列表(self):
        self.assertEqual(upgrade.list_backups(), [])


class Test安装用本地包不联网(unittest.TestCase):
    r"""重启时那几分钟只该是解压和搬文件，不能卡在网络上。"""

    def test_没有待装的东西时不乱装(self):
        old = upgrade.read_state
        upgrade.read_state = lambda: None
        try:
            r = upgrade.install()
        finally:
            upgrade.read_state = old
        self.assertFalse(r['ok'])
        self.assertIn('没有待安装', r['error'])


if __name__ == '__main__':
    unittest.main()
