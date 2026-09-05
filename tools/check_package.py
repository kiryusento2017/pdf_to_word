# -*- coding: utf-8 -*-
r"""检查打好的安装包里有没有不该有的东西。**发版前跑**。

跑法：.venv\Scripts\python.exe tools\check_package.py [--version v0.0.2]

## 为什么要有这个脚本

2026-09-02 发出去的 v0.0.2 里带着 **80 个 `_tmp` / `appdata` 条目** ——
那是在 `dist/PDF2Word` 里真跑过软件留下的（Electron 的 GPU 缓存、
字典、Code Cache、转换的中间产物）。既是垃圾，也带着开发机的痕迹。

而「在 dist 里真跑一次」是**必须做的**（发行版能不能启动，只有真跑
才知道）。所以垃圾一定会产生，问题在于打包时没排除。

同一次还发现包里的 `使用说明.txt` 是上一版的：`--sfx` 只打包已有产物，
不重新生成这些从模板来的小文件，而我改的是生成器。

这两件事都是「人眼扫一遍才发现」的，而人眼不该负责这个。

## 查什么

  · 不该有的目录：_tmp / appdata / logs / models / __pycache__
  · 使用说明.txt 和 version.json 里的版本号，跟 --version 对不对得上
  · 关键文件在不在：PDF转Word.exe、resources/app/、runtime/python/
  · torch 不该在包里（GPU 运行库是首启按需下的）
"""
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.chdir(ROOT)
DIST = os.path.join(ROOT, 'dist')

SEVEN = [r'C:\Program Files\7-Zip\7z.exe',
         r'C:\Program Files (x86)\7-Zip\7z.exe']

# 一条都不许有。匹配的是**目录名**，不是子串。
#
# 🔴 这条规则改过两次，两次都是被变异测试抓出来的：
#
#    第一版写成纯子串 `logs` —— openai 库里的 `audit_logs.py`、
#    `_logs.py` 被当成「开发机的日志」报了出来（误报）。
#
#    第二版改成 `\logs\`（前后都要反斜杠）—— 结果**漏报**了根目录下的
#    `appdata\`：7z 的列表里它显示成 `appdata\GPUCache\...`，
#    前面根本没有反斜杠。那次「包是干净的」是两个错互相抵消
#    （打包时真排除了 + 检查规则匹配不到），差一点就当成结论了。
#
#    现在用 lookbehind：目录名前面不能是单词字符（挡掉 `_logs`），
#    但可以是空格或反斜杠（根目录和子目录都认）。
FORBIDDEN = [
    # （显示用的名字, 匹配用的正则, 为什么不该有）
    ('_tmp/', r'(?<![\w-])_tmp[\\/]', '转换的中间产物'),
    ('appdata/', r'(?<![\w-])appdata[\\/]', 'Electron 缓存，带开发机痕迹'),
    ('logs/', r'(?<![\w-])logs[\\/]', '开发机的日志'),
    ('__pycache__/', r'(?<![\w-])__pycache__[\\/]', '里面嵌着开发机的源码路径'),
    ('site-packages/torch/', r'site-packages[\\/]torch[\\/]',
     'GPU 运行库该由用户首启时下，不进包'),
    ('vc_done.json', r'(?<![\w-])vc_done\.json',
     '「装过 vc_redist」的记号，打进包等于给每个新用户一个假标记'),
]

# 必须有的**目录**。
#
# 🔴 这一组是 v0.0.3 之后加的。那一版的包里 pip、modelscope、
#    transformers 各自的 `models` 目录全被 `-xr!models` 剔掉了，
#    用户装上之后点「装 GPU 运行库」直接报
#    `ModuleNotFoundError: No module named 'pip._internal.models'`。
#
#    而这个脚本当时报的是「包是干净的」—— 因为它只查「有没有不该有的
#    东西」，从不查「该有的还在不在」。干净得连 pip 都没了。
#
#    正向检查和反向检查抓的是两类错，缺一不可。
REQUIRED_DIRS = [
    (r'pip[\\/]_internal[\\/]models[\\/]', 'pip 的核心，缺了 pip 直接崩'),
    (r'modelscope[\\/]models[\\/]', '模型下载器的核心'),
    (r'transformers[\\/]models[\\/]', '转换引擎的核心'),
    (r'tokenizers[\\/]models[\\/]', '分词器的核心'),
]

# 必须有
REQUIRED = [
    'PDF转Word.exe',
    r'resources\app\main.js',
    r'resources\app\renderer\index.html',
    r'runtime\python\python.exe',
    r'pipeline\paths.py',
    # 漏了这两个，装在中文目录里的用户一份都转不了，而包本身看着完全正常。
    # run_mineru.py 更要命：它是 MinerU 的启动入口，漏了谁都转不了。
    r'pipeline\sitepatch\sitecustomize.py',
    r'pipeline\sitepatch\run_mineru.py',
    r'server\main.py',
    'version.json',
    '使用说明.txt',
]


def find_7z():
    for p in SEVEN:
        if os.path.isfile(p):
            return p
    return ''


def main():
    ver = ''
    if '--version' in sys.argv:
        ver = sys.argv[sys.argv.index('--version') + 1]
    if not ver:
        # 没给就用 dist 里最新的那个
        cands = [f for f in os.listdir(DIST)
                 if f.startswith('PDF2Word-Setup-') and f.endswith('.exe')]
        if not cands:
            print('dist 里没有安装包，先打一个')
            return 1
        # 🔴 **按修改时间取最新，不按文件名排序。**
        #    原来是 `cands.sort()` 拿字符串排 —— 那样 `v0.0.10` 会排在
        #    `v0.0.9` **前面**（字符串比较逐位看，'1' < '9'），于是版本号
        #    进到两位数的那天，这个脚本会安静地去查一个旧包，还报「干净」。
        #    现在没到两位数，属于「还没炸但一定会炸」，顺手改掉。
        #    影响面：只有不带 `--version` 跑时才走这里；发版流程都带参数。
        cands.sort(key=lambda f: os.path.getmtime(os.path.join(DIST, f)))
        ver = re.search(r'-(v[\d.]+)\.exe$', cands[-1]).group(1)

    exe = os.path.join(DIST, 'PDF2Word-Setup-%s.exe' % ver)
    if not os.path.isfile(exe):
        print('找不到 %s' % exe)
        return 1

    sz = find_7z()
    if not sz:
        print('找不到 7z.exe，跳过')
        return 0

    print('检查 %s（%.0f MB）' % (os.path.basename(exe),
                                 os.path.getsize(exe) / 1048576.0))
    print('=' * 60)

    # -sccUTF-8：让 7z 用 UTF-8 输出文件名。不加的话中文名
    # （PDF转Word.exe、使用说明.txt）在这边解出来是乱码，
    # 匹配不上就会误报「缺文件」。
    r = subprocess.run([sz, 'l', '-sccUTF-8', exe], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT)
    listing = r.stdout.decode('utf-8', 'replace')

    bad = []
    for name, pat, why in FORBIDDEN:
        hits = re.findall(pat, listing, re.I)
        if hits:
            bad.append('包里有 %d 个 `%s` —— %s' % (len(hits), name, why))
        else:
            print('  [ok] 没有 %s' % name)

    for pat, why in REQUIRED_DIRS:
        n = len(re.findall(pat, listing, re.I))
        if n:
            print('  [ok] %s 有 %d 个条目' % (pat.replace(chr(92) * 2, '/')
                                              .replace('[/]', '/'), n))
        else:
            bad.append('缺整个 `%s` —— %s' % (pat, why))

    for need in REQUIRED:
        if need not in listing:
            bad.append('缺 `%s`' % need)
    print('  [ok] %d 个关键文件都在' % len(REQUIRED)
          if not any('缺 ' in b for b in bad) else '')

    # 版本号：解出来看
    tmp = os.path.join(DIST, '_check_tmp')
    subprocess.run([sz, 'e', '-y', '-o' + tmp, exe, '使用说明.txt',
                    'version.json', r'resources\app\package.json'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for name in ('使用说明.txt', 'version.json'):
        p = os.path.join(tmp, name)
        if not os.path.isfile(p):
            bad.append('包里没有 %s' % name)
            continue
        txt = io.open(p, encoding='utf-8', errors='replace').read()
        if ver not in txt:
            bad.append('%s 里没有 %s —— `--sfx` 不重新生成这些从模板来的'
                       '小文件，改了 put_readme 要跑一次完整构建' % (name, ver))
        else:
            print('  [ok] %s 里的版本号是 %s' % (name, ver))

    # 🔴 package.json 的版本号也要对上。
    #
    #    `build_release.py --version` 会更新 version.json 和 使用说明.txt，
    #    **唯独不碰 app/package.json** —— 它只是被原样拷进发行包。
    #    2026-09-05 查出来时它停在 0.1.0，而实际已经发到 v0.1.1 了：
    #    不加这道检查的话，每发一版它就更落后一版。
    #
    #    没有任何代码读它（main.js 里没有 app.getVersion()），所以对不上
    #    不会出故障 —— 但发行包里带一个写错版本号的清单文件，早晚误导人。
    #    发版时手动同步这一步写在 RELEASE.md 第一节第 4 步。
    #
    #    ⚠️ 格式不一样：version.json 是 "v0.2.1"，package.json 不带 v。
    pj = os.path.join(tmp, 'package.json')
    if not os.path.isfile(pj):
        bad.append('包里没有 resources/app/package.json')
    else:
        got = ''
        try:
            got = (json.load(io.open(pj, encoding='utf-8')) or {}).get(
                'version', '')
        except Exception as e:
            bad.append('package.json 读不出来：%s' % str(e)[:80])
        want = ver.lstrip('v')
        if got and got != want:
            bad.append('package.json 的版本号是 %s，应该是 %s —— '
                       'build_release 不会自动改它，发版前要手动同步'
                       '（RELEASE.md 第一节第 4 步）' % (got, want))
        elif got:
            print('  [ok] package.json 里的版本号是 %s' % got)
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    print()
    if bad:
        print('发现 %d 处：' % len(bad))
        for b in bad:
            print('  ·', b)
        return 1
    print('包是干净的')
    return 0


if __name__ == '__main__':
    sys.exit(main())
