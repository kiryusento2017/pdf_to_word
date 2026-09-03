# -*- coding: utf-8 -*-
r"""起 MinerU 的引导器：先打中文路径补丁，再把控制权交给它的 CLI。

    python.exe run_mineru.py mineru.cli.client -p x.pdf -o out ...
                             ^^^^^^^^^^^^^^^^^ 要跑的模块，其余原样转交

## 为什么不用 sitecustomize 那条常规路（2026-09-03 差点栽在这儿）

原来的做法是把 `sitepatch/` 塞进子进程的 `PYTHONPATH`，靠 `site` 自动
import 同名的 `sitecustomize`。开发环境（`.venv`）里这条路是通的，
298 条测试全绿。

**但发行版根本走不到。** 发行版的 Python 是 embeddable 版，目录里有
`python312._pth`；**只要 `._pth` 存在，`sys.path` 就完全由它决定，
`PYTHONPATH` 环境变量被直接忽略**。实测发行版子进程的 `sys.path` 只有
三条（`python312.zip` / `.` / `Lib\site-packages`），没有我们的目录，
于是 `sitecustomize` 一次都没被加载过 —— 补丁形同不存在，而所有测试
照样是绿的。又一次「开发机上好好的」。

放进 `runtime/python/Lib/site-packages/` 倒是能被自动加载（实测可行），
但那条路对**老用户无效**：更新包只覆盖 `pipeline/` `server/` `app/`，
碰不到 `runtime/`。v0.1.0 的用户更新完，那份补丁还是不会出现。

所以改成这条：**用脚本路径启动**。Python 会把脚本所在目录放进
`sys.path[0]`，这个机制不看 `._pth`、不看 `PYTHONPATH`，发行版和开发
环境完全一致；而这个文件在 `pipeline/` 底下，更新包覆盖得到。

一句话：注入点必须落在**更新包能覆盖、且不依赖 site 机制**的地方，
同时满足这两条的只有这里。
"""
import os
import runpy
import sys

# 脚本所在目录已经是 sys.path[0]，同目录的 sitecustomize 直接能 import。
# 兜一手：万一将来有人用 `-m` 之类的方式起它，路径就不一定在了。
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    import sitecustomize  # noqa: F401  —— import 的副作用就是打补丁
except Exception as e:
    # 🔴 补丁挂不上也要让 MinerU 跑起来 —— 那只影响中文路径的用户，
    #    而在这儿抛异常会让**所有人**都转不了。这行会被 _spawn 收进
    #    转换日志，出事时有据可查。
    sys.stderr.write('[sitepatch] 中文路径补丁未生效：%r\n' % (e,))

if len(sys.argv) < 2:
    sys.stderr.write('用法：run_mineru.py <模块名> [参数...]\n')
    raise SystemExit(2)

_mod = sys.argv.pop(1)
# click 拿 argv[0] 当程序名印在帮助和报错里，给它一个像样的
sys.argv[0] = 'mineru'
runpy.run_module(_mod, run_name='__main__')
