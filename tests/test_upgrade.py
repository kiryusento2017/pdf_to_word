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
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import torchdep  # noqa: E402
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
        self._state, self._cache = upgrade.STATE, upgrade.CACHE
        if os.path.isdir(WORK):
            shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)
        upgrade.STATE = os.path.join(WORK, 'state.json')
        # CACHE 也隔离：pending 现在要看硬盘上 wheel 在不在，
        # 不隔离的话会去读开发目录里真实的 upgrade_cache。
        upgrade.CACHE = os.path.join(WORK, 'cache')
        os.makedirs(upgrade.CACHE)

    def tearDown(self):
        upgrade.STATE, upgrade.CACHE = self._state, self._cache
        shutil.rmtree(WORK, ignore_errors=True)

    def _wheel_for(self, *names):
        for n in names:
            io.open(os.path.join(upgrade.CACHE,
                                 '%s-1.0-py3-none-any.whl' % n), 'wb').write(b'x')

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
        self._wheel_for('mineru')      # 包真在硬盘上，才谈得上「能装」
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

    def test_回滚要删掉版本号对不上的那份元数据(self):
        r"""🔴 2026-09-08 从小蔡的备份目录里查出来的真实 bug。

        包本体的目录名不带版本号（永远叫 `fakepkg`），照备份里的名字删
        没问题；但元数据目录名**带版本号** —— 备份里那份叫
        `fakepkg-2.11.0.dist-info`，现场那份叫 `fakepkg-2.14.0.dist-info`。
        名字对不上，老实现就把它整个漏掉了，回滚完两份并排躺着。

        后果不是多占几十 KB：`importlib.metadata` 在两份元数据并存时
        返回哪一个**是不确定的**，而「检查更新」就靠这个数。
        """
        site = os.path.join(WORK, 'site')
        bak = os.path.join(WORK, 'backup', '20260908')
        # 现场：装着新版 —— 本体 + 新版本号的元数据
        os.makedirs(os.path.join(site, 'fakepkg'))
        os.makedirs(os.path.join(site, 'fakepkg-2.14.0.dist-info'))
        # 现场还有两个名字开头一样的**别的包**，一根汗毛都不许动
        os.makedirs(os.path.join(site, 'fakepkgvision'))
        os.makedirs(os.path.join(site, 'fakepkggen'))
        # 备份：旧版 —— 本体 + 旧版本号的元数据
        os.makedirs(os.path.join(bak, 'fakepkg'))
        os.makedirs(os.path.join(bak, 'fakepkg-2.11.0.dist-info'))
        io.open(os.path.join(bak, 'fakepkg', 'old.py'), 'w').write('旧的')
        io.open(os.path.join(bak, 'backup.json'), 'w',
                encoding='utf-8').write(json.dumps({'picked': ['fakepkg']}))

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
        left = os.listdir(site)
        self.assertIn('fakepkg-2.11.0.dist-info', left, '备份的元数据没拷回来')
        self.assertNotIn('fakepkg-2.14.0.dist-info', left,
                         '新版本的元数据没删掉 —— 版本号会读错')
        self.assertEqual(
            len([x for x in left if x.startswith('fakepkg-')]), 1,
            '一个包只该留一份元数据')
        self.assertIn('fakepkgvision', left, '误伤了别的包')
        self.assertIn('fakepkggen', left, '误伤了别的包')

    def test_退哪几个包先信备份自己记的(self):
        r"""用户主动点「退回」时，状态文件多半是 `phase=done` 甚至根本
        没有，里面的 picked 指的也是**上一次升级**、未必是这份备份。
        backup.json 是做这份备份时当场写的，更可信。"""
        site = os.path.join(WORK, 'site')
        bak = os.path.join(WORK, 'backup', '20260908')
        os.makedirs(os.path.join(site, 'fakepkg'))
        os.makedirs(os.path.join(site, 'fakepkg-9.9.9.dist-info'))
        os.makedirs(os.path.join(bak, 'fakepkg'))
        io.open(os.path.join(bak, 'backup.json'), 'w',
                encoding='utf-8').write(json.dumps({'picked': ['fakepkg']}))

        old_site = upgrade._site_dir
        upgrade._site_dir = lambda: site
        try:
            # 状态文件里**没有** picked，只能靠 backup.json
            r = upgrade.rollback(bak)
        finally:
            upgrade._site_dir = old_site

        self.assertTrue(r['ok'])
        self.assertNotIn('fakepkg-9.9.9.dist-info', os.listdir(site),
                         'backup.json 里的 picked 没被读到')

    def test_备份列表能列出来给用户清(self):
        r"""用户要看得见有哪几份、各占多大 —— 09-05 小蔡定的：
        「备份的东西要加入到环境监测，方便用户清理」。

        ⚠️ 那句话后面还有半句「我们不自动清理」，**09-08 被推翻了**：
        没人清就一直堆，他试三次升级堆出 12.4 GB。现在 `backup()` 会
        自己按版本去重、开机会收掉「跟当前版本一样」的那份，见
        `Test备份按版本去重` 和 `Test退回重启后备份自己消失`。
        这个列表本身照旧 —— 看得见、能手动清，只是不再只靠手动。"""
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

    def test_备份没做成就不许往下装(self):
        r"""🔴 整套事务设计都建立在「装之前先备份」上。

        回滚的做法是「照着备份目录里有什么，把 site-packages 里对应的
        删掉再拷回来」—— 备份是空的，回滚就什么也做不了，而那时环境
        已经被 pip 动过了，回不去。所以备份失败必须当场停住。
        （2026-09-05 复查发现这里原来不看 backup 的返回值就继续。）
        """
        old_read, old_backup, old_pip = (upgrade.read_state,
                                         upgrade.backup, upgrade._pip)
        装过了 = []
        upgrade.read_state = lambda: {'phase': 'downloaded',
                                      'picked': ['mineru']}
        upgrade.backup = lambda picked: {'ok': False, 'dir': '', 'files': 0,
                                         'error': '没备份到任何文件'}
        upgrade._pip = lambda *a, **k: (装过了.append(1), (0, ''))[1]
        try:
            r = upgrade.install()
        finally:
            upgrade.read_state, upgrade.backup, upgrade._pip = (
                old_read, old_backup, old_pip)

        self.assertFalse(r['ok'])
        self.assertIn('备份', r['error'])
        self.assertEqual(装过了, [], '备份失败了却还是去装了 —— 装坏就回不去')


class _卡住的stdout(object):
    """readline() 一直阻塞，直到进程被 kill —— 模拟 pip 卡住不吐东西。"""

    def __init__(self):
        self.放行 = threading.Event()

    def readline(self):
        # 上限 10 秒是防这条测试自己挂死；正常路径下 kill 会提前放行
        self.放行.wait(10)
        return b''


class _假进程(object):
    def __init__(self):
        self.stdout = _卡住的stdout()
        self.returncode = 0
        self.pid = 1
        self.killed = False

    def kill(self):
        self.killed = True
        self.stdout.放行.set()      # 管道关掉，readline 返回空

    def wait(self):
        return self.returncode


class Test超时对卡死的pip也要生效(unittest.TestCase):
    r"""🔴 超时检查必须放在独立线程里。

    原来写在读取循环里：`readline()` 之后才判断 time.time() - t0。
    而 readline 是阻塞的 —— pip 卡住不吐东西时（网络断了最常见）
    代码停在那一行，超时判断一次都执行不到，1800 秒上限形同虚设。

    这个坑 models.download / torchdep.install 都踩过并修好了，
    torchdep 的注释里写着。这条链 2026-09-05 复查时才发现漏了。
    """

    def setUp(self):
        self.real_popen = upgrade.subprocess.Popen
        self.fake = _假进程()
        upgrade.subprocess.Popen = lambda *a, **k: self.fake

    def tearDown(self):
        upgrade.subprocess.Popen = self.real_popen

    def test_子进程一个字都不吐时超时照样把它杀掉(self):
        t0 = time.time()
        rc, out = upgrade._pip(['install', 'x'], timeout=0.5)
        took = time.time() - t0

        self.assertTrue(self.fake.killed, '超时了却没杀掉进程')
        self.assertLess(took, 5.0,
                        '超时没生效 —— 一直等到 readline 自己返回才结束')
        self.assertIn('已中止', out, '中止了要在输出里说一声')

    def test_正常跑完不会被误杀(self):
        r"""超时线程不能反过来把正常结束的进程杀了。"""
        self.fake.stdout.放行.set()       # 立刻返回空 = 进程正常结束
        rc, out = upgrade._pip(['install', 'x'], timeout=30)
        self.assertFalse(self.fake.killed, '正常结束的进程被误杀了')
        self.assertNotIn('已中止', out)


class Testtorch和torchvision必须一起升(unittest.TestCase):
    r"""🔴 torchvision 是**编译期绑死 torch 版本**的：0.26.0 配的是
    torch 2.11，装上 torch 2.14 之后它多半加载不了。

    2026-09-07 小蔡实测撞上：只勾了 torch，`constraints_for` 就把
    torchvision 钉死在 0.26.0+cu128（那是「只升 A 不动 B」的有意设计），
    于是下好的 2.5 GB 里根本没有 torchvision —— 就算装上 torch 2.14，
    环境也是坏的。

    所以这两个包**在后端强制配对**，不管前端怎么勾：
    有 torch 就带 torchvision，有 torchvision 就带 torch。
    mineru 不受影响，单升 mineru 时这两个照旧钉死。
    """

    def test_只勾torch也要把torchvision带上(self):
        self.assertEqual(set(upgrade.pair_up(['torch'])), {'torch', 'torchvision'})

    def test_只勾torchvision也要把torch带上(self):
        self.assertEqual(set(upgrade.pair_up(['torchvision'])), {'torch', 'torchvision'})

    def test_单升mineru不牵连那两个(self):
        self.assertEqual(upgrade.pair_up(['mineru']), ['mineru'])

    def test_三个一起勾就原样(self):
        self.assertEqual(set(upgrade.pair_up(['torch', 'torchvision', 'mineru'])),
                         {'torch', 'torchvision', 'mineru'})

    def test_配对之后约束文件里不许再钉死torchvision(self):
        r"""这条是根子：钉死了就下不到新的 torchvision，装完环境是坏的。"""
        txt = upgrade.constraints_for(upgrade.pair_up(['torch']))
        self.assertNotIn('torchvision', txt,
                         '约束里还钉着 torchvision，它就升不上去了：%r' % txt)

    def test_单升mineru时那两个仍然要钉住(self):
        txt = upgrade.constraints_for(upgrade.pair_up(['mineru']))
        self.assertIn('torch', txt, '只升 mineru 时 torch 该被钉住不动')

    def test_plan和download真的用了配对不是光有函数(self):
        r"""🔴 光写个 pair_up 没人调等于没做。这条盯住两个入口都接上了。
        install 故意不接 —— 它只照状态文件执行，那份已经配过对了。"""
        import inspect
        src = inspect.getsource(upgrade)
        for fn in ('def plan(', 'def download('):
            at = src.index(fn)
            seg = src[at:at + 900]
            self.assertIn('pair_up(picked)', seg, '%s 没接上配对' % fn)

    def test_空的和乱七八糟的输入不炸(self):
        self.assertEqual(upgrade.pair_up([]), [])
        self.assertEqual(upgrade.pair_up(None), [])



class Test下好的包还在不在(unittest.TestCase):
    r"""🔴 `pending()` 原来只读状态文件就说「能装」，不看硬盘。

    而 `CACHE = paths.TMP/upgrade_cache`，用户在环境检测页点一下
    **「清理转换临时文件」**（界面上就这么写的），`rm_tree(paths.TMP)`
    会把下好的 2.5 GB 一起删掉 —— 状态文件在 logs/ 下不受影响，仍然
    写着 downloaded。于是重启后信心满满地去装，pip 带着 `--no-index`
    找不到 wheel，装失败、回滚，用户白等一场还看不懂为什么。

    所以「能不能装」必须以**硬盘上真有那几个 wheel** 为准。
    """

    def setUp(self):
        self.w = tempfile.mkdtemp(prefix='p2w_pend_')
        self._cache, self._state = upgrade.CACHE, upgrade.STATE
        upgrade.CACHE = os.path.join(self.w, 'cache')
        upgrade.STATE = os.path.join(self.w, 'upgrade_state.json')
        os.makedirs(upgrade.CACHE)

    def tearDown(self):
        upgrade.CACHE, upgrade.STATE = self._cache, self._state
        shutil.rmtree(self.w, ignore_errors=True)

    def _state_downloaded(self, picked):
        io.open(upgrade.STATE, 'w', encoding='utf-8').write(
            json.dumps({'phase': 'downloaded', 'picked': picked}))

    def _wheel(self, name, ver):
        p = os.path.join(upgrade.CACHE,
                         '%s-%s-cp312-cp312-win_amd64.whl' % (name, ver))
        io.open(p, 'wb').write(b'x' * 100)

    def test_包齐了就说能装(self):
        self._state_downloaded(['torch', 'torchvision'])
        self._wheel('torch', '2.14.0+cu126')
        self._wheel('torchvision', '0.27.0+cu126')
        r = upgrade.pending()
        self.assertEqual(r['action'], 'install')

    def test_包被清理掉了要说重新下不能说能装(self):
        self._state_downloaded(['torch'])
        # CACHE 是空的 —— 正是「点了清理转换临时文件」之后的样子
        r = upgrade.pending()
        self.assertEqual(r['action'], 'redownload',
                         '包都没了还说能装，装到一半才失败')

    def test_只少一个包也算不齐(self):
        r"""torch 在、torchvision 没下全 —— 装上去环境是坏的。"""
        self._state_downloaded(['torch', 'torchvision'])
        self._wheel('torch', '2.14.0+cu126')
        r = upgrade.pending()
        self.assertEqual(r['action'], 'redownload')

    def test_缺哪个包要说出来(self):
        self._state_downloaded(['torch', 'torchvision'])
        self._wheel('torch', '2.14.0+cu126')
        r = upgrade.pending()
        self.assertIn('torchvision', str(r.get('missing') or ''))

    def test_缓存目录整个不见了也要说重下(self):
        r"""🔴 用户点「清理」走的是 `rm_tree`，**删的是整个目录**，
        不是把它清空 —— 所以「目录不存在」才是清理之后的真实样子。
        2026-09-07 变异抓到：这条不写的话，`except OSError: return []`
        改成「当成包都在」也没人发现。"""
        self._state_downloaded(['torch'])
        shutil.rmtree(upgrade.CACHE, ignore_errors=True)
        r = upgrade.pending()
        self.assertEqual(r['action'], 'redownload')
        self.assertIn('torch', str(r.get('missing') or ''))

    def test_下完包却没来得及写状态也要认出来(self):
        r"""🔴 2026-09-07 小蔡真实撞上：14:43:52 两个 wheel 都落盘了，
        `upgrade_state.json` 却根本不存在 —— 2.5 GB 成了软件不认识的孤儿，
        重启后当然看不到「立即重启」，再点检查又说要重下。

        根子在 `download()` **只在全部成功之后才写一次状态**：pip 把文件
        写完、函数还没走到写状态那一句，进程被杀（关软件时 before-quit 里
        killTree 连整棵进程树一起砍），状态就永远停在「没下过」。

        所以：开工就写 `downloading`，而 `pending()` 看到这个状态时
        **要去数一下包齐没齐** —— 齐了就是能装，别让用户白下一次。
        """
        io.open(upgrade.STATE, 'w', encoding='utf-8').write(
            json.dumps({'phase': 'downloading', 'picked': ['torch', 'torchvision']}))
        self._wheel('torch', '2.14.0+cu126')
        self._wheel('torchvision', '0.29.0+cu126')
        r = upgrade.pending()
        self.assertEqual(r['action'], 'install',
                         '包明明齐了却不认，用户得白下一次')

    def test_下到一半被打断包不齐时不许说能装(self):
        r"""同上那条的反面：真下到一半被掐，包是残的，这时候说「能装」
        就会装到一半失败再回滚，比直接让他重下还糟。"""
        io.open(upgrade.STATE, 'w', encoding='utf-8').write(
            json.dumps({'phase': 'downloading', 'picked': ['torch', 'torchvision']}))
        self._wheel('torch', '2.14.0+cu126')      # torchvision 还没下到
        self.assertEqual(upgrade.pending()['action'], 'none')

    def test_开工就写下状态别等下完才写(self):
        r"""光让 pending 认得出还不够 —— download 得真的一开工就落一次盘，
        不然进程被杀时状态文件根本不存在，pending 无从认起。"""
        import inspect
        src = inspect.getsource(upgrade.download)
        at = src.find('_pip(')
        self.assertGreater(at, 0, '找不到跑 pip 那一步')
        self.assertIn("'downloading'", src[:at],
                      'download 没有在开跑之前写下 downloading 状态')

    def test_装到一半断电照旧回滚不受影响(self):
        io.open(upgrade.STATE, 'w', encoding='utf-8').write(
            json.dumps({'phase': 'installing', 'picked': ['torch'],
                        'backup': 'D:/x'}))
        self.assertEqual(upgrade.pending()['action'], 'rollback')

    def test_没状态文件就是没事(self):
        self.assertEqual(upgrade.pending()['action'], 'none')

    def test_包名带下划线或大小写也认得出(self):
        r"""pip 落盘时会把包名规范化，别因为大小写判成「没下」。"""
        self._state_downloaded(['torch'])
        p = os.path.join(upgrade.CACHE, 'Torch-2.14.0-cp312-cp312-win_amd64.whl')
        io.open(p, 'wb').write(b'x' * 100)
        self.assertEqual(upgrade.pending()['action'], 'install')



class Test清理升级备份(unittest.TestCase):
    r"""用户在环境检测页**手动清**走的那条路。自动那条见
    `Test备份按版本去重` 和 `Test退回重启后备份自己消失`。

    🔴 `install()` 装之前会把 site-packages 里那几个包整份备份下来，
    **装成功也不删那一份** —— 回滚要靠它。一次 4 GB 量级（torch 整份拷贝）。

    2026-09-07 小蔡机器上两份就 **8.26 GB、28092 个文件**，而
    `list_backups()` 早就写好、接口也有，**界面上却没有这一项，也没有任何
    地方能删** —— 又一个「做好了没人调」。

    **默认保留最新一份**：多留 4 GB，换一次「装完发现不对还能退回去」的
    机会，划算。
    """

    def setUp(self):
        self._bak = upgrade.BACKUP
        self._state = upgrade.STATE
        self.w = tempfile.mkdtemp(prefix='p2w_bak_')
        upgrade.BACKUP = os.path.join(self.w, 'backup')
        upgrade.STATE = os.path.join(self.w, 'st.json')
        for name in ('20260901_100000', '20260905_120000', '20260907_142606'):
            d = os.path.join(upgrade.BACKUP, name, 'torch')
            os.makedirs(d)
            io.open(os.path.join(d, 'x.pyd'), 'wb').write(b'x' * 3000)

    def tearDown(self):
        upgrade.BACKUP, upgrade.STATE = self._bak, self._state
        shutil.rmtree(self.w, ignore_errors=True)

    def _left(self):
        return sorted(os.listdir(upgrade.BACKUP))

    def test_默认留最新一份把老的删掉(self):
        r = upgrade.prune_backups()
        self.assertEqual(self._left(), ['20260907_142606'])
        self.assertEqual(r['removed'], 2)
        self.assertGreater(r['freed'], 5000)

    def test_装到一半的时候一份都不许删(self):
        r"""🔴 phase=installing 意味着上次装到一半断了，**回滚全靠备份**。
        这时候删备份等于把回头路砍了。"""
        io.open(upgrade.STATE, 'w', encoding='utf-8').write(
            json.dumps({'phase': 'installing', 'picked': ['torch'],
                        'backup': os.path.join(upgrade.BACKUP, '20260907_142606')}))
        r = upgrade.prune_backups()
        self.assertEqual(len(self._left()), 3, '装到一半还把备份删了')
        self.assertEqual(r['removed'], 0)
        self.assertIn('装到一半', r.get('why', ''))

    def test_只有一份时什么都不删(self):
        for n in ('20260901_100000', '20260905_120000'):
            shutil.rmtree(os.path.join(upgrade.BACKUP, n))
        r = upgrade.prune_backups()
        self.assertEqual(len(self._left()), 1)
        self.assertEqual(r['removed'], 0)

    def test_一份都没有时不炸(self):
        shutil.rmtree(upgrade.BACKUP)
        r = upgrade.prune_backups()
        self.assertEqual(r['removed'], 0)

    def test_想全清也可以(self):
        r = upgrade.prune_backups(keep=0)
        self.assertEqual(self._left(), [])
        self.assertEqual(r['removed'], 3)



if __name__ == '__main__':
    unittest.main()


class Test升级也要有进度条(unittest.TestCase):
    r"""点「升级」要下 2.7 GB、几十分钟，界面上原来只有滚动日志 —— 而
    **第一次装 torch 那条路一直有进度条**，同一件事长成了两个样。
    根子在 download 的 on_progress 从声明之后再没被用过。

    🔴 **这一组必须真的跑一遍 `_pip`，不许只查源码里有没有那几个字符串。**
    2026-09-07 第一版就是四条 `inspect.getsource()` + `in` 判断 ——
    把 ProgressAcc 换成一个返回负数的类、把回调改成永远抛异常，四条照样
    全绿。那测的是「代码长什么样」，不是「代码干什么」。
    """

    class _FakePopen(object):
        """假的 pip 进程：吐几行输出就结束。"""

        def __init__(self, lines, rc=0):
            self.stdout = io.BytesIO('\n'.join(lines).encode('utf-8') + b'\n')
            self.returncode = rc

        def wait(self):
            return self.returncode

        def kill(self):
            pass

    def _run(self, lines, rc=0):
        """真的调用 upgrade._pip，把子进程换成假的。返回 (进度, 日志, out)."""
        got, logs = [], []
        real = upgrade.subprocess.Popen
        upgrade.subprocess.Popen = lambda *a, **kw: self._FakePopen(lines, rc)
        try:
            code, out = upgrade._pip(
                ['download', 'torch'], timeout=30,
                on_log=lambda x: logs.append(x),
                on_progress=lambda c, t: got.append((c, t)))
        finally:
            upgrade.subprocess.Popen = real
        return got, logs, out

    def test_进度行真的喂给了回调(self):
        got, _logs, _out = self._run([
            'Collecting torch', 'Progress 100 of 1000', 'Progress 700 of 1000',
            'Downloading torch.whl'])
        self.assertEqual(got, [(100, 1000), (700, 1000)],
                         '进度没被解析出来喂给回调：%r' % (got,))

    def test_进度行不进日志区(self):
        r"""2.7 GB 会刷几千行，不拦的话 Collecting / Downloading 全被淹掉。"""
        _got, logs, _out = self._run([
            'Collecting torch', 'Progress 100 of 1000', 'Downloading torch.whl'])
        self.assertEqual(logs, ['Collecting torch', 'Downloading torch.whl'],
                         '进度行漏进日志了：%r' % (logs,))

    def test_没给进度回调时一切照旧(self):
        r"""别的调用方（plan / install）没传 on_progress，行为不能变。"""
        logs = []
        real = upgrade.subprocess.Popen
        upgrade.subprocess.Popen = lambda *a, **kw: self._FakePopen(
            ['Collecting torch', 'Progress 1 of 2'])
        try:
            upgrade._pip(['download'], timeout=30, on_log=lambda x: logs.append(x))
        finally:
            upgrade.subprocess.Popen = real
        self.assertIn('Progress 1 of 2', logs, '没传回调时不该把进度行吃掉')

    def test_失败摘要里不许全是进度数字(self):
        r"""🔴 下载中途被掐时，out 的最后三行很可能就是三行 Progress ——
        用户拿到的报错摘要会变成三个没意义的数字对。"""
        real = upgrade.subprocess.Popen
        upgrade.subprocess.Popen = lambda *a, **kw: self._FakePopen(
            ['ERROR: 连接被重置', 'Progress 100 of 1000',
             'Progress 200 of 1000', 'Progress 300 of 1000'], rc=1)
        try:
            r = upgrade.download(['torch'])
        finally:
            upgrade.subprocess.Popen = real
        self.assertFalse(r['ok'])
        self.assertIn('连接被重置', r['error'],
                      '真正的报错被进度行挤掉了：%r' % r['error'])

    def test_分母跟着已见过的包一起长(self):
        r"""两个包各自从 0 报起，总进度不能倒退。"""
        acc = torchdep.ProgressAcc(floor=0)
        a = acc.feed(500, 1000)          # 第一个包
        b = acc.feed(200, 3000)          # 换包了
        self.assertGreaterEqual(b, a, '换包时总进度倒退了：%s → %s' % (a, b))
        self.assertGreaterEqual(acc.total(), 3000, '分母没把新包算进来')


class Test备份按版本去重(unittest.TestCase):
    r"""🔴 2026-09-08 小蔡定的规矩，**推翻了 09-05「我们不自动清理」**。

    原来的规矩是「列在环境检测里让用户自己清」。代价是没人清就一直堆：
    他试了三次升级，硬盘上躺着三份**内容几乎一样**的 2.11.0，12.4 GB
    （逐文件按硬链接去重量过，是真占这么多，不是把同一份算了三遍）。

    新规矩两条：同版本只留最新一份；不同版本最多留两个。
    """

    def setUp(self):
        self._bak, self._state = upgrade.BACKUP, upgrade.STATE
        self.w = tempfile.mkdtemp(prefix='p2w_dup_')
        upgrade.BACKUP = os.path.join(self.w, 'backup')
        upgrade.STATE = os.path.join(self.w, 'st.json')

    def tearDown(self):
        upgrade.BACKUP, upgrade.STATE = self._bak, self._state
        shutil.rmtree(self.w, ignore_errors=True)

    def _mk(self, name, versions):
        d = os.path.join(upgrade.BACKUP, name)
        os.makedirs(d)
        io.open(os.path.join(d, 'x.bin'), 'wb').write(b'x' * 1000)
        io.open(os.path.join(d, 'backup.json'), 'w', encoding='utf-8').write(
            json.dumps({'picked': sorted(versions), 'versions': versions}))

    def _left(self):
        return sorted(os.listdir(upgrade.BACKUP))

    def test_同一个版本只留最新那份(self):
        self._mk('20260901_100000', {'torch': '2.11.0+cu128'})
        self._mk('20260905_120000', {'torch': '2.11.0+cu128'})
        self._mk('20260907_142606', {'torch': '2.11.0+cu128'})
        r = upgrade.prune_dup_backups()
        self.assertEqual(self._left(), ['20260907_142606'])
        self.assertEqual(r['removed'], 2)
        self.assertGreater(r['freed'], 0, '没报释放了多少')

    def test_版本不一样的都留着(self):
        r"""留着不同版本才有「退回上一版」的意义。"""
        self._mk('20260901_100000', {'torch': '2.11.0'})
        self._mk('20260905_120000', {'torch': '2.14.0'})
        upgrade.prune_dup_backups()
        self.assertEqual(self._left(), ['20260901_100000', '20260905_120000'])

    def test_不同版本也有上限不能一路堆下去(self):
        r"""一路 2.11→2.14→2.15→2.16 升上去、从不回退的人，按「每个版本
        留一份」一份都不会删 —— 照样堆到 12 GB。所以还得压个总数。"""
        self._mk('20260901_100000', {'torch': '2.11.0'})
        self._mk('20260903_100000', {'torch': '2.14.0'})
        self._mk('20260905_100000', {'torch': '2.15.0'})
        upgrade.prune_dup_backups()
        self.assertEqual(self._left(), ['20260903_100000', '20260905_100000'],
                         '该留最新的两个版本')

    def test_同版本去重和总数上限一起生效(self):
        self._mk('20260901_100000', {'torch': '2.11.0'})
        self._mk('20260902_100000', {'torch': '2.14.0'})
        self._mk('20260903_100000', {'torch': '2.14.0'})
        self._mk('20260904_100000', {'torch': '2.15.0'})
        upgrade.prune_dup_backups()
        self.assertEqual(self._left(), ['20260903_100000', '20260904_100000'])

    def test_装到一半断了就一份都不许删(self):
        r"""那意味着下次开机要靠备份回滚 —— 这时候删等于把回头路砍了。"""
        self._mk('20260901_100000', {'torch': '2.11.0'})
        self._mk('20260905_120000', {'torch': '2.11.0'})
        io.open(upgrade.STATE, 'w', encoding='utf-8').write(
            json.dumps({'phase': 'installing', 'backup': 'x'}))
        r = upgrade.prune_dup_backups()
        self.assertEqual(r['removed'], 0)
        self.assertEqual(len(self._left()), 2)
        self.assertIn('装到一半', r['why'])

    def test_备份完自己就调了不用等用户去点(self):
        r"""🔴 **这条是整块改动的要害。**

        上一版栽的就是这里：清理函数写好了、接口也有，**升级流程从头到
        尾一次都不调**，只等用户自己去环境检测页勾一下。功能齐、接口通、
        缺的是「谁按下那一下」—— 这个项目栽在这个形状上已经第三次了。
        """
        site = os.path.join(self.w, 'site')
        os.makedirs(os.path.join(site, 'torch'))
        io.open(os.path.join(site, 'torch', 'a.py'), 'w').write('x')
        # 先摆一份「同版本的老备份」在那儿
        self._mk('20260101_000000', {'torch': upgrade.local_version('torch')})

        old = upgrade._site_dir
        upgrade._site_dir = lambda: site
        try:
            b = upgrade.backup(['torch'])
        finally:
            upgrade._site_dir = old

        self.assertTrue(b['ok'], '备份本身就没成：%s' % b.get('error'))
        self.assertNotIn('20260101_000000', self._left(),
                         '老的同版本备份没被收拾掉 —— backup() 没调清理')

    def test_清理失手不能连累这次备份(self):
        r"""不然会变成「因为清理旧的失败，导致升级不敢往下装」，本末倒置
        （`install()` 看 backup 的 ok 决定要不要继续）。"""
        site = os.path.join(self.w, 'site')
        os.makedirs(os.path.join(site, 'torch'))
        io.open(os.path.join(site, 'torch', 'a.py'), 'w').write('x')
        old_site, old_prune = upgrade._site_dir, upgrade.prune_dup_backups
        upgrade._site_dir = lambda: site

        def _boom(*a, **kw):
            raise OSError('清理炸了')
        upgrade.prune_dup_backups = _boom
        try:
            b = upgrade.backup(['torch'])
        finally:
            upgrade._site_dir = old_site
            upgrade.prune_dup_backups = old_prune
        self.assertTrue(b['ok'], '清理失手把整次备份带崩了')


class Test退回重启后备份自己消失(unittest.TestCase):
    r"""🔴 小蔡 2026-09-08 定的流程：

        2.11 →升级→ 2.14，下面列一份「2.11 的备份」
        点退回 → 重启 → 环境回到 2.11，**下面那份备份也该没了**

    理由是他自己说的：「我退回并重启之后我用的是 2.11 的版本，那我这个
    时候我要再升级的话，那他直接再重新创建一个不就好了」—— 对，
    `install()` 装之前一定会重新备一份当前版本。

    **时机是重启之后，不是点退回那一刻**：退回是拷 4 GB，中途可能断，
    那时环境半新半旧，备份还得留着救命。
    """

    def setUp(self):
        self._bak, self._state = upgrade.BACKUP, upgrade.STATE
        self._site, self._ver = upgrade._site_dir, upgrade.local_version
        self.w = tempfile.mkdtemp(prefix='p2w_cur_')
        upgrade.BACKUP = os.path.join(self.w, 'backup')
        upgrade.STATE = os.path.join(self.w, 'st.json')
        self.site = os.path.join(self.w, 'site')
        os.makedirs(self.site)
        upgrade._site_dir = lambda: self.site
        self._meta('torch', '2.11.0')          # 环境里干净的一份元数据

    def tearDown(self):
        upgrade.BACKUP, upgrade.STATE = self._bak, self._state
        upgrade._site_dir, upgrade.local_version = self._site, self._ver
        shutil.rmtree(self.w, ignore_errors=True)

    def _meta(self, pkg, ver):
        os.makedirs(os.path.join(self.site, '%s-%s.dist-info' % (pkg, ver)))

    def _env_is(self, **vs):
        upgrade.local_version = lambda p: vs.get(p, '')

    def _mk(self, name, versions):
        d = os.path.join(upgrade.BACKUP, name)
        os.makedirs(d)
        io.open(os.path.join(d, 'x.bin'), 'wb').write(b'x' * 1000)
        io.open(os.path.join(d, 'backup.json'), 'w', encoding='utf-8').write(
            json.dumps({'picked': sorted(versions), 'versions': versions}))

    def _left(self):
        return sorted(os.listdir(upgrade.BACKUP))

    def test_版本跟现在一样的备份删掉(self):
        self._env_is(torch='2.11.0')
        self._mk('20260907_142606', {'torch': '2.11.0'})
        r = upgrade.drop_backups_of_current()
        self.assertEqual(self._left(), [])
        self.assertEqual(r['removed'], 1)

    def test_版本不一样的一根汗毛都不许动(self):
        r"""正常升级完重启，环境是新版、备份是旧版 —— 那份备份正是
        用来退回去的，删了就没退路了。"""
        self._env_is(torch='2.14.0')
        self._mk('20260907_142606', {'torch': '2.11.0'})
        upgrade.drop_backups_of_current()
        self.assertEqual(self._left(), ['20260907_142606'])

    def test_有一项对不上就不算一样(self):
        r"""备份记了两个包，就得两个都对上。"""
        self._meta('torchvision', '0.26.0')
        self._env_is(torch='2.11.0', torchvision='0.29.0')
        self._mk('20260907_142606',
                 {'torch': '2.11.0', 'torchvision': '0.26.0'})
        upgrade.drop_backups_of_current()
        self.assertEqual(self._left(), ['20260907_142606'])

    def test_没记版本的备份不敢碰(self):
        self._env_is(torch='2.11.0')
        self._mk('20260907_142606', {})
        upgrade.drop_backups_of_current()
        self.assertEqual(self._left(), ['20260907_142606'])

    def test_环境里有孤儿元数据时一份都不删(self):
        r"""🔴 这道保护是整条链最要紧的一环。

        同一个包躺着两份元数据时，`importlib.metadata` 返回哪一份**是不
        确定的** —— 2026-09-08 实测过，装的明明是 1.17 问出来是 1.16。
        这时候拿读出来的版本号去判断「该不该删备份」，删的可能正是唯一
        的退路。所以：环境脏就一份都不删，宁可多占 4 GB。
        """
        self._meta('torch', '2.14.0')          # 回滚留下的孤儿，两份并存
        self._env_is(torch='2.11.0')           # 读出来是旧的（碰运气碰的）
        self._mk('20260907_142606', {'torch': '2.11.0'})
        r = upgrade.drop_backups_of_current()
        self.assertEqual(self._left(), ['20260907_142606'],
                         '环境脏的时候不该信版本号')
        self.assertEqual(r['removed'], 0)

    def test_装到一半断了就一份都不许删(self):
        self._env_is(torch='2.11.0')
        self._mk('20260907_142606', {'torch': '2.11.0'})
        io.open(upgrade.STATE, 'w', encoding='utf-8').write(
            json.dumps({'phase': 'installing', 'backup': 'x'}))
        upgrade.drop_backups_of_current()
        self.assertEqual(self._left(), ['20260907_142606'])

    def test_读不到site目录时当脏的不删(self):
        self._env_is(torch='2.11.0')
        self._mk('20260907_142606', {'torch': '2.11.0'})
        upgrade._site_dir = lambda: ''
        upgrade.drop_backups_of_current()
        self.assertEqual(self._left(), ['20260907_142606'])


class Test退回之后还能装回去(unittest.TestCase):
    r"""小蔡 2026-09-08：「以前我下载过的升级包 2.14.0 应该依然存在，
    那我可以选择升级回去。」

    包确实还在（CACHE 在 %TEMP% 下，装完不删），但 `rollback()` 最后
    一步 `clear_state()` 把记录清了 —— 界面上就没有这条路了。
    """

    def setUp(self):
        self._state, self._cache = upgrade.STATE, upgrade.CACHE
        self.w = tempfile.mkdtemp(prefix='p2w_mk_')
        upgrade.STATE = os.path.join(self.w, 'st.json')
        upgrade.CACHE = os.path.join(self.w, 'cache')
        os.makedirs(upgrade.CACHE)

    def tearDown(self):
        upgrade.STATE, upgrade.CACHE = self._state, self._cache
        shutil.rmtree(self.w, ignore_errors=True)

    def _wheel(self, *names):
        for n in names:
            io.open(os.path.join(upgrade.CACHE,
                                 '%s-1.0-py3-none-any.whl' % n), 'wb').write(b'x')

    def test_包还在就把可以装这个记录留住(self):
        self._wheel('torch', 'torchvision')
        self.assertTrue(upgrade.mark_downloaded(['torch', 'torchvision']))
        r = upgrade.pending()
        self.assertEqual(r['action'], 'install')
        self.assertEqual(sorted(r['picked']), ['torch', 'torchvision'])

    def test_包被清掉了就不写这个记录(self):
        r"""硬盘上没 wheel 还说「能装」的话，pip 带着 --no-index 找不到
        文件会装失败，还要再回滚一次 —— 用户白等一场且看不懂。"""
        self.assertFalse(upgrade.mark_downloaded(['torch']))
        self.assertIsNone(upgrade.read_state())

    def test_白名单外的包不认(self):
        self._wheel('requests')
        self.assertFalse(upgrade.mark_downloaded(['requests']))
        self.assertIsNone(upgrade.read_state())

    def test_回滚函数自己绝不写这个记录(self):
        r"""🔴 `install()` 装失败时也会调 `rollback()`。那条路上要是留了
        「可以装」的记录，就成了「装 → 失败 → 回滚 → 开机提示可以装 →
        装 → 失败」的死循环。所以这件事只能由接口层在**用户手动退回**
        那条路上做。"""
        site = os.path.join(self.w, 'site')
        bak = os.path.join(self.w, 'bak')
        os.makedirs(site)
        os.makedirs(bak)
        io.open(os.path.join(bak, 'a.py'), 'w').write('x')
        self._wheel('torch')
        old = upgrade._site_dir
        upgrade._site_dir = lambda: site
        io.open(upgrade.STATE, 'w', encoding='utf-8').write(
            json.dumps({'phase': 'installing', 'backup': bak,
                        'picked': ['torch']}))
        try:
            upgrade.rollback()
        finally:
            upgrade._site_dir = old
        self.assertIsNone(upgrade.read_state(), 'rollback 自己留了状态')
