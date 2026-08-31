// tex2mml.js — 批量 LaTeX → MathML（tex2omml.py 的子进程）
// stdin: JSON [{tex, display}]  → stdout: JSON [{ok:true, mml}|{ok:false, err}]
// 用项目自带的 KaTeX，与 katex_check.js / 渲染样张同一个引擎 ——
// 公式在 Word 里和在样张里必须长得一样，换引擎就不一样了。
const katex = require('./vendor/katex/katex.min.js');
let src = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', d => src += d);
process.stdin.on('end', () => {
  const out = JSON.parse(src).map(it => {
    try {
      const html = katex.renderToString(it.tex, {
        output: 'mathml', throwOnError: true, displayMode: !!it.display,
      });
      const m = html.match(/<math[\s\S]*?<\/math>/);
      if (!m) return { ok: false, err: 'KaTeX 没吐出 MathML' };
      return { ok: true, mml: m[0] };
    } catch (e) {
      return { ok: false, err: String(e.message || e).slice(0, 200) };
    }
  });
  process.stdout.write(JSON.stringify(out));
});
