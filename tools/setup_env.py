# -*- coding: utf-8 -*-
r"""把这台机器上的运行环境装齐 —— 让 pdf_to_word 不再借工作台的 MinerU。

现在的状态：代码、pandoc、KaTeX 都在自己目录里，但 **MinerU 本体和那 4.6 GB
模型是借工作台的**（server/main.py 的 _find_mineru 有一条开发期退路）。
要独立分发，这台机器上得有自己的一份。

装的东西和体积：
    torch (CUDA 版)   约 2.8 GB   ← 大头，从 PyTorch 官方源拉
    mineru            约 200 MB
    模型权重          约 4.6 GB   ← 首次转换时 MinerU 自己下，不在这里装

跑法：
    .venv\Scripts\python.exe tools\setup_env.py          # 装
    .venv\Scripts\python.exe tools\setup_env.py --check  # 只看装没装
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, '.venv', 'Scripts', 'python.exe')

# torch 的源。B35 的实测结论：**torch 是官方源更快**，跟 Chromium 相反 ——
# 所以这里默认官方，国内用户可以用 --torch-mirror 换阿里云。
TORCH_INDEX = 'https://download.pytorch.org/whl/cu128'
TORCH_MIRRORS = {
    'official': TORCH_INDEX,
    'aliyun': 'https://mirrors.aliyun.com/pytorch-wheels/cu128/',
    'nju': 'https://mirrors.nju.edu.cn/pytorch/whl/cu128/',
}


def have(mod):
    r = subprocess.run([PY, '-c', 'import %s' % mod],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return r.returncode == 0


def check():
    rows = [
        ('pymupdf', have('pymupdf')),
        ('lxml', have('lxml')),
        ('fastapi', have('fastapi')),
        ('torch', have('torch')),
        ('mineru', os.path.isfile(os.path.join(ROOT, '.venv', 'Scripts', 'mineru.exe'))),
    ]
    print('%-12s %s' % ('组件', '装了没'))
    print('-' * 26)
    for name, ok in rows:
        print('%-12s %s' % (name, '是' if ok else '否'))
    missing = [n for n, ok in rows if not ok]
    print('')
    if missing:
        print('还缺：%s' % '、'.join(missing))
        print('跑 tools\\setup_env.py 装齐')
    else:
        print('都齐了。首次转换时 MinerU 会自己下模型（约 4.6 GB）。')
    return not missing


def pip(args, desc):
    print('')
    print('=== %s ===' % desc)
    r = subprocess.run([PY, '-m', 'pip', 'install'] + args)
    if r.returncode != 0:
        print('失败了：%s' % desc)
        return False
    return True


def main():
    if '--check' in sys.argv:
        sys.exit(0 if check() else 1)

    mirror = 'official'
    for i, a in enumerate(sys.argv):
        if a == '--torch-mirror' and i + 1 < len(sys.argv):
            mirror = sys.argv[i + 1]
    index = TORCH_MIRRORS.get(mirror, TORCH_INDEX)

    print('目标目录：%s' % ROOT)
    print('torch 源：%s（%s）' % (mirror, index))
    print('')
    print('总下载量约 3 GB，装完之后首次转换时 MinerU 还会自己下约 4.6 GB 模型。')

    if not have('torch'):
        # torch 单独装，且指定 CUDA 版的 index —— 不指定会装到 CPU 版，
        # 那样 MinerU 会悄悄退回 CPU 跑，慢十几倍而且没有任何提示。
        if not pip(['torch', 'torchvision', '--index-url', index],
                   '装 torch（CUDA 12.8 版，约 2.8 GB）'):
            sys.exit(1)

    if not os.path.isfile(os.path.join(ROOT, '.venv', 'Scripts', 'mineru.exe')):
        if not pip(['mineru'], '装 MinerU'):
            sys.exit(1)

    print('')
    check()


if __name__ == '__main__':
    main()
