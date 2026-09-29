# -*- coding: utf-8 -*-
"""问答页面。纯标准库 http.server，无额外依赖。

    python scripts/app.py [端口]
    浏览器打开 http://127.0.0.1:8848
"""
import json, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from ask import answer, load_dotenv          # noqa: E402
from search import HybridSearcher            # noqa: E402

load_dotenv()
SEARCHER = None
LOCK = threading.Lock()
STATS = {"queries": 0, "total_ms": 0}


def get_searcher():
    global SEARCHER
    if SEARCHER is None:
        SEARCHER = HybridSearcher()
    return SEARCHER


PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>光模块财报问答库</title>
<style>
  :root{--bg:#0f1115;--card:#181b22;--line:#272c36;--fg:#e6e8ee;--dim:#8b93a7;
        --vec:#4ea1ff;--bm25:#ffb454;--both:#5ddc9a;--acc:#6ea8fe}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:15px/1.7 "Source Han Sans CN","Microsoft YaHei",system-ui,sans-serif}
  .wrap{max-width:1000px;margin:0 auto;padding:32px 22px 70px}
  h1{font-size:22px;margin:0 0 4px;font-weight:600}
  .sub{color:var(--dim);font-size:13px;margin-bottom:22px}
  .bar{display:flex;gap:10px;margin-bottom:8px;flex-wrap:wrap}
  input[type=text]{flex:1;min-width:260px;background:var(--card);border:1px solid var(--line);
       color:var(--fg);padding:12px 14px;border-radius:9px;font-size:15px;outline:none}
  input[type=text]:focus{border-color:var(--acc)}
  select,button{background:var(--card);border:1px solid var(--line);color:var(--fg);
       padding:12px 14px;border-radius:9px;font-size:14px;cursor:pointer}
  button{background:var(--acc);border-color:var(--acc);color:#0b0e13;font-weight:600;padding:12px 22px}
  button:hover{filter:brightness(1.08)}
  .chip{display:inline-block;font-size:11px;padding:1px 7px;border-radius:20px;
        border:1px solid currentColor;margin-left:7px;vertical-align:1px}
  .c-vec{color:var(--vec)} .c-bm{color:var(--bm25)} .c-both{color:var(--both)}
  .c-tab{color:#c792ea}
  .ans{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--acc);
       border-radius:10px;padding:16px 18px;margin:16px 0;white-space:pre-wrap;font-size:14.5px}
  .ans.none{border-left-color:#ff6b6b;color:var(--dim)}
  .hit{background:var(--card);border:1px solid var(--line);border-radius:10px;
       padding:13px 16px;margin-bottom:10px}
  .hit h3{margin:0 0 3px;font-size:13.5px;font-weight:600;color:var(--fg)}
  .meta{color:var(--dim);font-size:12px;margin-bottom:8px}
  .txt{font-size:13.5px;color:#c3c9d6;white-space:pre-wrap;
       max-height:150px;overflow:hidden;cursor:pointer;position:relative}
  .txt.open{max-height:none}
  .txt:not(.open):after{content:"";position:absolute;left:0;right:0;bottom:0;height:44px;
       background:linear-gradient(transparent,var(--card))}
  .stats{color:var(--dim);font-size:12.5px;margin:6px 0 14px}
  code{background:#11141a;padding:1px 5px;border-radius:4px;font-size:12px}
</style></head><body><div class="wrap">
<h1>光模块 / 光通信 财报问答库</h1>
<div class="sub">12 家公司 · 2025 年报 + 2026 半年报 · 向量 + BM25 混合检索 · 答案带页码出处</div>
<div class="bar">
  <input type="text" id="q" placeholder="例：中际旭创和新易盛 2026 上半年毛利率分别是多少？" autofocus>
  <select id="mode">
    <option value="hybrid">混合 RRF</option>
    <option value="vector">仅向量</option>
    <option value="bm25">仅 BM25</option>
  </select>
  <select id="k"><option>4</option><option selected>6</option><option>8</option><option>10</option></select>
  <button onclick="run()">提问</button>
</div>
<div class="stats" id="stats"></div>
<div id="ans"></div>
<div id="hits"></div>
<script>
const $ = s => document.querySelector(s);
$("#q").addEventListener("keydown", e => { if (e.key === "Enter") run(); });
// 带参数进来就自动问一次：?q=…&mode=hybrid&k=6
(function(){
  const u = new URLSearchParams(location.search);
  if (!u.get("q")) return;
  $("#q").value = u.get("q");
  if (u.get("mode")) $("#mode").value = u.get("mode");
  if (u.get("k")) $("#k").value = u.get("k");
  addEventListener("load", run);
})();
function tag(vr, br, src){
  // src=="table" 是「表定位通道」：不走向量打分，直接按 $contains 捞表，专治全景/科目类问题
  const t = src === "table" ? '<span class="chip c-tab">表定位</span>' : "";
  if (vr && br) return t + '<span class="chip c-both">向量+BM25</span>';
  if (vr) return t + '<span class="chip c-vec">仅向量</span>';
  if (br) return t + '<span class="chip c-bm">仅BM25</span>';
  return t;
}
async function run(){
  const q = $("#q").value.trim(); if(!q) return;
  const u = new URL(location); u.searchParams.set("q", q);
  u.searchParams.set("mode", $("#mode").value); u.searchParams.set("k", $("#k").value);
  history.replaceState(null, "", u);   // 问题写进 URL，截图/复现直接贴链接
  $("#stats").textContent = "检索中…"; $("#ans").innerHTML=""; $("#hits").innerHTML="";
  const r = await fetch("/api/ask", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({q, mode:$("#mode").value, k:+$("#k").value})});
  const d = await r.json();
  $("#stats").textContent = `后端 ${d.backend} · 模式 ${d.mode} · 耗时 ${d.ms} ms · 召回 ${d.hits.length} 块`;
  $("#ans").innerHTML = d.answer
     ? `<div class="ans">${d.answer.replace(/&/g,"&amp;").replace(/</g,"&lt;")}</div>`
     : `<div class="ans none">未生成答案（后端 extractive 且无匹配句），请看下方召回片段。</div>`;
  $("#hits").innerHTML = d.hits.map(h => `
    <div class="hit">
      <h3>#${h.rank}　${h.cite}${tag(h.vrank, h.brank, h.src)}</h3>
      <div class="meta">RRF ${h.rrf.toFixed(5)} · 向量名次 ${h.vrank ?? "—"} · BM25 名次 ${h.brank ?? "—"}
        · chunk <code>${h.chunk_id.split("#")[1]}</code></div>
      <div class="txt" onclick="this.classList.toggle('open')">${h.text.replace(/&/g,"&amp;").replace(/</g,"&lt;")}</div>
    </div>`).join("");
}
</script></div></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"))
        else:
            self._send(404, b"not found")

    def do_POST(self):
        if urlparse(self.path).path != "/api/ask":
            self._send(404, b"not found")
            return
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        # 客户端编码不可控（curl/终端的本地编码可能不是 UTF-8）。
        # 解析失败必须回 400 JSON，不能让异常穿出去变成"空回复"——页面会表现为点了没反应。
        try:
            req = json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._send(400, json.dumps(
                {"error": f"请求体不是合法 UTF-8 JSON: {e}"}, ensure_ascii=False
            ).encode("utf-8"), "application/json; charset=utf-8")
            return
        q = (req.get("q") or "").strip()
        mode = req.get("mode", "hybrid")
        try:
            k = int(req.get("k", 6))
        except Exception:
            k = 6
        if not q:
            self._send(400, json.dumps({"error": "问题为空"}, ensure_ascii=False
                                       ).encode("utf-8"), "application/json; charset=utf-8")
            return
        t0 = time.time()
        try:
            with LOCK:
                s = get_searcher()
                r = answer(q, s, top_k=k, mode=mode)
            ms = int((time.time() - t0) * 1000)
            STATS["queries"] += 1
            STATS["total_ms"] += ms
            hits = [{"rank": h["rank"], "cite": s.cite(h["meta"]), "chunk_id": h["chunk_id"],
                     "rrf": h["rrf"], "vrank": h.get("vrank"), "brank": h.get("brank"),
                     "src": h.get("src", "retrieval"),
                     "text": h["text"][:1400],
                     "meta": {kk: h["meta"][kk] for kk in
                              ("company", "report", "section", "page_start", "page_end")}}
                    for h in r["hits"]]
            body = {"answer": r["answer"], "backend": r["backend"],
                    "mode": mode, "ms": ms, "hits": hits}
        except Exception as e:
            import traceback
            traceback.print_exc()
            body = {"error": f"检索失败: {e}", "answer": None, "hits": [],
                    "backend": "error", "mode": mode, "ms": int((time.time()-t0)*1000)}
        self._send(200, json.dumps(body, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8848
    print(f"载入索引…")
    get_searcher()
    print(f"就绪 -> http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
