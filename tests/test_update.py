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
        self._orig_api = update.api_race
        update.VERSION_FILE = os.path.join(self.tmp, 'version.json')

    def tearDown(self):
        update.VERSION_FILE = self._orig_vf
        update.api_race = self._orig_api
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _local(self, tag, published):
        with io.open(update.VERSION_FILE, 'w', encoding='utf-8') as f:
            f.write(json.dumps({'tag': tag, 'published_at': published}))

    def _remote(self, rel):
        update.api_race = lambda url: (rel, [])

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
        # 签名多了个 out（用来把 upgrade 段带出来），mock 要跟上
        update._requires_gap = lambda _rel, _out=None: ['某个包（没装）']
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
        update.api_race = boom
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
        update.api_race = boom
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

    def test_第一条通了也要把其余线路跑完(self):
        r"""🔴 **明细必须是完整的，不能只剩一条。**

        2026-09-05 一度在 api_race 里「第一个成功就 break」，理由是
        「明细是给排查用的，为它多等几秒不划算」。那笔账算错了：
        多等的不是几秒 —— 串行时代一条条等 6 秒超时才需要 break，
        并发之后等全部 = 等最慢的那条。本机实测六条并发：第一条成功
        0.94 秒，全部跑完 1.14 秒，**代价 0.21 秒**。

        而 break 掉之后剩下五条根本没测，界面只能说「1 条已通过，
        5 条未测」—— 小蔡升级后第一反应是「怎么只剩一条线路能用」。
        这个面板存在的全部理由就是出事时能看到谁通谁不通，砍掉五条
        等于把它废了。
        """
        seen = []
        real_open = update.urllib.request.urlopen

        def fake_open(req, timeout=None):
            seen.append(req.full_url)

            class R(object):
                def __enter__(self_in):
                    return self_in

                def __exit__(self_in, *a):
                    return False

                def read(self_in):
                    return json.dumps({'tag_name': 'v9.9.9',
                                       'assets': []}).encode('utf-8')
            return R()

        update.urllib.request.urlopen = fake_open
        self.addCleanup(setattr, update.urllib.request, 'urlopen', real_open)

        data, lines = update.api_race('https://api.github.com/x')

        self.assertIsNotNone(data, '一条都没成功？')
        self.assertEqual(len(seen), len(update.API_MIRRORS),
                         '有线路没被测到：只发了 %d 个请求，名单里有 %d 条'
                         % (len(seen), len(update.API_MIRRORS)))
        self.assertEqual(len(lines), len(update.API_MIRRORS),
                         '明细条数跟名单对不上')
        self.assertEqual([x for x in lines if x.get('pending')], [],
                         '全都跑完了还有 pending —— 说明又 break 了')
        self.assertEqual(len([x for x in lines if x['ok']]),
                         len(update.API_MIRRORS),
                         '每条都该是通的（fake 全部返回成功）')
        self.assertEqual(len([x for x in lines if x.get('used')]), 1,
                         '「本次采用」必须且只能标一条')

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
        update.api_race = boom
        r = update.check()
        self.assertTrue(r['ok'])
        self.assertFalse(r['has_update'])
        self.assertIn('还没有发布任何版本', r['error'])

    def test_连不上时报原因而不是崩(self):
        self._local('v1.0.0', '2026-09-01T00:00:00Z')

        def boom(url):
            raise Exception('timed out')
        update.api_race = boom
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

    def test_挂掉的moeyy已经清出去了(self):
        r"""2026-09-03 实测 moeyy.xyz 的 API 和下载双双 SSL 握手失败。
        留着不是「多一条兜底」，是每次都白等它一条超时。"""
        for which, rows in (('下载', update.GH_MIRRORS),
                            ('API', update.API_MIRRORS)):
            self.assertFalse(any('moeyy' in m['prefix'] for m in rows),
                             '%s 名单里还留着已经挂掉的 moeyy' % which)

    def test_API名单和下载名单是两份(self):
        r"""🔴 **这两份名单不一样，是故意的，别哪天「顺手」合并了。**

        `api.github.com` 对未认证请求限速 60 次/小时/IP，镜像代理 API
        等于让全世界共用它那点配额，所以不少镜像直接 403 拒绝；而文件
        下载走 CDN 没这问题。2026-09-03 实测 ghfast.top 和 ghproxy.net
        正是「下载能用、API 403」的，所以它俩只该出现在下载名单里。
        """
        api_ids = {m['id'] for m in update.API_MIRRORS}
        dl_ids = {m['id'] for m in update.GH_MIRRORS}
        for i in ('ghfast', 'ghproxy-net'):
            self.assertIn(i, dl_ids, '%s 的下载是好的，不该删' % i)
            self.assertNotIn(i, api_ids, '%s 现在 403，不该留在 API 名单' % i)

    def test_两份名单都得留直连(self):
        self.assertTrue(any(m['prefix'] == '' for m in update.API_MIRRORS))

    def test_查版本是并发的不是一条条试(self):
        r"""🔴 **钉住这条，别改回串行。**

        原来是依次试，而名单第一条是直连 —— 用户网络封了 GitHub 的话，
        得先干等满 6 秒超时才轮到第二条，五条全试一遍最坏 30 秒，
        界面上却只有一个转圈。

        这里让每条路都慢 0.3 秒：并发的话总耗时 ≈ 0.3 秒，串行是 0.3×条数。
        """
        import time as _t
        delay = 0.3

        class _Resp:
            def read(self):
                return b'{"tag_name": "v9.9.9"}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def slow_open(req, timeout=None):
            _t.sleep(delay)
            return _Resp()

        real = update.urllib.request.urlopen
        update.urllib.request.urlopen = slow_open
        self.addCleanup(setattr, update.urllib.request, 'urlopen', real)

        t0 = _t.time()
        data, lines = update.api_race('https://api.github.com/x')
        el = _t.time() - t0

        n = len(update.API_MIRRORS)
        self.assertEqual(data['tag_name'], 'v9.9.9')
        self.assertEqual(len(lines), n, '没把每条路的结果都带回来')
        self.assertLess(el, delay * n * 0.6,
                        '耗时 %.2fs，%d 条路 —— 看着像串行' % (el, n))

    def test_线路明细里要标出这次用了谁(self):
        r"""界面靠它显示「经 xxx · 1.4 秒」。"""
        class _Resp:
            def read(self):
                return b'{"tag_name": "v9.9.9"}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        real = update.urllib.request.urlopen
        update.urllib.request.urlopen = lambda req, timeout=None: _Resp()
        self.addCleanup(setattr, update.urllib.request, 'urlopen', real)

        _data, lines = update.api_race('https://api.github.com/x')
        used = [x for x in lines if x['used']]
        self.assertEqual(len(used), 1, '「本次采用」必须且只能标一条')
        self.assertTrue(used[0]['ok'])
        # 标的是**实际用了哪条**，不是「哪条最快」—— 后者要测速才知道。
        #
        # 🔴 2026-09-05 改成真赛跑之后，「实际用的」从「名单顺序里第一个
        #    成功的」变成了「最先成功的那条」。所以这里不能再断言它一定
        #    是 API_MIRRORS[0] —— 只能断言「被标的那条确实成功了」。
        #    「用的就是数据的来源」这条性质由 Test真赛跑 里那条守。
        self.assertIn(used[0]['id'], [m['id'] for m in update.API_MIRRORS])

    def test_可用线路保持名单原序不按延迟排(self):
        r"""🔴 **排序依据只能是速度，没测速就别排**（小蔡 2026-09-03）。

        按响应延迟排等于在暗示「排前面的下得快」，而 09-03 实测正好
        反过来：延迟最快的直连（903ms）下载只排第 4（663 KB/s），
        延迟垫底的 gh-proxy.org（1077ms）下载第一（723 KB/s）。
        两个排名几乎是反的 —— 照着延迟排的表选，会选错。
        """
        import time as _t

        class _Resp:
            def read(self):
                return b'{"tag_name": "v9.9.9"}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        # 让名单里**越靠前的越慢**：按延迟排的话顺序会整个倒过来
        order = [m['id'] for m in update.API_MIRRORS]
        delays = {mid: (len(order) - i) * 0.05 for i, mid in enumerate(order)}

        def slow(req, timeout=None):
            url = getattr(req, 'full_url', '')
            for m in update.API_MIRRORS:
                if m['prefix'] and url.startswith(m['prefix']):
                    _t.sleep(delays[m['id']])
                    return _Resp()
            _t.sleep(delays[order[0]])      # 直连
            return _Resp()

        real = update.urllib.request.urlopen
        update.urllib.request.urlopen = slow
        self.addCleanup(setattr, update.urllib.request, 'urlopen', real)

        _data, lines = update.api_race('https://api.github.com/x')

        # 🔴 2026-09-05 改成真赛跑之后，**第一个成功的就 break** ——
        #    所以「可用的」通常只有一条（最快返回的那条），其余是
        #    pending。这条测试守的意图没变：**排序不能受延迟影响**。
        #
        #    这里让名单越靠前的越慢，如果按延迟排，pending 那几条会
        #    整个倒过来。
        rest = [x['id'] for x in lines if not x['ok']]
        want = [mid for mid in order if mid not in
                {x['id'] for x in lines if x['ok']}]
        self.assertEqual(rest, want,
                         '没按名单原序排，像是拿延迟排的：%s' % rest)

        # 六条都要在明细里，不能因为没跑完就消失
        self.assertEqual(len(lines), len(order), '有线路从明细里消失了')

    def test_全都失败时明细也要带出来(self):
        r"""🔴 **失败时更需要这张表。**

        「连不上 GitHub」这句话没有任何可操作性；「五条里三条超时、
        两条 403」才能让人判断是断网还是被墙。
        """
        def boom(req, timeout=None):
            raise Exception('timed out')

        real = update.urllib.request.urlopen
        update.urllib.request.urlopen = boom
        self.addCleanup(setattr, update.urllib.request, 'urlopen', real)

        with self.assertRaises(Exception) as cm:
            update.api_race('https://api.github.com/x')
        lines = getattr(cm.exception, 'lines', None)
        self.assertIsNotNone(lines, '异常上没挂线路明细，界面拿不到')
        self.assertEqual(len(lines), len(update.API_MIRRORS))
        self.assertTrue(all(not x['ok'] for x in lines))
        self.assertTrue(all(x['error'] for x in lines),
                        '没说清每条各是为什么失败的')

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
    r"""更新包走的是第三方镜像（ghfast / gh-proxy.com / ghproxy.net /
    gh-proxy.org 系那三个），下回来直接解压覆盖安装目录里的 .py 和 .js，
    下次启动就执行。

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

    def _serve_per_url(self, plan, default_body=None):
        r"""按 URL 决定给什么：plan 里 key 是 URL 里的特征串，
        值是 body（bytes）或一个要抛的异常。"""
        def open_(req, timeout=None):
            url = getattr(req, 'full_url', str(req))
            body = default_body
            for k, v in plan.items():
                if k in url:
                    body = v
                    break
            if isinstance(body, Exception):
                raise body

            class _Resp(object):
                def __init__(self):
                    self.headers = {'Content-Length': str(len(body))}
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

            return _Resp()

        update.urllib.request.urlopen = open_

    def test_一条线路挂了会自动换下一条(self):
        r"""🔴 **2026-09-03 发 v0.1.1 当天就撞上了这个。**

        原来只试一条，挂了整个更新就失败，而错误信息还写着「再点一次会
        换个源重试」—— 那句话根本不成立：更新包只有 0.5 MB，走的是
        probe_mirrors 的「小包不测速」捷径，所有 bps 都是 0，pick_best
        返回 None，于是每次都落到名单第一条。实测连挑三次全是同一条。

        那天 ghfast.top 的 SSL 挂了，自动更新链路当场断掉 —— 而旁边
        六条是通的。0.5 MB 的东西，换一条重下的代价近乎为零。
        """
        body = b'PK\x03\x04 pretend this is a zip'
        # 第一条（setUp 里测速固定挑「直连」，prefix 为空）挂掉，
        # 第二条 ghfast 正常
        self._serve_per_url(
            {'ghfast.top': body},
            default_body=Exception('SSL: UNEXPECTED_EOF_WHILE_READING'))
        ok, err, via = update.download(
            update.ASSET_PREFIX + 'v1/u.zip', self.dest,
            digest=hashlib.sha256(body).hexdigest())
        self.assertTrue(ok, '第一条挂了就放弃了：%s' % err)
        self.assertEqual(via, 'ghfast.top', '没换到下一条线路：%s' % via)
        self.assertTrue(os.path.isfile(self.dest))

    def test_校验没过时不许换源碰运气(self):
        r"""🔴 **这条跟上一条是一对，方向相反。**

        网络挂了换一条是对的；内容跟原件对不上是**安全事件**，
        换源等于在一堆不可信的源里找一个碰巧对得上的。必须当场停。
        """
        opened = []
        real_serve = self._serve_per_url

        def counting(plan, default_body=None):
            real_serve(plan, default_body)
            inner = update.urllib.request.urlopen

            def spy(req, timeout=None):
                opened.append(getattr(req, 'full_url', ''))
                return inner(req, timeout=timeout)
            update.urllib.request.urlopen = spy

        counting({}, default_body='这不是原件，是被换掉的内容'.encode('utf-8'))
        ok, err, via = update.download(
            update.ASSET_PREFIX + 'v1/u.zip', self.dest,
            digest=hashlib.sha256(b'real content').hexdigest())
        self.assertFalse(ok, '校验没过却装了')
        self.assertIn('校验', err)
        self.assertEqual(len(opened), 1,
                         '校验失败后还试了别的源（试了 %d 条）——'
                         '那是拿安全去碰运气' % len(opened))
        self.assertFalse(os.path.isfile(self.dest), '坏包留在硬盘上了')

    def test_全部线路都挂时说清楚试过几条(self):
        self._serve_per_url({}, default_body=Exception('timed out'))
        ok, err, via = update.download(
            update.ASSET_PREFIX + 'v1/u.zip', self.dest, digest='00' * 32)
        self.assertFalse(ok)
        self.assertIn('条下载线路都试过了', err,
                      '没说试了几条，用户会以为只试了一条：%s' % err)

    def _record_urls(self, body):
        """记下真正请求了哪个 URL。"""
        urls = []
        self._serve(body)
        inner = update.urllib.request.urlopen

        def spy(req, timeout=None):
            urls.append(getattr(req, 'full_url', str(req)))
            return inner(req, timeout=timeout)

        update.urllib.request.urlopen = spy
        return urls

    def test_手动指定线路时就走那条并且不再测速(self):
        r"""🔴 **界面上那个选择必须真的生效。**

        线路表让用户能手动挑一条（因为最快的未必最稳，别人的网络跟
        开发机可能完全不同）。要是选了不算数，那比黑盒更糟 —— 黑盒
        只是不告诉你，假开关是骗你。

        指定之后连测速都该省掉：用户已经替我们做完选择了。
        """
        probed = []
        update.probe_mirrors = lambda url, seconds=2.0, size=0: (
            probed.append(url) or [{'id': 'direct'}])

        body = b'PK\x03\x04 pretend this is a zip'
        urls = self._record_urls(body)
        ok, err, via = update.download(
            update.ASSET_PREFIX + 'v1/u.zip', self.dest,
            digest=hashlib.sha256(body).hexdigest(), prefer='ghp-cdn')

        self.assertTrue(ok, err)
        self.assertEqual(probed, [], '指定了线路却还去测速，白等好几秒')
        self.assertTrue(urls, '压根没发请求')
        self.assertTrue(urls[0].startswith('https://cdn.gh-proxy.org/'),
                        '没走指定的那条线路：%s' % urls[0])

    def test_指定了不认识的线路就回到自动(self):
        r"""前端传进来的 id 万一对不上（老前端、拼错、恶意 POST），
        最坏也只该回到默认行为，不能整个更新流程罢工。"""
        body = b'PK\x03\x04 pretend this is a zip'
        urls = self._record_urls(body)
        ok, err, via = update.download(
            update.ASSET_PREFIX + 'v1/u.zip', self.dest,
            digest=hashlib.sha256(body).hexdigest(), prefer='不存在的线路')
        self.assertTrue(ok, err)
        # setUp 里把测速固定成了「直连」，所以回到自动就是不带任何前缀
        self.assertTrue(urls[0].startswith(update.ASSET_PREFIX),
                        '没回到自动：%s' % urls[0])


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


class Test更新说明拆成摘要和全文(unittest.TestCase):
    r"""Release 的正文是给人看的长篇散文，直接摆进 620x440 的小窗口
    只能看到一个残缺的开头。

    2026-09-05 之前是「截断 600 字符 + 前端只显示前 6 行」，v0.1.1 那版
    用户看到的是：

        ## 修了两件事
        ### 1. 装在中文路径里转换必失败
        （空行）
        有用户把软件放在桌面的「新建文件夹 (2)」里，转换跑满一分钟然后失败：

    —— 一条实质信息都没有。

    现在约定 Release 正文以摘要开头，用一条 `---` 分隔线隔开正文：

        - 新增 关于页面
        - 修复 中文路径转换失败

        ---

        ## 详细说明
        ……

    用可见的分隔线而不是 HTML 注释，是因为注释在 GitHub 网页上是隐藏的，
    用户在 Release 页面看不到摘要。用 `---` 两边都好看。
    """

    def test_有分隔线时摘要只取前面那段(self):
        body = ('- 新增 关于页面\n- 修复 中文路径\n\n---\n\n'
                '## 详细说明\n\n很长的正文……\n')
        brief, full = update.split_notes(body)
        self.assertIn('- 新增 关于页面', brief)
        self.assertIn('- 修复 中文路径', brief)
        self.assertNotIn('详细说明', brief, '摘要里混进了正文')
        self.assertIn('详细说明', full, '全文里应该有正文')

    def test_没有分隔线时全文当摘要(self):
        r"""老 Release 的正文里没有分隔线 —— 找不到就退回原来的行为，
        不能崩，也不能显示成空的。"""
        body = '## 修了两件事\n\n### 1. 装在中文路径里转换必失败\n\n正文……\n'
        brief, full = update.split_notes(body)
        self.assertEqual(brief.strip(), body.strip())
        self.assertEqual(full.strip(), body.strip())

    def test_空正文不炸(self):
        for body in ('', None, '   \n\n  '):
            brief, full = update.split_notes(body)
            self.assertEqual(brief, '')
            self.assertEqual(full, '')

    def test_分隔线要独占一行(self):
        r"""正文里出现的 `---`（比如表格分隔行 `|---|---|`）不能被
        当成分隔线，否则摘要会被腰斩在一个莫名其妙的地方。"""
        body = '- 新增 表格支持\n\n| 列 |\n|---|\n| 值 |\n'
        brief, _full = update.split_notes(body)
        self.assertIn('表格支持', brief)
        self.assertIn('| 值 |', brief, '被表格里的 --- 误切了')

    def test_check返回摘要和全文两个字段(self):
        r"""前端要拿摘要默认显示、拿全文备展开。"""
        body = '- 新增 甲\n\n---\n\n详细说明在这里\n'
        rel = {'tag_name': 'v9.9.9', 'published_at': '2026-01-01T00:00:00Z',
               'body': body, 'assets': []}

        def fake_race(url):
            return rel, []

        old_race = update.api_race
        old_local = update.local_version
        update.api_race = fake_race
        update.local_version = lambda: {'tag': 'v0.0.1', 'published_at': ''}
        try:
            out = update.check()
        finally:
            update.api_race = old_race
            update.local_version = old_local

        self.assertIn('notes_brief', out)
        self.assertIn('notes_full', out)
        self.assertIn('新增 甲', out['notes_brief'])
        self.assertNotIn('详细说明', out['notes_brief'])
        self.assertIn('详细说明', out['notes_full'])
        # 老字段留着，免得旧前端读不到东西
        self.assertIn('notes', out)

    def test_全文不再被600字符截断(self):
        r"""原来 notes 截断到 600 字符，那样「展开全文」也没东西可看。"""
        body = '- 摘要一行\n\n---\n\n' + ('详细说明。' * 400)
        rel = {'tag_name': 'v9.9.9', 'published_at': '', 'body': body,
               'assets': []}

        old_race = update.api_race
        old_local = update.local_version
        update.api_race = lambda url: (rel, [])
        update.local_version = lambda: {'tag': 'v0.0.1', 'published_at': ''}
        try:
            out = update.check()
        finally:
            update.api_race = old_race
            update.local_version = old_local

        self.assertGreater(len(out['notes_full']), 1000,
                           '全文被截断了，展开也看不到东西')


class Test升级策略(unittest.TestCase):
    r"""requires.json 里的 upgrade 段：**可选的黑名单，默认全空。**

    般配度不用人管 —— mineru 包里自带 torch<3,>=2.6.0 这样的声明，
    pip 解依赖时自己会拒。这一段只用于 pip 查不出来的那类问题：
    新版装得上、但实际效果变差了。
    """

    def test_读得出upgrade段(self):
        raw = json.dumps({'version': 'v1', 'requires': {},
                          'upgrade': {'mineru': {'ok': False, 'note': '识别率掉了'}}})
        up = update.read_upgrade(raw)
        self.assertIn('mineru', up)

    def test_老Release没有这一段时返回空字典(self):
        r"""🔴 老 Release 的 json 里没有 upgrade —— 不能崩，
        按「没测过」处理。"""
        raw = json.dumps({'version': 'v0.1.1', 'requires': {'mineru': '3.4.5'}})
        self.assertEqual(update.read_upgrade(raw), {})

    def test_json坏了也不崩(self):
        for raw in ('', 'not json', '[]', 'null'):
            self.assertEqual(update.read_upgrade(raw), {})

    def test_没测过时ok是None(self):
        r"""None 不是 True 也不是 False —— 界面要显示「我们没测过，
        升不升你自己定」，**不能当成可以升，也不能当成不能升**。"""
        up = update.read_upgrade(json.dumps({'upgrade': {
            'mineru': {'ok': None, 'to': '', 'note': ''}}}))
        pol = update.upgrade_policy(up, 'mineru')
        self.assertIsNone(pol['ok'])

    def test_实测不能升时给理由(self):
        up = update.read_upgrade(json.dumps({'upgrade': {
            'mineru': {'ok': False, 'to': '', 'note': '3.6 的表格识别退步了'}}}))
        pol = update.upgrade_policy(up, 'mineru')
        self.assertIs(pol['ok'], False)
        self.assertIn('表格', pol['note'])

    def test_torch按通道分开记(self):
        r"""🔴 cu128 上测通过，不能证明 cu126 上也行 —— 那条通道上的
        torch 是另一个包（打包的是 CUDA 12.6 的库）。"""
        up = update.read_upgrade(json.dumps({'upgrade': {'torch': {
            'cu128': {'ok': True, 'to': '2.12.0', 'note': '本机实测通过'},
            'cu126': {'ok': None, 'to': '', 'note': ''},
        }}}))
        a = update.upgrade_policy(up, 'torch', 'cu128')
        self.assertIs(a['ok'], True)
        self.assertEqual(a['to'], '2.12.0')

        b = update.upgrade_policy(up, 'torch', 'cu126')
        self.assertIsNone(b['ok'], '没测过的通道不能沿用别的通道的结论')

    def test_没记录的通道算没测过(self):
        up = update.read_upgrade(json.dumps({'upgrade': {'torch': {
            'cu128': {'ok': True, 'to': '2.12.0'}}}}))
        self.assertIsNone(update.upgrade_policy(up, 'torch', 'cu130')['ok'])

    def test_查不到的包算没测过(self):
        self.assertIsNone(update.upgrade_policy({}, 'mineru')['ok'])
        self.assertIsNone(update.upgrade_policy(None, 'mineru')['ok'])

    def test_老客户端读新格式json不受影响(self):
        r"""🔴 双向兼容：check_requires 只取 requires 这一个键，
        多出来的 upgrade 段它根本不看。"""
        raw = json.dumps({'version': 'v2', 'requires': {'mineru': '3.4.5'},
                          'upgrade': {'torch': {'cu128': {'ok': False}}}})
        miss = update.check_requires(raw)
        self.assertEqual(miss, [], '新格式不该让老逻辑报缺依赖')


class Test升级策略要真的送到前端(unittest.TestCase):
    r"""🔴 2026-09-05 渲染验证抓到过一次断线：read_upgrade() 定义了
    但没人调用，前端那段「策略说不能升就显示理由」永远拿到空对象，
    界面上什么都不显示。

    这一条锁住整条链路：requires.json → _requires_gap → check() → 前端。
    """

    def _fake_check(self, upgrade_seg):
        """让 check() 走完整流程，但网络层是假的。"""
        rel = {'tag_name': 'v9.9.9', 'published_at': '', 'body': 'x',
               'assets': [{'name': 'requires-v9.9.9.json',
                           'browser_download_url': 'https://x/requires.json'}]}
        payload = json.dumps({'version': 'v9.9.9', 'requires': {},
                              'upgrade': upgrade_seg})

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return payload.encode('utf-8')

        import urllib.request
        old_race = update.api_race
        old_local = update.local_version
        old_open = urllib.request.urlopen
        update.api_race = lambda url: (rel, [])
        update.local_version = lambda: {'tag': 'v0.0.1', 'published_at': ''}
        urllib.request.urlopen = lambda *a, **k: FakeResp()
        try:
            return update.check()
        finally:
            update.api_race = old_race
            update.local_version = old_local
            urllib.request.urlopen = old_open

    def test_check返回里带着upgrade段(self):
        out = self._fake_check({'mineru': {'ok': False, 'note': '识别率掉了'}})
        self.assertIn('upgrade', out)
        self.assertIn('mineru', out['upgrade'])
        self.assertIs(out['upgrade']['mineru']['ok'], False)
        self.assertIn('识别率', out['upgrade']['mineru']['note'])

    def test_老Release没这一段时是空字典而不是缺键(self):
        r"""缺键的话前端读 st.upd.upgrade 会拿到 undefined，
        虽然 JS 那边有 || {} 兜底，但后端该给个明确的空值。"""
        rel = {'tag_name': 'v9.9.9', 'published_at': '', 'body': 'x',
               'assets': []}
        old_race = update.api_race
        old_local = update.local_version
        update.api_race = lambda url: (rel, [])
        update.local_version = lambda: {'tag': 'v0.0.1', 'published_at': ''}
        try:
            out = update.check()
        finally:
            update.api_race = old_race
            update.local_version = old_local
        self.assertIn('upgrade', out)
        self.assertEqual(out['upgrade'], {})


class Test真赛跑(unittest.TestCase):
    r"""2026-09-05 的修正：`ex.map` 换成 `as_completed`。

    改之前六条确实同时发，但 `ex.map` **按输入顺序**返回，for 循环
    必须走完六个才结束 —— 直连排第一且它不通的网络里，每次检查更新
    都要等满 6 秒，即使某个镜像 1 秒就返回了。

    而这个软件的目标用户（老师的电脑）大概率直连不通。
    """

    def _fake(self, delays, fails=()):
        """delays: {mirror_id: 秒}；fails: 哪几条抛异常。"""
        import time as _t

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"tag_name": "v1.0.0"}'

        def fake(req, timeout=None):
            url = getattr(req, 'full_url', '')
            mid = 'direct'
            for m in update.API_MIRRORS:
                if m['prefix'] and url.startswith(m['prefix']):
                    mid = m['id']
                    break
            _t.sleep(delays.get(mid, 0.01))
            if mid in fails:
                raise Exception('timed out')
            return _Resp()

        real = update.urllib.request.urlopen
        update.urllib.request.urlopen = fake
        self.addCleanup(setattr, update.urllib.request, 'urlopen', real)

    def test_第一条慢且失败时不等它(self):
        r"""🔴 这是整个修正的要害。

        直连（名单第一条）卡到超时才失败，而第二条 0.01 秒就成功。
        改之前是 `ex.map`，得走完第一个才轮到第二个，要干等满超时；
        现在拿到第一个成功结果之后只再给 预算用完就返回，
        到点就返回，**不陪那条卡死的线路等下去**。

        ⚠️ 场景必须让第一条**明显慢过总预算**，否则区分不出
        两种行为 —— 它要是在宽限期内就完成了，那本来就该被收进明细。
        （2026-09-05 这条测试原来用 0.6 秒，而宽限期是 1.5 秒，
          于是等它完成才返回，看着像退化，其实是场景没设对。）
        """
        import time as _t
        # 把宽限期临时调小，免得这条测试自己跑成几秒
        real_grace = update.API_DETAIL_BUDGET
        update.API_DETAIL_BUDGET = 0.3
        self.addCleanup(setattr, update, 'API_DETAIL_BUDGET', real_grace)

        slow = update.API_DETAIL_BUDGET * 3          # 远超总预算
        ids = [m['id'] for m in update.API_MIRRORS]
        self._fake({ids[0]: slow}, fails={ids[0]})

        t0 = _t.time()
        data, lines = update.api_race('https://api.github.com/x')
        took = _t.time() - t0

        self.assertIsNotNone(data, '有能用的线路却没拿到数据')
        self.assertLess(took, update.API_DETAIL_BUDGET + 0.6,
                        '等了 %.2f 秒 —— 像是在陪第一条等到底'
                        '（该在拿到结果后 %.1f 秒内返回）'
                        % (took, update.API_DETAIL_BUDGET))
        self.assertLess(took, slow,
                        '等满了第一条的超时（%.1f 秒），真赛跑白改了' % slow)
        # 那条没等到的要标 pending，不能写成「连不上」
        first = [x for x in lines if x['id'] == ids[0]][0]
        self.assertTrue(first.get('pending'),
                        '没等完的线路该标 pending')
        self.assertEqual(first['error'], '', 'pending 的不该带错误信息')

    def test_有一条秒回也要等其余线路(self):
        r"""🔴 **这条测试 2026-09-05 反过来写了一次。**

        原来钉的是「提前 break 时把没跑完的标 pending」—— 那是在为
        break 这个行为兜底。后来发现 break 本身就是错的：并发之后
        等全部只比等第一条多 0.21 秒（本机实测 0.94 → 1.14 秒），
        而代价是明细只剩一条，界面显示「1 条已通过，5 条未测」，
        用户以为线路都挂了。

        现在钉相反的事：**哪怕第一条秒回，也要把其余线路等完**，
        明细必须是完整的。

        （`pending` 字段保留，前端也仍认它 —— 万一将来加了总超时上限，
        那时提前返回的兜底行为还是「标未测」，不是「假装挂了」。）
        """
        ids = [m['id'] for m in update.API_MIRRORS]
        # 第一条秒回成功，其余慢一些但都在超时之内
        self._fake({mid: (0.01 if mid == ids[0] else 0.20) for mid in ids})

        _data, lines = update.api_race('https://api.github.com/x')
        self.assertEqual(len(lines), len(update.API_MIRRORS),
                         '明细条数跟名单对不上')
        self.assertEqual([x for x in lines if x.get('pending')], [],
                         '有线路没等完就返回了 —— 明细会残缺')
        self.assertEqual(len([x for x in lines if x['ok']]),
                         len(update.API_MIRRORS),
                         '每条都该跑出结果')

    def test_标出实际用的是哪条(self):
        r"""界面靠 used 显示「经 xxx」。

        🔴 改成 as_completed 之后 `lines[0]` 不一定是数据的来源了
        （排序按名单顺序，实际用的是最先成功的），所以必须显式标记。
        """
        ids = [m['id'] for m in update.API_MIRRORS]
        # 让第三条最快，前两条慢且失败
        self._fake({ids[0]: 0.5, ids[1]: 0.5, ids[2]: 0.01},
                   fails={ids[0], ids[1]})

        _data, lines = update.api_race('https://api.github.com/x')
        used = [x for x in lines if x.get('used')]
        self.assertEqual(len(used), 1, '「本次采用」要且只要标一条')
        self.assertTrue(used[0]['ok'], '标的那条居然是失败的')

    def test_全都失败时明细是完整的(self):
        r"""全失败那种情况反倒跑完了六条 —— 那时明细最有价值：
        「六条里三条超时、两条 403」才能判断是断网还是被墙。"""
        ids = [m['id'] for m in update.API_MIRRORS]
        self._fake({mid: 0.01 for mid in ids}, fails=set(ids))

        with self.assertRaises(Exception) as cm:
            update.api_race('https://api.github.com/x')
        lines = getattr(cm.exception, 'lines', [])
        self.assertEqual(len(lines), len(ids), '全失败时明细该是完整的')
        self.assertFalse([x for x in lines if x.get('pending')],
                         '全失败时不该有 pending')


class Test三个时间数的大小关系(unittest.TestCase):
    r"""查版本这条路上有三个时间数，写在两个文件里，关系错了都不报错。

    单条超时(API_TRY_TIMEOUT) < 兜底窗口(API_DETAIL_BUDGET) < 前端倒计时。

    2026-09-05 就是前两个反了栽的：超时 6 秒、窗口 3 秒，一条真不通的
    线路要 6 秒才超时，永远赶不上 3 秒的窗口，于是每次都被补成 pending，
    界面显示「未测」—— 而它其实是**确定不通**的，用户看着「未测」
    不知道该怎么办。
    """

    def test_单条超时要短过兜底窗口(self):
        r"""反了的话，线路来不及给出结论就被窗口踢成「未测」。

        这是上面那个 bug 的直接判据。窗口的定位是**兜底**（防线程卡死），
        不该抢在线路自己超时之前生效。
        """
        self.assertLess(
            update.API_TRY_TIMEOUT, update.API_DETAIL_BUDGET,
            '单条超时 %s 秒没短过兜底窗口 %s 秒 —— 真不通的线路会被'
            '踢成「未测」，而不是老实报「超时」'
            % (update.API_TRY_TIMEOUT, update.API_DETAIL_BUDGET))

    def test_前端倒计时要长过后端兜底窗口(self):
        r"""小蔡定的交互：倒计时归零和界面出结果必须是同一时刻。

        倒计时要是没长过后端最坏耗时，就会「数完了还在转」。这两个数
        一个在 actions.js、一个在 update.py，改一边忘一边不报错，
        所以在这儿钉住。
        """
        js = io.open(os.path.join(ROOT, 'app', 'renderer', 'actions.js'),
                     encoding='utf-8').read()
        mark = 'UPD_COUNTDOWN = '
        self.assertIn(mark, js, 'actions.js 里找不到 UPD_COUNTDOWN')
        raw = js.split(mark, 1)[1].split(';', 1)[0].strip()
        self.assertGreater(
            float(raw), update.API_DETAIL_BUDGET,
            '前端倒计时 %s 秒没长过后端兜底 %s 秒 —— 会数完了还在转'
            % (raw, update.API_DETAIL_BUDGET))


if __name__ == '__main__':
    unittest.main()
