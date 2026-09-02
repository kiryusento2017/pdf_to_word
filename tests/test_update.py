# -*- coding: utf-8 -*-
r"""检查更新。

要盯住的两条，错了都是用户直接受害：
  1. **挑对 asset** —— 挑成完整包的话，老师为 0.4 MB 的改动重下 0.69 GB
  2. **版本比较要看方向** —— tag 不同不等于有更新，也可能本地比远端新
     （小蔡手动发给老师的版本会踩这个，装上就一直提示更新）
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

import paths   # noqa: E402
import sources  # noqa: E402
import update  # noqa: E402


def _rel(tag='v1.1.0', published='2026-09-05T10:00:00Z', assets=None, body=''):
    return {'tag_name': tag, 'published_at': published, 'body': body,
            'assets': assets if assets is not None else [
                {'name': 'pdf_to_word-%s-update.zip' % tag,
                 'browser_download_url': 'https://x/u.zip', 'size': 400000}]}


class Test挑对更新包(unittest.TestCase):

    def test_有update包时必须挑它而不是完整包(self):
        r"""挑错的代价：老师为 0.4 MB 的改动重下 0.69 GB。"""
        rel = _rel(assets=[
            {'name': 'pdf_to_word-v1.1.0-full.zip',
             'browser_download_url': 'https://x/full.zip', 'size': 740000000},
            {'name': 'pdf_to_word-v1.1.0-update.zip',
             'browser_download_url': 'https://x/up.zip', 'size': 400000},
        ])
        got = update._pick_asset(rel)
        self.assertIn('update', got['name'])
        self.assertEqual(got['size'], 400000)

    def test_顺序颠倒也要挑对(self):
        rel = _rel(assets=[
            {'name': 'a-update.zip', 'browser_download_url': 'u', 'size': 1},
            {'name': 'b-full.zip', 'browser_download_url': 'f', 'size': 999},
        ])
        self.assertIn('update', update._pick_asset(rel)['name'])

    def test_只有一个包时就用它(self):
        rel = _rel(assets=[{'name': 'anything.zip',
                            'browser_download_url': 'x', 'size': 5}])
        self.assertEqual(update._pick_asset(rel)['name'], 'anything.zip')

    def test_没有zip时返回None(self):
        rel = _rel(assets=[{'name': 'notes.txt',
                            'browser_download_url': 'x', 'size': 5}])
        self.assertIsNone(update._pick_asset(rel))

    def test_一个附件都没有(self):
        self.assertIsNone(update._pick_asset(_rel(assets=[])))


class Test版本比较看方向(unittest.TestCase):
    r"""tag 不同**不等于**有更新。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_vf = update.VERSION_FILE
        self._orig_api = update._api
        update.VERSION_FILE = os.path.join(self.tmp, 'version.json')

    def tearDown(self):
        update.VERSION_FILE = self._orig_vf
        update._api = self._orig_api
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _local(self, tag, published):
        with io.open(update.VERSION_FILE, 'w', encoding='utf-8') as f:
            f.write(json.dumps({'tag': tag, 'published_at': published}))

    def _remote(self, rel):
        update._api = lambda url: rel

    def test_远端更新时报有更新(self):
        # 用修订号更新（v1.0.0 → v1.0.1）—— 那是典型场景，
        # 也是更新包唯一能安全覆盖的场景。跨次版本另有一条测试。
        self._local('v1.0.0', '2026-09-01T00:00:00Z')
        self._remote(_rel('v1.0.1', '2026-09-05T00:00:00Z'))
        r = update.check()
        self.assertTrue(r['has_update'])
        self.assertEqual(r['latest'], 'v1.0.1')
        self.assertTrue(r['asset'])

    def test_跨了多少个修订号都一步到位(self):
        r"""小蔡 2026-09-02：「一个人手里有一个旧版本，结果有一天检查更新，
        github 上有一个版本比他快上 30 个版本，难道他要一个一个版本
        更新上去吗？」

        不用。更新包是**全量替换**（装的是当前版本的全部业务代码，
        不是 diff），v0.0.1 直接下 v0.0.31 的包就变成 v0.0.31。
        """
        self._local('v1.0.1', '')
        self._remote(_rel('v1.0.31', '2026-09-05T00:00:00Z'))
        r = update.check()
        self.assertTrue(r['has_update'], '跨 30 个修订号却不让自动更新')
        self.assertFalse(r['need_full'])
        self.assertTrue(r['asset'])

    def test_禁止拿版本号判断能不能自动更新(self):
        r"""🔴 小蔡 2026-09-02 的指令：「禁止版本号作为判断依据」。

        这里原来有两条测试，钉的是「跨次版本 → need_full」——
        那是拿「次版本号变了」去**推断**「依赖变了」，中间隔着一个约定
        （RELEASE.md 里的「依赖变了必须进次版本」）。约定靠发版的人
        不出错：哪天加了个 pip 包却只改修订号，用户就会拿到新代码配旧依赖，
        下次启动 ImportError，而他刚「更新成功」过。

        现在判据换成**依赖清单跟本地实际装的比对**（那是事实）。
        所以跨多大的版本号，只要依赖满足，就该正常自动更新。
        """
        # 跨次版本
        self._local('v1.0.5', '')
        self._remote(_rel('v1.1.0', '2026-09-05T00:00:00Z'))
        r = update.check()
        self.assertTrue(r['has_update'],
                        '又拿版本号把正常更新挡住了')
        self.assertFalse(r['need_full'])

        # 跨主版本
        self._local('v1.9.9', '')
        self._remote(_rel('v2.0.0', '2026-09-05T00:00:00Z'))
        r = update.check()
        self.assertTrue(r['has_update'], '又拿版本号挡了')
        self.assertFalse(r['need_full'])

    def test_依赖不满足才拦住(self):
        r"""拦不拦看的是依赖，不是版本号。"""
        self._local('v1.0.0', '')
        rel = _rel('v1.0.1', '2026-09-05T00:00:00Z')
        rel['assets'].append({
            'name': 'requires-v1.0.1.json',
            'browser_download_url': 'https://x/requires.json', 'size': 100})
        self._remote(rel)

        orig = update._requires_gap
        update._requires_gap = lambda _rel: ['某个包（没装）']
        self.addCleanup(setattr, update, '_requires_gap', orig)

        r = update.check()
        self.assertTrue(r['need_full'], '依赖不满足却让它自动更新')
        self.assertFalse(r['has_update'])
        self.assertIn('完整安装包', r['error'])
        self.assertIn('某个包', r['error'], '没说清楚缺什么')

    def test_拉不到依赖清单时不挡人(self):
        r"""一次网络失败不该把正常更新挡住 —— 真装不了的话
        apply_update 里还有一道（更新包里也带着同一份清单）。"""
        self._local('v1.0.0', '')
        self._remote(_rel('v1.0.1', '2026-09-05T00:00:00Z'))
        r = update.check()
        self.assertTrue(r['has_update'])
        self.assertFalse(r['need_full'])

    def test_一样的版本不报更新(self):
        self._local('v1.1.0', '2026-09-05T00:00:00Z')
        self._remote(_rel('v1.1.0', '2026-09-05T00:00:00Z'))
        self.assertFalse(update.check()['has_update'])

    def test_本地比远端新时不报更新(self):
        r"""🔴 小蔡手动发给老师的测试版会踩这个：tag 不同，但本地更新。
        只比「相不相等」的话，那台机器会永远提示有更新。"""
        self._local('v1.2.0', '2026-09-10T00:00:00Z')
        self._remote(_rel('v1.1.0', '2026-09-05T00:00:00Z'))
        r = update.check()
        self.assertFalse(r['has_update'], '本地更新却报了「有新版本」')
        self.assertIn('比仓库里的', r['error'])
        self.assertIn('还新', r['error'])

    def test_本地比远端新时不报更新_用打包脚本真会写出的version(self):
        r"""🔴 上一条测试自己造了 published_at，而 **build_release.py
        写出来的 version.json 里那个字段是空串** —— 于是防降级判断的
        前半 `if loc['published_at'] and ...` 恒为假，保护从来没生效过。

        测试全绿，bug 却在生产里躺着：测试喂的是手写数据，生产喂的是
        打包脚本的产物，两者不一样。这条测试就用打包脚本真会写出的形状。
        """
        self._local('v1.2.0', '')          # ← 打包脚本写的就是空串
        self._remote(_rel('v1.1.0', '2026-09-05T00:00:00Z'))
        r = update.check()
        self.assertFalse(r['has_update'], '本地更新却报了「有新版本」')
        self.assertIn('比仓库里的', r['error'])
        self.assertIn('还新', r['error'])

    def test_版本号相同但写法不同时算最新(self):
        r"""tag 写成 `0.0.1` 和 `v0.0.1` 是同一个版本，不该提示更新，
        也不该说「本地比远端新」。"""
        self._local('v1.1.0', '')
        self._remote(_rel('1.1.0', '2026-09-05T00:00:00Z'))
        r = update.check()
        self.assertFalse(r['has_update'])
        self.assertEqual(r['error'], '', '同一个版本却报了错误：%s' % r['error'])

    def test_API全断时退回网页拿版本号(self):
        r"""小蔡 2026-09-02 在外面：「检查更新它现在疯狂显示，连不上 github」。

        当时查版本只有 api.github.com 直连一条路 —— 那条断了整个功能就废。
        重测发现镜像的行为会变（09-01 gh-proxy 代理 api 是 403，
        09-02 反而能用，而 ghfast 从能用变成了 403），所以绑死一条路是脆的。

        最后一道退路是网页版 `/releases/latest` 的 302：只能拿到版本号，
        没有 asset 和校验值 —— 够告诉用户「有新版本，去页面手动下」，
        装是装不了的。
        """
        self._local('v1.0.0', '')

        def boom(url):
            raise Exception('timed out')
        update._api = boom
        orig_web = update._latest_tag_via_web
        update._latest_tag_via_web = lambda: 'v1.2.0'
        self.addCleanup(setattr, update, '_latest_tag_via_web', orig_web)

        r = update.check()
        self.assertTrue(r['ok'], '有退路却报成彻底失败')
        self.assertEqual(r['latest'], 'v1.2.0')
        self.assertIn('手动下载', r['error'], '没告诉用户接下来怎么办')
        self.assertFalse(r['has_update'],
                         '没有校验值却让用户去点自动更新')

    def test_API全断且本地已是最新时别吓人(self):
        self._local('v1.2.0', '')

        def boom(url):
            raise Exception('timed out')
        update._api = boom
        orig_web = update._latest_tag_via_web
        update._latest_tag_via_web = lambda: 'v1.2.0'
        self.addCleanup(setattr, update, '_latest_tag_via_web', orig_web)

        r = update.check()
        self.assertTrue(r['ok'])
        self.assertIn('已经是最新', r['error'])

    def test_查版本会依次试镜像(self):
        r"""钉住「不许只走一条路」。"""
        tried = []

        real_open = update.urllib.request.urlopen

        def fake_open(req, timeout=None):
            tried.append(req.full_url)
            raise Exception('boom')

        update.urllib.request.urlopen = fake_open
        self.addCleanup(setattr, update.urllib.request, 'urlopen', real_open)
        try:
            update._api('https://api.github.com/x')
        except Exception:
            pass
        self.assertGreater(len(tried), 1,
                           '只试了一条路：%s' % tried)
        self.assertTrue(any('gh-proxy' in u or 'ghfast' in u for u in tried),
                        '没试镜像：%s' % tried)

    def test_有更新但没附更新包时说清楚(self):
        self._local('v1.0.0', '2026-09-01T00:00:00Z')
        self._remote(_rel('v1.0.1', '2026-09-05T00:00:00Z', assets=[]))
        r = update.check()
        self.assertFalse(r['has_update'], '没包却让用户去下')
        self.assertIn('没有附更新包', r['error'])

    def test_不知道本地版本时不瞎猜(self):
        self._remote(_rel())
        r = update.check()
        self.assertFalse(r['has_update'])
        self.assertIn('不知道当前是哪个版本', r['error'])

    def test_仓库没发过版本时说人话(self):
        self._local('v1.0.0', '2026-09-01T00:00:00Z')

        def boom(url):
            raise Exception('HTTP Error 404: Not Found')
        update._api = boom
        r = update.check()
        self.assertTrue(r['ok'])
        self.assertFalse(r['has_update'])
        self.assertIn('还没有发布任何版本', r['error'])

    def test_连不上时报原因而不是崩(self):
        self._local('v1.0.0', '2026-09-01T00:00:00Z')

        def boom(url):
            raise Exception('timed out')
        update._api = boom
        # 🔴 网页退路也要挡掉 —— 不挡的话这条测试会真的去连 github，
        #    单元测试打真网络等于把 CI 绑在别人的服务器上。
        orig_web = update._latest_tag_via_web
        update._latest_tag_via_web = lambda: ''
        self.addCleanup(setattr, update, '_latest_tag_via_web', orig_web)
        r = update.check()
        self.assertFalse(r['ok'])
        self.assertIn('连不上 GitHub', r['error'])


class Test安装更新包(unittest.TestCase):
    r"""解压覆盖。小蔡定的体验：点更新 → 自动下载 → 自动装好 → 重启。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_root = update.paths.ROOT
        update.paths.ROOT = self.tmp

    def tearDown(self):
        update.paths.ROOT = self._orig_root
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _zip(self, entries):
        import zipfile
        p = os.path.join(self.tmp, '_pkg.zip')
        with zipfile.ZipFile(p, 'w') as z:
            for name, data in entries:
                z.writestr(name, data)
        return p

    def test_正常覆盖(self):
        old = os.path.join(self.tmp, 'pipeline')
        os.makedirs(old)
        io.open(os.path.join(old, 'a.py'), 'w', encoding='utf-8').write('旧内容')
        z = self._zip([('pipeline/a.py', '新内容'),
                       ('server/b.py', 'x'),
                       ('version.json', '{"tag":"v9"}')])
        ok, err, n = update.apply_update(z)
        self.assertTrue(ok, err)
        self.assertEqual(n, 3)
        self.assertEqual(io.open(os.path.join(old, 'a.py'), encoding='utf-8').read(), '新内容')
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, 'server', 'b.py')))

    def test_挡住路径穿越(self):
        r"""🔴 zip slip：包里的 `../../xxx` 会写到安装目录外面去。

        更新包是从网上下的，名字由打包方给定 —— 只要那一环被人动过手脚，
        或者哪天打包脚本手滑，就能往用户的系统目录里写文件。
        必须逐个成员算出最终落点，确认在安装目录内才写。
        """
        z = self._zip([('../../evil.txt', '坏东西'),
                       ('pipeline/ok.py', '好的')])
        ok, err, n = update.apply_update(z)
        self.assertFalse(ok, '路径穿越没被挡住')
        self.assertIn('安装目录外面', err)
        # 一个都不该写 —— 先全解到临时目录再搬，中途发现问题就整批不动
        self.assertFalse(os.path.isfile(os.path.join(self.tmp, 'pipeline', 'ok.py')),
                         '发现坏成员后还是写了别的文件')

    def test_绝对路径也要挡(self):
        z = self._zip([('C:/Windows/evil.txt', '坏东西')])
        ok, err, _n = update.apply_update(z)
        self.assertFalse(ok)

    def test_空包不算成功(self):
        z = self._zip([])
        ok, err, _n = update.apply_update(z)
        self.assertFalse(ok)
        self.assertIn('空', err)

    def test_坏zip报人话(self):
        p = os.path.join(self.tmp, 'bad.zip')
        io.open(p, 'wb').write('这不是 zip'.encode('utf-8'))
        ok, err, _n = update.apply_update(p)
        self.assertFalse(ok)
        self.assertIn('没下完整', err)

    def test_文件不存在(self):
        ok, err, _n = update.apply_update(os.path.join(self.tmp, '没有.zip'))
        self.assertFalse(ok)
        self.assertIn('不见了', err)


class Test镜像(unittest.TestCase):

    def test_候选里必须留一条直连(self):
        r"""某些网络下反而是直连通 —— 全指望镜像的话那些人一个源都没有。"""
        self.assertTrue(any(m['prefix'] == '' for m in update.GH_MIRRORS))

    def test_镜像不止一个(self):
        r"""实测六个候选当场坏三个，只留一个等于把命押在它不挂上。"""
        self.assertGreaterEqual(len(update.GH_MIRRORS), 3)

    def test_下载空url时不发请求(self):
        ok, err, via = update.download('', os.path.join(self.__class__.__name__))
        self.assertFalse(ok)
        self.assertIn('没有可下载', err)


class Test依赖比对(unittest.TestCase):
    r"""小蔡 2026-09-02：「那问题来了，难道仅凭版本号判断吗，
    真实的技术路线是什么」。

    原来判断「这次更新能不能自动装」靠的是「次版本号变没变」，而那条
    建立在一个**约定**上：「依赖变了必须进次版本」（写在 RELEASE.md 里）。
    约定靠发版的人不出错 —— 哪天加了个 pip 包却只改了修订号，
    用户就会拿到新代码配旧依赖，下次启动 ImportError，
    而他刚「更新成功」过，根本想不到是更新害的。

    真实的技术路线是**直接检查依赖本身**：打包时把这一版需要的包和
    当时装的版本写进更新包的 requires.json，客户端解压之后、覆盖之前
    拿它跟本地比。版本号那道留作快速筛（省一次下载），
    最终判据是这里 —— 那是事实，不是约定。
    """

    def test_都装了就放行(self):
        raw = json.dumps({'version': 'v9', 'requires': {
            'fastapi': '0.141.1', 'lxml': '6.1.2'}})
        self.assertEqual(update.check_requires(raw), [])

    def test_缺包要拦住(self):
        raw = json.dumps({'version': 'v9', 'requires': {
            'fastapi': '0.141.1', '这个包根本不存在xyz': '1.0.0'}})
        miss = update.check_requires(raw)
        self.assertTrue(miss, '缺了包却放行了')
        self.assertIn('没装', miss[0])

    def test_大版本对不上要拦住(self):
        r"""比如 mineru 3.x → 4.x：更新包里的新代码按 4.x 写，
        本地还是 3.x，装上就崩。"""
        raw = json.dumps({'version': 'v9', 'requires': {'fastapi': '99.0.0'}})
        miss = update.check_requires(raw)
        self.assertTrue(miss, '大版本差了却放行')
        self.assertIn('99', miss[0])

    def test_小版本差异不拦(self):
        r"""我们本来就不锁版本，锁了反而会因为无关的小版本差异
        挡住正常更新。"""
        import importlib.metadata as md
        cur = md.version('fastapi')
        major = cur.split('.')[0]
        raw = json.dumps({'version': 'v9',
                          'requires': {'fastapi': major + '.999.999'}})
        self.assertEqual(update.check_requires(raw), [],
                         '小版本不同就把人拦下来了')

    def test_清单坏了或没有时不拿它卡人(self):
        r"""老版本的更新包里没有 requires.json；清单本身也可能损坏。
        这两种情况都交回版本号那道判断，不在这里卡死。"""
        self.assertEqual(update.check_requires(''), [])
        self.assertEqual(update.check_requires('这不是 json'), [])
        self.assertEqual(update.check_requires('{}'), [])

    def test_装之前就拦住不是装完才发现(self):
        r"""🔴 顺序很要紧：**解压之后、覆盖之前**比对。
        覆盖完才发现的话，用户拿到的是一个启动就崩的软件。"""
        work = tempfile.mkdtemp(prefix='p2w_req_')
        self.addCleanup(shutil.rmtree, work, True)
        root = os.path.join(work, 'app')
        os.makedirs(os.path.join(root, 'pipeline'))
        old_file = os.path.join(root, 'pipeline', 'a.py')
        with io.open(old_file, 'w', encoding='utf-8') as f:
            f.write('# 旧的\n')
        self.addCleanup(setattr, paths, 'ROOT', paths.ROOT)
        paths.ROOT = root

        zp = os.path.join(work, 'u.zip')
        with zipfile.ZipFile(zp, 'w') as z:
            z.writestr('requires.json', json.dumps(
                {'version': 'v9', 'requires': {'绝对没装的包abc': '1.0.0'}}))
            z.writestr('pipeline/a.py', '# 新的\n')

        ok, err, n = update.apply_update(zp)
        self.assertFalse(ok)
        self.assertIn('完整安装包', err)
        with io.open(old_file, encoding='utf-8') as f:
            self.assertIn('旧的', f.read(), '拦住了却还是把文件覆盖了')


class Test安装失败要回滚(unittest.TestCase):
    r"""搬运阶段中途失败，已经搬过去的必须还原。

    `apply_update` 的注释写着：「直接往安装目录解压的话，中途失败会留下
    一半新一半旧的代码，那种状态比不更新糟得多。先全部解到临时目录，
    都成功了再逐个搬过去。」

    但**搬过去的那一步本身也会中途失败**（文件被占用是最常见的），
    失败就直接 return，已经覆盖掉的不还原 —— 承诺的原子性只兑现了
    解压那一半。一半新一半旧的代码，正是它自己说的最糟状态。
    """

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix='p2w_roll_')
        self.addCleanup(shutil.rmtree, self.work, True)
        self.root = os.path.join(self.work, 'app')
        os.makedirs(os.path.join(self.root, 'pipeline'))
        self.addCleanup(setattr, paths, 'ROOT', paths.ROOT)
        paths.ROOT = self.root
        # 三个旧文件，等着被覆盖
        for name in ('a.py', 'b.py', 'c.py'):
            with io.open(os.path.join(self.root, 'pipeline', name), 'w',
                         encoding='utf-8') as f:
                f.write('# 旧的 %s\n' % name)
        self.zip = os.path.join(self.work, 'u.zip')
        with zipfile.ZipFile(self.zip, 'w') as z:
            for name in ('a.py', 'b.py', 'c.py'):
                z.writestr('pipeline/' + name, '# 新的 %s\n' % name)

    def _read(self, name):
        with io.open(os.path.join(self.root, 'pipeline', name),
                     encoding='utf-8') as f:
            return f.read()

    def test_搬到一半失败要把已经换掉的还原(self):
        import shutil as _sh
        real = _sh.copyfile
        seen = []

        def flaky(src, dst):
            seen.append(dst)
            if len(seen) == 2:          # 第二个文件写不进去
                raise OSError(13, '文件被占用')
            return real(src, dst)

        _sh.copyfile = flaky
        self.addCleanup(lambda: setattr(_sh, 'copyfile', real))

        ok, err, n = update.apply_update(self.zip)
        self.assertFalse(ok)
        self.assertIn('占用', err)
        for name in ('a.py', 'b.py', 'c.py'):
            self.assertIn('旧的', self._read(name),
                          '%s 被换成新的却没还原 —— 现在是一半新一半旧' % name)

    def test_全都成功时确实换成了新的(self):
        ok, err, n = update.apply_update(self.zip)
        self.assertTrue(ok, err)
        self.assertEqual(n, 3)
        for name in ('a.py', 'b.py', 'c.py'):
            self.assertIn('新的', self._read(name))


class Test下载必须校验(unittest.TestCase):
    r"""更新包走的是第三方镜像（ghfast / gh-proxy / ghproxy.net / moeyy），
    下回来直接解压覆盖安装目录里的 .py 和 .js，下次启动就执行。

    镜像不可信这件事，原来的文档只把它定义成「可能挂」—— 但镜像同样
    可以**返回假内容**。没有校验的话，任何一个镜像（或中间人）都能把
    任意代码塞进老师的电脑，这是一条完整的远程执行路径。

    校验值从哪来才可信：GitHub 的 Releases API 每个 asset 都带
    `digest: "sha256:..."`（2026-09-02 实测确认，v0.0.1 的两个附件都有），
    而 api.github.com 是**直连**的（镜像访问 API 会被 403 拒绝，
    见 update.py 顶部的实测表）。所以校验值可信、文件不可信，
    拿可信的值去验不可信的文件，链是完整的。
    """

    def setUp(self):
        self.work = os.path.join(tempfile.gettempdir(), 'p2w_dl_test')
        shutil.rmtree(self.work, ignore_errors=True)
        os.makedirs(self.work)
        self.dest = os.path.join(self.work, 'u.zip')
        self._probe = update.probe_mirrors
        self._pick = sources.pick_best
        self._open = update.urllib.request.urlopen
        # 测速固定挑「直连」，把测速这条变量从测试里拿掉
        update.probe_mirrors = (lambda url, seconds=2.0, size=0:
                        [{'id': 'direct'}])
        sources.pick_best = lambda rows: {'id': 'direct', 'name': 'GitHub 官方'}

    def tearDown(self):
        update.probe_mirrors = self._probe
        sources.pick_best = self._pick
        update.urllib.request.urlopen = self._open
        shutil.rmtree(self.work, ignore_errors=True)

    def _serve(self, body, claim_len=None):
        """让 urlopen 返回这段内容。claim_len 是 Content-Length 声称的长度。"""
        class _Resp(object):
            def __init__(self):
                self.headers = {'Content-Length':
                                str(len(body) if claim_len is None else claim_len)}
                self._pos = 0

            def read(self, n=-1):
                if n is None or n < 0:
                    b = body[self._pos:]
                    self._pos = len(body)
                    return b
                b = body[self._pos:self._pos + n]
                self._pos += n
                return b

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        update.urllib.request.urlopen = lambda req, timeout=None: _Resp()

    def test_校验值对得上就装(self):
        body = b'PK\x03\x04 pretend this is a zip'
        self._serve(body)
        ok, err, via = update.download(
            update.ASSET_PREFIX + 'v1/u.zip', self.dest,
            digest=hashlib.sha256(body).hexdigest())
        self.assertTrue(ok, err)
        self.assertTrue(os.path.isfile(self.dest))

    def test_内容被换掉就拒装并删掉文件(self):
        r"""镜像返回了别的东西 —— 这正是要防的那一下。"""
        self._serve('这不是我们发的包，是镜像塞的'.encode('utf-8'))
        ok, err, via = update.download(
            update.ASSET_PREFIX + 'v1/u.zip', self.dest,
            digest=hashlib.sha256('原本该是这个'.encode('utf-8')).hexdigest())
        self.assertFalse(ok, '内容对不上却装了')
        self.assertIn('校验', err)
        self.assertFalse(os.path.isfile(self.dest),
                         '拒装了却把坏包留在硬盘上')

    def test_没有官方校验值就不下(self):
        r"""GitHub 没给 digest（老 Release，或哪天 API 变了）时宁可不更新。

        更新包会直接覆盖会被执行的代码，"没法校验"和"校验失败"的
        风险是一样的，不能因为拿不到值就放行。
        """
        self._serve(b'whatever')
        ok, err, via = update.download(
            update.ASSET_PREFIX + 'v1/u.zip', self.dest,
            digest='')
        self.assertFalse(ok, '没有校验值却装了')
        self.assertIn('校验', err)

    def test_没下完就报没下完而不是报包坏了(self):
        r"""截断的包原来一路走到 apply_update 才报「更新包打不开」，
        那句话把网络问题说成了文件问题，用户会去怀疑错的东西。"""
        body = '只下了一半'.encode('utf-8')
        self._serve(body, claim_len=len(body) + 5000)
        ok, err, via = update.download(
            update.ASSET_PREFIX + 'v1/u.zip', self.dest,
            digest=hashlib.sha256(body).hexdigest())
        self.assertFalse(ok)
        self.assertIn('没下完', err)

    def test_不是本仓库的地址一律拒绝(self):
        r"""服务只绑 127.0.0.1，但本机任意进程都能 POST 一个自己的 URL
        让它下载并解压覆盖安装目录。zip slip 挡住了目录外，
        目录内的 .py 照样能被换掉。"""
        self._serve(b'x')
        ok, err, via = update.download('https://evil.example.com/u.zip',
                                       self.dest, digest='00' * 32)
        self.assertFalse(ok, '陌生地址却下了')
        self.assertIn('地址', err)


class 测速结果的字段契约(unittest.TestCase):
    r"""probe_mirrors 的返回值要能直接喂给 sources.pick_best。

    2026-09-02：小包捷径返回的是 ok/speed/why，而 pick_best 读 bps，
    于是**任何人点一键更新都崩**（KeyError: 'bps'），跟网络无关。
    端到端真跑一次才发现的 —— 单看两边代码都挑不出毛病。
    """

    def test_小包捷径的字段能喂给pick_best(self):
        rows = update.probe_mirrors('https://example.com/x.zip', size=1024)
        self.assertTrue(rows, '至少该给一个镜像')
        for r in rows:
            self.assertIn('bps', r, '少了 bps，pick_best 会 KeyError')
            self.assertIn('id', r)
            self.assertIn('name', r)
        # 真喂一次，不是只看字段在不在
        best = sources.pick_best(rows) or rows[0]
        self.assertIn('id', best)

    def test_大包才测速(self):
        r"""小包不值当测 —— 五个镜像各下 2 秒，等于先下五遍再下第六遍。"""
        small = update.probe_mirrors('https://example.com/x.zip', size=1024)
        self.assertTrue(all(r.get('why') for r in small), '小包应该走捷径')


if __name__ == '__main__':
    unittest.main()
