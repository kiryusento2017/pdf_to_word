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
# 产物目录用英文。中文路径要经过 Electron → Python 子进程 → MinerU →
# pandoc 好几手，而我们全程在英文路径下开发测试，没验过中文路径。
# 默认给中文路径等于让老师去踩一条没人走过的路。
OUT = os.path.join(DIST, 'PDF2Word')

PY_VER = '3.12.10'          # 跟开发环境一致
EMBED_URL = ('https://www.python.org/ftp/python/%s/python-%s-embed-amd64.zip'
             % (PY_VER, PY_VER))
GETPIP_URL = 'https://bootstrap.pypa.io/get-pip.py'

# 业务代码：拷这些，别的一概不拷（测试、文档、开发脚本都不该发给老师）
# 业务代码：拷这些，别的一概不拷（测试、文档、开发脚本都不该发给老师）。
# app 下那几样放进 resources/app —— Electron 改名后的 exe 会自动找那儿，
# 这样双击就能开，不用再弹 cmd 黑框去调它。
CODE = [
    ('pipeline', 'pipeline'),
    ('server', 'server'),
    ('app/main.js', 'resources/app/main.js'),
    ('app/icon.ico', 'resources/app/icon.ico'),
    ('app/preload.js', 'resources/app/preload.js'),
    ('app/package.json', 'resources/app/package.json'),
    ('app/renderer', 'resources/app/renderer'),
    ('runtime/pandoc', 'runtime/pandoc'),
]

# 双击的那个 exe 叫什么。这是用户唯一会点的东西，用中文名友好；
# 文件名不是路径的一部分，不影响那条「路径别用中文」的规矩。
APP_EXE = 'PDF转Word.exe'

# Layer 1 的依赖。跟 setup_env.py 保持一致。
DEPS = ['pymupdf', 'lxml', 'fastapi', 'uvicorn', 'python-docx', 'mineru[core]']
# ⚠️ 2026-09-02 起不再往包里装 CPU 版 torch（规矩改成只用 GPU）。
#    这行留着是因为 --slim 构建生成的「首次安装.cmd」还在用同一个源地址，
#    真要删的话两处得一起改。别以为它是死代码就顺手删掉。
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
    r"""Electron 运行时**摊在根目录**，exe 改成中文名。

    这是 Electron 应用的标准形态：exe 旁边一堆 dll，代码在 resources/app。
    改名之后双击 exe 直接开窗 —— 原来要靠 `启动.cmd` 调
    `electron.exe app`，会弹一个黑框，既难看用户还不敢关。

    只拷 dist/（367 MB），electron 包里其余 13 MB 是 TypeScript 定义、
    安装脚本这些开发用的东西。
    """
    src = os.path.join(ROOT, 'app', 'node_modules', 'electron', 'dist')
    if not os.path.isdir(src):
        raise SystemExit('找不到 electron/dist，先在 app/ 里跑一次 npm install')
    for name in os.listdir(src):
        s_path = os.path.join(src, name)
        d_path = os.path.join(out, name)
        if os.path.isdir(s_path):
            rm(d_path)
            shutil.copytree(s_path, d_path)
        else:
            shutil.copy2(s_path, d_path)
    old = os.path.join(out, 'electron.exe')
    new = os.path.join(out, APP_EXE)
    if os.path.isfile(old):
        if os.path.isfile(new):
            os.remove(new)
        os.rename(old, new)
    say('Electron 运行时就位，主程序 %s' % APP_EXE)


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


def put_launcher(out, version, slim=False):
    r"""只在 --slim 构建时才放 `安装依赖.cmd`。

    默认构建把依赖也装进去了，用户双击 `PDF转Word.exe` 就能用 ——
    **不再有 cmd 黑框**。小蔡的原话：「为什么 cmd 启动，我看其他软件不是」。
    """
    if not slim:
        say('启动器：不需要（双击 %s 即可）' % APP_EXE)
        return

    dep = (
        '@echo off\r\n'
        'chcp 65001 >nul\r\n'
        'cd /d "%~dp0"\r\n'
        'echo 正在安装转换引擎，需要联网，大约 3-10 分钟...\r\n'
        'echo.\r\n'
        'runtime\\python\\python.exe -m pip install --no-warn-script-location '
        '__DEPS__ -i https://pypi.tuna.tsinghua.edu.cn/simple\r\n'
        'if errorlevel 1 goto failed\r\n'
        'runtime\\python\\python.exe -m pip install --no-warn-script-location '
        'torch torchvision --index-url https://download.pytorch.org/whl/cpu\r\n'
        'if errorlevel 1 goto failed\r\n'
        'echo.\r\n'
        'echo 装好了，可以关掉这个窗口，双击「PDF转Word.exe」开始用。\r\n'
        'pause\r\n'
        'exit /b 0\r\n'
        ':failed\r\n'
        'echo.\r\n'
        'echo 装失败了 —— 上面几行红字是原因，多半是网络不通。\r\n'
        'echo 连上网之后再双击这个文件跑一次就行，已经装好的部分不会重下。\r\n'
        'pause\r\n'
        'exit /b 1\r\n'
    ).replace('__DEPS__', ' '.join(DEPS))
    io.open(os.path.join(out, '首次安装.cmd'), 'w', encoding='utf-8', newline='').write(dep)
    say('slim 构建：放了「首次安装.cmd」')


def put_version(out, version, sha=''):
    r"""写 version.json。软件靠它知道自己是哪个版本。

    ⚠️ `published_at` 永远是空串，**这是对的不是漏填** —— 打包这一刻
       Release 还没创建，真实发布时间根本拿不到。

       所以 `update.check()` 的防降级判断**不能**拿它当主判据：
       那样写的话保护恒不生效（2026-09-02 实测：本地 v0.0.3、远端
       v0.0.1，界面照样提示「有新版本」，点更新就是降级）。
       现在主判据是版本号本身，published_at 只在版本号看不懂时兜底。
       字段保留是为了兼容已经发出去的旧包。
    """
    io.open(os.path.join(out, 'version.json'), 'w', encoding='utf-8').write(
        json.dumps({'tag': version, 'published_at': '', 'sha': sha},
                   ensure_ascii=False, indent=2))
    say('版本号 %s' % version)


def put_readme(out, version):
    r"""发行版根目录那份给用户看的说明。

    ⚠️ 这段文字是**用户在软件之外唯一能看到的说明**（Release 页面的
       发布说明得联网才看得到）。所以「用不了」的条件必须写在最前面 ——
       没有 N 卡的人应该在双击之前就知道，而不是装完 287 MB、
       下完 7 GB 才发现。
    """
    txt = (
        'PDF 转 Word  __VER__\r\n'
        '\r\n'
        '双击「PDF转Word.exe」开始用。\r\n'
        '\r\n'
        '=== 用之前先确认两件事 ===\r\n'
        '\r\n'
        '1. 这台电脑要有 NVIDIA 独立显卡。\r\n'
        '   这个软件只用显卡转换，不用 CPU 顶替。没有 N 卡的话装了也转不了，\r\n'
        '   点开始会直接报错。\r\n'
        '\r\n'
        '2. 要装微软 Office。\r\n'
        '   公式要转成 Word 原生公式，得用 Office 自带的一个转换文件。\r\n'
        '   只装 WPS 不行。软件打开时会自己检查并告诉你。\r\n'
        '\r\n'
        '=== 第一次打开要下大约 7.4 GB ===\r\n'
        '\r\n'
        '  · 显卡运行库  约 2.8 GB（软件会给一个「现在就装」的按钮）\r\n'
        '  · 识别模型    约 4.6 GB（界面里会让你选下载源，自动测哪个快）\r\n'
        '\r\n'
        '这一步只有第一次要做，之后打开就直接能用。\r\n'
        '\r\n'
        '=== 其他 ===\r\n'
        '\r\n'
        '所有文件都在这个文件夹里，不往系统盘乱塞东西。不想用了直接删掉\r\n'
        '整个文件夹就行，转好的 Word 不受影响。\r\n'
        '\r\n'
        '⚠ 不要放在 C:\\Program Files 里 —— 那个位置写不了文件。\r\n'
        '   放 D 盘之类的地方最好，路径里别带中文和空格。\r\n'
        '\r\n'
        '以后要更新：软件里点「检查更新」，会自动下载安装。\r\n'
    ).replace('__VER__', version)
    io.open(os.path.join(out, '使用说明.txt'), 'w', encoding='utf-8',
            newline='').write(txt)


# ── Layer 1 ─────────────────────────────────────────────────────────────
def install_deps(py_exe, cuda=False):
    r"""装 Layer 1 依赖。

    🔴 装完**把 torch 卸掉**（除非 --cuda）。

       小蔡 2026-09-02 定「只用 GPU」，而 CUDA 版 torch 解压后 4.2 GB：
       打进安装包会让它从 356 MB 涨到 1.5~2 GB，逼近 GitHub 单文件
       2 GiB 的上限，而且没有显卡的人也得跟着下这 4 GB。
       所以改成首次启动时按需装 —— 反正首启本来就要下 4.6 GB 模型，
       两件事合成一个流程，用户只等一次。

       为什么是「先装再卸」而不是干脆别装：mineru 的依赖树里 torch 是
       必需项，pip 装 mineru 的时候会自己把它拉下来。让它装、再干净卸掉，
       比想办法拦住它可靠 —— 卸载走的是 pip 自己的元数据，不会留下
       半截的 dist-info 让首启那次安装误判成「已经装过了」。

       结果：CPU 版 torch 那 486 MB 不进包，安装包小掉一半。
    """
    say('装依赖（这一步最久）…')
    mirror = ['-i', 'https://pypi.tuna.tsinghua.edu.cn/simple']
    subprocess.run([py_exe, '-m', 'pip', 'install', '-q',
                    '--no-warn-script-location'] + DEPS + mirror, check=True)
    if cuda:
        # --cuda：直接把 GPU 版打进包，装完即用不用再联网。
        # 代价是包 1.5~2 GB，只在确实需要离线分发时才这么打。
        subprocess.run([py_exe, '-m', 'pip', 'install', '-q',
                        '--no-warn-script-location'] + TORCH_CUDA, check=True)
        say('依赖装完（含 CUDA 版 torch）')
        return

    subprocess.run([py_exe, '-m', 'pip', 'uninstall', '-y', '-q',
                    'torch', 'torchvision'], check=False)
    say('依赖装完（torch 已卸掉，首启时按需装 CUDA 版）')


# 更新包里放什么。**只有会改的那些** —— Electron 367 MB、pandoc 223 MB、
# python 运行时、torch、模型都不动，推它们等于让老师重下 10 GB 换 0.9 MB。
#
# 🔴 写成 (源码里的路径, 发行版里的路径)，**两边不一样**。
#    前端代码在源码里是 app/，在发行版里是 resources/app/（Electron 标准
#    形态，exe 改个名就能双击直接开）。照源码路径打包的话，解压出来落在
#    <安装目录>/app/，而软件读的是 <安装目录>/resources/app/ ——
#    **前端更新完全无效**。
#
#    这个 bug 最坏的地方是它不报错：pipeline/ 和 server/ 在根目录，
#    两边路径一致，所以 Python 那半更新得好好的，用户看到「更新完成」，
#    只有前端悄悄停在旧版本。跟 CODE 清单对着看，两处必须一致。
# 更新包里那份依赖清单叫什么。客户端装之前拿它跟本地比对 ——
# 见 update.check_requires 的说明。
REQUIRES_NAME = 'requires.json'

UPDATE_PARTS = [
    ('pipeline', 'pipeline'),
    ('server', 'server'),
    ('app/main.js', 'resources/app/main.js'),
    ('app/preload.js', 'resources/app/preload.js'),
    ('app/package.json', 'resources/app/package.json'),
    ('app/icon.ico', 'resources/app/icon.ico'),
    ('app/renderer', 'resources/app/renderer'),
    ('version.json', 'version.json'),
]


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
    # published_at 留空的理由见 put_version 的说明（打包时还没发布）
    vj = os.path.join(DIST, '_version_tmp.json')
    io.open(vj, 'w', encoding='utf-8').write(
        json.dumps({'tag': version, 'published_at': '', 'sha': sha},
                   ensure_ascii=False, indent=2))

    # 🔴 这一版需要哪些 pip 包，写进更新包。
    #
    #    客户端解压之后、覆盖之前拿它跟本地比对：缺了或者版本对不上就
    #    拒绝覆盖。为什么不能只靠版本号 —— 「次版本变了就是依赖变了」
    #    是个**约定**，靠发版的人不出错。哪天加了个包却只改修订号，
    #    用户就会拿到新代码配旧依赖，下次启动直接 ImportError。
    #    依赖清单是**事实**。
    #
    #    只记包名和「打包时装的是哪个版本」，不记 pin —— 我们本来就不
    #    锁版本，比对时只看「本地有没有、大版本对不对得上」。
    rq = os.path.join(DIST, 'requires-%s.json' % version)
    reqs = {}
    for spec in DEPS:
        name = spec.split('[')[0]
        try:
            import importlib.metadata as _md
            reqs[name] = _md.version(name)
        except Exception:
            reqs[name] = ''
    io.open(rq, 'w', encoding='utf-8').write(
        json.dumps({'version': version, 'requires': reqs},
                   ensure_ascii=False, indent=2))
    # 🔴 这份要**单独作为 Release 附件上传**（几百字节）。
    #    客户端 check() 时先拉它跟本地比，下载前就知道这次更新能不能装 ——
    #    只放在更新包里的话，用户得先下 0.5 MB 才发现装不了。
    #    包里那份也保留，双保险 + 兼容拉不到附件的情况。
    say('依赖清单：%s' % os.path.basename(rq))

    n = 0
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(rq, REQUIRES_NAME)
        n += 1
        for rel, arc_base in UPDATE_PARTS:
            if rel == 'version.json':
                z.write(vj, 'version.json')
                n += 1
                continue
            src = os.path.join(ROOT, rel.replace('/', os.sep))
            if not os.path.exists(src):
                say('⚠️ 更新包里少了 %s（源文件不存在）' % rel)
                continue
            if os.path.isfile(src):
                z.write(src, arc_base)
                n += 1
                continue
            for dp, dn, fns in os.walk(src):
                dn[:] = [d for d in dn if d not in ('__pycache__', '_tmp')]
                for fn in fns:
                    if fn.endswith('.pyc'):
                        continue
                    full = os.path.join(dp, fn)
                    # 包内路径 = 发行版里的位置 + 它在源目录里的相对位置
                    sub = os.path.relpath(full, src).replace(os.sep, '/')
                    arc = arc_base + '/' + sub
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

    # 🔴 排除**运行时**产生的东西。开发时在 dist/PDF2Word 里真跑过软件
    #    （那是验证发行版的必要动作），于是留下一堆缓存和临时文件：
    #
    #      appdata/   Electron 的 GPU 缓存、字典、Code Cache —— 带本机痕迹
    #      _tmp/      转换的中间产物
    #      logs/      我这台机器的日志
    #      models/    4.6 GB 模型，用户自己下
    #      __pycache__ 里面嵌着开发机的源码路径
    #
    #    2026-09-02 的 v0.0.2 就带着 80 个 _tmp/appdata 条目发出去了。
    #    🔴 `-x!` 不递归，`-xr!` 递归 —— 这一个字母的差别毁掉过一整版。
    #
    #    v0.0.3 用的是 `-xr!models`，本意是排除根目录下那个 4.6 GB 的
    #    模型目录，实际把**所有**叫 models 的目录都剔了，一共 37 个：
    #      pip/_internal/models     → pip 直接崩（用户点安装就报
    #                                 ModuleNotFoundError）
    #      modelscope/models        → 模型下载器的核心
    #      transformers/models      → 转换引擎的核心
    #      tokenizers、onnxruntime、magika 各自的 models
    #
    #    前四个都是**只在根目录出现**的名字，用 `-x!` 就够。
    #    后两个必须递归 —— __pycache__ 和 .pyc 本来就散在各处。
    #    vc_done.json 是运行时才产生的「这台机器装过 vc_redist」的记号。
    #    打进包的话，每个新用户装上就自带一个假记号，第一步直接跳过 ——
    #    而那一步正是为了修「没装 VC 却往下走」才加的。
    exclude = ['-x!_tmp', '-x!appdata', '-x!logs', '-x!models',
               '-x!vc_done.json',
               '-xr!__pycache__', '-xr!*.pyc']
    say('压缩中（要几分钟）…')
    r = subprocess.run([sz, 'a', '-t7z', '-mx=5', '-mmt=on'] + exclude
                       + [archive, os.path.join(OUT, '*')],
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
        '路径里最好不要有中文和空格。\\n\\n'
        '所有文件都会留在这个文件夹里，不想用了直接删掉即可。"\n'
        'ExtractDialogText="正在解压，大约 1-3 分钟…"\n'
        'ExtractTitle="正在安装 PDF 转 Word"\n'
        'GUIMode="2"\n'
        'OverwriteMode="2"\n'
        'ExtractPathText="安装到："\n'
        'InstallPath="D:\\\\PDF2Word"\n'
        'RunProgram="explorer.exe ."\n'
        ';!@InstallEnd@!\n'
    ).replace('__VER__', version)
    cfg_path = os.path.join(DIST, '_sfx_config.txt')
    io.open(cfg_path, 'w', encoding='utf-8').write(cfg)

    # 文件名用英文：GitHub 会把 Release 附件名里的中文吃掉
    # （PDF转Word-v0.0.1.exe 上传后显示成 PDF.Word-v0.0.1.exe）
    exe = os.path.join(DIST, 'PDF2Word-Setup-%s.exe' % version)
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
    ap.add_argument('--bump', action='store_true',
                    help='配合 --sfx：产物里是旧版本号也照打。'
                         '用在「手工同步了代码、现在要升版本号」的场合，'
                         '**你得自己确认代码真同步过了**')
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
        # 🔴 但要确认「打的这堆东西」确实就是 --version 说的那个版本。
        #    不查的话，`--version v0.0.2 --sfx` 会把上次 v0.0.1 的产物
        #    打成 PDF2Word-Setup-v0.0.2.exe —— 文件名和内容对不上，
        #    而这种包发出去之后，用户装完点「检查更新」还会告诉他
        #    「已是最新」，因为里面的 version.json 写的是老版本。
        try:
            with io.open(os.path.join(OUT, 'version.json'),
                         encoding='utf-8') as f:
                have = json.load(f).get('tag', '')
        except Exception:
            have = ''
        if have != a.version and not a.bump:
            raise SystemExit(
                '产物里的版本是 %s，跟 --version %s 对不上。\n'
                '· 只是重打同一版 → 把 --version 改成 %s\n'
                '· 手工同步过代码、现在要升版本号 → 加 --bump\n'
                '· 依赖也变了 → 跑一次不带 --sfx 的完整构建'
                % (have or '(读不出来)', a.version, have or a.version))
        if have != a.version and a.bump:
            # 放行，但把这件事说出来 —— 打错版本的代价是包发出去之后
            # 用户装完点检查更新还被告知「已是最新」，很难查。
            say('⚠️  产物里原本是 %s，按 --bump 打成 %s。'
                % (have or '(读不出来)', a.version))
            say('    请确认 dist\\PDF2Word 里的代码已经同步过了 —— '
                '前端的目标是 resources\\app\\，不是 app\\。')
        # 🔴 这几个小文件每次都重新生成。
        #    它们是**从模板生成**的，不是组装来的 —— 改了 put_readme
        #    却只跑 --sfx 的话，产物里还是旧的那份。
        #    2026-09-02 就这么把一份写着 v0.0.1、下载量还是旧数字的
        #    使用说明发了出去。
        put_readme(OUT, a.version)
        put_version(OUT, a.version, sha)
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
    put_launcher(OUT, a.version, slim=a.slim)
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
