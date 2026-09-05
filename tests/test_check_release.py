# -*- coding: utf-8 -*-
r"""发布后的 Release 状态检查（tools/check_release.py）。

这个模块存在的理由：**GitHub 侧的状态跟本地产物对不上时，页面上看着
一切正常，而用户那边直接受害，还不报任何错。**

2026-09-05 发 v0.2.2 时踩的那次最典型：`--prerelease=false` 摘掉了预发行版
标记，Release 页面三个附件齐全、tag 也对，而 `releases/latest` 返回的仍然是
上一版 —— 所有用户点「检查更新」都拿不到新版本，界面显示「已是最新」。

所以这里的每一条都钉着一个「不报错的失败」。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import check_release  # noqa: E402


VER = 'v0.2.2'
SHA = '973450fdf0da1a5b667e7d6061266a92c884123c'

GOOD_BODY = """- 修复 点了更新半天不出进度条
- 修复 检查更新只报一条线路可用
- 修改 「自动（用最快的）」现在名副其实

从更早的版本升上来的话，v0.2.1 那批新功能也会一并拿到。

---

## 详细说明

随便写多长都行。
"""


def _rel(body=GOOD_BODY, prerelease=False, draft=False, assets=None):
    return {
        'tag_name': VER, 'prerelease': prerelease, 'draft': draft,
        'body': body,
        'assets': assets if assets is not None else [
            {'name': 'PDF2Word-Setup-v0.2.2.exe', 'size': 301061857},
            {'name': 'pdf_to_word-v0.2.2-update.zip', 'size': 573807},
            {'name': 'requires-v0.2.2.json', 'size': 590},
        ],
    }


LOCAL = {'sha': SHA, 'sizes': {
    'PDF2Word-Setup-v0.2.2.exe': 301061857,
    'pdf_to_word-v0.2.2-update.zip': 573807,
    'requires-v0.2.2.json': 590,
}}


def _audit(**kw):
    a = {'ver': VER, 'rel': _rel(), 'latest_tag': VER,
         'tag_sha': SHA, 'local': LOCAL, 'others': {}}
    a.update(kw)
    return check_release.audit(**a)


class Test全对时不报(unittest.TestCase):

    def test_一切正常时问题列表是空的(self):
        self.assertEqual(_audit(), [])


class Testlatest指向(unittest.TestCase):
    r"""踩过：--prerelease=false 只摘标记，不重算 latest。"""

    def test_latest还指着上一版必须报(self):
        p = _audit(latest_tag='v0.2.1')
        self.assertTrue(any('latest' in x for x in p), p)

    def test_报错里要说清楚补哪条命令(self):
        p = ' '.join(_audit(latest_tag='v0.2.1'))
        self.assertIn('--latest', p)


class Test发行状态(unittest.TestCase):

    def test_还挂着预发行版标记必须报(self):
        p = _audit(rel=_rel(prerelease=True))
        self.assertTrue(any('预发行版' in x for x in p), p)

    def test_还是草稿必须报(self):
        p = _audit(rel=_rel(draft=True))
        self.assertTrue(any('草稿' in x for x in p), p)


class Testtag指向的commit(unittest.TestCase):
    r"""踩过 2026-09-02：推成了 master，tag 打在上一版的 commit 上。"""

    def test_tag跟本地version_json对不上必须报(self):
        p = _audit(tag_sha='0' * 40)
        self.assertTrue(any('sha' in x for x in p), p)

    def test_短sha也算对得上(self):
        p = _audit(tag_sha=SHA[:7])
        self.assertEqual(p, [])


class Test三个附件(unittest.TestCase):
    r"""少一个的后果：只传更新包新用户装不了，只传安装包老用户更不了。"""

    def test_少了更新包必须报(self):
        p = _audit(rel=_rel(assets=[
            {'name': 'PDF2Word-Setup-v0.2.2.exe', 'size': 301061857},
            {'name': 'requires-v0.2.2.json', 'size': 590}]))
        self.assertTrue(any('update' in x for x in p), p)

    def test_少了依赖清单必须报(self):
        p = _audit(rel=_rel(assets=[
            {'name': 'PDF2Word-Setup-v0.2.2.exe', 'size': 301061857},
            {'name': 'pdf_to_word-v0.2.2-update.zip', 'size': 573807}]))
        self.assertTrue(any('requires' in x for x in p), p)

    def test_少了安装包必须报(self):
        p = _audit(rel=_rel(assets=[
            {'name': 'pdf_to_word-v0.2.2-update.zip', 'size': 573807},
            {'name': 'requires-v0.2.2.json', 'size': 590}]))
        self.assertTrue(any('Setup' in x or '安装包' in x for x in p), p)

    def test_字节数跟本地产物对不上必须报(self):
        r"""--clobber 是先删后传，中断过两次，附件因此少过、也传残过。"""
        p = _audit(rel=_rel(assets=[
            {'name': 'PDF2Word-Setup-v0.2.2.exe', 'size': 301061857},
            {'name': 'pdf_to_word-v0.2.2-update.zip', 'size': 12345},
            {'name': 'requires-v0.2.2.json', 'size': 590}]))
        self.assertTrue(any('字节' in x for x in p), p)

    def test_附件名带别的版本号必须报(self):
        p = _audit(rel=_rel(assets=[
            {'name': 'PDF2Word-Setup-v0.2.1.exe', 'size': 301061857},
            {'name': 'pdf_to_word-v0.2.2-update.zip', 'size': 573807},
            {'name': 'requires-v0.2.2.json', 'size': 590}]))
        self.assertTrue(p)


class Test发布说明的摘要区(unittest.TestCase):
    r"""软件里「检查更新」那个 620x440 的面板只显示分隔线之前那段。
    v0.2.0 踩过：没写分隔线，全文当摘要，用户看到 78 行 1384 字符。"""

    def test_没有独占一行的分隔线必须报(self):
        p = _audit(rel=_rel(body='- 修复 什么什么\n\n## 详细说明\n随便写'))
        self.assertTrue(any('---' in x or '分隔' in x for x in p), p)

    def test_表格的分隔行不算数(self):
        r"""split_notes() 只认整行都是连字符的那种，|---|---| 不是。"""
        body = '- 修复 什么什么\n\n| a | b |\n|---|---|\n| 1 | 2 |\n'
        p = _audit(rel=_rel(body=body))
        self.assertTrue(any('---' in x or '分隔' in x for x in p), p)

    def test_摘要里有不以新增修改修复开头的条目要报(self):
        body = '- 修复 这条没问题\n- 顺手把那个也调了\n\n---\n\n## 详细说明\n'
        p = _audit(rel=_rel(body=body))
        self.assertTrue(any('新增' in x for x in p), p)

    def test_摘要一条都没有要报(self):
        body = '这一版改了点东西。\n\n---\n\n## 详细说明\n'
        p = _audit(rel=_rel(body=body))
        self.assertTrue(p)

    def test_摘要太长要报(self):
        body = '\n'.join('- 修复 第 %d 条' % i for i in range(12))
        p = _audit(rel=_rel(body=body + '\n\n---\n\n## 详细说明\n'))
        self.assertTrue(any('条' in x for x in p), p)


class Test说明里的残留与假版本(unittest.TestCase):

    def test_残留的预发行版字样必须报(self):
        r"""转正后那句就是假的了。2026-09-05 是手工删的，容易忘。"""
        body = GOOD_BODY.replace('---', '⚠️ 这是预发行版，还没完整验过。\n\n---', 1)
        p = _audit(rel=_rel(body=body))
        self.assertTrue(any('预发行版' in x for x in p), p)

    def test_拿没转正过的版本当基准必须报(self):
        r"""v0.2.1 差点栽在这儿：按「相对 v0.2.0」写，而 v0.2.0 从没转正，
        用户实际是从 v0.1.1 升上来的，一次拿到两版内容。"""
        body = GOOD_BODY.replace('v0.2.1', 'v0.2.0')
        p = _audit(rel=_rel(body=body), others={'v0.2.0': True})
        self.assertTrue(any('v0.2.0' in x for x in p), p)

    def test_提到已转正的版本不报(self):
        p = _audit(others={'v0.2.1': False})
        self.assertEqual(p, [])


if __name__ == '__main__':
    unittest.main()
