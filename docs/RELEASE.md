# 发行版规矩

发一个版本要做什么、不能做什么。**每次发版前从头照着走一遍**，
别凭记忆——这份文件存在的理由就是记忆会出错。

---

## 一、发之前必须做的事

按顺序，一步不能少。

### 1. 测试全绿

```
.venv\Scripts\python.exe -m unittest discover -s tests -q   # 225 条
node tests\front_check.js                                   # 67 条
```

**红一条都不许发。** 不存在「这条测试早就坏了不用管」——
真不用管就该删掉它，留着等于养一个会说谎的哨兵。

### 2. 界面自己看一遍

改动碰了界面的话，真开窗截图确认。这个项目栽过两次「测试全绿但
实际没生效」，前端尤其容易——测试断言的是字符串，不是眼睛看到的东西。

### 3. 文档跟上

先跑自动检查：

```
.venv\Scripts\python.exe tools\check_docs.py
```

它查两件事：文档提到的文件实际存不存在（照着做会不会撞墙），
以及同一个事实在几处写的是不是同一个数（测试条数、安装包体积……）。
真值取自代码和产物，不是取自另一份文档。

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

**依赖变了必须进次版本**，因为那意味着老用户光靠更新包补不上，
得重新下完整安装包。

这条规矩**代码里有人执行**（`update.check()`）：次版本或主版本不同时
返回 `need_full`，界面改成「去下载页」而不是「自动更新」。
`tests/test_update.py` 里钉着。

⚠️ 反过来说：**跨多少个修订号都不用一个一个更新**。更新包是全量替换
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
PDF2Word-Setup-v0.0.1.exe       287 MB   新用户下这个
pdf_to_word-v0.0.2-update.zip   0.5 MB   软件「检查更新」自动下这个
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

覆盖已发布的附件用 `gh release upload <tag> <文件> --clobber`；
删掉某个附件用 `gh release delete-asset <tag> <文件名> --yes`。

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
