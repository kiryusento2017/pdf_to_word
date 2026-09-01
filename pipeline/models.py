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
