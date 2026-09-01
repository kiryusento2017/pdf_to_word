# -*- coding: utf-8 -*-
"""用 GitHub 头像（终末诗篇·手写体）做多尺寸 .ico。

跑法：.venv\\Scripts\\python.exe tools\\make_icon.py
产物：app/icon.ico（预览图落系统临时目录）

两个必须解决的问题：
  1. 头像是**全透明底 + 黑墨迹** —— 深色任务栏上等于隐形。加白色圆角底。
  2. 四个字缩到 16px 每字只剩 8x8 像素，糊成灰块 —— 小尺寸改用单字。
"""
import os
from PIL import Image, ImageChops, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, 'app')
# 源图是小蔡的 GitHub 头像（github.com/kiryusento2017.png），
# 留在项目里，免得哪天头像换了就再也生成不出同一个图标。
src = Image.open(os.path.join(APP, 'icon_source.png')).convert('RGBA')


def ink_box(im):
    """墨迹的实际范围（按 alpha 找）。"""
    a = im.getchannel('A')
    return a.getbbox()


def tile(ink, size, pad_ratio=0.12, radius_ratio=0.18):
    """把一块墨迹放进白色圆角方块里。"""
    w, h = ink.size
    inner = int(size * (1 - pad_ratio * 2))
    scale = min(inner / float(w), inner / float(h))
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    ink2 = ink.resize((nw, nh), Image.LANCZOS)

    card = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * radius_ratio), fill=255)
    white = Image.new('RGBA', (size, size), (255, 255, 255, 255))
    card.paste(white, (0, 0), mask)
    card.paste(ink2, ((size - nw) // 2, (size - nh) // 2), ink2)
    # 圆角外面切掉
    out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    out.paste(card, (0, 0), mask)
    return out


full = src.crop(ink_box(src))

# 单字「终」：草书四字是斜排的 2x2，第一个字在左上。
# 范围目测切，切完会打印尺寸，看预览确认。
ZHONG = (39, 100, 172, 240)
one = src.crop(ZHONG)
one = one.crop(ink_box(one))
print('全图墨迹', full.size, ' 单字「终」', one.size)

# 小尺寸笔画要粗一点才看得见，所以留白更少
imgs = {
    16: tile(one, 16, pad_ratio=0.06, radius_ratio=0.16),
    24: tile(one, 24, pad_ratio=0.06, radius_ratio=0.16),
    32: tile(one, 32, pad_ratio=0.08, radius_ratio=0.17),
    48: tile(full, 48, pad_ratio=0.10),
    64: tile(full, 64, pad_ratio=0.10),
    128: tile(full, 128, pad_ratio=0.12),
    256: tile(full, 256, pad_ratio=0.12),
}

ico = os.path.join(APP, 'icon.ico')
imgs[256].save(ico, format='ICO',
               sizes=[(s, s) for s in sorted(imgs)],
               append_images=[imgs[s] for s in sorted(imgs) if s != 256])
print('[ico] %s  %d 字节' % (ico, os.path.getsize(ico)))

# 预览：每档放大 4 倍并排，深浅两种背景各一行
row = sorted(imgs)
cell = 128
for bgname, bg in (('light', (245, 245, 245)), ('dark', (32, 32, 32))):
    canvas = Image.new('RGB', (cell * len(row) + 10 * (len(row) + 1),
                               cell + 20), bg)
    for i, s in enumerate(row):
        big = imgs[s].resize((cell, cell), Image.NEAREST)
        canvas.paste(big, (10 + i * (cell + 10), 10), big)
    import tempfile
    canvas.save(os.path.join(tempfile.gettempdir(),
                             'icon_preview_%s.png' % bgname))
print('预览已出：', row)
