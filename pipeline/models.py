# -*- coding: utf-8 -*-
r"""模型：认已有的 / 下新的。

两条路都要写我们自己那份 `mineru.json`（`paths.CONFIG`），
**绝不碰用户主目录里的全局配置** —— 用户机器上可能装着别的用 MinerU
的东西，改它等于动别人的家当。MinerU 认 `MINERU_TOOLS_CONFIG_JSON`
环境变量来找配置，`paths.child_env()` 已经把它指过来了。

配置长这样（照着 MinerU 自己生成的抄，键名不能改）：

    {
      "models-dir": {"pipeline": "<...>/snapshots/master",
                     "vlm":      "<...>/snapshots/master"},
      "model-source": "modelscope",
      "config_version": "1.3.2"
    }

⚠️ `models-dir` 要的是 **snapshots/master 那一层**，不是模型仓库的根。
   指错一层 MinerU 不会报错，只会在推理时说找不到权重 —— 那种错误
   很难查到根因上，所以这里做自动识别而不是让用户手填。
"""
import io
import json
import os
import re

import paths

CONFIG_VERSION = '1.3.2'

# MinerU 的两个模型仓库。名字里的版本号会变（MinerU2.5-Pro-2605-1.2B），
# 所以用前缀匹配，别写死整个目录名。
_PIPELINE_RE = re.compile(r'PDF-Extract-Kit', re.I)
_VLM_RE = re.compile(r'MinerU[\d.]*-?\w*', re.I)


def _find_snapshot(root, pattern, max_depth=6):
    r"""在 root 下找出 `<匹配 pattern 的目录>/snapshots/<任意>` 这一层。

    深度设了上限：用户可能选中一个巨大的目录（比如整个 D 盘），
    无限递归会让界面卡死几分钟。
    """
    root = os.path.abspath(root)
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, _filenames in os.walk(root):
        if dirpath.count(os.sep) - base_depth > max_depth:
            dirnames[:] = []          # 太深了，不再往下走
            continue
        if os.path.basename(dirpath).lower() != 'snapshots':
            continue
        parent = os.path.basename(os.path.dirname(dirpath))
        if not pattern.search(parent):
            continue
        # snapshots 下面通常只有一个 master/ 或一串哈希，取第一个非空的
        for sub in sorted(dirnames):
            cand = os.path.join(dirpath, sub)
            if _has_weights(cand):
                return cand
    return None


def _has_weights(d):
    """目录里有没有像模型权重的大文件（> 1 MB）。空壳目录不算数。"""
    for dirpath, _dirnames, filenames in os.walk(d):
        for fn in filenames:
            try:
                if os.path.getsize(os.path.join(dirpath, fn)) > 1024 * 1024:
                    return True
            except OSError:
                continue
    return False


def detect(root):
    r"""认一个已有的模型目录。返回 {'pipeline': ..., 'vlm': ...}，
    认不出来的那项是 ''。两项都空说明这目录里没有模型。

    用户可能选中 `.../models`，也可能选中它的上一层，所以是递归找。
    """
    if not root or not os.path.isdir(root):
        return {'pipeline': '', 'vlm': ''}
    return {
        'pipeline': _find_snapshot(root, _PIPELINE_RE) or '',
        'vlm': _find_snapshot(root, _VLM_RE) or '',
    }


def read_config():
    """读我们自己那份配置。没有或坏了都返回空 dict，不抛异常。"""
    try:
        with io.open(paths.CONFIG, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def write_config(pipeline_dir='', vlm_dir='', source='modelscope'):
    r"""写我们自己那份 mineru.json。

    只写必要的键。**保留已有的其他键** —— MinerU 升级后配置里可能多出
    新字段，整个覆盖会把它们抹掉。
    """
    cfg = read_config()
    md = cfg.get('models-dir')
    if not isinstance(md, dict):
        md = {}
    if pipeline_dir:
        md['pipeline'] = pipeline_dir
    if vlm_dir:
        md['vlm'] = vlm_dir
    cfg['models-dir'] = md
    cfg['model-source'] = source or cfg.get('model-source') or 'modelscope'
    cfg.setdefault('config_version', CONFIG_VERSION)
    paths.ensure(os.path.dirname(paths.CONFIG) or paths.ROOT)
    with io.open(paths.CONFIG, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


def configured_dirs():
    """当前配置里指向哪。返回 (pipeline, vlm)，没配就是空串。"""
    md = read_config().get('models-dir')
    if not isinstance(md, dict):
        return '', ''
    return md.get('pipeline') or '', md.get('vlm') or ''


def ready():
    r"""模型能不能用。

    判据是**配置里指的那个目录真有权重**，而不是「models/ 目录非空」——
    用户可能把模型放在别处（我们支持指过去），也可能下载中断留下空壳。
    两种情况只看目录都会判错。

    ⚠️ **这个判据宽松，绝不能拿来判断「下载完成了没有」。**
       它只看「有没有 >1 MB 的文件」，下载刚开始、第一个文件落盘就成立。
       2026-09-02 真机上前端拿它当完成条件，结果进度条跑了一点点就跳回
       主界面、后台还在下，用户以为下完了直接去转换。
       判断下载完成要看下载流程自己的状态（server 那边的 _DL['state']）。

       为什么不把它改严（比如按总大小卡）：那会误判**已经下好**的用户，
       逼他重下 4.6 GB，代价比漏判大。它的本职是「启动时判断模型在不在」，
       在那个场景下宽松是对的 —— 真下坏了转换会失败并报错，重下即可。
    """
    p, v = configured_dirs()
    for d in (p, v):
        if d and os.path.isdir(d) and _has_weights(d):
            return True
    return False


def where():
    """模型实际在哪（给界面显示）。没有就返回空串。"""
    p, v = configured_dirs()
    for d in (p, v):
        if d and os.path.isdir(d):
            return d
    return ''


# ── 下载 ────────────────────────────────────────────────────────────────
# 模型总量按 MinerU 实际拉下来的量估。用来算进度百分比，估偏一点不要紧，
# 界面显示的是「已下 X GB」这个真数，百分比只是个参考。
TOTAL_BYTES = int(4.6 * 1024 * 1024 * 1024)


def download_cmd():
    r"""跑 MinerU 模型下载器的命令前缀。

    🔴 **不用 `Scripts/mineru-models-download.exe`**。那个 exe 是 pip
       生成的 launcher，尾部硬编码了打包机器上的 python.exe 绝对路径，
       换台机器就找不到解释器 —— 2026-09-02 网吧实测就是栽在这儿：
       测速正常（走我们自己的 Python 代码），下载一直失败（走 launcher）。
       详见 paths.py 里那段说明。
    """
    return paths.models_download_cmd()


def download_argv(source='modelscope'):
    """下模型的完整命令。"""
    return download_cmd() + ['-s', source or 'modelscope', '-m', 'all']


def download_cmd_text(source='modelscope'):
    """命令的可读形式，显示在日志区第一行。"""
    return ' '.join(download_argv(source))


def log_path():
    """完整日志落在哪。界面上把这个路径给用户，出问题直接发文件。"""
    return os.path.join(paths.LOGS, 'model_download.log')


def download(source='modelscope', on_progress=None, on_log=None,
             stop_flag=None):
    r"""下模型。返回 (ok, error)。

    **不自己实现下载器**，调 MinerU 自带的 `mineru-models-download` ——
    自己实现等于跟它抢活，两边对模型清单的理解一旦不一致，就会下出一个
    结构看着对、跑起来缺文件的半套。它下完还会把路径写进配置，
    而 `paths.child_env()` 已经把配置文件指向我们自己那份了。

    进度**不解析它的 tqdm 输出**：那格式带单位（`1.2G/2.7G`）、随
    modelscope/huggingface 的版本变，解析器很容易在某次升级后静默失效。
    改成每秒统计目标目录的实际大小 —— 实测 walk 4.6 GB 只要 1 毫秒，
    而且这个数就是用户真正关心的「下了多少」。

    stop_flag: 一个可调用对象，返回 True 表示用户要求中止。
    """
    if not paths.mineru_available():
        return False, '这个 Python 环境里没有 MinerU，安装环境不完整'
    cmd = download_cmd()

    # 🔴 起下载器之前先把配置文件建出来。
    #
    #    MinerU 下完模型要写配置，走的是 download_and_modify_json：
    #        if os.path.exists(配置):  ...（版本不过期就不联网）
    #        else:                     data = download_json(模板URL)  ← 必须联网
    #    模板在 jsdelivr 上。新用户机器上配置不存在，于是**下完 4.6 GB
    #    之后**才需要联网拉那个模板 —— 失败点落在最贵的位置：拉不到就
    #    整个下载器非 0 退出，模型白下，用户被要求重来一遍。
    #    实测那个 URL 6 次里失败过 1 次（SSL 错误）。
    #
    #    先写一份出来，MinerU 就走 exists 分支，全程不联网。
    #    开发机上永远撞不见这个坑（配置早就有了），只能靠读它的源码发现。
    if not os.path.isfile(paths.CONFIG):
        try:
            write_config(source=source or 'modelscope')
        except Exception:
            pass          # 写不出来也别拦着下载，让它自己去试

    import subprocess
    import threading
    import time

    target = paths.ensure(paths.MODELS)
    env = paths.child_env({'MINERU_MODEL_SOURCE': source} if source else None)

    # 🔴 下载器说了什么，必须留下来。
    #    2026-09-02 网吧实测：测速正常、下载一直失败，而这边只有一句
    #    「多半是网络断了」—— 那句话是猜的，真实原因（磁盘满？SSL？
    #    代理？权限？）被丢得一干二净，隔着几百公里没法查。
    #    日志落在安装目录内的 logs/，符合「删文件夹 = 卸载干净」。
    paths.ensure(paths.LOGS)
    log_file = log_path()
    try:
        log_fp = io.open(log_file, 'w', encoding='utf-8', errors='replace')
        log_fp.write('# %s\n' % ' '.join(cmd + ['-s', source or 'modelscope',
                                                  '-m', 'all']))
        log_fp.write('# 目标: %s\n\n' % target)
    except Exception:
        log_fp = None
    tail = []                      # 最后几行，失败时拼进错误信息

    stop_watch = threading.Event()
    proc_box = []          # watch 线程通过它拿到进程对象
    killed = []            # 记一下「是用户主动停的」

    def kill_tree(p):
        """连子孙一起杀。下载器自己不起子进程，但 /T 不会有坏处。"""
        try:
            subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        except Exception:
            pass
        try:
            p.terminate()
        except Exception:
            pass

    def watch():
        """每秒报一次已下多少，顺便看用户是不是要停。

        🔴 停止检查放在这里，不放主循环 —— 主循环阻塞在
           `p.stdout.read()` 上，下载器不吐东西时根本走不到检查那行，
           而「卡住不动」正是用户最想点停止的时候。
        """
        while not stop_watch.is_set():
            if stop_flag and stop_flag() and proc_box:
                killed.append(True)
                kill_tree(proc_box[0])
                return
            try:
                got = 0
                for dp, _dn, fns in os.walk(target):
                    for fn in fns:
                        try:
                            got += os.path.getsize(os.path.join(dp, fn))
                        except OSError:
                            pass
                if on_progress:
                    on_progress(got, TOTAL_BYTES)
            except Exception:
                pass
            stop_watch.wait(1.0)

    t = threading.Thread(target=watch, daemon=True)
    t.start()
    try:
        p = subprocess.Popen(
            cmd + ['-s', source or 'modelscope', '-m', 'all'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=env, cwd=paths.ROOT)
        proc_box.append(p)
        buf = b''
        while True:
            chunk = p.stdout.read(256)
            if not chunk:
                break
            buf += chunk
            # CR 和 LF 都算行尾 —— tqdm 用回车刷新不换行，
            # 按行读会一直卡到进度条结束（extract.py 那边栽过这个）
            while True:
                i = min([x for x in (buf.find(b'\r'), buf.find(b'\n')) if x >= 0]
                        or [-1])
                if i < 0:
                    break
                line = buf[:i].decode('utf-8', 'replace').strip()
                buf = buf[i + 1:]
                if line:
                    if log_fp:
                        try:
                            log_fp.write(line + '\n')
                            log_fp.flush()   # 卡住时也要能看见已经到哪了
                        except Exception:
                            pass
                    # tqdm 的进度条刷屏，别把 tail 占满 —— 只留非进度行
                    if '%|' not in line:
                        tail.append(line)
                        if len(tail) > 40:
                            del tail[0]
                    if on_log:
                        on_log(line)
        # 🔴 最后一段常常没有换行符 —— 而进程崩掉时，最后那一行
        #    恰恰是最重要的一行。原来的循环只处理带 \r 或 \n 的部分，
        #    buf 里剩下的直接丢了。
        last = buf.decode('utf-8', 'replace').strip()
        if last:
            if log_fp:
                try:
                    log_fp.write(last + '\n')
                except Exception:
                    pass
            if '%|' not in last:
                tail.append(last)
        p.wait()
        rc = p.returncode
    except Exception as e:
        return False, '%s: %s' % (type(e).__name__, str(e)[:200])
    finally:
        stop_watch.set()
        if log_fp:
            try:
                log_fp.close()
            except Exception:
                pass
        time.sleep(0)

    if killed:
        # 用户主动停的，不是故障。
        # **半截文件不删**：modelscope 支持断点续传，删了等于让人从头再下
        # 4.6 GB。留着的代价只是占点硬盘，而那些文件本来就在 models/ 里，
        # 删掉整个软件文件夹一样清得干净。
        return False, '已取消'

    if rc != 0:
        # 不再说「多半是网络断了」—— 那是猜的。把下载器自己说的话给出来，
        # 完整日志的路径也一并告诉用户，好让他直接把文件发过来。
        why = ' / '.join(tail[-3:])[:300]
        return False, ('下载失败（退出码 %s）。%s完整日志：%s'
                       % (rc, ('下载器说：%s。' % why) if why else '', log_file))
    if not ready():
        return False, ('下载结束了，但没找到可用的模型文件。完整日志：%s'
                       % log_file)
    return True, ''
