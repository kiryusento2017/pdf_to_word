# -*- coding: utf-8 -*-
r"""组装发行版。

跑法：
    .venv\Scripts\python.exe tools\build_release.py --version v0.0.1
    .venv\Scripts\python.exe tools\build_release.py --version v0.0.1 --slim

产物在 `dist\PDF转Word\`，整个文件夹拷到别的机器就能用。

## 为什么不能直接打包 .venv

`.venv\Lib\` 下只有 site-packages，**没有 stdlib**；`os.__file__` 指向
开发机的 `C:\Users\kiryusento\AppData\Local\Programs\Python\Python312\Lib\os.py`，
`sys.base_prefix` 同理。换台机器第一句 import 就死。

所以用官方的 **Python embeddable 包**（自带 python312.zip 那份 stdlib，
10.6 MB）。2026-09-01 实测四关全过：装 pip、装 pymupdf 并真开了一份
16 页 PDF、装 torch 并真跑了矩阵乘法。

## 分层

    Layer 0  壳     Electron 运行时 + Python embeddable + node + pandoc + 业务代码
    Layer 1  依赖   torch / mineru 等，pip 装进安装目录内
    Layer 2  模型   4.6 GB，软件里点「开始下载」时才下

`--slim` 只出 Layer 0（发出去小，用户首次打开时装依赖）；
默认把 Layer 1 也装进去（包大，但拷过去就能跑，适合当面装机）。

## 所有落点都在安装文件夹内

这是小蔡定的规矩：删掉文件夹 = 卸载干净。paths.py 已经把模型、临时
文件、MinerU 配置锁在里面，Electron 的缓存也用 app.setPath 挪进来了。
"""
import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(ROOT, 'dist')
OUT = os.path.join(DIST, 'PDF转Word')

PY_VER = '3.12.10'          # 跟开发环境一致
EMBED_URL = ('https://www.python.org/ftp/python/%s/python-%s-embed-amd64.zip'
             % (PY_VER, PY_VER))
GETPIP_URL = 'https://bootstrap.pypa.io/get-pip.py'

# 业务代码：拷这些，别的一概不拷（测试、文档、开发脚本都不该发给老师）
CODE = [
    ('pipeline', 'pipeline'),
    ('server', 'server'),
    ('app/main.js', 'app/main.js'),
    ('app/preload.js', 'app/preload.js'),
    ('app/package.json', 'app/package.json'),
    ('app/renderer', 'app/renderer'),
    ('runtime/pandoc', 'runtime/pandoc'),
]

# Layer 1 的依赖。跟 setup_env.py 保持一致。
DEPS = ['pymupdf', 'lxml', 'fastapi', 'uvicorn', 'python-docx', 'mineru[core]']
TORCH_CPU = ['torch', 'torchvision', '--index-url',
             'https://download.pytorch.org/whl/cpu']
TORCH_CUDA = ['torch', 'torchvision', '--index-url',
              'https://download.pytorch.org/whl/cu128']


def say(msg):
    print('  ' + msg, flush=True)


def rm(path):
    shutil.rmtree(path, ignore_errors=True)


def fetch(url, dest):
    say('下载 %s' % os.path.basename(dest))
    with urllib.request.urlopen(url, timeout=180) as r, io.open(dest, 'wb') as f:
        shutil.copyfileobj(r, f)
    return dest


# ── Layer 0 ─────────────────────────────────────────────────────────────
def put_python(out):
    r"""放 Python embeddable 并让它能 import 第三方包。

    embeddable 默认 sys.path 只有 python312.zip 和自己那一层，而且
    `import site` 是注释掉的 —— 不改这两处，pip 装了也 import 不到。
    """
    py_dir = os.path.join(out, 'runtime', 'python')
    os.makedirs(py_dir, exist_ok=True)
    zp = os.path.join(DIST, os.path.basename(EMBED_URL))
    if not os.path.isfile(zp):
        fetch(EMBED_URL, zp)
    with zipfile.ZipFile(zp) as z:
        z.extractall(py_dir)

    pth = None
    for fn in os.listdir(py_dir):
        if fn.endswith('._pth'):
            pth = os.path.join(py_dir, fn)
    if not pth:
        raise SystemExit('embeddable 包里没有 ._pth，结构变了')
    s = io.open(pth, encoding='utf-8').read()
    s = s.replace('#import site', 'import site')
    if 'Lib\\site-packages' not in s:
        s = s.replace('python312.zip\n.', 'python312.zip\n.\nLib\\site-packages')
    io.open(pth, 'w', encoding='utf-8').write(s)
    say('Python embeddable %s 就位（含 stdlib，自包含）' % PY_VER)

    exe = os.path.join(py_dir, 'python.exe')
    gp = os.path.join(DIST, 'get-pip.py')
    if not os.path.isfile(gp):
        fetch(GETPIP_URL, gp)
    subprocess.run([exe, gp, '--no-warn-script-location', '-q'], check=True)
    say('pip 就位')
    return exe


def put_electron(out):
    """Electron 运行时。只拷 dist/（367 MB），别的都是开发用的。"""
    src = os.path.join(ROOT, 'app', 'node_modules', 'electron', 'dist')
    if not os.path.isdir(src):
        raise SystemExit('找不到 electron/dist，先在 app/ 里跑一次 npm install')
    dst = os.path.join(out, 'runtime', 'electron')
    rm(dst)
    shutil.copytree(src, dst)
    say('Electron 运行时就位')


def put_node(out):
    r"""node.exe。**必须打包** —— 老师的电脑上不会有 Node.js，那是开发者
    工具，而 KaTeX 要靠它把 LaTeX 转成 MathML，XSL 又是硬性要求。
    不打包的话老师会被自己的拦截屏挡在门外。
    """
    import shutil as sh
    src = sh.which('node')
    if not src:
        raise SystemExit('本机找不到 node.exe，装一个再打包')
    dst_dir = os.path.join(out, 'runtime')
    os.makedirs(dst_dir, exist_ok=True)
    sh.copy2(src, os.path.join(dst_dir, 'node.exe'))
    say('node.exe 就位（%.0f MB）'
        % (os.path.getsize(os.path.join(dst_dir, 'node.exe')) / 1024 / 1024))


def put_code(out):
    for rel, to in CODE:
        s = os.path.join(ROOT, rel.replace('/', os.sep))
        d = os.path.join(out, to.replace('/', os.sep))
        if not os.path.exists(s):
            say('跳过（不存在）：%s' % rel)
            continue
        os.makedirs(os.path.dirname(d), exist_ok=True)
        if os.path.isdir(s):
            rm(d)
            shutil.copytree(s, d, ignore=shutil.ignore_patterns(
                '__pycache__', '*.pyc', '_tmp'))
        else:
            shutil.copy2(s, d)
    say('业务代码就位')


def put_launcher(out, version):
    r"""启动器。**不能跑 npm install** —— 老师机器上没有 npm，
    而且依赖都已经打包好了。
    """
    cmd = (
        '@echo off\r\n'
        'chcp 65001 >nul\r\n'
        'cd /d "%~dp0"\r\n'
        '\r\n'
        'if not exist "runtime\\python\\python.exe" (\r\n'
        '  echo 运行环境不完整，请重新解压安装包。\r\n'
        '  pause\r\n'
        '  exit /b 1\r\n'
        ')\r\n'
        '\r\n'
        'if not exist "runtime\\python\\Lib\\site-packages\\mineru" (\r\n'
        '  echo 第一次使用需要安装转换引擎，大约几分钟...\r\n'
        '  call "安装依赖.cmd"\r\n'
        ')\r\n'
        '\r\n'
        'start "" "runtime\\electron\\electron.exe" "app"\r\n'
    )
    io.open(os.path.join(out, '启动.cmd'), 'w', encoding='utf-8').write(cmd)

    dep = (
        '@echo off\r\n'
        'chcp 65001 >nul\r\n'
        'cd /d "%~dp0"\r\n'
        'echo 正在安装转换引擎，需要联网，大约 3-10 分钟...\r\n'
        'echo.\r\n'
        'runtime\\python\\python.exe -m pip install --no-warn-script-location '
        '__DEPS__ -i https://pypi.tuna.tsinghua.edu.cn/simple\r\n'
        'runtime\\python\\python.exe -m pip install --no-warn-script-location '
        'torch torchvision --index-url https://download.pytorch.org/whl/cpu\r\n'
        'echo.\r\n'
        'echo 装好了，可以关掉这个窗口，双击「启动.cmd」开始用。\r\n'
        'pause\r\n'
    ).replace('__DEPS__', ' '.join(DEPS))
    io.open(os.path.join(out, '安装依赖.cmd'), 'w', encoding='utf-8').write(dep)
    say('启动器就位')


def put_version(out, version, sha=''):
    io.open(os.path.join(out, 'version.json'), 'w', encoding='utf-8').write(
        json.dumps({'tag': version, 'published_at': '', 'sha': sha},
                   ensure_ascii=False, indent=2))
    say('版本号 %s' % version)


def put_readme(out, version):
    txt = (
        'PDF 转 Word  __VER__\r\n'
        '\r\n'
        '双击「启动.cmd」开始用。\r\n'
        '\r\n'
        '第一次打开会做两件事：\r\n'
        '  1. 装转换引擎（联网，几分钟）\r\n'
        '  2. 下识别模型（约 4.6 GB，界面里会让你选下载源）\r\n'
        '\r\n'
        '需要先装微软 Office —— 公式要转成 Word 原生公式，得用 Office 自带的\r\n'
        '一个转换文件。只装 WPS 不行。软件打开时会自己检查并告诉你。\r\n'
        '\r\n'
        '所有文件都在这个文件夹里，不往系统盘乱塞东西。不想用了直接删掉\r\n'
        '整个文件夹就行，转好的 Word 不受影响。\r\n'
        '\r\n'
        '⚠ 不要放在 C:\\Program Files 里 —— 那个位置写不了文件。\r\n'
        '   放 D 盘之类的地方最好。\r\n'
    ).replace('__VER__', version)
    io.open(os.path.join(out, '使用说明.txt'), 'w', encoding='utf-8').write(txt)


# ── Layer 1 ─────────────────────────────────────────────────────────────
def install_deps(py_exe, cuda=False):
    say('装依赖（这一步最久）…')
    mirror = ['-i', 'https://pypi.tuna.tsinghua.edu.cn/simple']
    subprocess.run([py_exe, '-m', 'pip', 'install', '-q',
                    '--no-warn-script-location'] + DEPS + mirror, check=True)
    torch = TORCH_CUDA if cuda else TORCH_CPU
    subprocess.run([py_exe, '-m', 'pip', 'install', '-q',
                    '--no-warn-script-location'] + torch, check=True)
    say('依赖装完')


# 更新包里放什么。**只有会改的那些** —— Electron 367 MB、pandoc 223 MB、
# python 运行时、torch、模型都不动，推它们等于让老师重下 10 GB 换 0.9 MB。
UPDATE_PARTS = ['pipeline', 'server', 'app/main.js', 'app/preload.js',
                'app/package.json', 'app/renderer', 'version.json']


def make_update_zip(version, sha=''):
    r"""打业务代码更新包。用户下载后解压覆盖即可。

    刻意不含 runtime/ —— 那些不会变，而且加进来包就从 0.9 MB 变成 700 MB。
    """
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, 'pdf_to_word-%s-update.zip' % version)
    if os.path.isfile(out):
        os.remove(out)

    # version.json 要跟着更新包一起走，否则装完还是旧版本号，
    # 下次检查更新会一直提示同一个版本。
    vj = os.path.join(DIST, '_version_tmp.json')
    io.open(vj, 'w', encoding='utf-8').write(
        json.dumps({'tag': version, 'published_at': '', 'sha': sha},
                   ensure_ascii=False, indent=2))

    n = 0
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for rel in UPDATE_PARTS:
            if rel == 'version.json':
                z.write(vj, 'version.json')
                n += 1
                continue
            src = os.path.join(ROOT, rel.replace('/', os.sep))
            if not os.path.exists(src):
                continue
            if os.path.isfile(src):
                z.write(src, rel)
                n += 1
                continue
            for dp, dn, fns in os.walk(src):
                dn[:] = [d for d in dn if d not in ('__pycache__', '_tmp')]
                for fn in fns:
                    if fn.endswith('.pyc'):
                        continue
                    full = os.path.join(dp, fn)
                    arc = os.path.relpath(full, ROOT).replace(os.sep, '/')
                    z.write(full, arc)
                    n += 1
    os.remove(vj)
    say('更新包：%s（%d 个文件，%.2f MB）'
        % (os.path.basename(out), n, os.path.getsize(out) / 1024 / 1024))
    return out


# 7-Zip 的位置。做自解压 exe 要用它的 SFX 模块。
_7Z_CANDS = [
    r'C:\Program Files\7-Zip\7z.exe',
    r'C:\Program Files (x86)\7-Zip\7z.exe',
]


def find_7z():
    import shutil as sh
    for p in _7Z_CANDS:
        if os.path.isfile(p):
            return p
    return sh.which('7z') or ''


def make_sfx(version):
    r"""把发行版做成自解压 exe：双击 → 弹框问放哪 → 解压完成。

    用 7z.sfx（带界面那个），不是 7zCon.sfx（控制台版，双击会弹黑框）。
    拼法是：sfx 模块 + 配置 + .7z 数据，三个文件按顺序拼成一个 exe。

    压缩用 -mx=5：-mx=9 对这堆东西（大量已压缩的 dll 和 wheel）只多省
    几十 MB，却要多花好几倍时间。
    """
    sz = find_7z()
    if not sz:
        raise SystemExit('找不到 7z.exe。装一个：winget install 7zip.7zip')
    sfx = os.path.join(os.path.dirname(sz), '7z.sfx')
    if not os.path.isfile(sfx):
        raise SystemExit('找不到 7z.sfx（7-Zip 的自解压模块）')

    archive = os.path.join(DIST, '_payload.7z')
    if os.path.isfile(archive):
        os.remove(archive)

    say('压缩中（1.97 GB，要几分钟）…')
    r = subprocess.run([sz, 'a', '-t7z', '-mx=5', '-mmt=on', archive,
                        os.path.join(OUT, '*')],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        raise SystemExit('压缩失败：%s' % r.stdout.decode('utf-8', 'replace')[-400:])
    say('压缩完 %.2f GB' % (os.path.getsize(archive) / 1024 ** 3))

    # SFX 配置。GUIMode=2 是「问用户解压到哪」，正是普通安装包的体验。
    # 解压完自动打开那个文件夹，省得老师找不到。
    cfg = (
        ';!@Install@!UTF-8!\n'
        'Title="PDF 转 Word __VER__"\n'
        'BeginPrompt="要把「PDF 转 Word」安装到哪里？\\n\\n'
        '请不要选 C:\\\\Program Files —— 那个位置写不了文件。\\n'
        '建议放 D 盘，比如 D:\\\\PDF转Word\\n\\n'
        '所有文件都会留在这个文件夹里，不想用了直接删掉即可。"\n'
        'ExtractDialogText="正在解压，大约 1-3 分钟…"\n'
        'ExtractTitle="正在安装 PDF 转 Word"\n'
        'GUIMode="2"\n'
        'OverwriteMode="2"\n'
        'ExtractPathText="安装到："\n'
        'InstallPath="D:\\\\PDF转Word"\n'
        'RunProgram="explorer.exe ."\n'
        ';!@InstallEnd@!\n'
    ).replace('__VER__', version)
    cfg_path = os.path.join(DIST, '_sfx_config.txt')
    io.open(cfg_path, 'w', encoding='utf-8').write(cfg)

    exe = os.path.join(DIST, 'PDF转Word-%s.exe' % version)
    if os.path.isfile(exe):
        os.remove(exe)
    say('拼装 exe…')
    with io.open(exe, 'wb') as out:
        for part in (sfx, cfg_path, archive):
            with io.open(part, 'rb') as f:
                shutil.copyfileobj(f, out, 1024 * 1024)
    os.remove(archive)
    os.remove(cfg_path)
    say('安装包：%s（%.2f GB）'
        % (os.path.basename(exe), os.path.getsize(exe) / 1024 ** 3))
    return exe


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--version', required=True, help='比如 v0.0.1')
    ap.add_argument('--slim', action='store_true',
                    help='只出 Layer 0，依赖留给用户首启时装')
    ap.add_argument('--cuda', action='store_true',
                    help='装 CUDA 版 torch（+4 GB，转换快一倍）')
    ap.add_argument('--update-only', action='store_true',
                    help='只打业务代码更新包（0.4 MB），不组装完整发行版')
    ap.add_argument('--sfx', action='store_true',
                    help='把已组装好的发行版做成自解压 exe（不重新组装）')
    a = ap.parse_args()

    sha = ''
    try:
        p = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                           stdout=subprocess.PIPE, timeout=5)
        sha = p.stdout.decode('ascii', 'ignore').strip()
    except Exception:
        pass

    if a.update_only:
        make_update_zip(a.version, sha)
        return

    if a.sfx:
        # 只打包已有的产物，不重新组装 —— 组装一次要下 Electron、装依赖，
        # 十几分钟，改个 SFX 配置不该重来一遍。
        if not os.path.isdir(OUT):
            raise SystemExit('还没组装过，先跑一次不带 --sfx 的构建')
        make_sfx(a.version)
        make_update_zip(a.version, sha)
        return

    print('组装 %s%s' % (a.version, '（slim）' if a.slim else ''))
    rm(OUT)
    os.makedirs(OUT, exist_ok=True)

    py = put_python(OUT)
    put_electron(OUT)
    put_node(OUT)
    put_code(OUT)
    put_launcher(OUT, a.version)
    put_version(OUT, a.version, sha)
    put_readme(OUT, a.version)

    if not a.slim:
        install_deps(py, cuda=a.cuda)

    total = 0
    for dp, _dn, fns in os.walk(OUT):
        for fn in fns:
            try:
                total += os.path.getsize(os.path.join(dp, fn))
            except OSError:
                pass
    make_update_zip(a.version, sha)

    print('')
    print('好了：%s' % OUT)
    print('体积：%.2f GB' % (total / 1024 / 1024 / 1024))


if __name__ == '__main__':
    main()
