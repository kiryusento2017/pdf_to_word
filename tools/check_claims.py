# -*- coding: utf-8 -*-
r"""文档里那些「软件会怎么做」的断言，代码是不是真那么做的。

跑法：.venv\Scripts\python.exe tools\check_claims.py
**发版前跑**（docs/RELEASE.md 第一节），跟 check_docs.py 一对。

两个脚本查的是不同的东西：

    check_docs    数字对不对、提到的文件在不在      —— 硬事实
    check_claims  文档说的行为跟代码一不一致        —— 行为断言


check_docs 查的是「数字对不对、文件在不在」。这一遍查的是另一类东西：
文档里那些**关于软件会怎么做的断言**，代码是不是真那么做的。

这个项目在这上面栽过两次：
  · sources.py 的注释第二条写着「不用 ping 判优」，而实现算的就是延迟
  · docs/RELEASE.md 写着「依赖变了必须进次版本」，代码里没人执行

所以每条断言都得回到代码里找到那一行。
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))

bad = []
ok = 0


def read(p):
    return io.open(p, encoding='utf-8').read()


def claim(desc, cond, detail=''):
    global ok
    if cond:
        ok += 1
        print('  [ok] %s' % desc)
    else:
        bad.append('%s%s' % (desc, ('  —— ' + detail) if detail else ''))
        print('  [!!] %s' % desc)


print('文档断言 vs 代码行为')
print('=' * 60)
print()

# ── 落点与环境变量 ────────────────────────────────────────────────────
print('落点与环境变量')
import paths
env = paths.child_env()
claim('MINERU_DEVICE_MODE 写死成 cuda（README/DESIGN/RELEASE 都这么说）',
      env.get('MINERU_DEVICE_MODE') == 'cuda', repr(env.get('MINERU_DEVICE_MODE')))
claim('child_env 给四个变量（README 说「四个环境变量」）',
      all(k in env for k in ('MODELSCOPE_CACHE', 'HF_HOME',
                             'MINERU_TOOLS_CONFIG_JSON', 'MINERU_DEVICE_MODE')))
claim('配置指向安装目录内，不碰 ~/mineru.json',
      os.path.abspath(paths.CONFIG).startswith(os.path.abspath(paths.ROOT)))
claim('日志落在安装目录内的 logs/',
      os.path.abspath(paths.LOGS).startswith(os.path.abspath(paths.ROOT)))

# ── 跑 MinerU 的方式 ─────────────────────────────────────────────────
print()
print('跑 MinerU 的方式')
cmd = paths.mineru_cmd()
claim('用「解释器 + 我们自己的 .py」，不调 Scripts/*.exe（README 有专门一节）',
      cmd[1].endswith('run_mineru.py')
      and not any('.exe' in a.lower() for a in cmd[1:]),
      repr(cmd))
claim('models_download 也走同一个引导脚本',
      paths.models_download_cmd()[1].endswith('run_mineru.py'))
# 查的是「有没有往 env 里塞 PYTHONPATH」这个动作，不是文本里出现过
# 这个词 —— child_env 的说明里正解释着为什么不能用它。
claim('中文路径补丁不靠 PYTHONPATH（发行版 ._pth 会忽略它）',
      "env['PYTHONPATH']" not in read('pipeline/paths.py'))
claim('判 MinerU 在不在用 find_spec，不是查文件',
      'find_spec' in read('pipeline/paths.py'))

# ── GPU 运行库 ───────────────────────────────────────────────────────
print()
print('GPU 运行库')
import torchdep
claim('驱动 ≥570 → cu128（README 那张表）',
      torchdep.pick_channel('572.83')[0] == 'cu128')
claim('驱动 ≥525 → cu126',
      torchdep.pick_channel('531.41')[0] == 'cu126')
claim('其余 / 读不到 → cu118',
      torchdep.pick_channel('470.05')[0] == 'cu118'
      and torchdep.pick_channel('')[0] == 'cu118')
claim('装完会真 import 一次（README：「装完还会真的 import 一次」）',
      'can_load()' in read('pipeline/torchdep.py'))
claim('验不过会把 torch 卸掉（DESIGN 第七节坑三）',
      'uninstall()' in read('pipeline/torchdep.py'))
# ── C++ 运行库：2026-09-02 换了方案 ─────────────────────────────────
#
# 原来这里钉的是「会查 vcruntime140.dll 在不在」。那个做法当天被真机
# 推翻了：Python embeddable 自带 vcruntime140*，哪台机器上都在，于是
# 一台从没装过 VC++ 的电脑照样打勾（小蔡在网吧那台抓到）。
#
# 现在钉的是新方案：不判断系统装没装，直接装一次，记个自己的标记。
tv = read('pipeline/vcredist.py')
claim('有独立的 vcredist 模块负责装 C++ 运行库',
      'def install(' in tv and 'vc_redist.x64.exe' in tv)
claim('判据是「我们装过没有」，不是数系统里的 DLL（小蔡 2026-09-02 抓到的 bug）',
      'def already_done' in tv and 'vc_done.json' in tv)
claim('认 0x666 / 1638 为成功 —— 那是「已有更新版本」不是失败',
      '0x666' in tv and '1638' in tv)
claim('启动安装程序之后不等它（小蔡：「一旦开始装你就退出」）',
      'subprocess.Popen' in tv and 'subprocess.run' not in tv)
claim('启动失败时给手动装的出路，不卡死',
      '打不开安装程序' in tv and '双击' in tv)
claim('装 C++ 运行库排在装 GPU 运行库之前（小蔡：「不要排在 gpu 库后面」）',
      read('pipeline/torchdep.py').index('vcredist.already_done()',
          read('pipeline/torchdep.py').index('def install('))
      < read('pipeline/torchdep.py').index('subprocess.Popen',
          read('pipeline/torchdep.py').index('def install(')))
claim('前端的拦截顺序也是 C++ 运行库在前',
      read('app/renderer/pages.js').index("return 'vcredist'")
      < read('app/renderer/pages.js').index("return 'cudalib'"))
claim('vc_done.json 不许打进安装包（打进去等于给每个新用户假标记）',
      "'-x!vc_done.json'" in read('tools/build_release.py')
      and 'vc_done.json' in read('tools/check_package.py'))

# ── 公式 ─────────────────────────────────────────────────────────────
print()
print('公式')
td = read('pipeline/todocx.py')
claim('失败时把已写出的 Word 改名留下（README：「不删，改名」）',
      'degraded_path(out_path)' in td and '公式未完全转换' in td)
claim('公式走占位符定位，不靠数量相等（README：「先把每个公式换成占位符」）',
      '_ast_swap_math' in td and '_fill_placeholders' in td)
claim('每个读子进程输出的地方都强制 UTF-8（中文 Windows 默认 cp936）',
      'PYTHONIOENCODING' in read('pipeline/paths.py')
      and 'utf8_env' in read('pipeline/gpu.py')
      and read('pipeline/torchdep.py').count('utf8_env') >= 2
      and 'utf8_env' in read('pipeline/extract.py')
      and 'PYTHONIOENCODING' in read('app/main.js'),
      '漏一处，那一处的中文错误信息就是乱码')
claim('四条降级路径都判失败（DESIGN 第四节）',
      td.count("rep['error']") >= 4)

# ── 更新 ─────────────────────────────────────────────────────────────
print()
print('更新')
up = read('pipeline/update.py')
claim('查版本会并发试多条路（README/DESIGN 都写了镜像会变）',
      'API_MIRRORS' in up and 'def api_race' in up
      and 'ThreadPoolExecutor' in up and up.count("'https://") >= 4)
claim('API 名单和下载名单是两份（api.github.com 限速，镜像多半 403）',
      'API_MIRRORS = [' in up and 'GH_MIRRORS = [' in up)
claim('线路明细交给界面，失败时也给（README「不给黑盒」）',
      'e.lines = lines' in up and "'lines': []" in up)
claim('手动指定的线路真的生效，不是个假开关',
      'prefer=' in up and "prefer='ghp-cdn'" in read('tests/test_update.py'))
claim('**禁止**拿版本号判断能不能自动装（小蔡 2026-09-02 的指令）',
      'rv[:2] != lv[:2]' not in up.split('#')[0] or
      up.count('rv[:2] != lv[:2]') == up.count('#    这里原来是'),
      '代码里还在拿版本号判断')
claim('改成拉依赖清单比对（RELEASE 四点五节）',
      '_requires_gap' in up and 'check_requires' in up)
claim('依赖清单从实际装的包里读，不是手写（build_release）',
      '_md.version' in read('tools/build_release.py'))
claim('装之前就比对，不是装完才发现（apply_update）',
      up.index('check_requires(raw)') < up.index('shutil.copyfile'))
claim('下完校验 SHA256（DESIGN：「校验 SHA256」）',
      'hashlib.sha256' in up)
claim('拿不到校验值不是硬拒绝，而是要用户确认（RELEASE 报警不阻拦）',
      'NEED_CONFIRM' in up)
claim('zip slip 防护还在',
      'startswith(root + os.sep)' in up)
srv = read('server/main.py')
claim('后端自己查 Release，不看前端传的 url',
      'update.check()' in srv and 'req.url' not in srv)
claim('digest 和 size 都传给了 download',
      "digest=asset.get('digest'" in srv and "size=asset.get('size'" in srv)

# ── 更新包路径 ───────────────────────────────────────────────────────
print()
print('打包')
br = read('tools/build_release.py')
claim('更新包路径映射到 resources/app（RELEASE 里的红字警告）',
      "'app/renderer', 'resources/app/renderer'" in br.replace('(', '').replace(')', ''))
claim('装完卸掉 torch（RELEASE：CUDA 版 torch 不打进安装包）',
      "'pip', 'uninstall'" in br)
claim('--sfx 会校验版本（审计第 9 条）',
      "跟 --version" in br or 'have != a.version' in br)

# ── 停止 ─────────────────────────────────────────────────────────────
print()
print('停止')
ex = read('pipeline/extract.py')
claim('转换的停止用独立 watch 线程（RELEASE：不许写进阻塞的读取循环）',
      'def watch()' in ex and 'taskkill' in ex)
claim('models 下载的停止同样',
      'def watch()' in read('pipeline/models.py'))
claim('torchdep 安装的停止同样',
      'def watch()' in read('pipeline/torchdep.py'))

# ── 进度 ─────────────────────────────────────────────────────────────
print()
print('进度与估时')
claim('只有 GPU 一个速率（README：「速率是实测的（26 秒/页）」）',
      'SEC_PER_PAGE_GPU = 26.0' in srv and 'SEC_PER_PAGE_CPU' not in srv)
pg = read('app/renderer/pages.js')
claim('不显示阶段内的 x/y 数字（README/DESIGN 都写了为什么）',
      "t.stage_cur + '/' + t.stage_total" not in pg)
claim('下载面板显示命令（小蔡：「要弹出背后的命令」）',
      "o.cmd" in pg and 'class="log"' in pg)

# ── 服务 ─────────────────────────────────────────────────────────────
print()
print('服务')
claim('只绑 127.0.0.1（README/DESIGN）',
      "'127.0.0.1'" in srv)
claim('端口系统分配，不写死',
      'port=0' in srv or 'find_free_port' in srv or 'PDF2WORD_PORT' in srv)

print()
print('=' * 60)
print('通过 %d 条' % ok)
if bad:
    print('对不上 %d 条：' % len(bad))
    for b in bad:
        print('  ·', b)
    sys.exit(1)
print('文档说的和代码做的一致')
