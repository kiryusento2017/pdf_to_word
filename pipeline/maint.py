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
import time
import zipfile

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


def scan_logs():
    """日志和转换临时文件有多大。"""
    return {'logs': _dir_size(paths.LOGS), 'tmp': _dir_size(paths.TMP)}


def scan():
    r"""全部占用。给环境检测那一屏用。

    返回 {ok, items: [...], pip: {...}}，items 每项是一类占用：
    {key, label, size, note, cleanable}
    """
    pip = scan_pip_cache()
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
        {'key': 'logs', 'label': '日志', 'size': logs['logs'],
         'note': '', 'cleanable': True},
        {'key': 'tmp', 'label': '转换临时文件', 'size': logs['tmp'],
         'note': '', 'cleanable': True},
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

    def rm_tree(d, keep_root=True):
        nonlocal freed
        if not os.path.isdir(d):
            return
        for dp, dns, fns in os.walk(d, topdown=False):
            for fn in fns:
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

    keys = set(keys or ())
    if 'logs' in keys:
        rm_tree(paths.LOGS)
    if 'tmp' in keys:
        rm_tree(paths.TMP)
    if 'pip_cache' in keys and not pip_paths:
        # 没指定具体文件 = 清掉本软件的那些（不碰别人的）
        s = scan_pip_cache()
        pip_paths = [r['path'] for r in s.get('items', []) if r.get('ours')]

    # 🔴 只删缓存目录内的文件。路径是前端传来的，必须验 ——
    #    本机任意进程都能 POST 一个自己的路径过来（server 只绑
    #    127.0.0.1，但那不等于只有我们能连）。
    cache_dir = os.path.abspath(pip_cache_dir()) if pip_paths else ''
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


def note_run(rep, pdf_name='', took_sec=0):
    r"""记一次转换的结果。**每转完一份就写，不等整批结束。**

    整批结束才写的话，中途崩溃就什么都没有 —— 而那正是最需要看的
    时刻。这样写，最后一条记录停在崩溃前那份，正好指向病根。
    """
    rep = rep or {}
    return _write_json(LAST_RUN, {
        'time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'file': pdf_name or os.path.basename(rep.get('pdf', '') or ''),
        'pages': rep.get('pages', 0),
        'ok': bool(rep.get('ok')),
        'error': (rep.get('error') or '')[:200],
        'formulas': '%s/%s' % (rep.get('formulas_ok', '?'),
                               rep.get('formulas_src', '?')),
        'took_sec': int(took_sec or 0),
    })


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
