# -*- coding: utf-8 -*-
"""逐题评测：三路检索各跑一遍，记录召回了哪些块、命中没命中、答案对不对。

命中判定不靠人眼：每道题带 gold(公司/报告/页)，检索结果里出现该页的块即算命中。
跨公司题另算"覆盖率"——12 家里召回了几个。

输出: eval/results.json (机器可读) + eval/report.md (逐题记录)
用法: python eval/run_eval.py [--k 8] [--llm]
"""
import argparse, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
EVAL = ROOT / "eval"
TOL = 1          # 页码容差：块与金标页差 1 页内也算命中

MODES = ["vector", "bm25", "hybrid"]


def hit_rank(hits, gold):
    """金标块在召回里的名次；没有则 None。"""
    for h in hits:
        m = h["meta"]
        if m["company"] != gold["company"]:
            continue
        if gold["report"] not in (m["report"] or ""):
            continue
        if abs(int(m["page_start"]) - gold["page"]) <= TOL:
            return h["rank"]
    return None


def cross_coverage(hits, cross):
    """跨公司题：召回的块覆盖了 12 家里的几家，以及覆盖了前几名。"""
    seen = {}
    for h in hits:
        m = h["meta"]
        if cross["report"] not in (m["report"] or ""):
            continue
        if m["company"] in cross["companies"]:
            seen.setdefault(m["company"], h["rank"])
    return seen


def snippet(t, n=260):
    return " ".join((t or "").split())[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--llm", action="store_true", help="同时跑大模型作答（需 .env 有 key）")
    a = ap.parse_args()

    from search import HybridSearcher
    from ask import (answer, extractive, load_dotenv, llm_available, build_ctx,
                     call_llm, PROMPT, panorama_answer)

    load_dotenv()
    qs = json.loads((EVAL / "questions.json").read_text(encoding="utf-8"))["questions"]

    # gold_cross 用 ref_q 指到另一题的覆盖表（Q6 与 Q5 同一张表），这里展开，
    # 免得同一份 12 家数据抄两遍、改一处漏一处
    by_id = {q["id"]: q for q in qs}
    for q in qs:
        gc = q.get("gold_cross")
        if gc and gc.get("ref_q") and "companies" not in gc:
            gc["companies"] = by_id[gc["ref_q"]]["gold_cross"]["companies"]

    t0 = time.time()
    s = HybridSearcher()
    print(f"检索器就绪 {time.time()-t0:.0f}s  后端={llm_available() or 'extractive'}",
          file=sys.stderr, flush=True)

    out = []
    for q in qs:
        rec = {"id": q["id"], "type": q["type"], "question": q["question"],
               "gt": q["gt"], "source": q["source"], "trap": q.get("trap", ""),
               "modes": {}}

        for mode in MODES:
            hits = s.search(q["question"], top_k=a.k, mode=mode)
            s.last_query = q["question"]
            m = {"retrieved": [
                {"rank": h["rank"], "cite": s.cite(h["meta"]),
                 "rrf": round(h.get("rrf", 0), 5),
                 "vrank": h.get("vrank"), "brank": h.get("brank"),
                 "snip": snippet(h["text"])} for h in hits],
                "companies": sorted({h["meta"]["company"] for h in hits})}

            if q.get("gold"):
                m["gold_rank"] = hit_rank(hits, q["gold"])
                m["hit"] = m["gold_rank"] is not None
            if q.get("gold_cross") and "companies" in q["gold_cross"]:
                cov = cross_coverage(hits, q["gold_cross"])
                m["coverage"] = cov
                m["coverage_n"] = len(cov)
                m["coverage_total"] = len(q["gold_cross"]["companies"])
            rec["modes"][mode] = m
            print(f"  {q['id']} {mode:6s} 命中={m.get('gold_rank')} "
                  f"覆盖={m.get('coverage_n', '-')}  片段数={len(m['retrieved'])}",
                  file=sys.stderr, flush=True)

        # ---- 对照组：强制全库（关掉公司预过滤），看修复值多少 ----
        hits = s.search(q["question"], top_k=a.k, mode="hybrid", scope="all")
        m = {"retrieved": [
            {"rank": h["rank"], "cite": s.cite(h["meta"]), "rrf": round(h.get("rrf", 0), 5),
             "vrank": h.get("vrank"), "brank": h.get("brank"),
             "snip": snippet(h["text"])} for h in hits]}
        if q.get("gold"):
            m["gold_rank"] = hit_rank(hits, q["gold"])
            m["hit"] = m["gold_rank"] is not None
        if q.get("gold_cross") and "companies" in q["gold_cross"]:
            cov = cross_coverage(hits, q["gold_cross"])
            m["coverage"], m["coverage_n"] = cov, len(cov)
            m["coverage_total"] = len(q["gold_cross"]["companies"])
        rec["modes"]["hybrid_all"] = m
        print(f"  {q['id']} hybrid_all 命中={m.get('gold_rank')} "
              f"覆盖={m.get('coverage_n', '-')}  (对照组)", file=sys.stderr, flush=True)

        # ---- 跨公司题：逐公司枚举检索（看"枚举"补了多少覆盖率）----
        if q.get("gold_cross") and "companies" in q["gold_cross"]:
            ph = s.panorama(q["question"], per_company=1, mode="hybrid")
            covp = {}
            for h in ph:
                c = h["meta"]["company"]
                if q["gold_cross"]["report"] in (h["meta"]["report"] or "") and c in q["gold_cross"]["companies"]:
                    covp.setdefault(c, h["rank"])
            rec["panorama"] = {
                "coverage_n": len(covp), "coverage_total": len(q["gold_cross"]["companies"]),
                "coverage": covp,
                "retrieved": [{"rank": h["rank"], "cite": s.cite(h["meta"]),
                               "snip": snippet(h["text"], 160)} for h in ph]}
            print(f"  {q['id']} panorama  覆盖={len(covp)}/{len(q['gold_cross']['companies'])}",
                  file=sys.stderr, flush=True)

        # ---- 作答（hybrid 召回 + 摘录式；--llm 时再跑大模型）----
        ans = answer(q["question"], s, top_k=a.k, mode="hybrid", backend="extractive")
        rec["answer_extractive"] = ans["answer"]
        rec["answer_backend"] = ans["backend"]
        rec["hits_for_answer"] = [s.cite(h["meta"]) for h in ans["hits"]]

        # ---- 跨公司题对账：解析结果逐家比金标（金额 1%、同比 0.05 以内算对）----
        if q.get("gold_cross") and "companies" in q["gold_cross"]:
            pa = panorama_answer(q["question"], s)
            gmap = q["gold_cross"]["companies"]
            checked, ok_n = {}, 0
            for c, g in gmap.items():
                gv = float(g["value"].replace(",", "")) * (1e3 if g.get("unit") == "千元" else 1.0)
                got = pa["parsed"].get(c)
                if not got:
                    checked[c] = {"ok": False, "why": "未取到", "gold_value": gv,
                                  "gold_yoy": g["yoy"]}
                    continue
                dv = abs(got["value"] - gv) / gv if gv else 9.0
                dy = None if (got["yoy"] is None or g["yoy"] is None) \
                    else abs(got["yoy"] - g["yoy"])
                ok = dv < 0.01 and dy is not None and dy < 0.05
                ok_n += ok
                checked[c] = {"ok": bool(ok), "value": got["value"], "yoy": got["yoy"],
                              "gold_value": gv, "gold_yoy": g["yoy"],
                              "value_diff": round(dv, 4), "yoy_diff": dy,
                              "cite": got["cite"]}
            rec["panorama_answer"] = {"answer": pa["answer"], "backend": pa["backend"],
                                      "n_ok": ok_n, "n_total": len(gmap), "checked": checked}
            print(f"  {q['id']} 逐家对账 {ok_n}/{len(gmap)}", file=sys.stderr, flush=True)

        if a.llm and llm_available():
            try:
                hits = s.search(q["question"], top_k=a.k, mode="hybrid")
                txt = call_llm(PROMPT.format(ctx=build_ctx(hits, s), q=q["question"]))
                rec["answer_llm"] = txt
            except Exception as e:
                rec["answer_llm"] = f"[失败 {e}]"

        out.append(rec)

    (EVAL / "results.json").write_text(
        json.dumps({"k": a.k, "modes": MODES, "tolerance_pages": TOL,
                    "records": out}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"写出 {EVAL/'results.json'}", file=sys.stderr)


if __name__ == "__main__":
    main()
