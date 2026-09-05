# -*- coding: utf-8 -*-
r"""发布后的 Release 状态检查。**转正之后立刻跑**（docs/RELEASE.md 第四节）。

跑法：
    .venv\Scripts\python.exe tools\check_release.py            # 查 dist 里那个版本
    .venv\Scripts\python.exe tools\check_release.py v0.2.2     # 指定版本
    .venv\Scripts\python.exe tools\check_release.py --full     # 连 SHA256 一起验（要下 287 MB）

## 为什么要有这个脚本

这一类错有个共同特征：**GitHub 侧的状态跟本地产物对不上时，Release 页面
上看着一切正常，用户那边直接受害，而且不报任何错。**

2026-09-05 发 v0.2.2 时踩的那次最典型：`--prerelease=false` 摘掉了预发行版
标记，页面上三个附件齐全、tag 也对，而 `releases/latest` 返回的仍然是上一版
—— 所有用户点「检查更新」都拿不到新版本，界面显示的是「已是最新」。
原因是「谁是 latest」是 Release 自己的属性（`make_latest`），摘标记不会
触发重算，得再补一条 `gh release edit vX --latest`。

RELEASE.md 里当然可以（也确实）写上这条。但那份文件已经八节七百多行，
它自己第 2 条坑就是「跳着读必然漏」—— 再往里塞一段红字，下次照样可能漏。
这个项目对「靠人记得」的问题一律的解法是**把它变成一条能跑的命令**。

## 查八件事

每一条都钉着一个真踩过的、不报错的失败：

  1. `releases/latest` 指的是不是这一版          ← 2026-09-05 v0.2.2
  2. 预发行版标记摘了没、是不是还是草稿
  3. tag 指向的 commit == 本地 version.json 的 sha  ← 2026-09-02 推成 master
  4. 三个附件齐不齐（安装包 / 更新包 / 依赖清单）  ← v0.0.1 只传了更新包
  5. 附件名里的版本号对不对
  6. 附件字节数跟本地产物一不一样                ← --clobber 中断过两次
  7. 发布说明有没有独占一行的 `---`              ← v0.2.0，摘要变 78 行
  8. 摘要区条目格式、有没有残留「预发行版」字样、
     有没有拿一个从没转正过的版本当基准          ← v0.2.1 差点栽

真值一头取自 GitHub API，一头取自 `dist/` 里的产物 —— 两头都不是文档。
"""
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

# 仓库地址跟客户端用的是同一份，别在这儿再抄一遍
from update import OWNER, REPO   # noqa: E402

DIST = os.path.join(ROOT, 'dist')

# 摘要区每条要以这几个词开头（RELEASE.md 第五节），三到五条为宜。
# 上限放到 6：多一条不至于撑爆 620x440 那个面板，再多就该往详细说明里放了。
HEADS = ('新增', '修改', '修复')
MAX_BRIEF = 6


def _is_hr(line):
    r"""整行都是连字符才算分隔线。

    Markdown 表格的分隔行（`|---|---|`）不算 —— `update.split_notes()`
    认的就是整行连字符那种，这里必须跟它同一个判据，不然查了个寂寞。
    """
    s = line.strip()
    return len(s) >= 3 and set(s) == set('-')


def _brief(body):
    r"""摘要区 = 第一条分隔线之前那段。软件里只显示这一段。"""
    out = []
    for line in (body or '').split('\n'):
        if _is_hr(line):
            break
        out.append(line)
    return '\n'.join(out)


def _vers_in(text):
    r"""文本里提到的版本号。用于查「拿没转正过的版本当基准」。"""
    return set(re.findall(r'v\d+\.\d+\.\d+', text or ''))


def audit(ver, rel, latest_tag, tag_sha, local, others):
    r"""纯函数：该查的都查一遍，返回问题列表（空列表 = 全对）。

    ver        要发的版本号，如 'v0.2.2'
    rel        该 Release 的 API 对象（tag_name/prerelease/draft/body/assets）
    latest_tag `releases/latest` 返回的 tag_name
    tag_sha    tag 指向的 commit（长短都行）
    local      {'sha': 本地 version.json 的 sha, 'sizes': {附件名: 字节数}}
    others     {版本号: 是不是预发行版}，用来判断发布说明的基准对不对

    **不联网、不读文件** —— 取数放在下面，判断放在这里。这样测试能直接
    喂假数据；CLAUDE.md 第 4 条坑（测试和实现一起错所以一起绿）就是
    因为构造的数据跟真实调用方不是同一个来源。
    """
    p = []

    # 1. latest 指的是不是这一版
    if latest_tag != ver:
        p.append('[latest] releases/latest 现在指的是 %s，不是 %s —— '
                 '用户点「检查更新」拿不到这一版，而且不报错，界面显示'
                 '「已是最新」。补一条：gh release edit %s --latest'
                 % (latest_tag or '(空)', ver, ver))

    # 2. 发行状态
    if rel.get('prerelease'):
        p.append('[状态] 还挂着预发行版标记，它不是 latest，用户拿不到')
    if rel.get('draft'):
        p.append('[状态] 还是草稿，外面根本看不见')

    # 3. tag 指向的 commit
    ls, ts = (local.get('sha') or ''), (tag_sha or '')
    if not (ls and ts and (ls.startswith(ts) or ts.startswith(ls))):
        p.append('[sha] tag %s 指向 %s，而本地 version.json 是 %s —— '
                 '日后拿这个 tag 检出来的代码不是用户手里那份'
                 % (ver, ts[:12] or '(空)', ls[:12] or '(空)'))

    # 4~6. 附件
    names = [a.get('name', '') for a in rel.get('assets', [])]
    if not any(n.endswith('.exe') for n in names):
        p.append('[附件] 缺安装包（PDF2Word-Setup-%s.exe）—— 新用户没法装' % ver)
    if not any('update' in n and n.endswith('.zip') for n in names):
        p.append('[附件] 缺更新包（名字带 update 的 .zip）—— 老用户点「检查更新」'
                 '会看到「有新版本，但那个 Release 没有附更新包」')
    if not any(n.startswith('requires') and n.endswith('.json') for n in names):
        p.append('[附件] 缺依赖清单（requires-%s.json）—— 客户端只能等下完'
                 '更新包才知道装不装得了，用户白下一趟' % ver)

    for a in rel.get('assets', []):
        n = a.get('name', '')
        vs = _vers_in(n)
        if vs and ver not in vs:
            p.append('[附件] %s 的版本号不是 %s —— 十有八九是拿上一版的产物传的'
                     % (n, ver))
        want = (local.get('sizes') or {}).get(n)
        if want is not None and a.get('size') != want:
            p.append('[附件] %s 远端 %s 字节，本地产物 %s 字节 —— 传残了，'
                     '或者传的根本不是这一份' % (n, a.get('size'), want))

    # 7~8. 发布说明
    body = rel.get('body') or ''
    if not any(_is_hr(x) for x in body.split('\n')):
        p.append('[说明] 没有独占一行的 --- 分隔线 —— 全文会被当成摘要塞进'
                 '620x440 的面板（v0.2.0 那次是 78 行 1384 字符）')

    br = _brief(body)
    items = [x.strip() for x in br.split('\n') if x.strip().startswith('- ')]
    if not items:
        p.append('[说明] 摘要区一条「- 」条目都没有 —— 界面上那块是空的')
    if len(items) > MAX_BRIEF:
        p.append('[说明] 摘要 %d 条，超过 %d 条 —— 面板塞不下，往详细说明里放'
                 % (len(items), MAX_BRIEF))
    for it in items:
        if not any(it[2:].lstrip().startswith(h) for h in HEADS):
            p.append('[说明] 摘要这条没以 新增/修改/修复 开头：%s' % it[:30])

    if '预发行版' in br:
        p.append('[说明] 摘要区还留着「预发行版」字样 —— 已经转正了，那句是假的')

    for v in sorted(_vers_in(br) - set([ver])):
        if others.get(v):
            p.append('[说明] 摘要拿 %s 当基准，而它从没转正过、用户看不到它 —— '
                     '基准应该是转正前的 latest（RELEASE.md 第五节）' % v)

    return p


# ---------------- 以下是取数，联网 ----------------

def _gh(path, jq=None):
    cmd = ['gh', 'api', path]
    if jq:
        cmd += ['--jq', jq]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode('utf-8', 'replace').strip()[:200])
    return r.stdout.decode('utf-8', 'replace').strip()


def _local(ver):
    vj = os.path.join(DIST, 'PDF2Word', 'version.json')
    sha = ''
    if os.path.isfile(vj):
        sha = json.load(io.open(vj, encoding='utf-8')).get('sha', '')
    sizes = {}
    for n in ('PDF2Word-Setup-%s.exe' % ver,
              'pdf_to_word-%s-update.zip' % ver,
              'requires-%s.json' % ver):
        f = os.path.join(DIST, n)
        if os.path.isfile(f):
            sizes[n] = os.path.getsize(f)
    return {'sha': sha, 'sizes': sizes}


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with io.open(path, 'rb') as f:
        while True:
            chunk = f.read(1048576)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def check_hashes(ver):
    r"""真下载一遍比 SHA256。只在 --full 时跑 —— 要下 287 MB。

    字节数对得上不代表内容一样。这一条是给「发给老师之前」那次用的。
    """
    import shutil
    import tempfile
    out = []
    tmp = tempfile.mkdtemp(prefix='relcheck_')
    try:
        subprocess.run(['gh', 'release', 'download', ver, '-D', tmp, '--clobber'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        got = sorted(os.listdir(tmp))
        if not got:
            return ['[哈希] 一个附件都没下下来，gh release download 失败了']
        for n in got:
            loc = os.path.join(DIST, n)
            if not os.path.isfile(loc):
                out.append('[哈希] %s 本地 dist 里没有，没法比' % n)
                continue
            if _sha256(os.path.join(tmp, n)) != _sha256(loc):
                out.append('[哈希] %s 远端跟本地产物内容不一样' % n)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def main(argv):
    full = '--full' in argv
    args = [a for a in argv if not a.startswith('-')]
    ver = args[0] if args else ''
    if not ver:
        vj = os.path.join(DIST, 'PDF2Word', 'version.json')
        if not os.path.isfile(vj):
            print('没给版本号，dist\\PDF2Word\\version.json 也不在。'
                  '用法：check_release.py v0.2.2')
            return 1
        ver = json.load(io.open(vj, encoding='utf-8')).get('tag', '')

    print('Release 状态检查：%s' % ver)
    print('=' * 60)

    try:
        base = 'repos/%s/%s' % (OWNER, REPO)
        rel = json.loads(_gh('%s/releases/tags/%s' % (base, ver)))
        latest_tag = _gh('%s/releases/latest' % base, '.tag_name')
        tag_sha = _gh('%s/git/ref/tags/%s' % (base, ver), '.object.sha')
        allrel = json.loads(_gh('%s/releases?per_page=100' % base))
        others = dict((r.get('tag_name'), bool(r.get('prerelease')))
                      for r in allrel)
    except Exception as e:
        print('拿不到 GitHub 上的状态：%s' % e)
        print('（这个脚本要 gh CLI 并且已登录，先跑一次 gh auth status）')
        return 1

    local = _local(ver)
    print('本地 version.json 的 sha：%s' % (local['sha'][:12] or '(没有)'))
    print('本地产物：')
    if local['sizes']:
        for n, s in sorted(local['sizes'].items()):
            print('  %-34s %12d 字节' % (n, s))
    else:
        print('  (dist 里一个都没有)')
    print('远端 latest：%s' % latest_tag)
    print()

    problems = audit(ver, rel, latest_tag, tag_sha, local, others)
    if full:
        print('--full：真下载比 SHA256（要几分钟）…')
        problems += check_hashes(ver)

    if problems:
        print('发现 %d 处：' % len(problems))
        for x in problems:
            print('  ·', x)
        return 1
    print('这一版在 GitHub 上的状态是对的')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
