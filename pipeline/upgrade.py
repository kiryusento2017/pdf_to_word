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


def _pip(argv, timeout=1800, on_log=None):
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
    picked = [p for p in (picked or ()) if p in ALLOWED]
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
    picked = [p for p in (picked or ()) if p in ALLOWED]
    if not picked:
        return {'ok': False, 'error': '没选要升级的包', 'cmd': ''}

    paths.ensure(CACHE)
    cfile = os.path.join(CACHE, 'constraints.txt')
    io.open(cfile, 'w', encoding='utf-8').write(constraints_for(picked))

    spec = []
    for p in picked:
        t = (targets or {}).get(p)
        spec.append('%s==%s' % (p, t) if t else p)

    argv = ['download', '-d', CACHE, '-c', cfile] + spec + _index_args(picked)
    rc, out = _pip(argv, timeout=7200, on_log=on_log)
    cmd = 'pip ' + ' '.join(argv)
    if rc != 0:
        tail = [x for x in out.strip().splitlines() if x.strip()][-3:]
        return {'ok': False, 'cmd': cmd, 'error': '  '.join(tail)[:300]}

    _write_state({'phase': 'downloaded', 'picked': picked,
                  'targets': targets or {},
                  'time': time.strftime('%Y-%m-%d %H:%M:%S')})
    return {'ok': True, 'error': '', 'cmd': cmd}


def _backup_one(pkg, dest):
    r"""备份一个包的目录和它的 dist-info。返回备份了几个文件。

    先试硬链接（瞬间，不占额外空间），跨盘之类失败了退回真拷贝。
    """
    site = _site_dir()
    if not site:
        return 0
    n = 0
    for name in os.listdir(site):
        low = name.lower()
        base = pkg.lower().replace('-', '_')
        if low != base and not low.startswith(base + '-'):
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

    picked = st.get('picked') or []
    # ① 先把现场删干净
    for name in os.listdir(d):
        if name == 'backup.json':
            continue
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


def list_backups():
    """有哪些备份。给环境检测那一屏列出来让用户清。"""
    out = []
    if not os.path.isdir(BACKUP):
        return out
    for name in sorted(os.listdir(BACKUP), reverse=True):
        d = os.path.join(BACKUP, name)
        if not os.path.isdir(d):
            continue
        size = 0
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
    if phase == 'downloaded':
        return {'action': 'install', 'picked': st.get('picked', [])}
    return {'action': 'none'}
