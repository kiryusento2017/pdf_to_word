# 发行版规矩

发一个版本要做什么、不能做什么。**每次发版前从头照着走一遍**，
别凭记忆——这份文件存在的理由就是记忆会出错。

---

## 一、发之前必须做的事

按顺序，一步不能少。

### 1. 测试全绿

```
.venv\Scripts\python.exe -m unittest discover -s tests -q   # 242 条
node tests\front_check.js                                   # 78 条
```

**红一条都不许发。** 不存在「这条测试早就坏了不用管」——
真不用管就该删掉它，留着等于养一个会说谎的哨兵。

### 2. 界面自己看一遍

改动碰了界面的话，真开窗截图确认。这个项目栽过两次「测试全绿但
实际没生效」，前端尤其容易——测试断言的是字符串，不是眼睛看到的东西。

### 3. 文档跟上

先跑自动检查：

```
.venv\Scripts\python.exe tools\check_docs.py      # 硬事实
.venv\Scripts\python.exe tools\check_claims.py    # 行为断言
.venv\Scripts\python.exe tools\check_package.py   # 打完包之后跑
```

三个查的是不同的东西：

| | 查什么 | 抓得到的那类错 |
|---|---|---|
| `check_docs` | 数字对不对、提到的文件在不在 | 「README 写 208 条，实际 240 条」 |
| `check_claims` | 文档说的行为跟代码一不一致 | 「注释写『不用 ping 判优』，实现算的就是延迟」 |
| `check_package` | 安装包里有没有不该有的东西 | 「包里 80 个 `_tmp`/`appdata` 条目」「`使用说明.txt` 还是上一版」 |

`check_package` 要在**打完包之后**跑，前两个随时能跑。它的存在是因为
v0.0.2 就带着开发机的运行时垃圾发出去了 —— 而「在 dist 里真跑一次」
是验证发行版的必要动作，垃圾一定会产生，只能靠打包时排除 + 打完再查一遍。

真值都取自**代码和产物**，不是取自另一份文档 —— 那样只会让错误互相印证。

**它查不了的那部分要人看**：代码改了策略，README / DESIGN 必须同步。
判断标准：**一个没参与过开发的人照着文档做，会不会做错。**

历史教训：09-01 把 XSL 从「优先」改成「硬性要求」，README 整节还写着
「没装 Office 就用内置 Pandoc」，跟实际行为完全相反。

### 4. 版本号

`v主.次.修`，从 `v0.0.1` 起。什么时候进位：

| | 什么情况 |
|---|---|
| 修订号 `v0.0.x` | 只改业务代码（bug 修复、界面调整、文案） |
| 次版本 `v0.x.0` | 依赖变了（换 MinerU 版本、加新的 pip 包）、或功能有明显变化 |
| 主版本 `vx.0.0` | 用户要重新学怎么用，或者不兼容旧的安装 |

**依赖变了建议进次版本** —— 但这只是给人看的约定，**软件不靠它做判断**
（见下面第四点五节）。

⚠️ 反过来说：**跨多少个版本都不用一个一个更新**。更新包是全量替换
（装的是当前版本的全部业务代码，不是 diff），v0.0.1 直接下 v0.0.31
的包就变成 v0.0.31。

⚠️ 已知限制：更新包**不会删文件**。哪个版本删掉了某个 `.py`，
老用户更新后那个文件还留在硬盘上。目前无害（没人 import 它），
真要删文件的话得在更新包里带一份「该删什么」的清单。

---

## 二、打包

### 完整安装包（新用户装的）

```
.venv\Scripts\python.exe tools\build_release.py --version v0.0.1
```

组装 `dist\PDF2Word\` 并装好依赖，然后：

```
.venv\Scripts\python.exe tools\build_release.py --version v0.0.1 --sfx
```

出 `dist\PDF2Word-Setup-v0.0.1.exe`（自解压，双击就装）。

`--sfx` 不重新组装，只打包已有产物——组装一次要下 Electron、装依赖，
十几分钟，改个 SFX 配置不该重来一遍。

**升版本号时要加 `--bump`**：`--sfx` 会校验产物里的版本号跟 `--version`
一致，防的是「拿上次的产物打成新版本号」——那种包发出去，用户装完点
检查更新还会被告知「已是最新」，因为里面的 `version.json` 写的是老版本。

但「同步代码 → 升版本号 → `--sfx`」是正常流程（见下面「手工改过发行版
目录的话」），所以留了显式开关：

```
.venv\Scripts\python.exe tools\build_release.py --version v0.0.3 --sfx --bump
```

加了 `--bump` 就得自己确认代码真同步过了 —— 工具没法替你验这件事。
**依赖也变了的话不能用这条路**，要跑完整构建，否则 `requires.json`
记的还是旧依赖。

### 更新包（老用户自动下的）

```
.venv\Scripts\python.exe tools\build_release.py --version v0.0.1 --update-only
```

出 `dist\pdf_to_word-v0.0.1-update.zip`，约 0.4 MB。

### 手工改过发行版目录的话

`--sfx` 打的是 `dist\PDF2Word\` 里**当前**的内容。如果代码改了，
先同步再打包，否则打出来的是旧代码：

🔴 **前端的目标路径是 `resources\app\`，不是 `app\`。**
源码里前端在 `app/`，发行版里在 `resources/app/`（Electron 标准形态）。
同步到 `app\` 的话软件根本读不到，而且**不会报错** —— 它会安静地
跑着旧的前端代码。

```
robocopy pipeline dist\PDF2Word\pipeline /MIR
robocopy server dist\PDF2Word\server /MIR
robocopy app\renderer dist\PDF2Word\resources\app\renderer /MIR
copy app\main.js app\preload.js app\package.json app\icon.ico ^
     dist\PDF2Word\resources\app\
```

同步完记得清 `__pycache__`，不然它会被打进安装包（里面还嵌着开发机的路径）：

```
for /d /r dist\PDF2Word %d in (__pycache__) do @rd /s /q "%d" 2>nul
```

---

## 三、命名规矩

**一律英文。** 两个原因：

1. GitHub 会把 Release 附件名里的中文吃掉——`PDF转Word-v0.0.1.exe`
   传上去显示成 `PDF.Word-v0.0.1.exe`（「转」变成了点）。
2. 中文路径要经过 Electron → Python 子进程 → MinerU → pandoc 好几手，
   任何一环处理不好都会出问题，而**我们全程在英文路径下开发测试，
   中文路径一次没验过**。默认给中文路径等于让老师去踩没人走过的路。

| | 名字 |
|---|---|
| 安装包 | `PDF2Word-Setup-v0.0.1.exe` |
| 更新包 | `pdf_to_word-v0.0.1-update.zip` |
| 解压出来的目录 | `PDF2Word` |
| SFX 默认安装路径 | `D:\PDF2Word` |

**界面上的中文全部保留**——那是给用户看的，跟文件系统无关。

---

## 三点五、发行版长什么样

Electron 应用的标准形态（VS Code、Discord 都是这样）：

```
PDF2Word/
  PDF转Word.exe          ← 用户双击这个，直接开窗
  resources/app/         ← main.js / preload.js / package.json / renderer
  *.dll *.pak locales/   ← Electron 运行时，摊在根目录
  runtime/python/        ← Python embeddable + 依赖
  runtime/pandoc/
  runtime/node.exe
  pipeline/ server/
  version.json 使用说明.txt
```

### 🔴 不要用 .cmd 当启动器

早期版本是 `启动.cmd` 调 `electron.exe app`，会弹一个黑色命令行窗口。
小蔡的原话：「为什么 cmd 启动，我看其他软件不是」—— 正经软件都是双击
一个 exe 直接开窗，黑框既难看，用户还不知道能不能关。

改法就是把代码放进 `resources/app`，Electron 的 exe 改个名就会自动找到它。
`main.js` 里的 `ROOT` 要跟着算多一层：

```js
const ROOT = path.basename(path.dirname(__dirname)) === 'resources'
  ? path.join(__dirname, '..', '..')   // 发行版
  : path.join(__dirname, '..');        // 开发环境
```

`--slim` 构建例外：那种包里没有 Layer 1 依赖，会放一个 `首次安装.cmd`
让用户先装依赖。默认构建不放任何 .cmd。

---

## 四、Release 上传

一个 Release 挂**两个**附件 —— **首版（v0.0.1）例外，只挂安装包**：

```
PDF2Word-Setup-vX.exe       287 MB    新用户下这个
pdf_to_word-vX-update.zip   0.5 MB    软件「检查更新」自动下这个
requires-vX.json            几百字节  依赖清单，客户端下载前拿它判断能不能装
```

首版（只有安装包）：

```
gh release create v0.0.1 "dist\PDF2Word-Setup-v0.0.1.exe" ^
  --title "v0.0.1" --notes-file 发布说明.md
```

之后每一版（两个都要）：

```
gh release create v0.0.2 ^
  "dist\PDF2Word-Setup-v0.0.2.exe" ^
  "dist\pdf_to_word-v0.0.2-update.zip" ^
  --title "v0.0.2" --notes-file 发布说明.md
```

三个附件都要传：安装包、更新包、**依赖清单**（`requires-vX.json`）。
清单漏了的话，客户端只能等下完 0.5 MB 更新包才知道装不装得了 ——
它存在的意义就是「下载前就知道」。

覆盖已发布的附件用 `gh release upload <tag> <文件> --clobber`；
删掉某个附件用 `gh release delete-asset <tag> <文件名> --yes`。

### 🔴 先 `git push`，再 `gh release create`

**分支名是 `main`。** 2026-09-02 发 v0.0.3 时推成了 `master`，
推送失败（`src refspec master does not match any`）。

而 `gh release create` **不管你推没推**。它照发不误，tag 打在远端当时的
最新 commit 上 —— 也就是上一版的代码。结果是 v0.0.3 的 tag 指着
v0.0.2 的源码，附件里的 exe 却是新代码。

发完必须验一次，两个数要一样：

```
gh api repos/<owner>/<repo>/git/ref/tags/v0.0.3 --jq '.object.sha'
python -c "import json,io;print(json.load(io.open('dist/PDF2Word/version.json',encoding='utf-8'))['sha'])"
```

对不上就改 tag 指向（**只在还没人下载的时候**，`downloadCount` 都是 0）：

```
gh api -X PATCH repos/<owner>/<repo>/git/refs/tags/v0.0.3 \
  -f sha=<正确的 commit> -F force=true
```

已经有人下过就别改了 —— 那时候「同一个 tag 对应过两份代码」比
「tag 指错」更难查。发一个新的修订号，把旧的标成不要用。

### 🔴 覆盖已发布的 Release：只在没有真实用户时

2026-09-02 干过一次：v0.0.3 的产物覆盖掉了 v0.0.2 的附件，v0.0.3 连 tag
一起删掉。小蔡的理由是「版本更新太快了」—— 一天之内发到 v0.0.3，
而中间那两版都是坏的，没必要在版本历史里各占一格。

**代价是「v0.0.2」这个版本号对应了两种不同的内容。** 将来谁说
「我用的 v0.0.2」，没法确定是哪一份 —— version.json 里的 sha 是唯一
能分辨的东西。

所以这条只在**用户只有自己**的时候成立。发给老师之后就不要再这么干：
那时候版本号是排查问题的唯一坐标，覆盖它等于把坐标系搞乱。
正确的做法是往前发新版本，把坏的那个在发布说明里标注清楚。

### 首版为什么不用挂更新包

v0.0.1 之前没有版本，不存在「从旧版更新上来」的人。代码上也走不到那条路：
`check()` 一看本地 tag 和远端一样就直接 return「已是最新」，根本不读附件列表。

**但从 v0.0.2 开始必须挂。** `_pick_asset` 只认名字带 `update` 的 `.zip`，
找不到就返回 None，那时 v0.0.1 的用户点「检查更新」会看到
「有新版本，但那个 Release 没有附更新包」—— 修复到不了他手上。

### 🔴 只传更新包会怎样

新用户下载 0.4 MB，解压出来一堆 `.py`，**什么都干不了**。
这个错犯过一次（09-01 首次发 v0.0.1 时）。

### 🔴 只传安装包会怎样

`update.py` 的 `_pick_asset` 只认名字带 `update` 的 `.zip`，
找不到就返回 None，老用户点「检查更新」会看到
「有新版本，但那个 Release 没有附更新包」—— 修复到不了他手上。

（安装包现在是 `.exe` 不是 `.zip`，所以不会被误挑成更新包；
  但换成 zip 分发的话就要小心这条了。）

---

## 四点五、版本与更新（**这一节最容易出事，改动前整节读一遍**）

一天之内这块出过三次问题，每次都发到用户手上才发现。所以单独立一节。

### 🔴 判断「能不能自动更新」时，禁止用版本号

小蔡 2026-09-02 的指令。原因是这样一条推断链靠不住：

```
次版本号变了 →（因为定了「依赖变了必须进次版本」的规矩）→ 依赖变了
```

中间那一环**是约定，不是事实** —— 它靠发版的人不出错。哪天加了个
pip 包却只改了修订号，用户就会拿到新代码配旧依赖，下次启动 ImportError，
而他刚「更新成功」过，根本想不到是更新害的。

**真实的判据是依赖清单**：

| 在哪 | 什么时候用 | 拿不到怎么办 |
|---|---|---|
| Release 附件 `requires-vX.json`（几百字节） | `check()` 时拉下来比对，**下载前**就知道 | 交给下面那道 |
| 更新包里的 `requires.json` | `apply_update()` 解压后、覆盖前再比一次 | 放行（老包没有这个文件） |

两份内容一样，一份为了「别让人白下 0.5 MB」，一份是兜底。
清单是打包时从**实际装的包**里读的（`importlib.metadata.version`），
不是手写的 —— 所以它是事实。

比对到什么程度：只看「本地有没有这个包」和「大版本对不对得上」，
不做精确 pin。我们本来就不锁版本，锁了反而会因为无关的小版本差异
挡住正常更新。

版本号还留着干**一件**事：判断有没有新版本（本地 tag != 远端 tag）。
那件事没有别的办法，也不涉及「能不能装」的推断。

### 用户会遇到的三条路

| 情况 | 用户要做什么 | 下多大 |
|---|---|---|
| 依赖满足（绝大多数更新） | 点「检查更新」，自动 | 0.5 MB |
| 依赖不满足（加了新的 pip 包） | 去下载页，手动覆盖安装 | 287 MB |
| 手上是 v0.0.1 / v0.0.2 | **只能手动**（那两版更新功能是坏的） | 287 MB |

**跨多少个版本都是一步到位，不用逐个爬。** 更新包是全量替换；
完整安装包更是本来就完整。区别只在下 0.5 MB 还是 287 MB。

### 更新这条链上，每一环都出过事

按用户点下「检查更新」之后的顺序：

| 环节 | 出过什么事 | 现在靠什么防 |
|---|---|---|
| 查版本 | 绑死 api.github.com 直连，那条断了整个功能废 | 直连 + 4 个镜像依次试，全断退到网页 302 |
| 比版本 | `published_at` 恒为空串，防降级判断从没执行过 | 改成比语义版本号 |
| 判能不能装 | 拿版本号推断依赖，靠发版的人不出错 | 拉依赖清单跟本地实际装的比 |
| 挑附件 | 挑成完整包，为 0.5 MB 的改动下 287 MB | `_pick_asset` 只认名字带 update 的 zip |
| 拿校验值 | **后端漏传 digest，任何人点更新都失败** | `tests/test_server.py::Test更新的接缝` |
| 下载 | 零校验走第三方镜像 | SHA256（值走直连 API、文件走镜像） |
| 校验失败 | 硬拒绝，更新按钮直接作废 | 报警但不阻拦，让用户自己决定 |
| 解压覆盖 | 路径写的是 `app/`，发行版里是 `resources/app/` | `UPDATE_PARTS` 用「源路径 → 发行版路径」映射 |
| 搬运失败 | 不回滚，留下一半新一半旧的代码 | 先备份再搬，任一失败全部还原 |

**最后两条最阴险，因为它们不报错。** 路径写错时 Python 那半更新得
好好的（`pipeline/` 和 `server/` 在根目录，两边路径一致），只有前端悄悄
停在旧版本，用户还看到「更新完成」。

### 发版时必须验的三件事

1. **模拟老用户查一次更新**：把 `version.json` 的 tag 改成上一个版本，
   跑 `update.check()`，确认 `has_update`、挑中的是 update 包不是完整包、
   `digest` 非空。
2. **确认更新包里的前端路径是 `resources/app/`**：

```
python -c "import zipfile; z=zipfile.ZipFile('dist/pdf_to_word-vX-update.zip'); print([n for n in z.namelist() if 'renderer' in n][:3])"
```

3. **依赖清单要作为附件上传**（`requires-vX.json`）。不传的话客户端
   拉不到，会退到「下完再比对」那道 —— 能用，但用户白下一趟。

### 覆盖已发布的 Release：只在没有真实用户时

见第八节「已知的坑」。简单说：**版本号是排查问题的唯一坐标**，
覆盖它等于把坐标系搞乱。2026-09-02 干过一次（v0.0.3 覆盖 v0.0.2），
理由是当时只有小蔡一个用户、而中间那两版都是坏的。发给老师之后不能再这么干。

---

## 五、发布说明写什么

给老师看的，不是给开发者看的。必须有这四段：

1. **怎么装**——下哪个文件、双击之后干什么
2. **用之前要知道的**——必须装 Office（且**只装 WPS 不行**）、
   **必须有 NVIDIA 独立显卡**（只用 GPU，没有 N 卡装了也转不了）、
   别装 `C:\Program Files`、首次要下约 7.4 GB
   （4.6 GB 模型 + 2.8 GB GPU 运行库）
3. **不想用了怎么办**——删文件夹即可，干净
4. **以后怎么更新**——软件里点「检查更新」，不用再来 GitHub

改了什么要用人话写，一条一句：

```
好： · 剩余时间不再越等越久
差： · 修复 _remain 函数中 spp 计算的逻辑错误
```

---

## 六、这个软件的硬约束（改动时不能破坏的）

这几条是产品的骨架，任何改动碰到它们都要先想清楚。

### 所有文件留在安装文件夹内

小蔡定的规矩：**删掉文件夹 = 卸载干净**，不留注册表、不留 AppData。

落实在三处，少一处就漏：

| | 靠什么 |
|---|---|
| 模型、临时文件、MinerU 配置 | `paths.child_env()` 的前三个环境变量 |
| 日志 | `logs/model_download.log`、`logs/torch_install.log` |
| Electron 缓存 | `app.setPath('userData'/'sessionData')`，**必须在 app ready 之前调** |
| 转好的 Word | 用户自己选的目录（唯一的例外） |

代价是**不能装进 `C:\Program Files`**——那目录普通用户不可写。
`paths.writable()` 在启动自检里拦这种情况。

### 不碰用户的全局配置

`~/mineru.json` 是 MinerU 的全局配置，用户机器上可能装着别的用
MinerU 的东西。我们通过 `MINERU_TOOLS_CONFIG_JSON` 指向自己那份。

### 公式必须走 XSL，不降级

09-01 定的。没有 Office 就拦住并引导去装，**不静默用 Pandoc 顶替**——
两条路的产物有实质差异（Pandoc 把空集 ∅ 转成直径符号 ⌀，那是错的）。

⚠️ Pandoc **不能从包里去掉**：整个 docx 是它生成的（md→html→docx），
XSL 只是把生成物里的公式替换掉。

### 任何耗时操作都不给黑盒

下载（GPU 运行库 / 模型 / 更新包）要有：进度条 + 真实字节数 +
**跑的那条命令** + 滚动日志；失败时日志留在原地不跳走。
转换的日志在状态栏「日志」按钮后面，全量落 `logs/convert.log`。

这条不是锦上添花：2026-09-02 小蔡在外面测试时，唯一能定位问题的途径
就是界面给出的那个日志路径。**没有日志 = 远程排查等于零**。

停止也要当场生效。曾经的实现只在「两份 PDF 之间」检查，只转一份的话
用户点了完全没反应 —— 这类「检查点够不着」的 bug 在本项目出现过三次
（models.download / torchdep.install / extract），解法统一是独立的
watch 线程加 taskkill /T，别再把检查写进会阻塞的读取循环里。

### 只用 GPU，不用 CPU

2026-09-02 小蔡定的。理由不是 CPU 跑不动（实测只慢 2 倍），是
**静默降级最坑人**：MinerU 的 `get_device()` 探不到显卡就自己换 CPU，
用户完全不知道自己在等一件本可以快一倍的事。

落实在三处：

| | 靠什么 |
|---|---|
| 不许悄悄用 CPU | `paths.child_env()` 写死 `MINERU_DEVICE_MODE=cuda` |
| 显卡不达标 | 报警但**不阻拦**（点了当场报错，不是白等） |
| 话术 | 不许出现「会退回 CPU」，`tests/test_gpu.py` 钉着 |

### CUDA 版 torch 不打进安装包

它解压后 4.2 GB（wheel 约 2.8 GB），打进去包会从 287 MB 涨到 1.5~2 GB，
逼近 GitHub 单文件 2 GiB 上限，而且没显卡的人也得跟着下。

**下哪个 CUDA 版本由用户的驱动决定，不写死**（`torchdep.pick_channel`）：
驱动 ≥570 用 cu128、≥525 用 cu126、其余用 cu118。写死最新的 cu128 会让
驱动低于 570 的机器在 `import torch` 时报 WinError 1114，而 modelscope
的 import 链里有 `import torch` —— **连模型下载都一起废**。

装完还要**真 import 一次**才算装好；验不过就把 torch 卸掉，退回
「干净的没装」状态。留着一个「在、但加载不了」的 torch 比没装更糟：
modelscope 用 `find_spec` 判断它在不在（只看文件），找得到就直接 import。

`install_deps()` 装完依赖会**把 torch 卸掉**（pip 装 mineru 时会自己拉一份
CPU 版），首次启动由 `pipeline/torchdep.py` 按需装 CUDA 版（约 2.8 GB），
跟那 4.6 GB 模型走同一个下载流程。

⚠️ `--cuda` 构建是例外：那种包直接把 GPU 版打进去，装完即用不用联网，
   只在确实需要离线分发时才这么打。

### 不打包微软的 XSL

`MML2OMML.XSL` 是随 Office 分发的版权文件，提取出来再分发是侵权。
只读用户本机那份。这条没有商量余地。

---

## 七、发完之后

1. **自己装一遍**。下载 Release 里的 exe，找一台（或一个干净目录）
   真装一次。开发机上「能跑」证明不了别人机器上能跑——
   这个项目栽过：`_find_mineru` 曾经有条退路是用隔壁工作台的 MinerU，
   在我这台机器上永远测不出问题。

2. **验证检查更新**。把 `version.json` 里的 tag 改成上一个版本，
   点「检查更新」，确认能查到、挑对包（是 update 不是 full）、能装上。

3. **在一台不是开发机的电脑上装一次**。这条比什么都重要 ——
   v0.0.1 发出去之后才发现它在任何非开发机上都跑不了
   （pip launcher 硬编码了打包机器的解释器路径），
   而开发机上七项自检全绿。

4. **进度档更新**（`_scratch\pdf_to_word_progress.md`）。

---

## 八、已知的坑

| | 说明 |
|---|---|
| 中文安装路径 | **没测过**。默认给英文路径，SFX 提示里也写了「路径最好别有中文和空格」 |
| 首次启动要装依赖 | `--slim` 打的包里没有 Layer 1，用户首次打开要跑 `首次安装.cmd`（联网几分钟）。默认构建已经装好，不走这条路 |
| GitHub 单文件 2 GiB | 安装包 287 MB，离上限还远。**但 `--cuda` 构建会到 1.5~2 GB**，那种包逼近上限；真要发的话先量一下，7-Zip 支持分卷 SFX |
| SmartScreen | exe 没有代码签名，Windows 会弹「未知发布者」。老师需要点「更多信息 → 仍要运行」。签名要买证书（一年几百到几千）。**7z 自解压格式触发率更高**，Edge 可能直接「已阻止此不安全下载」——真发给老师时考虑改发 zip，或者直接微信/U盘传 |
| 模型完整性 | `models.ready()` 的判据是「配置指的目录里有 >1 MB 的文件」，**下到一半也算就绪**。改严的话（比如按总大小卡）会误判已经下好的用户、逼他重下 4.6 GB，代价比漏判大，所以维持现状。真下坏了转换会失败并报错，重下一次即可 |
| pip launcher | `runtime/python/Scripts/*.exe` 里硬编码着打包机器的解释器路径，**换台机器全废**。跑 MinerU 一律走 `paths.mineru_cmd()`（解释器 + `-m` 模块），别再用 `find_exe('mineru')`。v0.0.1 就是栽在这上面 |
| 更新包路径 | 前端在源码里是 `app/`，在发行版里是 `resources/app/`。`UPDATE_PARTS` 写的是「源路径 → 发行版路径」的映射，**两边不一样**。写错不会报错，只会安静地只更新一半（Python 那半路径一致所以是好的） |
| 「装了但坏了」 | 任何「装完就宣布成功」的地方都要问一句：装上了 ≠ 能用。torch 的 `version.py` 在，不代表 `c10.dll` 加载得起来；modelscope 和我们同时栽在这个判据上，叠加起来就是「模型下载器崩在一个跟模型无关的地方」 |
| 网吧还原机 | 重启就重置，装什么都留不住，而且文件系统被 hook 过，DLL 加载可能异常。**不是有效的测试环境**，测出来的故障未必会在真实用户身上出现 |
