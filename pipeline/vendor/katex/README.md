# vendor/katex — KaTeX 0.18.4（离线自足，拍板项 C，小蔡 2026-08-21 批准）

来源：npm registry `katex@0.18.4` 官方包 dist/，MIT License（LICENSE 在本目录）。
只保留 woff2 字体（Chrome 全支持；woff/ttf 是老浏览器冗余，留着白白翻三倍体积——老项目 D127 同款取舍）。

用途：题干校对页与讲义渲染的公式排版。HTML 用相对路径引本目录，离线可开、打印不依赖网络
（小蔡「禁云端资源」铁则）。Node 侧可直接 `require('katex.min.js')` 做服务端渲染/试渲染。

更新方式：`npm pack katex` 取新版 dist 覆盖，更新本 README 版本号；禁 CDN。
