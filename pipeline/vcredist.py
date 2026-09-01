# -*- coding: utf-8 -*-
r"""装 Microsoft Visual C++ 运行库。**第一步就装，不判断装没装。**

## 为什么不判断

小蔡 2026-09-02：「计算机重复安装 vc 会不会有问题」→「没有问题的话
第一步就强制安装 vc」。

那天之前，这件事上连着栽了四次，每次都是同一个形状 ——
**把「某个东西在不在」当成「能不能用」**：

    ① torch/version.py 在 → 报「GPU 运行库装好了」，而 c10.dll 加载不了
    ② models 目录在     → 报「模型齐了」，而里面是半截的
    ③ 数 DLL 文件        → 只查了 msvcp140.dll 一个，缺 msvcp140_1 的机器放行
    ④ 数 DLL + 我们自己的目录 → **没装 VC 的机器也打勾**，因为
                             Python embeddable 自带 vcruntime140*，
                             任何机器上都在（小蔡在网吧那台抓到的）

每次我的反应都是「把判断做得更准一点」，第五次该换思路了：**不判断**。

微软自己的文档就说：直接跑最新的 vc_redist 可以修复运行库，不用先卸载。
已经装了更新版本时它返回 `0x666` 自己退出，不做任何改动。所以重复运行
是安全的 —— 那就别猜了，装一次。

## 那怎么知道要不要装

只看**我们自己的记录**：安装目录里有没有 `vc_done.json`。

这是个准确的判据 —— 它记的是「这个软件在这台机器上装过一次 vc_redist」，
不是「这台机器装没装 VC++」。前者我们说了算，后者永远猜不准。

文件落在安装目录内，删文件夹一起没，符合项目的规矩。

## 权限

vc_redist 装系统级组件，会弹 UAC。用户点「否」或者账号没有管理员权限
就装不上 —— 这时候必须给出路（手动装的说明），不能卡死在这一屏。
"""
import io
import json
import os
import subprocess
import time

import paths

# 微软官方的短链，永远指向最新的 VC++ 2015-2022 x64 运行库。
# 用短链不用固定版本号：微软会更新它，而我们要的就是「最新那个」。
URL = 'https://aka.ms/vs/17/release/vc_redist.x64.exe'
SIZE_HINT = 25 * 1024 * 1024        # 约 25 MB，给进度条当兜底总量

# 装完认哪些退出码。
#
#   0       装好了
#   3010    装好了，但要重启才完全生效（我们不强制重启，运行库本身已可用）
#   1638    机器上有更新的版本（旧文档里的行为）
#   0x666   同上，新版安装器用这个码自己退出
#
# 后两个不是失败 —— 它们的意思是「你要的东西已经有了，而且更新」。
OK_CODES = (0, 3010, 1638, 0x666)


def marker_path():
    """装过的记号落在哪。安装目录内，删文件夹一起没。"""
    return os.path.join(paths.ROOT, 'vc_done.json')


def already_done():
    r"""这个软件在这台机器上装过 vc_redist 没有。

    **只看我们自己的记录**，不去猜系统装没装 —— 后者试过四次，
    每次都判错（见模块开头）。记录不在就再装一次，重复安装无害。
    """
    p = marker_path()
    if not os.path.isfile(p):
        return False
    try:
        with io.open(p, encoding='utf-8') as f:
            return bool(json.load(f).get('ok'))
    except Exception:
        return False        # 记号读不出来就当没装过，大不了多装一次


def _write_marker(code, version=''):
    try:
        paths.ensure(paths.ROOT)
        with io.open(marker_path(), 'w', encoding='utf-8') as f:
            f.write(json.dumps({'ok': True, 'exit_code': code,
                                'version': version,
                                'when': time.strftime('%Y-%m-%d %H:%M:%S')},
                               ensure_ascii=False, indent=2))
    except Exception:
        pass                # 记号写不下不影响这次已经装成功的事实


def installer_path():
    """下下来的安装包放哪。跟别的临时产物一样留在安装目录内。"""
    return os.path.join(paths.TMP, 'vc_redist.x64.exe')


def cmd_text():
    """跑的那条命令，显示给用户看。"""
    return '"%s" /install /passive /norestart' % installer_path()


def install(on_log=None, on_progress=None, stop_flag=None):
    r"""下载并运行 vc_redist。返回 (ok, error)。

    `/passive` 而不是 `/quiet`：让用户看得见微软自己的安装进度条。
    完全静默的话，UAC 弹出来用户不知道那是什么，更容易点「否」。
    """
    def log(s):
        if on_log:
            try:
                on_log(s)
            except Exception:
                pass

    exe = installer_path()
    paths.ensure(os.path.dirname(exe))

    # ── 下载 ────────────────────────────────────────────────────────
    log('下载 %s' % URL)
    try:
        import urllib.request
        req = urllib.request.Request(URL, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=60) as r:
            total = int(r.headers.get('Content-Length') or SIZE_HINT)
            got = 0
            with io.open(exe, 'wb') as f:
                while True:
                    if stop_flag and stop_flag():
                        return False, '已取消'
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if on_progress:
                        try:
                            on_progress(got, total)
                        except Exception:
                            pass
    except Exception as e:
        return False, ('下载 Visual C++ 运行库失败：%s。'
                       '可以自己去 %s 下一个装上，再回来点「重新检查」。'
                       % (str(e)[:120], URL))

    if os.path.getsize(exe) < 1024 * 1024:
        return False, '下下来的文件不完整，重试一次'
    log('下好了，%.1f MB' % (os.path.getsize(exe) / 1048576.0))

    # ── 启动安装程序，然后**不等它** ────────────────────────────────
    #
    # 小蔡 2026-09-02：「一旦开始装你就退出，然后用户装完了再把你
    # 点开来不好吗」。他是对的：
    #
    #   · vc_redist 自己有进度界面，我们再摆一个条是纯装饰，而且
    #     装完退出之后我们那个条还空着挂在那儿（他真见到了）
    #   · 不等 = 不用轮询 = 没有「轮询挂了界面卡死」这类 bug
    #     （第一版正是这么坏的：编了两个不存在的全局函数名）
    #   · 它有时要求重启电脑，我们本来就该让开
    log('')
    log(cmd_text())
    log('安装程序这就打开。如果弹出权限确认框，点「是」。')
    try:
        subprocess.Popen([exe, '/install', '/passive', '/norestart'],
                         close_fds=True)
    except Exception as e:
        return False, ('打不开安装程序：%s。'
                       '可以自己双击 %s 装一下。' % (str(e)[:100], exe))

    # 乐观写标记。万一用户中途取消，后面装 GPU 运行库那步会失败，
    # 那一屏有「再装一次 C++ 运行库」的出路，不会卡死。
    _write_marker(0)
    log('已经交给微软的安装程序了。装完请重新打开本软件。')
    return True, 
