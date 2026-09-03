# -*- coding: utf-8 -*-
r"""让 MinerU 子进程能在**中文安装路径**下跑起来。

## 为什么会有这个文件

2026-09-03 真机报错，用户把软件装在 `C:\Users\32854\Desktop\新建文件夹 (2)`：

    Failed to load FastText model: ...\lid.176.ftz cannot be opened for loading!

MinerU 退出码 1，一个产物都没有。

**不是文件缺失。** `fast_langdetect/ft_detect/infer.py` 先做
`model_path.exists()`，不存在会报另一句话；报到「cannot be opened」，
说明文件就在那儿，是 fasttext 打不开它：

  · Python 把 str 路径按 **UTF-8** 编码交给 pybind11
  · fasttext 的 C++ 层用 `std::ifstream`，按系统 **ACP（中文 Windows 是
    936/GBK）** 解
  · 中文字节对不上，那个文件在 C++ 眼里就是不存在

而 MinerU 的 `utils/language.py` 那层 `try/except` 救不了 —— except 分支里
调的还是同一个函数，第二次照样失败，异常直接冒到顶，整个进程挂掉。

## 为什么是 mbcs，不是别的

本机实测（D 盘，ACP=936，`PYTHONUTF8` 开与不开结果一致）：

    ASCII 路径 + str          OK
    中文路径 + str            FAIL     ← 用户踩的
    中文路径 + 8.3 短路径     不可用    ← 见下
    中文路径 + mbcs bytes     OK       ← 采用
    中文路径 + utf-8 bytes    FAIL

🔴 **8.3 短路径这条看着最美，但是死路。** Win10 起非系统盘默认关闭
8.3 名生成（`fsutil 8dot3name`），`GetShortPathNameW` 会把长路径原样还
给你 —— 不报错、不降级，静默失效。小蔡的机器 D 盘就是这样。要是按那个
方案做，C 盘用户好好的，D 盘用户照炸，而且现象一模一样。

pybind11 的 str→std::string 走的是固定 UTF-8，跟 `PYTHONUTF8` 无关，
所以 **改不了 Python 那侧的编码，只能绕过它**：直接递 bytes 进去，
pybind11 原样透传，C++ 拿到的就是 ACP 字节，和它自己的解释方式对上了。

## 为什么用 sitecustomize，而不是直接改 fast_langdetect 的源码

改源码要在开发环境的 `.venv` 和发行版的 `runtime` **各改一次**，两边
迟早不同步；`pip` 重装或升级 `fast_langdetect` 就丢。这里是一份补丁同时
管两边，且第三方包保持原样。

**怎么挂上去的**：见同目录的 `run_mineru.py`。一句话——不是靠
`PYTHONPATH` + `site` 那条常规路（发行版的 embeddable Python 有 `._pth`，
PYTHONPATH 会被整个忽略，那条路在真实发行版上一次都没生效过），
而是让 MinerU 经由 `run_mineru.py` 启动，它 import 本模块作为第一步。

🔴 **单开一个目录只放这一个文件是有意的。** `pipeline/` 里有 `paths.py`、
`models.py`、`update.py` 这些名字很普通的模块，整个目录塞进 MinerU 子进程
的 `sys.path` 就可能盖掉它自己或它依赖的同名模块，那种冲突极难查。
"""
import sys


def native_path(path):
    r"""把路径转成 fasttext 的 C++ 层能打开的形式。

    ASCII 路径**原样返回**（`is` 相同的对象）—— 绝大多数用户走这条路，
    不该为了 1% 的人去改动 100% 的人的行为。
    """
    if not isinstance(path, str) or path.isascii():
        return path
    try:
        return path.encode('mbcs')
    except UnicodeEncodeError:
        # 中文系统上出现日文/韩文路径这类情况：当前代码页表示不了。
        # 🔴 必须在这儿翻成人话。让 UnicodeEncodeError 自己冒上去的话，
        #    用户在界面上只看到一串英文堆栈，完全不知道该改什么。
        raise ValueError(
            '路径里有当前系统编码无法表示的字符，识别引擎打不开它：\n'
            '  %s\n'
            '请把软件所在文件夹改成中文或英文名（不要混入日文、韩文等其他文字）。'
            % path)


def patch_fasttext():
    r"""接管 `fasttext.load_model`，中文路径改递 bytes。

    重复调用是安全的（认 `_zh_path_patched` 标记）—— 套两层的话第二层
    拿到的是 bytes，判断逻辑会全部落空。

    fasttext 没装就什么都不做：这个补丁挂在**所有**子进程上，而模型下载、
    装 GPU 运行库那两条链根本用不到 fasttext。
    """
    try:
        import fasttext
        import fasttext.FastText as _ft_mod
    except Exception:
        return False

    orig = fasttext.load_model
    if getattr(orig, '_zh_path_patched', False):
        return True

    def load_model(path, *args, **kwargs):
        return orig(native_path(path), *args, **kwargs)

    load_model._zh_path_patched = True
    load_model._zh_path_orig = orig

    # 🔴 两个都要换。`fasttext.load_model` 和 `fasttext.FastText.load_model`
    #    本来是同一个函数对象，但那是**两个模块各自的属性**：只换外面那个，
    #    从 FastText 子模块导入的调用方照样踩坑。
    fasttext.load_model = load_model
    _ft_mod.load_model = load_model
    return True


# 只有 Windows 有这个毛病（Linux/macOS 的文件系统编码就是 UTF-8）。
if sys.platform == 'win32':
    try:
        patch_fasttext()
    except Exception as e:
        # 🔴 补丁自己出问题，绝不能连累子进程起不来 —— 那会把「中文路径
        #    转不了」升级成「什么都转不了」。
        #    但也不能一声不吭：这行会被 _spawn 收进转换日志，出事时有据可查。
        sys.stderr.write('[sitepatch] fasttext 中文路径补丁未生效：%r\n' % (e,))
