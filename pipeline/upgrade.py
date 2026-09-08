# -*- coding: utf-8 -*-
r"""依赖升级：torch / mineru。带备份和无条件回滚。

## 分成两步，因为要「不拦人」

小蔡定的体验：**更新全程不打扰，完了提醒重启。**

    下载阶段（几十分钟）  后台跑，用户照常转 PDF
            ↓ 下完了
    提示「重启后生效」    不装，只提示
            ↓ 用户想重启时点
    重启 → 进主界面之前装（几分钟）→ 正常进入

一开始这跟「完全用 pip」是打架的 —— `pip install` 是下载加安装一体的。
后来发现 pip 自己就能拆开，仍然一行自研下载代码都不写：

    pip download -d <cache> -c <约束文件>          只下不装
    pip install --no-index --find-links <cache>    只从本地装，不联网

`--no-index` 让第二步完全不联网，所以重启时那几分钟只是解压和搬文件。

⚠️ **约束文件在下载阶段就要用上**，否则下下来的可能是错的组合，
到安装时才发现就晚了。

## 用约束文件，不用 --no-deps

mineru 的包里自带 `torch<3,>=2.6.0`。用户只勾了 mineru 时，pip 解
依赖完全可能顺手把 4.2 GB 的 torch 也换掉。两种堵法：

  · `--no-deps`  强行只装 mineru 不管依赖。风险是新版 mineru 真需要
                 新 torch 时，装完直接 import 失败
  · **约束文件**  把没勾的包钉在当前版本交给 pip，pip 解不出来就报错

第二种更好：它让冲突**显式暴露**，而不是装出一个坏组合。

## 断电了不判断，无条件回滚

🔴 **pip 没有事务。** 它装一个包分四步：解压 wheel → 卸载旧版（删
文件、删 RECORD 清单）→ 搬新文件进去 → 写新 RECORD。断电停在哪一步，
后果完全不同：

    断在卸载中  旧版清单已删、文件剩一半 → pip 以为「没装过」，直接
                装新的，**剩下的旧文件没人清理**，新旧混在一个目录里
    断在搬文件  新文件到位一半、清单没写 → 重跑能修
    断在写清单  文件全在、清单不全 → 表面能用，以后卸载卸不干净

**壁垒不在「修不修得好」，在于我们根本不知道它断在哪一步。** 更糟的
是：torch 有几万个文件、37 个 dll，新旧混着的时候 `import torch`
**可能是成功的** —— 直到用户转到某一页调用了那个缺失的算子才崩。
「import 通过」完全不能证明环境是好的。

所以在 pip 外面自己套一层事务：装之前备份、状态文件记着、下次启动
读到「安装中」就**无条件回滚** —— 不检查坏没坏、不判断断在哪、不试图
修补，现场多乱都无所谓，删干净再从备份拷回去，得到的一定是升级前那个
能用的版本。回滚本身再断电也不怕：删加拷这个动作重复多少次结果一样。

## 备份用硬链接

2026-09-05 实测验证过前提（pip 卸载是删文件重写，不是原地改内容）：
装 filelock 3.20.0 → 硬链接 → 升到 3.32.5 → 备份那份**仍是旧内容**。

所以备份 4.2 GB 的 torch 是瞬间完成的，不是拷几分钟。硬链接不占额外
空间（同一份数据两个名字），但**同盘才行** —— 备份目录就在安装目录
里，天然同盘。跨盘会 OSError，那时退回真拷贝。

## 不做升级后自检

原本设计是内置一个小 PDF、升级后真转一遍。小蔡否掉了，两条理由都
成立：让用户等很久（加载模型 30 秒起步），而且**显得对软件不自信** ——
正常软件升级完不会说「让我先测测我还能不能用」。

改成不主动测，**升级后第一次转换失败才提示回滚**。
"""
import io
import json
import os
import re
import shutil
import subprocess
import threading
import time

import paths

# 状态文件。放安装目录，跟着文件夹一起删。
STATE = os.path.join(paths.LOGS, 'upgrade_state.json')
# 下载好的 wheel 放这儿，装完不删 —— 回滚要用（重装旧版不必再下）。
CACHE = os.path.join(paths.TMP, 'upgrade_cache')
# 备份放这儿。硬链接，不占额外空间，但要让用户在环境检测里看得见能删。
BACKUP = os.path.join(paths.ROOT, 'backup')

# 能升的包。别的不给碰 —— 用户没有理由在这个界面里装任意包。
ALLOWED = ('torch', 'torchvision', 'mineru')


def _site_dir():
    """site-packages 在哪。"""
    import site
    try:
        for p in site.getsitepackages():
            if p.endswith('site-packages'):
                return p
    except Exception:
        pass
    # embeddable Python 上 getsitepackages 可能没有
    import sysconfig
    return sysconfig.get_paths().get('purelib', '')


def read_state():
    """当前有没有没做完的升级。没有返回 None。"""
    try:
        with io.open(STATE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _write_state(d):
    try:
        paths.ensure(os.path.dirname(STATE))
        with io.open(STATE, 'w', encoding='utf-8') as f:
            f.write(json.dumps(d, ensure_ascii=False, indent=2))
        return True
    except Exception:
        return False


def clear_state():
    try:
        os.remove(STATE)
    except OSError:
        pass


def local_version(pkg):
    try:
        import importlib.metadata as md
        return md.version(pkg)
    except Exception:
        return ''


# torch 和 torchvision 必须同进同退。
#
# 🔴 torchvision 是**编译期绑死 torch 版本**的：0.26.0 配的是 torch 2.11，
#    装上 torch 2.14 之后它多半加载不了（典型是 import 时报 undefined
#    symbol）。所以「只升其中一个」这件事本身就是错的，不该让用户勾得出来。
#
#    2026-09-07 小蔡实测撞上：只勾了 torch，constraints_for 就把
#    torchvision 钉死在当前版本（那是「只升 A 不动 B」的有意设计），
#    于是下好的 2.5 GB 里根本没有 torchvision —— 就算装上 torch，
#    环境也是坏的。
#
#    配对放在**后端**做，不放前端：不管界面上怎么勾、将来谁调这个函数，
#    出来的组合都是自洽的。mineru 跟它俩没有这种绑定，不受影响。
_PAIRED = ('torch', 'torchvision')


def pair_up(picked):
    """把必须同进同退的包补齐。顺序按 ALLOWED，去重。"""
    got = set(p for p in (picked or ()) if p in ALLOWED)
    if got & set(_PAIRED):
        got |= set(_PAIRED)
    return [p for p in ALLOWED if p in got]


def constraints_for(picked):
    r"""给没勾的包生成约束文件内容，把它们钉在当前版本。

    这是「只升 A 不动 B」的实现手段。交给 pip 之后，如果 A 的新版
    要求 B 也升，pip 会**报错而不是偷偷把 B 换掉**。
    """
    lines = []
    for pkg in ALLOWED:
        if pkg in picked:
            continue
        v = local_version(pkg)
        if v:
            lines.append('%s==%s' % (pkg, v))
    return '\n'.join(lines) + ('\n' if lines else '')


def _pip(argv, timeout=1800, on_log=None, on_progress=None):
    r"""跑一条 pip 命令，边跑边喂日志。返回 (returncode, 全部输出)。

    🔴 **超时检查放在独立线程里，不放读取循环。**

       原来写的是「读到一行之后判断一次 time.time() - t0」——
       而 `readline()` 是阻塞的：pip 卡住不吐东西时（网络断了最常见）
       代码就停在那一行上，超时判断**一次都执行不到**，1800 秒的上限
       形同虚设，升级流程会一直挂着。

       这个坑 models.download 和 torchdep.install 都踩过并修好了，
       torchdep 那边的注释写得很清楚：「readline() 会阻塞。pip 卡住
       不吐东西时，代码就停在那儿 —— 而『卡住不动』正是用户最想点
       停止的时候」。这里是同一个形状，用同一套解法。
       （2026-09-05 复查发现这条链漏了。）
    """
    out = []
    try:
        p = subprocess.Popen(
            [paths.python_exe(), '-m', 'pip'] + list(argv),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=paths.ROOT, env=paths.child_env())
    except Exception as e:
        return 1, '%s: %s' % (type(e).__name__, e)

    killed = []
    stop_watch = threading.Event()

    def watch():
        # wait 返回 False = 等满了 timeout 还没被 set，说明超时了
        if not stop_watch.wait(timeout):
            killed.append(True)
            try:
                p.kill()
            except Exception:
                pass

    threading.Thread(target=watch, daemon=True).start()
    try:
        while True:
            line = p.stdout.readline()
            if not line:
                break
            s = line.decode('utf-8', 'replace').rstrip()
            out.append(s)
            # 🔴 进度行喂给进度条，**并且不进日志区**。2.7 GB 会刷出几千行
            #    `Progress N of M`，不拦的话 Collecting / Downloading 这些
            #    真正有用的行全被淹掉 —— 装 torch 那条路早就这么做了
            #    （torchdep.is_noise 就是干这个的），这里照抄。
            if on_progress:
                import torchdep
                pg = torchdep.parse_progress(s)
                if pg:
                    try:
                        on_progress(pg[0], pg[1])
                    except Exception:
                        pass
                    continue
            if on_log:
                try:
                    on_log(s)
                except Exception:
                    pass
    finally:
        stop_watch.set()
    p.wait()
    if killed:
        out.append('（超过 %d 秒没跑完，已中止）' % timeout)
    return p.returncode, '\n'.join(out)


def plan(picked, targets=None, on_log=None):
    r"""预演：这次升级到底会动哪些包。**不真装。**

    返回 {ok, changes: [{name, to}], error, cmd}。

    用 pip 自己的 --dry-run --report，所以「该下哪个文件」仍然是 pip
    判断的（它认得出这台机器是 cp312 + win_amd64）。

    ⚠️ 需要 pip >= 22.2。发行版实测是 26.2.1，支持。不支持时返回
    ok=False 并说明 —— 那时候前端要退化成「不预览、只警告」，
    不能让整个功能废掉。
    """
    picked = pair_up(picked)      # torch / torchvision 必须同进同退
    if not picked:
        return {'ok': False, 'changes': [], 'error': '没选要升级的包', 'cmd': ''}

    paths.ensure(CACHE)
    cfile = os.path.join(CACHE, 'constraints.txt')
    io.open(cfile, 'w', encoding='utf-8').write(constraints_for(picked))
    report = os.path.join(CACHE, 'report.json')
    try:
        os.remove(report)
    except OSError:
        pass

    spec = []
    for p in picked:
        t = (targets or {}).get(p)
        spec.append('%s==%s' % (p, t) if t else p)

    argv = ['install', '--dry-run', '--report', report, '--upgrade',
            '-c', cfile] + spec
    argv += _index_args(picked)
    rc, out = _pip(argv, timeout=300, on_log=on_log)
    cmd = 'pip ' + ' '.join(argv)

    if rc != 0:
        # 🔴 pip 解不出来 = 这几个升级是打架的。**这正是约束文件要的
        #    效果** —— 显式报错，而不是偷偷装出一个坏组合。
        tail = [x for x in out.strip().splitlines() if x.strip()][-3:]
        return {'ok': False, 'changes': [], 'cmd': cmd,
                'error': '  '.join(tail)[:300] or '预演失败'}

    try:
        with io.open(report, encoding='utf-8') as f:
            d = json.load(f)
    except Exception as e:
        return {'ok': False, 'changes': [], 'cmd': cmd,
                'error': '这个 pip 不支持 --report（%s）' % str(e)[:60]}

    changes = []
    for it in d.get('install', []):
        m = it.get('metadata') or {}
        name = (m.get('name') or '').lower()
        changes.append({'name': name, 'to': m.get('version') or '',
                        'from': local_version(name)})
    return {'ok': True, 'changes': changes, 'error': '', 'cmd': cmd}


def _index_args(picked):
    """torch 要指定通道的源，mineru 走默认 PyPI。"""
    if 'torch' not in picked and 'torchvision' not in picked:
        return []
    try:
        import torchdep
        tag = torchdep.pick_channel(torchdep.current_driver())[0]
    except Exception:
        tag = 'cu128'
    return ['--index-url', 'https://download.pytorch.org/whl/%s/' % tag]


def download(picked, targets=None, on_log=None, on_progress=None):
    r"""只下不装。用户可以继续转 PDF，全程不打扰。

    返回 {ok, error, cmd}。下好的 wheel 留在 CACHE 里，等重启时装。
    """
    picked = pair_up(picked)      # torch / torchvision 必须同进同退
    if not picked:
        return {'ok': False, 'error': '没选要升级的包', 'cmd': ''}

    paths.ensure(CACHE)
    cfile = os.path.join(CACHE, 'constraints.txt')
    io.open(cfile, 'w', encoding='utf-8').write(constraints_for(picked))

    spec = []
    for p in picked:
        t = (targets or {}).get(p)
        spec.append('%s==%s' % (p, t) if t else p)

    # 🔴 **开工就写一次状态，别等下完才写。**
    #
    #    2026-09-07 小蔡真实撞上：两个 wheel 都落盘了、upgrade_state.json
    #    却根本不存在 —— pip 把文件写完、这个函数还没走到底下那句
    #    `_write_state({'phase': 'downloaded'})`，进程就被杀了（关软件时
    #    before-quit 里 killTree 连整棵进程树一起砍）。于是 2.5 GB 成了
    #    软件不认识的孤儿，重启后看不到「立即重启」，再点检查又说要重下。
    #
    #    先落一个 downloading，pending() 看到它会去数包齐没齐 —— 齐了
    #    就照样能装，不让用户白下一次。
    _write_state({'phase': 'downloading', 'picked': picked,
                  'targets': targets or {},
                  'time': time.strftime('%Y-%m-%d %H:%M:%S')})

    argv = ['download', '-d', CACHE, '-c', cfile] + spec + _index_args(picked)
    # 🔴 **分子分母都来自 pip 自己吐的字节数，不新写估算常量。**
    #    「下完停在 92%」那次事故就是估算常量惹的（照着 pip 打印的十进制
    #    MB 当 MiB 换算，分母比真实大 8.8%）。ProgressAcc 的分母是「已见
    #    过的包大小之和」，跟分子同源，而且只增不减。
    #    floor 给 0：升级下的是哪几个包不一定，没有合适的兜底值，
    #    直接让已见之和接管。
    import torchdep
    _acc = torchdep.ProgressAcc(floor=0)

    def _pg(cur, tot):
        if on_progress:
            try:
                on_progress(_acc.feed(cur, tot), _acc.total())
            except Exception:
                pass

    argv = list(argv) + ['--progress-bar', 'raw']
    rc, out = _pip(argv, timeout=7200, on_log=on_log, on_progress=_pg)
    cmd = 'pip ' + ' '.join(argv)
    if rc != 0:
        # 🔴 **摘要要把进度行滤掉。** 加了 --progress-bar raw 之后，2.7 GB
        #    会吐几千行 `Progress N of M`。pip 通常把 ERROR 打在最后，可下载
        #    中途被掐（超时、连接重置）时，最后三行很可能就是三行进度数字，
        #    用户拿到的报错摘要变成三个没意义的数字对。
        import torchdep
        tail = [x for x in out.strip().splitlines()
                if x.strip() and not torchdep.parse_progress(x)][-3:]
        return {'ok': False, 'cmd': cmd, 'error': '  '.join(tail)[:300]}

    _write_state({'phase': 'downloaded', 'picked': picked,
                  'targets': targets or {},
                  'time': time.strftime('%Y-%m-%d %H:%M:%S')})
    return {'ok': True, 'error': '', 'cmd': cmd}


def _belongs(name, pkg):
    r"""site-packages 里这个条目是不是属于这个包。

    一个包在 site-packages 里摊成两样东西：本体目录（永远叫 `torch`，
    **不带版本号**）和元数据目录（`torch-2.14.0+cu126.dist-info`，
    **带版本号**）。所以判据是「名字相等，或者以 `包名-` 开头」。

    `torchvision` 既不等于 `torch`、也不以 `torch-` 开头，`torchgen`
    同理 —— 不会被误伤。
    """
    low = name.lower()
    base = pkg.lower().replace('-', '_')
    return low == base or low.startswith(base + '-')


def _backup_one(pkg, dest):
    r"""备份一个包的目录和它的 dist-info。返回备份了几个文件。

    先试硬链接（瞬间，不占额外空间），跨盘之类失败了退回真拷贝。
    """
    site = _site_dir()
    if not site:
        return 0
    n = 0
    for name in os.listdir(site):
        if not _belongs(name, pkg):
            continue
        src = os.path.join(site, name)
        dst = os.path.join(dest, name)
        if os.path.isdir(src):
            for dp, _dn, fns in os.walk(src):
                rel = os.path.relpath(dp, src)
                out = os.path.join(dst, rel) if rel != '.' else dst
                paths.ensure(out)
                for fn in fns:
                    s = os.path.join(dp, fn)
                    d = os.path.join(out, fn)
                    try:
                        os.link(s, d)
                    except OSError:
                        try:
                            shutil.copy2(s, d)
                        except OSError:
                            continue
                    n += 1
        else:
            paths.ensure(dest)
            try:
                os.link(src, dst)
            except OSError:
                try:
                    shutil.copy2(src, dst)
                except OSError:
                    continue
            n += 1
    return n


def backup(picked):
    """升级前备份。返回 {ok, dir, files, error}。"""
    picked = [p for p in (picked or ()) if p in ALLOWED]
    stamp = time.strftime('%Y%m%d_%H%M%S')
    dest = os.path.join(BACKUP, stamp)
    paths.ensure(dest)
    total = 0
    for pkg in picked:
        total += _backup_one(pkg, dest)
    meta = {'time': stamp, 'picked': picked,
            'versions': {p: local_version(p) for p in picked}}
    try:
        io.open(os.path.join(dest, 'backup.json'), 'w',
                encoding='utf-8').write(json.dumps(meta, ensure_ascii=False,
                                                   indent=2))
    except Exception:
        pass
    # 备完顺手收拾旧的。**这一步失手不能影响「这次备份成功了」这个结论** ——
    # 不然会变成「因为清理旧的失败，导致升级不敢往下装」，本末倒置。
    if total > 0:
        try:
            prune_dup_backups()
        except Exception:
            pass
    return {'ok': total > 0, 'dir': dest, 'files': total,
            'error': '' if total else '没备份到任何文件'}


def install(on_log=None):
    r"""装下好的那批。**重启时调**，此时没有转换在跑。

    `--no-index` 完全不联网，只用 CACHE 里下好的 wheel。
    """
    st = read_state()
    if not st or st.get('phase') not in ('downloaded', 'installing'):
        return {'ok': False, 'error': '没有待安装的升级'}
    picked = st.get('picked') or []
    if not picked:
        return {'ok': False, 'error': '状态文件里没记要装什么'}

    b = backup(picked)
    # 🔴 **备份没做成就别往下装。**
    #
    #    这个模块整套事务设计（见文件开头）都建立在「装之前先备份」上：
    #    装到一半断电 → 下次开机读到 phase=installing → 无条件回滚。
    #    而回滚是「照着备份目录里有什么，就把 site-packages 里对应的
    #    删掉再拷回来」—— 备份是空的，回滚就什么也做不了，那时候环境
    #    已经被 pip 动过了，回不去。
    #
    #    原来这里不看 b['ok'] 就继续（2026-09-05 复查发现）。备份失败
    #    概率确实很低（硬链接同盘必成、_site_dir 有 sysconfig 兜底），
    #    但「概率低」不是「不会发生」，而这一步失手的代价是环境废掉。
    #    此时状态仍是 downloaded，用户下次还能重试安装，不丢东西。
    if not b.get('ok'):
        return {'ok': False,
                'error': '升级前的备份没做成（%s），没有往下装 —— '
                         '没有备份的话，装到一半出问题就回不去了。'
                         % (b.get('error') or '原因不明')}

    st = dict(st, phase='installing', backup=b.get('dir', ''),
              backup_files=b.get('files', 0))
    _write_state(st)

    cfile = os.path.join(CACHE, 'constraints.txt')
    argv = ['install', '--no-index', '--find-links', CACHE, '--upgrade']
    if os.path.isfile(cfile):
        argv += ['-c', cfile]
    targets = st.get('targets') or {}
    for p in picked:
        t = targets.get(p)
        argv.append('%s==%s' % (p, t) if t else p)

    rc, out = _pip(argv, timeout=3600, on_log=on_log)
    if rc != 0:
        tail = [x for x in out.strip().splitlines() if x.strip()][-3:]
        r = rollback()
        return {'ok': False, 'rolled_back': r.get('ok'),
                'error': '装失败：%s' % ('  '.join(tail)[:250])}

    _write_state({'phase': 'done', 'picked': picked,
                  'backup': b.get('dir', ''),
                  'time': time.strftime('%Y-%m-%d %H:%M:%S')})
    return {'ok': True, 'error': '', 'backup': b.get('dir', '')}


def rollback(backup_dir=''):
    r"""回滚。**无条件** —— 不检查坏没坏、不判断断在哪。

    现场多乱都无所谓：把 site-packages 里那几个包整个删掉，从备份原样
    拷回去。得到的一定是升级前那个能用的版本。

    这个动作重复做多少次结果都一样（备份还在原地），所以回滚本身再
    断电也不怕。
    """
    st = read_state() or {}
    d = backup_dir or st.get('backup') or ''
    if not d or not os.path.isdir(d):
        return {'ok': False, 'error': '找不到备份目录'}

    site = _site_dir()
    if not site:
        return {'ok': False, 'error': '找不到 site-packages'}

    # 这次退的是哪几个包 —— **先信备份自己记的**。用户主动点「退回」时，
    # 状态文件多半是 `phase=done` 甚至根本没有，里面的 picked 指的也是
    # 上一次升级、未必是这份备份；而 backup.json 是做这份备份时当场写的。
    picked = []
    try:
        with io.open(os.path.join(d, 'backup.json'), encoding='utf-8') as f:
            picked = json.load(f).get('picked') or []
    except Exception:
        pass
    if not picked:
        picked = st.get('picked') or []

    # ① 先把现场删干净
    #
    # 🔴 **不能只照着备份里有哪些名字删**（2026-09-08 查出来的）。
    #
    #    一个包在 site-packages 里摊成两样：本体目录名**不带版本号**
    #    （永远叫 `torch`），元数据目录名**带版本号**。备份里那份叫
    #    `torch-2.11.0+cu128.dist-info`，而现场那份叫
    #    `torch-2.14.0+cu126.dist-info` —— 名字对不上，于是**新版本的
    #    元数据一个字都没被碰过**，回滚完两份并排躺着。
    #
    #    后果不是多占几十 KB：`local_version()` 走 importlib.metadata，
    #    两份元数据并存时它返回哪一个**是不确定的**。2026-09-08 实测：
    #    装的明明是 1.17，问出来是 1.16，连 pip 自己收尾那行都打错了。
    #    而「检查更新」就靠这个数去跟服务器比 —— 读错就可能明明能升却
    #    说「已是最新」。
    #
    #    小蔡的备份目录里能看到这个过程的脚印：09-07 14:26 那份干净，
    #    14:57 那份多了一张 torch-2.14 的孤儿，09-08 那份又多了一张
    #    torchvision-0.29 的 —— **每回滚一次多一张**。
    #
    #    所以在原来那套之外**再扫一遍**：凡是属于这几个包的，不管叫什么
    #    名字全删掉。**只加不减** —— 原来能删掉的照样删，这一遍只多清
    #    掉名字对不上的那些。
    doomed = set(n for n in os.listdir(d) if n != 'backup.json')
    for name in os.listdir(site):
        if any(_belongs(name, p) for p in picked):
            doomed.add(name)
    for name in doomed:
        live = os.path.join(site, name)
        try:
            if os.path.isdir(live):
                shutil.rmtree(live, ignore_errors=True)
            elif os.path.isfile(live):
                os.remove(live)
        except OSError:
            pass
    # ② 再从备份拷回去
    n = 0
    for name in os.listdir(d):
        if name == 'backup.json':
            continue
        src = os.path.join(d, name)
        dst = os.path.join(site, name)
        try:
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
            n += 1
        except OSError:
            continue

    clear_state()
    return {'ok': n > 0, 'restored': n, 'error': '' if n else '一个都没恢复',
            'picked': picked}


def mark_downloaded(picked, targets=None):
    r"""把「下好了、可以装」这个记录写回去。写成了返回 True。

    用在**用户主动退回之后**：`rollback()` 最后一步 `clear_state()`，
    于是哪怕新版的 wheel 完好躺在 CACHE 里，界面上也不给入口 —— 用户
    得重新点一次「检查更新」，走一遍完整流程才回得去（pip 认已经下好
    的文件、其实很快，但界面上根本没有这条路）。

    小蔡 2026-09-08：「以前我下载过的升级包 2.14.0 应该依然存在，
    那我可以选择升级回去。」

    🔴 **只给「用户主动退回」这一条路用。** `install()` 装失败时也会调
       `rollback()`，那条路上绝不能写这个记录 —— 会变成
       「装 → 失败 → 回滚 → 开机提示可以装 → 装 → 失败」的死循环。
       所以调用点在 `/api/upgrade/rollback` 那个接口里，不在这个模块内。

    包不齐就不写：`install()` 带 `--no-index` 找不到 wheel 会装失败，
    还要再回滚一次，用户白等一场且看不懂。
    """
    picked = [p for p in (picked or ()) if p in ALLOWED]
    if not picked or missing_wheels(picked):
        return False
    _write_state({'phase': 'downloaded', 'picked': picked,
                  'targets': targets or {},
                  'time': time.strftime('%Y-%m-%d %H:%M:%S')})
    return True


def prune_backups(keep=1):
    r"""删掉旧的升级备份，**默认留最新一份**。返回 {removed, freed, why}。

    这是**用户在环境检测页手动清**走的那条路（`maint.clean`）。自动那
    条在 `prune_dup_backups`（备份完按版本去重）和
    `drop_backups_of_current`（开机收掉跟当前版本一样的那份）。

    🔴 `install()` 装之前会把 site-packages 里那几个包整份备份下来，
       **装成功也不删那一份** —— 回滚要靠它。代价是一次 4 GB 量级
       （torch 整份拷贝）：2026-09-07 小蔡机器上两份就 8.26 GB、
       28092 个文件，而 `list_backups()` 早就写好、接口也有，
       **界面上却没有这一项、也没有任何地方能删**，8 GB 就那么躺着。

    **为什么默认留一份而不是全清**：多留 4 GB，换一次「装完发现不对还能
    退回去」的机会。想全清传 `keep=0`。

    🔴 **phase=installing 时一份都不删。** 那意味着上次装到一半断了，
       下次开机要靠备份回滚 —— 这时候删备份等于把回头路砍了。
    """
    st = read_state() or {}
    if st.get('phase') == 'installing':
        return {'removed': 0, 'freed': 0,
                'why': '上次装到一半断了，回滚还要用这些备份，'
                       '一份都没删。等它装完或者回滚完再来。'}

    rows = list_backups()          # 已经按时间倒序（新的在前）
    removed, freed = _rm_backups(rows[max(int(keep), 0):])
    return {'removed': removed, 'freed': freed, 'why': ''}


def _rm_backups(rows):
    """删掉这几份备份。返回 (删了几份, 释放多少字节)。"""
    removed = freed = 0
    for r in rows:
        d = r.get('dir') or os.path.join(BACKUP, r.get('name', ''))
        if not os.path.isdir(d):
            continue
        try:
            shutil.rmtree(d)
        except OSError:
            continue
        removed += 1
        freed += r.get('size', 0)
    return removed, freed


def prune_dup_backups(keep_versions=2):
    r"""**备份完顺手收拾旧的**（`backup()` 末尾调）。两条规则：

      · 同一个版本只留一份 —— 留最新的那份
      · 不同版本最多留 `keep_versions` 个 —— 留最新的几个

    🔴 2026-09-08 小蔡定的，**推翻了 09-05 「我们不自动清理」那条**。
       原来的规矩是「列在环境检测里让用户自己清」，代价是没人清就一直
       堆：他试了三次升级，硬盘上躺着三份**内容几乎一样**的 2.11.0，
       12.4 GB（逐文件按硬链接去重量过，是真占这么多）。

    **为什么按版本去重而不是只留最新一份**：留着不同版本才有「退回上
    一版」的意义；同一个版本留三份则纯粹是同一堆文件存三遍。而
    `keep_versions` 是防另一头 —— 一路 2.11→2.14→2.15→2.16 升上去的
    人从不回退，按「每版本一份」一份都不会删，照样堆到 12 GB。

    🔴 `phase=installing` 时一份都不删 —— 跟 `prune_backups` 同一条
       保护：那意味着上次装到一半断了，回滚要靠备份。
    """
    st = read_state() or {}
    if st.get('phase') == 'installing':
        return {'removed': 0, 'freed': 0,
                'why': '上次装到一半断了，回滚还要用这些备份，一份都没删。'}

    seen = []                      # 见过哪些版本组合，顺序即新旧
    doomed = []
    # 判断该不该删只看版本号，不需要知道多大 —— 别去量那五万个文件。
    for r in list_backups(with_size=False):   # 已按时间倒序（新的在前）
        key = json.dumps(r.get('versions') or {}, sort_keys=True)
        if key in seen or len(seen) >= max(int(keep_versions), 1):
            doomed.append(r)
        else:
            seen.append(key)
    removed, freed = _rm_backups(doomed)
    return {'removed': removed, 'freed': freed, 'why': ''}


def _meta_dirty(pkgs):
    r"""这几个包里，有没有谁在 site-packages 里躺着**不止一份**元数据。

    有的话说明环境是脏的（历史上回滚留下的孤儿），这时候
    `local_version()` 读出来的版本号本身就不可信 —— 谁也不知道
    importlib.metadata 会挑哪一份。读不到 site-packages 也当脏的：
    宁可少删，不可误删。
    """
    site = _site_dir()
    if not site:
        return True
    try:
        names = os.listdir(site)
    except OSError:
        return True
    for pkg in pkgs:
        n = len([x for x in names
                 if _belongs(x, pkg) and x.lower().endswith('.dist-info')])
        if n > 1:
            return True
    return False


def drop_backups_of_current():
    r"""删掉「版本跟当前环境**完全一样**」的备份。返回 {removed, freed, why}。

    小蔡 2026-09-08 定的：退回并重启之后，那份备份就该从列表里消失 ——
    我已经在这个版本上了，再「退回」到它没有意义；真要再升级，
    `install()` 装之前会重新备一份当前版本，不缺。

    🔴 **时机是重启之后，不是点退回那一刻。** 退回是拷 4 GB 文件，中途
       可能断；那时环境半新半旧，备份还得留着救命。重启起来一比版本，
       对上了才说明真退成功了。所以这个函数挂在开机那一问上。

    🔴 **只能挂在 `/api/upgrade/pending` 那个接口里，不能写进
       `pending()` 函数**：生成诊断文件时也会调那个函数，那就成了
       「点一下生成诊断，顺手删了 4 GB」。生成诊断不该有任何副作用。

    两道误删保护：

      · 备份记的版本要**每一项都对上**才删；没记版本的一份都不碰
      · 环境脏（同一个包躺着不止一份元数据）时一份都不删 —— 那时候
        版本号本身就读不准，见 `_meta_dirty`
    """
    st = read_state() or {}
    if st.get('phase') == 'installing':
        return {'removed': 0, 'freed': 0,
                'why': '上次装到一半断了，回滚还要用这些备份，一份都没删。'}

    doomed = []
    for r in list_backups(with_size=False):
        vs = r.get('versions') or {}
        if not vs or _meta_dirty(vs.keys()):
            continue
        if all(local_version(p) == v for p, v in vs.items()):
            doomed.append(r)
    removed, freed = _rm_backups(doomed)
    return {'removed': removed, 'freed': freed, 'why': ''}


def list_backups(with_size=True):
    r"""有哪些备份。给环境检测那一屏列出来让用户清。

    🔴 `with_size=False` 时 `size` 一律是 0，**不去量每份多大**。

       量大小是这里最贵的一步：要 `os.walk` 遍历整份备份、对每个文件
       `getsize` 一次 —— 一份 torch 备份就是 14000+ 个文件，四份就是
       五万多次系统调用，而且多半是冷读（刚写完 4 GB，缓存全被冲掉）。

       而自动清理那两条路（`prune_dup_backups` /
       `drop_backups_of_current`）判断该不该删**只看版本号**，压根不需
       要知道多大 —— 它们返回的 `freed` 没有任何调用方在看。所以那两
       条传 `False`，把这五万次遍历整个省掉。

       真需要大小的只有三处：环境检测页那一屏、`maint.scan_logs` 的占
       用统计、诊断文件 —— 那三处照旧走默认的 `True`。
    """
    out = []
    if not os.path.isdir(BACKUP):
        return out
    for name in sorted(os.listdir(BACKUP), reverse=True):
        d = os.path.join(BACKUP, name)
        if not os.path.isdir(d):
            continue
        size = 0
        if with_size:
            for dp, _dn, fns in os.walk(d):
                for fn in fns:
                    try:
                        size += os.path.getsize(os.path.join(dp, fn))
                    except OSError:
                        continue
        meta = {}
        try:
            with io.open(os.path.join(d, 'backup.json'), encoding='utf-8') as f:
                meta = json.load(f)
        except Exception:
            pass
        out.append({'name': name, 'dir': d, 'size': size,
                    'picked': meta.get('picked', []),
                    'versions': meta.get('versions', {})})
    return out


def pending():
    r"""开机时调：有没有没做完的升级，该怎么办。

    返回 {action, ...}：

      'none'      没事，正常进主界面
      'install'   下好了没装 —— 重启时该装了
      'rollback'  **装到一半断电了** —— 无条件回滚，不问用户

    🔴 下载中断电不算事（环境没坏，旧的还能用），**正常进主界面**。
       只有装到一半才必须处理 —— 那时 import torch 可能已经失败，
       让用户进主界面点转换只会得到一个看不懂的报错。
    """
    st = read_state()
    if not st:
        return {'action': 'none'}
    phase = st.get('phase')
    if phase == 'installing':
        return {'action': 'rollback', 'backup': st.get('backup', ''),
                'picked': st.get('picked', [])}
    if phase == 'downloading':
        # 下载中断电**本身**不算事（环境没坏，旧的还能用）。但要分清两种：
        #   · 包已经齐了 —— 那是「下完了没来得及写状态」，照样能装
        #   · 包还残着   —— 那才是真的下到一半，让他重下，别装到一半失败
        picked = st.get('picked') or []
        if picked and not missing_wheels(picked):
            return {'action': 'install', 'picked': picked}
        return {'action': 'none'}
    if phase == 'downloaded':
        picked = st.get('picked') or []
        missing = missing_wheels(picked)
        if missing:
            # 🔴 状态说下好了，硬盘上却没有 —— **最常见的原因是用户点了
            #    环境检测页的「清理转换临时文件」**：CACHE 就住在
            #    paths.TMP 底下，那一下把 2.5 GB 一起带走了，而状态文件
            #    在 logs/ 下毫发无损。
            #    这时候绝不能说「能装」：pip 带着 --no-index 找不到 wheel，
            #    装失败还要回滚一次，用户白等一场且看不懂。
            return {'action': 'redownload', 'picked': picked,
                    'missing': missing}
        return {'action': 'install', 'picked': picked}
    return {'action': 'none'}


def _norm(name):
    """包名规范化，跟 pip 落盘时一个规矩：小写，- . 都当 _。"""
    return re.sub(r'[-_.]+', '_', str(name or '').strip().lower())


def missing_wheels(picked):
    """这几个包里，哪些在 CACHE 里找不到 wheel。返回缺的那些名字。"""
    try:
        files = [f for f in os.listdir(CACHE) if f.lower().endswith('.whl')]
    except OSError:
        return list(picked or ())          # 目录都没了，等于全缺
    have = set()
    for f in files:
        # wheel 文件名是 `名字-版本-...whl`，名字那段按上面的规矩规范化过
        have.add(_norm(f.split('-')[0]))
    return [p for p in (picked or ()) if _norm(p) not in have]
