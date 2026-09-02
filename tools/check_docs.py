# -*- coding: utf-8 -*-
r"""文档一致性检查。**发版前跑一次**（docs/RELEASE.md 第一节）。

跑法：.venv\Scripts\python.exe tools\check_docs.py

## 为什么要有这个脚本

文档过时是这个项目反复栽的坑：

  · 2026-09-01 把 XSL 从「优先」改成「硬性要求」，README 整节还写着
    「没装 Office 就用内置 Pandoc」，跟实际行为完全相反
  · 2026-09-02 一次核对查出 60 多处对不上：安装包体积、测试条数、
    环境变量个数、拦截屏种类、打包命令的目标路径……

靠人眼「检查三遍」不可持续 —— 每改一次代码就得重来一遍，而且人眼扫到
第三遍时已经对内容脱敏了。能自动查的就别用眼睛。

## 查两件事

**一、照着文档做会不会撞墙**：文档里提到的每个项目内文件，实际存不存在。
   （2026-09-02 真撞过：引擎缺失那屏让用户「跑一次 tools\setup_env.py」，
     而发行版根目录里既没有 tools/ 也没有 README。）

**二、同一个事实在几处写的是不是同一个数**：测试条数、安装包体积、
   下载量这些，散落在四份文档里，改一处漏三处是常态。

真值从**代码和产物**里取，不是从另一份文档里取 —— 那样只会让错误互相印证。
"""
import io
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.chdir(ROOT)

DOCS = [
    'README.md',
    'docs/DESIGN.md',
    'docs/RELEASE.md',
    os.path.join(os.path.dirname(ROOT), '_scratch', 'pdf_to_word_progress.md'),
]

# 文档里会出现、但**本来就不该在项目里**的名字：
#   · 产物（dist/ 下，.gitignore 排除了）
#   · 第三方包内部的文件（site-packages 里）
#   · 举例用的假名字（讲 GitHub 会吃掉中文时举的例）
NOT_PROJECT_FILES = {
    'PDF2Word-Setup-v0.0.1.exe', 'PDF转Word-v0.0.1.exe', 'PDF.Word-v0.0.1.exe',
    'PDF2Word-Setup-v0.1.0.exe',          # 发行版产物在 dist，不在源码树
    'pdf_to_word-v0.0.1-update.zip', 'pdf_to_word-v0.0.2-update.zip',
    'version.json', 'version.py', 'torch/version.py', 'logger.py',
    'pandoc.exe', 'node.exe', 'python.exe', 'PDF转Word.exe', 'mineru.exe',
    'vc_redist.x64.exe', 'c10.dll', 'index.html',
    'runtime/pandoc/pandoc.exe',          # README 明说不在版本库里
    'model_download.log', 'torch_install.log', 'convert.log',
    'pdf_to_word_progress.md',            # 在 _scratch，不在项目里
    '安装依赖.cmd', '首次安装.cmd', '启动.cmd', '使用说明.txt',
    'mineru.json', 'MML2OMML.XSL', 'COPYRIGHT.txt', 'LICENSE',
    'python312.zip',                      # Python embeddable 包内部的 stdlib
    'requires.json', 'requires-vX.json',  # 打包时生成的依赖清单（Release 附件）
    # 运行时才生成的记号：装过一次 vc_redist 之后写在安装目录里。
    # 跟 logs/*.log 一样，源码库里本来就不该有 —— 有反而说明打包会
    # 把开发机的假记号带给用户（build_release 已把它排除）。
    'vc_done.json',
}

PATH_RE = re.compile(
    r'`([\w][\w./\\-]*\.(?:py|js|md|json|ico|png|txt|cmd|exe|html|xsl|log|zip))`')
CMD_RE = re.compile(r'(tools|tests)\\(\w+)\.py')

problems = []


def read(p):
    return io.open(p, encoding='utf-8').read()


def check_files():
    """一、文档提到的项目内文件，实际存不存在。"""
    for doc in DOCS:
        if not os.path.isfile(doc):
            problems.append('[缺文档] %s 不存在' % doc)
            continue
        name = os.path.basename(doc)
        txt = read(doc)

        for h in sorted(set(PATH_RE.findall(txt))):
            plain = h.replace('\\', '/')
            if plain in NOT_PROJECT_FILES or os.path.basename(plain) in NOT_PROJECT_FILES:
                continue
            cands = [plain] + [os.path.join(d, plain) for d in
                               ('app', 'app/renderer', 'pipeline', 'tools',
                                'tests', 'docs', 'server')]
            if not any(os.path.exists(c) for c in cands):
                problems.append('[缺文件] %s 提到 `%s`，项目里找不到' % (name, h))

        for sub, script in CMD_RE.findall(txt):
            if not os.path.isfile('%s/%s.py' % (sub, script)):
                problems.append('[缺脚本] %s 的命令用到 %s\\%s.py，不存在'
                                % (name, sub, script))


def truth():
    """真值：从代码和产物里取，不从另一份文档里取。"""
    t = {}
    r = subprocess.run([r'.venv\Scripts\python.exe', '-m', 'unittest',
                        'discover', '-s', 'tests', '-q'],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    m = re.search(r'Ran (\d+) tests', r.stdout.decode('utf-8', 'replace'))
    t['py'] = int(m.group(1)) if m else 0

    r = subprocess.run(['node', 'tests/front_check.js'],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    t['js'] = r.stdout.decode('utf-8', 'replace').count('✓')

    exe = 'dist/PDF2Word-Setup-v0.0.1.exe'
    t['exe_mb'] = (round(os.path.getsize(exe) / 1048576.0)
                   if os.path.isfile(exe) else 0)

    m = re.search(r'DOWNLOAD_BYTES = int\(([\d.]+) \* 1024',
                  read('pipeline/torchdep.py'))
    t['torch_gb'] = float(m.group(1)) if m else 0
    m = re.search(r'TOTAL_BYTES = int\(([\d.]+) \* 1024',
                  read('pipeline/models.py'))
    t['models_gb'] = float(m.group(1)) if m else 0
    return t


def check_numbers(t):
    """二、同一个事实在几处是不是同一个数。"""
    for doc in DOCS:
        if not os.path.isfile(doc):
            continue
        name = os.path.basename(doc)
        txt = read(doc)

        for n in set(int(x) for x in re.findall(r'(\d{3}) 条 Python', txt)):
            if n != t['py']:
                problems.append('[数不对] %s 写 Python %d 条，实际 %d 条'
                                % (name, n, t['py']))
        for n in set(int(x) for x in re.findall(r'(\d{2,3}) 条前端', txt)):
            if n != t['js']:
                problems.append('[数不对] %s 写前端 %d 条，实际 %d 条'
                                % (name, n, t['js']))
        # 命令后面跟的 `# N 条` 注释。
        #
        # 🔴 这条规则漏过一次：原来写 `#\s*(\d{3}) 条`，只认三位数，
        #    于是 RELEASE.md 里 `node tests\front_check.js   # 67 条`
        #    躲过去了（67 是两位，而且不带「前端」二字，上面那条
        #    `(\d{2,3}) 条前端` 也匹配不到）。两条规则各差一点，
        #    凑在一起就是个洞 —— 发 v0.0.3 前照着 RELEASE 走流程才撞见。
        #
        #    现在按行判断该跟哪个数比：提到 front_check 的就是前端。
        for line in txt.split('\n'):
            m = re.search(r'#\s*(\d{2,4}) 条', line)
            if not m:
                continue
            n = int(m.group(1))
            want = t['js'] if 'front_check' in line else t['py']
            what = '前端' if 'front_check' in line else 'Python'
            if n != want:
                problems.append('[数不对] %s 的命令注释写 %d 条（%s），'
                                '实际 %d 条' % (name, n, what, want))
        if t['exe_mb']:
            for n in set(int(x) for x in re.findall(r'(\d{3}) MB', txt)):
                if 250 <= n <= 400 and n != t['exe_mb']:
                    problems.append('[数不对] %s 出现 %d MB，安装包实际 %d MB'
                                    % (name, n, t['exe_mb']))


def main():
    print('文档一致性检查')
    print('=' * 60)
    check_files()
    t = truth()
    print('真值（取自代码和产物）：')
    print('  测试 %d 条 Python / %d 条前端' % (t['py'], t['js']))
    print('  安装包 %s' % ('%d MB' % t['exe_mb'] if t['exe_mb'] else '(还没打)'))
    print('  GPU 运行库 %.1f GB / 模型 %.1f GB' % (t['torch_gb'], t['models_gb']))
    print()
    check_numbers(t)

    if problems:
        print('发现 %d 处：' % len(problems))
        for p in problems:
            print('  ·', p)
        return 1
    print('没查出问题')
    return 0


if __name__ == '__main__':
    sys.exit(main())
