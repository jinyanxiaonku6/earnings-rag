# -*- coding: utf-8 -*-
"""data/pages/*.jsonl -> data/chunks.jsonl

切块规则（v2，修掉超长块与章节名不统一）:
  - 段落累积，但**硬上限 MAX_CHARS**；超长句按字符强切，绝不产出超长块
  - 表格原子化；超长表按行切，每片重复表头
  - 章节名先归一化（第X节 统一取全文最长标题），避免「第八节」与「第八节 财务报告」并存
  - 相邻块重叠 OVERLAP 字符
"""
import json, re, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PG_DIR, OUT = ROOT/"data"/"pages", ROOT/"data"/"chunks.jsonl"

CHUNK_CHARS    = 900    # 目标块长
MAX_CHARS      = 1050   # 硬上限：超过就强切（编码上限 1024 token ≈ 1500 字，留足余量）
OVERLAP_CHARS  = 100
MIN_CHARS      = 60
MAX_TABLE_ROWS = 20

TBL_RE = re.compile(r"\[表格\]\n(.*?)\n\[/表格\]", re.S)
NAME_RE = re.compile(r"^(\d{6})_(.+?)_(\d{4})(年度报告|半年度报告)$")
SEC_RE = re.compile(r"^\s*(第[一二三四五六七八九十]+[节章])\s*(.{0,40})$")


def parse_meta(file: str) -> dict:
    m = NAME_RE.match(file)
    if not m:
        return {"code": "", "company": file, "year": "", "rtype": "", "report": file}
    code, name, year, rtype = m.groups()
    return {"code": code, "company": name, "year": year, "rtype": rtype,
            "report": f"{year}{rtype}"}


def scan_headings(pages, votes: dict) -> None:
    """收集章节标题投票。votes 跨文件累积，保证全语料同一节号只有一个名字。"""
    for r in pages:
        ls = [l.strip() for l in r["text"].splitlines() if l.strip()]
        for i, line in enumerate(ls):
            m = SEC_RE.match(line)
            if not m or len(line) > 50:
                continue
            key, title = m.group(1), m.group(2).strip()
            if not title and i + 1 < len(ls) and len(ls[i+1]) <= 40:
                title = ls[i+1].strip()             # 标题换到下一行
            if title:
                votes.setdefault(key, Counter())[title] += 1


def canonical_sections(votes: dict) -> dict:
    """节号 -> 规范名。

    只用文档自身投票，不套硬编码的标准结构 —— 实测这批报告是 8 节结构
    （第七节=债券相关情况、第八节=财务报告），与常见 10 节模板不一致。
    票数过少（<5）说明是零散提及而非真章节标题，退回裸节号。
    """
    out = {}
    for k, c in votes.items():
        title, n = c.most_common(1)[0]
        out[k] = f"{k} {title}".strip() if n >= 5 else k
    return out


def blocks_of(text: str):
    out, pos = [], 0
    for m in TBL_RE.finditer(text):
        head = text[pos:m.start()]
        if head.strip():
            out.append(("text", head))
        out.append(("table", m.group(1)))
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        out.append(("text", tail))
    return out


def split_table(body: str, max_rows=MAX_TABLE_ROWS):
    rows = [r for r in body.split("\n") if r.strip()]
    if len(rows) <= max_rows:
        return ["\n".join(rows)] if rows else []
    head, rest, out = rows[0], rows[1:], []
    step = max_rows - 1
    for i in range(0, len(rest), step):
        out.append("\n".join([head] + rest[i:i+step]))
    return out


def hard_split(seg: str, limit: int):
    """把一个超长段强行切成 <=limit 的片。"""
    for i in range(0, len(seg), limit):
        piece = seg[i:i+limit]
        if piece.strip():
            yield piece


def pieces(text: str, limit: int):
    """文本 -> 不超过 limit 的片段序列（先按段/句，必要时强切）。"""
    for raw in re.split(r"\n\s*\n", text):
        seg = raw.strip()
        if not seg:
            continue
        if len(seg) <= limit:
            yield seg
            continue
        buf = ""
        for s in re.split(r"(?<=[。；!?])", seg):
            if len(s) > limit:                       # 单句就超长 -> 强切
                if buf:
                    yield buf; buf = ""
                yield from hard_split(s, limit)
                continue
            if len(buf) + len(s) > limit and buf:
                yield buf
                buf = s
            else:
                buf += s
        if buf.strip():
            yield buf


def chunks_for_file(path: Path, sec_map: dict):
    meta = parse_meta(path.stem)
    recs = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    cur_sec, chunks = "封面/目录", []
    for r in recs:
        # 章节归一化：命中标题就用规范名，否则沿用上一页
        lines = [l.strip() for l in r["text"].split("\n") if l.strip()]
        for i, line in enumerate(lines):
            m = SEC_RE.match(line)
            if m and len(line) <= 50:
                cur_sec = sec_map.get(m.group(1), m.group(1))
                break
        # 逐页独立切块：块不跨页、不跨章节，页码天然精确
        buf = []
        def flush():
            if not buf:
                return
            for p in pieces("\n".join(buf), CHUNK_CHARS):
                chunks.append({"text": p, "page_start": r["page"],
                               "page_end": r["page"], "section": cur_sec})
            buf.clear()
        for kind, body in blocks_of(r["text"]):
            if kind == "text":
                buf.append(body)
                if sum(len(x) for x in buf) > MAX_CHARS:
                    flush()
            else:
                flush()
                for piece in split_table(body):
                    chunks.append({"text": piece, "page_start": r["page"],
                                   "page_end": r["page"], "section": cur_sec})
        flush()

    final = [c for c in chunks if len(c["text"].strip()) >= MIN_CHARS]
    for i in range(1, len(final)):
        prev, cur = final[i-1], final[i]
        if (prev["section"] == cur["section"] and prev["page_end"] == cur["page_start"]
                and len(prev["text"]) >= OVERLAP_CHARS):
            cur["text"] = prev["text"][-OVERLAP_CHARS:] + "\n" + cur["text"]
    for c in final:                                   # 兜底：重叠后再超限就截断
        if len(c["text"]) > MAX_CHARS + OVERLAP_CHARS:
            c["text"] = c["text"][:MAX_CHARS + OVERLAP_CHARS]

    out = []
    for i, c in enumerate(final):
        prefix = f"【{meta['company']} {meta['report']} {c['section']} P{c['page_start']}】"
        out.append({
            "chunk_id": f"{path.stem}#{i:04d}", "chunk_index": i,
            "company": meta["company"], "code": meta["code"],
            "report": meta.get("report", ""), "year": meta["year"], "rtype": meta["rtype"],
            "file": path.stem, "section": c["section"],
            "page_start": c["page_start"], "page_end": c["page_end"],
            "text": c["text"].strip(),
            "embed_text": prefix + "\n" + c["text"].strip(),
        })
    return out


def main():
    files = sorted(PG_DIR.glob("*.jsonl"))
    if len(sys.argv) > 1:
        files = [f for f in files if sys.argv[1] in f.name]
    # 第一遍：全语料投票决定每个节号的规范名
    votes = {}
    all_recs = {}
    for f in files:
        recs = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        all_recs[f] = recs
        scan_headings(recs, votes)
    sec_map = canonical_sections(votes)
    print("章节规范映射:")
    for k in sorted(sec_map): print(f"   {k} -> {sec_map[k]}")

    total = 0
    with open(OUT, "w", encoding="utf-8") as w:
        for f in files:
            cs = chunks_for_file(f, sec_map)
            for c in cs:
                w.write(json.dumps(c, ensure_ascii=False) + "\n")
            total += len(cs)
            print(f"{f.stem[:44]:46} {len(cs):5d} 块")
    print(f"\n总块数 {total}")


if __name__ == "__main__":
    main()
