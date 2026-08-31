# PDF 转 Word

把 PDF 讲义转成 Word，**文字、公式、表格、图片都是原生对象**，不是截图。

金石工作台（`edu_book_generator`）的专精版——那边做「PDF → 题库 → 重新组卷」，
这边只做「PDF → Word」这一段。

## 现在能做什么（阶段 0 已完成）

拿 MinerU 的产物转 Word，**不需要 GPU**：

```
.venv\Scripts\python.exe tests\real_convert_check.py
```

实测 11 份真实讲义：

```
成功 11 ｜ 失败 0 ｜ 用时 10.7 秒
公式合计 3163 个，其中 2884 个走了 Office 的 XSL（10/11 份）
```

## 公式走哪条路

小蔡定的优先级：**有 XSL 先用 XSL，没有才启用内置的 Pandoc**。

```
Pandoc 出骨架（段落、表格、图片，以及它自己转的公式）
  ↓
有 Office 的 MML2OMML.XSL → 把公式逐个换成 XSL 转出来的
没有                      → 就用 Pandoc 转的那批
```

两条路都产出 Word 原生公式对象（可编辑、可搜索），不是图片。
每次转换的报告里写明**这次实际走了哪条**——同一份文件在两台机器上结果可能不同，
不写明的话人根本查不出为什么。

微软的 `MML2OMML.XSL` **不打包**（那是随 Office 分发的版权文件），只读用户本机的。
没装 Office 的用户由 Pandoc 接管，功能不缺。

## 目录

```
pipeline/   probe(判文字层) tomath(XSL公式) todocx(出Word) vendor/katex
runtime/    pandoc.exe + 许可证
tests/      单元测试 + 两个真实数据验证脚本
docs/       DESIGN.md
```

## 跑测试

```
.venv\Scripts\python.exe -m unittest discover -s tests -q     # 29 条，秒级
.venv\Scripts\python.exe tests\real_probe_check.py            # 真实 PDF 探测
.venv\Scripts\python.exe tests\real_convert_check.py          # 真实产物转 Word
```

`real_*` 那两个**不是单元测试**，依赖本机真实文件，换台机器跑不了。
它们存在的理由：单元测试用的是脚本造的假数据，只能证明逻辑自洽，
不能证明在真书上做对了。

## 还没做

阶段 1 接 MinerU（要 GPU）、阶段 2 界面、阶段 3 首启向导与多源测速、阶段 4 打包。
详见 `docs/DESIGN.md`。

## 许可证

| | 协议 | 约束 |
|---|---|---|
| MinerU | Apache 2.0 + 附加条款 | 商用免费（门槛月活 1 亿／月入 2000 万美元）；做在线服务须标注 |
| Pandoc | GPL-2.0+ | 独立调用不传染；分发须附协议全文（见 `runtime/pandoc/COPYRIGHT.txt`） |
| KaTeX | MIT | 见 `pipeline/vendor/katex/LICENSE` |
| 微软 MML2OMML.XSL | 闭源 | 只读用户本机的，**不分发** |
