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
        io.open(update.VERSION_FILE, 'w', encoding='utf-8').write(
            json.dumps({'tag': tag, 'published_at': published}))

    def _remote(self, rel):
        update._api = lambda url: rel

    def test_远端更新时报有更新(self):
        self._local('v1.0.0', '2026-09-01T00:00:00Z')
        self._remote(_rel('v1.1.0', '2026-09-05T00:00:00Z'))
        r = update.check()
        self.assertTrue(r['has_update'])
        self.assertEqual(r['latest'], 'v1.1.0')
        self.assertTrue(r['asset'])

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
        self._remote(_rel('v1.1.0', '2026-09-05T00:00:00Z', assets=[]))
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


if __name__ == '__main__':
    unittest.main()
