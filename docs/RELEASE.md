# 发行版规矩

发一个版本要做什么、不能做什么。**每次发版前从头照着走一遍**，
别凭记忆——这份文件存在的理由就是记忆会出错。

---

## 一、发之前必须做的事

按顺序，一步不能少。

### 1. 测试全绿

```
.venv\Scripts\python.exe -m unittest discover -s tests -q
node tests\front_check.js
```

**红一条都不许发。** 不存在「这条测试早就坏了不用管」——
真不用管就该删掉它，留着等于养一个会说谎的哨兵。

### 2. 界面自己看一遍

改动碰了界面的话，真开窗截图确认。这个项目栽过两次「测试全绿但
实际没生效」，前端尤其容易——测试断言的是字符串，不是眼睛看到的东西。

### 3. 文档跟上

代码改了策略，README / DESIGN 必须同步。判断标准：
**一个没参与过开发的人照着文档做，会不会做错。**

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

```
robocopy pipeline dist\PDF2Word\pipeline /MIR
robocopy server dist\PDF2Word\server /MIR
robocopy app\renderer dist\PDF2Word\app\renderer /MIR
copy app\main.js app\preload.js app\package.json dist\PDF2Word\app\
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

## 四、Release 上传

一个 Release 挂**两个**附件，缺一不可：

```
PDF2Word-Setup-v0.0.1.exe       357 MB   新用户下这个
pdf_to_word-v0.0.1-update.zip   0.4 MB   软件「检查更新」自动下这个
```

```
gh release create v0.0.1 ^
  "dist\PDF2Word-Setup-v0.0.1.exe" ^
  "dist\pdf_to_word-v0.0.1-update.zip" ^
  --title "v0.0.1" --notes-file 发布说明.md
```

### 🔴 只传更新包会怎样

新用户下载 0.4 MB，解压出来一堆 `.py`，**什么都干不了**。
这个错犯过一次（09-01 首次发 v0.0.1 时）。

### 🔴 只传安装包会怎样

`update.py` 的 `_pick_asset` 找不到名字带 `update` 的附件，
会退回用第一个 zip——如果那是完整包，老用户点「检查更新」会去下
357 MB 来替换 0.4 MB 的改动。

---

## 五、发布说明写什么

给老师看的，不是给开发者看的。必须有这四段：

1. **怎么装**——下哪个文件、双击之后干什么
2. **用之前要知道的**——必须装 Office（且**只装 WPS 不行**）、
   别装 `C:\Program Files`、首次要下 4.6 GB 模型
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
| 模型、临时文件、MinerU 配置 | `paths.child_env()` 的三个环境变量 |
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

3. **进度档更新**（`_scratch\pdf_to_word_progress.md`）。

---

## 八、已知的坑

| | 说明 |
|---|---|
| 中文安装路径 | **没测过**。默认给英文路径，SFX 提示里也写了「路径最好别有中文和空格」 |
| 首次启动要装依赖 | `--slim` 打的包里没有 Layer 1，用户首次打开要跑 `安装依赖.cmd`（联网几分钟）。默认构建已经装好，不走这条路 |
| GitHub 单文件 2 GiB | 安装包 357 MB 没超。哪天依赖膨胀到超了，7-Zip 支持分卷 SFX |
| SmartScreen | exe 没有代码签名，Windows 会弹「未知发布者」。老师需要点「更多信息 → 仍要运行」。签名要买证书（一年几百到几千） |
