# -*- coding: utf-8 -*-
r"""占用扫描与清理。给「关于 → 环境检测」那一屏用。

## 为什么要有这个

用户装完之后 C 盘会莫名其妙少几个 G，而他**永远发现不了是谁干的** ——
pip 把下载过的 wheel 全存在 `%LOCALAPPDATA%\pip\cache` 里，那地方有
三层遮挡：

    · AppData 是隐藏文件夹，资源管理器默认不显示
    · 文件名是哈希，140d28266e23cac4… 看不出是什么
    · 扩展名是 .body 不是 .whl，双击打不开，搜「whl」也搜不到

实测小蔡机器上那个目录 **4125 MB / 1462 个文件**，最大的一个 2.6 GB
正是 torch 的 wheel。

## 为什么不把缓存改到安装目录

2026-09-05 小蔡定：**留在 C 盘**。理由是「万一别人用得上」，而实测
证明这个判断是对的 —— 扫出来的东西里有 `pyside6_addons` 160 MB、
`pyside6_essentials` 73 MB（他自己金石工作台那个 Qt 项目的），还有
`torch-2.13.0+cpu`（别的项目的）。

**pip 缓存是按 Windows 用户走的，不是按 Python 环境走的** —— 开发
环境的 .venv、发行版的 embeddable Python、他别的项目，全往同一个
目录里塞。改 PIP_CACHE_DIR 等于把公共缓存拆成私有的，反而更费流量。

代价是 README 里「不留 AppData」那句要改（已改），补偿是这个模块 ——
**看得到，才谈得上删不删**。

## 🔴 必须列明细，不能只给一个总数加清理按钮

缓存里混着别的程序下的包。一键清理会误伤，所以每一项都要标清楚
「是不是本软件的」，让用户自己决定。判据是包名 + 版本都对得上：
`torch-2.11.0+cu128` 是我们装的，`torch-2.13.0+cpu` 显然不是。

## 缓存目录必须问 pip，不能硬编码

实测 `PIP_CACHE_DIR=D:/tmp/x pip cache dir` 的输出立刻就变了。用户
可能通过环境变量、pip.ini、命令行参数改过位置 —— 硬编码
`%LOCALAPPDATA%\pip\cache` 的话，遇到改过的用户会显示「0 字节」，
而他 C 盘明明被占着。**那种假数据比不显示更糟。**
"""
import io
import json
import os
import subprocess
import tempfile
import time
import zipfile

import models
import paths

# 本软件会装的 pip 包。用来判断缓存里哪个 wheel 是我们下的。
# 跟 tools/build_release.py 的 DEPS 保持一致，外加 torch 那两个
# （它们不在 DEPS 里 —— 打包时故意卸掉，首启才装）。
OUR_PACKAGES = frozenset([
    'pymupdf', 'lxml', 'fastapi', 'uvicorn', 'python-docx', 'python_docx',
    'mineru', 'torch', 'torchvision',
    # mineru 拖下来的大件，也算我们的
    'onnxruntime', 'onnxruntime-gpu', 'onnxruntime_gpu',
    'transformers', 'modelscope', 'opencv-python', 'opencv_python',
    'huggingface-hub', 'huggingface_hub', 'tokenizers', 'magika',
])

# 小于这个大小的缓存条目不单独列出来（几百个几十 KB 的元数据文件，
# 列出来只会淹没真正值得删的那几个）。仍然计入总量。
MIN_LIST_BYTES = 1024 * 1024

# TEMP 里那些 pip 残骸，最后动过的时间在这个小时数以内就不碰 ——
# 可能是正在跑的安装用着的。下 2.7 GB 的 torch 本身要几十分钟，
# 门槛太短会撞上正在解包的目录。
# 扫描时的年龄底线。**这才是防误删的主防线**（10 分钟）。
#
# 🔴 原来是 6 小时，太长：刚产生的残骸一个都看不见 —— 2026-09-07 小蔡
#    机器上 92 个、519.7 MB，界面显示 0，因为都是刚关软件时留下的。
#
# 🔴 **一度以为可以换成 `can_take()`（改名试探）当主判据，实测证明不行。**
#    真跑一次 pip download 探测：新建的 5 个临时目录里，**4 个在 pip
#    正用着的时候被 can_take 判成「没人用、可以删」** —— 因为 pip 大部分
#    时间只是「目录建着」，文件写完就关，目录本身没被任何进程打开，
#    改名当然成功。「能改名」只等于「此刻没有文件被打开」，
#    **不等于「没人在用」**。
#
#    同一次实测里，那 5 个目录一个都没进清理清单 —— 挡住它们的正是
#    这道年龄底线（刚建的，最新文件时间就是此刻）。
#
#    所以分工是：**年龄底线挡「正在用的」，can_take 挡「删到一半留下
#    残缺目录」**（见它自己的注释）。10 分钟是给「pip 卡住不动」买的
#    安全余量：真卡了 30 分钟，那次下载多半也废了。（小蔡定 30 分钟）
TEMP_PIP_MIN_AGE_H = 30.0 / 60.0


def _dir_size(path):
    """一个目录有多大（字节）。算不出来的跳过，不抛异常。"""
    total = 0
    if not os.path.isdir(path):
        return 0
    for dp, _dn, fns in os.walk(path):
        for fn in fns:
            try:
                total += os.path.getsize(os.path.join(dp, fn))
            except OSError:
                continue
    return total


def pip_cache_dir():
    r"""pip 的缓存目录在哪。取不到返回空串。

    **必须问 pip 自己**，理由见模块开头。用发行版那个 python.exe 问，
    不是开发环境的 —— 两者的配置可能不一样。
    """
    try:
        p = subprocess.run(
            [paths.python_exe(), '-m', 'pip', 'cache', 'dir'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=30, env=paths.utf8_env(), cwd=paths.ROOT)
    except Exception:
        return ''
    if p.returncode != 0:
        return ''
    out = (p.stdout or b'').decode('utf-8', 'replace').strip()
    # pip 可能在前面打警告，取最后一行像路径的
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line and (':' in line[:3] or line.startswith('/')):
            return line
    return ''


def _wheel_name(path):
    """从一个缓存文件里读出它是哪个包。读不出来返回空串。

    缓存文件是原样存下来的 wheel（zip），里面有 `<包名>-<版本>.dist-info/`。
    只读 zip 的目录，不解压 —— 实测扫 731 个文件是秒级的。
    """
    try:
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                i = n.find('.dist-info/')
                if i > 0:
                    return n[:i]
    except Exception:
        pass
    return ''


def _is_ours(wheel_name):
    """这个 wheel 是不是本软件装的。

    只看包名，不比版本 —— 版本比对会误判：用户可能装过我们的老版本，
    那也是我们的。名字对不上的（pyside6 那类）才是别人的。
    """
    if not wheel_name:
        return False
    base = wheel_name.split('-')[0].lower().replace('_', '-')
    return base in OUR_PACKAGES or base.replace('-', '_') in OUR_PACKAGES


def scan_pip_cache(detail=True):
    r"""扫 pip 缓存。返回 {ok, dir, total, count, items, error}。

    items 每项：{name, size, ours}，按大小倒序。detail=False 时只算
    总量不解析包名（快，用在只要总数的场合）。
    """
    out = {'ok': False, 'dir': '', 'total': 0, 'count': 0,
           'items': [], 'ours_total': 0, 'error': ''}
    d = pip_cache_dir()
    if not d:
        out['error'] = '问不出 pip 的缓存目录（pip 可能没装好）'
        return out
    out['dir'] = d
    if not os.path.isdir(d):
        # 目录不存在 = 从没下载过东西，不是错误
        out['ok'] = True
        return out

    rows = []
    total = 0
    count = 0
    for dp, _dn, fns in os.walk(d):
        for fn in fns:
            p = os.path.join(dp, fn)
            try:
                sz = os.path.getsize(p)
            except OSError:
                continue
            total += sz
            count += 1
            if not detail or sz < MIN_LIST_BYTES:
                continue
            if not fn.endswith('.body'):
                continue
            name = _wheel_name(p)
            rows.append({'name': name or '(不是 wheel)', 'size': sz,
                         'ours': _is_ours(name), 'path': p})

    rows.sort(key=lambda x: -x['size'])
    out['ok'] = True
    out['total'] = total
    out['count'] = count
    out['items'] = rows
    out['ours_total'] = sum(r['size'] for r in rows if r['ours'])
    return out


def can_take(d):
    r"""此刻有没有文件被打开着？没有才返回 True。

    判据是「能不能改名」—— Windows 上只要目录里有文件被打开着、正在被
    写、或者被某个进程当作工作目录，改名就会失败。

    🔴 **它不能当「有没有人在用」的判据，别拿它当主防线。** 2026-09-07
       真跑一次 pip download 实测：新建的 5 个临时目录，**4 个在 pip
       正用着的时候被这个函数判成「可以删」** —— pip 大部分时间只是
       「目录建着」，文件写完就关，目录本身没被任何进程打开。
       **「能改名」只等于「此刻没有文件被打开」。**
       挡住「正在用的」是 TEMP_PIP_MIN_AGE_H 那道年龄底线。

    那它有什么用：**防止删出一个残缺目录**。不先问一句的话，删是一个
    文件一个文件试 —— 被打开的那个删不掉，而同目录里没被打开的其它文件
    **已经删掉了**。pip 正用着它，缺了文件那次安装多半直接失败，而用户
    只看到「清理完成」。

    🔴 **试探完必须把名字还回去。** 留在 .trylock 上的话，前缀对不上，
       下次扫描反而永远看不到它，成了真正的孤儿。
    """
    tmp = d + '.trylock'
    try:
        os.rename(d, tmp)
    except OSError:
        return False
    try:
        os.rename(tmp, d)
    except OSError:
        # 极罕见：改过去了却改不回来。目录还在（没丢东西），但名字变了。
        # 宁可返回 False —— 这一轮别动它，下一轮它已经不叫 pip- 了。
        return False
    return True


def _is_link(p):
    r"""这个路径是不是链接（软链接或目录联接）。

    🔴 **`os.path.islink()` 对 Windows 的目录联接（junction）返回 False**，
    别想当然。2026-09-06 实测：在临时目录下造一个指向别处的
    `pip-evil-junction`，`islink` 说不是链接，`os.walk` 直接走进去，
    照着 `clean()` 里那段 rm_tree 跑一遍，**外面那个文件真被删了，
    而且一个错都不报，报告「清理成功」**。

    判据用 `os.path.isjunction()`（3.12 新增）。两个环境实测都有：
    发行版自带的 python 和开发用的 .venv 都是 **3.12.10**。

    ⚠️ 取不到它的时候退回只判软链接，**那种环境下这个函数是不够用的**。
    别把下面这句读成「有兜底所以没事」：`temp_pip_dirs()` 的 realpath
    父目录校验只保护**顶层**那个候选目录，而 `_tree_stat()` 和
    `rm_tree_safe()` 在目录**内部**剪枝时也调这个函数 —— 那里没有任何
    兜底。所以真跑在 3.12 以下时，藏在残骸里面的联接会被跟进去。
    留这个分支只是为了不当场抛 AttributeError。
    """
    try:
        if os.path.islink(p):
            return True
    except OSError:
        return True          # 判不了就当是，宁可不删
    fn = getattr(os.path, 'isjunction', None)
    if fn is None:
        return False
    try:
        return bool(fn(p))
    except OSError:
        return True


def _tree_stat(d):
    r"""这个目录多大、里面最新的东西是什么时候动的。返回 (字节, mtime)。

    **不跟着链接走** —— `os.walk` 的 followlinks=False 只管软链接，
    对 junction 无效（见 `_is_link`），所以自己把链接子目录摘掉。

    🔴 **时间必须看里面的东西，不能只看目录自己的 mtime。**
    实测：往目录里**已有的**文件追加 100 KB，目录的 mtime **一动不动**
    —— 只有新建 / 删除条目才更新它。而 pip 下一个 2.7 GB 的 wheel 正是
    这个形状：创建文件那一刻目录时间更新，之后几十分钟都在往同一个文件
    里写，目录时间停在最开始。拿目录 mtime 当「多久没动过」的判据，
    会把**正在下载的目录**判成老残骸删掉。

    （2026-09-06 审查时实测发现，第一版就是只看目录 mtime。）

    用 `lstat` 不用 `stat`：万一里面有指向大文件的软链接，跟着算会把
    大小算成目标的，而我们删的是链接本身。
    """
    total = 0
    try:
        newest = os.path.getmtime(d)
    except OSError:
        # 🔴 问不出时间要当成「刚动过」，不能当成「很老」。0.0 是 1970 年，
        #    算出来就是「一直没动过」，调用方会拿去删。问不出来通常意味着
        #    目录正被占用或刚消失 —— 两种情况都该躲开。
        #    （方向搞反的判据比没有判据更危险：它会主动去删。）
        newest = time.time()
    for dp, dns, fns in os.walk(d):
        dns[:] = [n for n in dns if not _is_link(os.path.join(dp, n))]
        for n in dns:
            try:
                mt = os.lstat(os.path.join(dp, n)).st_mtime
            except OSError:
                continue
            if mt > newest:
                newest = mt
        for n in fns:
            try:
                st = os.lstat(os.path.join(dp, n))
            except OSError:
                continue
            total += st.st_size
            if st.st_mtime > newest:
                newest = st.st_mtime
    return total, newest


def temp_pip_dirs(min_age_h=TEMP_PIP_MIN_AGE_H):
    r"""系统临时目录里 pip 留下的残骸。返回 [{path, name, size, mtime}]。

    ## 这些是怎么来的

    pip 装一个大 wheel 时会在 `%TEMP%` 下开工作目录
    （`pip/_internal/utils/temp_dir.py:174` 的
    `mkdtemp(prefix=f"pip-{kind}-")`，kind 有 unpack / install /
    uninstall / build-env / req-build / modern-metadata 等 15 种，
    **全部以 `pip-` 开头**，所以按这一个前缀扫就够，不会漏）。

    正常跑完 pip 自己会清掉，**中断就不会** —— 进程被杀、断网、
    用户点取消、关机，那几 GB 就永久留下。而「删掉软件文件夹 =
    卸载干净」这个承诺覆盖不到 `%TEMP%`。

    2026-09-06 在开发机上扫出 45 个目录 1709.6 MB，最大的一个
    1640.2 MB 是 8-21 那次下到 62.5% 断掉的 torch wheel。

    ## 目录必须问系统要，不能硬编码

    跟 `pip_cache_dir()` 一个道理。实测同一台机器上 `%TEMP%` 环境变量
    给的是 8.3 短名（`C:\Users\KIRYUS~1\...`），系统 API 算出来的是长名
    （`C:\Users\kiryusento\...`）—— 同一个地方两种写法，**字符串不相等**。
    用户名不到 8 个字符的机器根本没有短名，还有人把临时目录改到别的盘。

    ## 🔴 三道防护

    1. `_is_link()` 挡住链接和目录联接 —— 顺着它删会爬到临时目录外面
    2. **父目录按 realpath 归一化后比对** —— 兜住 `_is_link` 漏掉的其它
       重解析点，短名/长名的问题也一并解决（两边 realpath 之后实测相等）
    3. **只列一层**找候选，实测 55585 项的临时目录列一层 0.06 秒。
       候选目录本身要走一遍 `_tree_stat`（判年龄必须看里面的文件，
       原因见那个函数）
    """
    root = tempfile.gettempdir()
    try:
        names = os.listdir(root)
    except OSError:
        return []
    real_root = os.path.realpath(root).lower()
    now = time.time()
    cut = float(min_age_h) * 3600.0
    rows = []
    for n in names:
        if not n.startswith('pip-'):
            continue
        p = os.path.join(root, n)
        # 顺序不能反：先排链接再 isdir —— isdir 会跟着链接走
        if _is_link(p) or not os.path.isdir(p):
            continue
        real = os.path.realpath(p)
        if os.path.dirname(real).lower() != real_root:
            continue
        size, newest = _tree_stat(real)
        if now - newest < cut:
            continue
        rows.append({'path': real, 'name': n,
                     'size': size, 'mtime': newest})
    rows.sort(key=lambda x: -x['size'])
    return rows


def scan_temp_pip(min_age_h=TEMP_PIP_MIN_AGE_H):
    """汇总 temp_pip_dirs()。返回 {dir, total, count, items}。"""
    rows = temp_pip_dirs(min_age_h)
    return {'dir': tempfile.gettempdir(),
            'total': sum(r['size'] for r in rows),
            'count': len(rows), 'items': rows}


# 下好的升级包住在这儿（跟 upgrade.CACHE 是同一个地方）。
#
# 🔴 它在 paths.TMP 底下，但**不能算进「转换临时文件」那一行** ——
#    2026-09-07 小蔡下好 2.5 GB 的 torch，界面上只显示一行「转换临时
#    文件」，点一下清理就全没了，而状态文件在 logs/ 下毫发无损、仍写着
#    「已下好」，于是重启后装不上还得重下。让用户看得见自己在删什么。
UPGRADE_CACHE = os.path.join(paths.TMP, 'upgrade_cache')


def scan_logs():
    """日志、转换临时文件、下好的升级包，各自多大。"""
    up = _dir_size(UPGRADE_CACHE)
    return {'logs': _dir_size(paths.LOGS),
            'tmp': max(_dir_size(paths.TMP) - up, 0),
            'upgrade_cache': up}


def scan():
    r"""全部占用。给环境检测那一屏用。

    返回 {ok, items: [...], pip: {...}}，items 每项是一类占用：
    {key, label, size, note, cleanable}
    """
    pip = scan_pip_cache()
    tmp_pip = scan_temp_pip()
    logs = scan_logs()
    models = paths.models_size()

    items = [
        {'key': 'pip_cache',
         'label': 'pip 下载缓存（C 盘，其他 Python 程序共用）',
         'size': pip.get('total', 0),
         'note': ('其中本软件的约 %d MB'
                  % (pip.get('ours_total', 0) // 1024 // 1024))
                 if pip.get('ours_total') else pip.get('error', ''),
         'cleanable': True},
        # 🔴 这一行即使是 0 也照样显示。这一屏存在的意义就是「看得见
        #    C 盘被谁占了」，只在有东西时才冒出来的行，用户不会知道
        #    软件替他看过这个地方。跟 pip 缓存那行一个待遇。
        {'key': 'temp_pip',
         'label': 'pip 安装残留（装到一半中断留下的）',
         'size': tmp_pip['total'],
         # 说明文字跟着判据走：现在拦的是「有没有人在用」，
         # 不是「多久没动过」（见 can_take）。
         'note': ('%d 个目录，正在用的会跳过' % tmp_pip['count'])
                 if tmp_pip['count'] else '没有',
         'cleanable': True},
        {'key': 'logs', 'label': '日志', 'size': logs['logs'],
         'note': '', 'cleanable': True},
        {'key': 'tmp', 'label': '转换临时文件', 'size': logs['tmp'],
         'note': '', 'cleanable': True},
        {'key': 'upgrade_cache', 'label': '下好的升级包（等重启安装）',
         'size': logs['upgrade_cache'],
         'note': '清了要重新下一次' if logs['upgrade_cache'] else '没有',
         'cleanable': True},
        {'key': 'models', 'label': '识别模型', 'size': models,
         'note': '清了要重下 4.6 GB，一般别动', 'cleanable': False},
    ]
    return {'ok': True, 'items': items, 'pip': pip}


def clean(keys=(), pip_paths=()):
    r"""清理。返回 {ok, freed, failed, error}。

    keys 是 scan() 里那些 key（不含 models —— 那个 cleanable=False，
    要清得用户在文件管理器里自己删，免得手滑点掉 4.6 GB）。
    pip_paths 是要删的缓存文件的具体路径（前端勾选的那几项）。

    🔴 **逐个删，失败的记下来，最后老实报。** 转换正在跑的时候
    某个文件可能被占用 —— Windows 上删不掉。那种情况必须说
    「清了 3.7 GB，1 个文件正在使用没删掉」，**不能假装全成功**。
    """
    freed = 0
    failed = []

    def rm_file(p):
        nonlocal freed
        try:
            sz = os.path.getsize(p)
        except OSError:
            return
        try:
            os.remove(p)
            freed += sz
        except OSError as e:
            failed.append('%s（%s）' % (os.path.basename(p), e.strerror or '删不掉'))

    def rm_tree(d, keep_root=True, keep=()):
        nonlocal freed
        if not os.path.isdir(d):
            return
        for dp, dns, fns in os.walk(d, topdown=False):
            for fn in fns:
                if fn in keep:
                    continue
                rm_file(os.path.join(dp, fn))
            for dn in dns:
                try:
                    os.rmdir(os.path.join(dp, dn))
                except OSError:
                    pass
        if not keep_root:
            try:
                os.rmdir(d)
            except OSError:
                pass

    def rm_tree_safe(d):
        r"""删一个 TEMP 残骸目录，**不跟着链接走**。

        🔴 **故意跟上面的 rm_tree 分开写，不是给它加参数。** 那个跑在
        `logs/` 和 `_tmp/` 上，是软件自己的目录，行为一个字都不能动。
        这个跑在 `%TEMP%` 上 —— 那是本机任何程序都能写的公共目录，
        谁都能往里放一个指向别处的目录联接。

        `os.walk` 的 followlinks=False 只管软链接，**对 Windows 的
        junction 无效**（`os.path.islink` 对它返回 False）。所以自己
        在 topdown 遍历里剪枝：链接目录只删链接本身（`os.rmdir` 删的是
        链接点，不碰目标），绝不往里钻。

        （剪枝必须在 topdown=True 时做 —— topdown=False 改 dirnames
          对遍历没有任何影响。）
        """
        if _is_link(d) or not os.path.isdir(d):
            return
        dirs = []
        files = []
        for dp, dns, fns in os.walk(d):
            subs = []
            for n in dns:
                p = os.path.join(dp, n)
                dirs.append(p)
                if not _is_link(p):
                    subs.append(n)
            dns[:] = subs
            for fn in fns:
                files.append(os.path.join(dp, fn))
        for p in files:
            rm_file(p)
        for p in sorted(dirs, key=len, reverse=True):
            try:
                os.rmdir(p)
            except OSError:
                pass
        try:
            os.rmdir(d)
        except OSError:
            pass
        # 🔴 删的是目录，报的却是文件名 —— 粒度对不上，用户看到
        #    「x.whl（拒绝访问）」不知道哪个目录还在。补一条目录级的。
        #    （rm_file 是 pip 缓存那支也在用的既有函数，不动它。）
        if os.path.isdir(d):
            failed.append('%s（里面有文件正被占用，整个目录没删掉）'
                          % os.path.basename(d))

    keys = set(keys or ())
    if 'logs' in keys:
        # 🔴 **这两个不是日志，是数据，清「日志」不能把它们一起带走。**
        #    runs.json 是 200 条转换历史（用户要靠它找回转过的文件、
        #    查当时的报错），models_size.json 是学到的模型总量（下载进度条
        #    的分母）。它们跟 last_run.json 一样住在 logs/ 下，而那一栏在
        #    界面上写的只是「日志」—— 用户点一下就没了，界面上一个字都没提。
        rm_tree(paths.LOGS, keep=(os.path.basename(RUNS), SIZE_FILE_NAME))
    if 'tmp' in keys:
        # 🔴 **绕开下好的升级包** —— 它虽然住在 TMP 底下，但界面上是
        #    单独一行，用户没勾就不能删（见 UPGRADE_CACHE 的注释）。
        try:
            names = os.listdir(paths.TMP)
        except OSError:
            names = []
        keep_at = os.path.abspath(UPGRADE_CACHE)
        for name in names:
            p = os.path.join(paths.TMP, name)
            if os.path.abspath(p) == keep_at:
                continue
            if os.path.isdir(p):
                rm_tree(p, keep_root=False)
            else:
                rm_file(p)
    if 'upgrade_cache' in keys:
        rm_tree(UPGRADE_CACHE, keep_root=False)
    if 'temp_pip' in keys:
        # 🔴 路径由后端自己列，**不接受前端传进来的**。temp_pip_dirs()
        #    里已经把前缀、链接、父目录、年龄四道判据全过了一遍，
        #    所以这里不用碰下面那段 pip_paths 的白名单校验。
        for _r in temp_pip_dirs():
            # 🔴 **先问一句「还有没有人在用」。**
            #
            #    不问的话，删是「一个文件一个文件试」：被打开的那个删不掉，
            #    而**同目录里没被打开的其它文件已经删掉了** —— 留下一个
            #    残缺的目录。pip 正在用它，缺了文件之后那次安装多半直接
            #    失败，用户什么都不知道，只看到「清理完成」。
            #
            #    can_take 靠「能不能改名」判断，比时间准：Windows 上只要
            #    里面有文件被打开、正在被写、或者被当作工作目录，改名就
            #    会失败（2026-09-07 实测五种情况全对）。
            if not can_take(_r['path']):
                failed.append('%s（正在用，跳过了）' % _r['name'])
                continue
            rm_tree_safe(_r['path'])
    if 'pip_cache' in keys and not pip_paths:
        # 没指定具体文件 = 清掉本软件的那些（不碰别人的）
        s = scan_pip_cache()
        pip_paths = [r['path'] for r in s.get('items', []) if r.get('ours')]

    # 🔴 只删缓存目录内的文件。路径是前端传来的，必须验 ——
    #    本机任意进程都能 POST 一个自己的路径过来（server 只绑
    #    127.0.0.1，但那不等于只有我们能连）。
    #
    # 🔴 **必须先判空再 abspath。** os.path.abspath('') 返回的是
    #    当前工作目录 —— 直接套在 pip_cache_dir() 外面的话，pip 坏了
    #    问不出目录时，白名单会从「pip 缓存目录」悄悄退化成「当前工作
    #    目录」，等于把安装目录整个敞开给那个 POST。问不出来就一个都
    #    不删，这是唯一安全的降级方向。（2026-09-05 全量审查查出来的，
    #    测试见 test_问不出pip缓存目录时不能退化成删当前目录）
    _cache_root = pip_cache_dir() if pip_paths else ''
    cache_dir = os.path.abspath(_cache_root) if _cache_root else ''
    for p in (pip_paths or ()):
        ap = os.path.abspath(p)
        if not cache_dir or not ap.startswith(cache_dir + os.sep):
            failed.append('%s（不在缓存目录内，拒绝删除）' % os.path.basename(p))
            continue
        rm_file(ap)

    return {'ok': True, 'freed': freed, 'failed': failed}


# ── 运行记录 ───────────────────────────────────────────────────────────
#
# 诊断报告里最值钱的两条，而现有的 convert.log 给不了 —— 它只记了
# 时间和文件路径，**没有结果**：
#
#     ===== 2026-09-04 19:47:07 =====
#     D:\...\a.pdf
#
# 转成没转成、错在哪，一个字都没有。解析一个本来就没记结果的文件，
# 那才是编数据。所以另开两个小文件。

LAST_RUN = os.path.join(paths.LOGS, 'last_run.json')
LAST_ERROR = os.path.join(paths.LOGS, 'last_error.json')

# 转换历史。**跟 last_run.json 分开放，那个一个字节都不动** ——
# 诊断报告读的是它，改格式等于动一个正在用的东西。这里另起一个文件，
# 每条的结构跟 last_run 那条一样，只多三个字段（源路径、产物路径、
# 完整报错），列表最新的在最前面。
RUNS = os.path.join(paths.LOGS, 'runs.json')
# 模型总量那份也住在 logs/ 下，清日志时一并保住。
#
# 🔴 **名字从 models.py 那边取，不在这里再写一遍。** 原来这里硬编码
#    'models_size.json'，跟 `models.SIZE_FILE` 是两处各写各的 —— 那边一改名
#    或换目录，这里的保护当场失效，用户点一次「清理日志」分母就没了，
#    而且不报任何错。更糟的是测试也引用这个常量，两边一起错就一起绿
#    （CLAUDE.md 第 4 条那个形状）。现在派生过来，不一致在语法上就不可能。
SIZE_FILE_NAME = os.path.basename(models.SIZE_FILE)
RUNS_KEEP = 200          # 小蔡定的。每条带完整路径和完整报错，不设上限会越滚越大


def _write_json(path, data):
    """写一个小 json。**永不抛异常** —— 记日志这件事不能把转换搞崩。"""
    try:
        paths.ensure(os.path.dirname(path))
        with io.open(path, 'w', encoding='utf-8') as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=2))
        return True
    except Exception:
        return False


def _read_json(path):
    try:
        with io.open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _append_run(row):
    """把一条记录插到历史最前面，超过 RUNS_KEEP 条就扔掉最老的。

    **永不抛异常** —— 跟 `_write_json` 一个道理，记账不能把转换搞崩。
    文件读坏了（半截 JSON、被人手动改过）就当没有历史，从头开始记，
    不能因为读不出旧的就连新的也不记。
    """
    r = _read_json(RUNS)
    rows = r if isinstance(r, list) else []
    rows.insert(0, row)
    del rows[RUNS_KEEP:]
    return _write_json(RUNS, rows)


def runs(limit=None):
    r"""读转换历史。最新的在最前面。读不出来返回空列表，不抛异常。

    🔴 判据是 `limit is None`，不是 `if limit`。后者把 0 当成「没给上限」，
    于是 `/api/runs?limit=0`（要 0 条）会拿回全部 200 条 —— 要多少给多少
    才对，要 0 条就给空的。负数按 Python 切片自己的语义走（`r[:-1]`），
    界面不会传，也不值得为它多写一层判断。
    """
    r = _read_json(RUNS)
    if not isinstance(r, list):
        return []
    return r if limit is None else r[:limit]


def note_run(rep, pdf_name='', took_sec=0):
    r"""记一次转换的结果。**每转完一份就写，不等整批结束。**

    整批结束才写的话，中途崩溃就什么都没有 —— 而那正是最需要看的
    时刻。这样写，最后一条记录停在崩溃前那份，正好指向病根。
    """
    rep = rep or {}
    err = rep.get('error') or ''
    row = {
        'time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'file': pdf_name or os.path.basename(rep.get('pdf', '') or ''),
        'pages': rep.get('pages', 0),
        'ok': bool(rep.get('ok')),
        'error': err[:200],
        # 🔴 字段名取自 convert.pdf_to_word 的 rep：**formulas 是源文
        #    公式总数，formulas_xsl 是成功转成 Word 原生公式的个数**。
        #    这里原来读的是 formulas_ok / formulas_src —— 那两个名字
        #    只存在于 todocx 内部（且叫 formulas_replaced / formulas_src），
        #    convert 往上传的时候已经改过名。于是这一格永远是 "?/?"。
        'formulas': '%s/%s' % (rep.get('formulas_xsl', '?'),
                               rep.get('formulas', '?')),
        'took_sec': int(took_sec or 0),
    }
    # 历史那份比诊断那份多几样。前三样给界面用：源 PDF 全路径（一键重转）、
    # 产物 Word 全路径（打开文件 / 打开所在文件夹）、**没截断的报错**
    # （上面那个 200 字符是给诊断报告用的，不动它）。
    #
    # 🔴 后三样给倒计时学速度用（`server/main.py` 的 `_learned_rates`）。
    #    **少写一个，「越用越准」就永远学不到东西、恒吃出厂常量。**
    #    2026-09-07 就栽在这儿：这几个字段 convert 那边算得好好的，
    #    却没被写进历史；而唯一相关的测试把 `maint.runs` 整个 mock 成手工
    #    捏的数据，于是断链被测试盖住、全绿。跟 CLAUDE.md 第 4 条
    #    （formulas_ok / formulas_src 那次）是同一个形状：
    #    **测试和实现一起错，于是一起绿。**
    #    字段名必须跟 `convert.pdf_to_word` 的 rep 对齐，别再自己起名。
    _append_run(dict(row, pdf=rep.get('pdf', '') or '',
                     docx=rep.get('docx', '') or '', error_full=err,
                     pass1_sec=rep.get('pass1_sec', 0) or 0,
                     pass2_sec=rep.get('pass2_sec', 0) or 0,
                     elements=rep.get('elements', 0) or 0))
    return _write_json(LAST_RUN, row)


def note_error(where, msg, hint=''):
    """记一条给用户看过的错误。同样永不抛异常。"""
    return _write_json(LAST_ERROR, {
        'time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'where': where,
        'msg': (msg or '')[:300],
        'hint': hint,
    })


def last_run():
    """最近一次转换。没有返回 None。"""
    return _read_json(LAST_RUN)


def last_error():
    """最近一次错误。没有返回 None。"""
    return _read_json(LAST_ERROR)
