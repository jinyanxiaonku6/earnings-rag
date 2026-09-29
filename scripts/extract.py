# -*- coding: utf-8 -*-
"""财报 PDF -> 带页码/章节/表格的结构化文本。

产物:
  data/txt/<name>.txt        人可读、带页码标记（溯源用）
  data/pages/<name>.jsonl    每页一条，供切块用

要点:
  - 表格用 PyMuPDF find_tables 还原成「列 | 列 | 列」的行文本，按纵坐标插回正文流
  - 自动去页眉页脚（在多页重复出现的短行）
"""
import json, re, sys, time
from collections import Counter
from pathlib import Path
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
PDF_DIR, TXT_DIR, PG_DIR = ROOT/"data"/"pdfs", ROOT/"data"/"txt", ROOT/"data"/"pages"
TXT_DIR.mkdir(parents=True, exist_ok=True); PG_DIR.mkdir(parents=True, exist_ok=True)

# 章节标题：第X节 XXX / 第X章 XXX
SEC_RE = re.compile(r"^\s*(第[一二三四五六七八九十]+[节章])\s*([^\n]{0,40})")
# 页码/页眉类噪声行
NOISE_RE = re.compile(r"^\s*(\d{1,4}|第\s*\d+\s*页\s*(共\s*\d+\s*页)?|-\s*\d+\s*-)\s*$")


def render_table(tab) -> str:
    """把找出来的表渲染成行文本。"""
    rows = tab.extract()
    out = []
    for r in rows:
        cells = [(str(c).replace("\n", " ").strip() if c is not None else "") for c in r]
        if any(cells):
            out.append(" | ".join(cells))
    return "\n".join(out)


def page_flow(page) -> tuple[str, int]:
    """按阅读顺序拼正文与表格。返回 (文本, 表格数)。"""
    items = []
    for b in page.get_text("blocks"):
        x0, y0, x1, y1, txt = b[0], b[1], b[2], b[3], b[4]
        if txt and txt.strip():
            items.append((y0, x0, "text", txt))
    ntab = 0
    try:
        found = page.find_tables()
    except Exception:
        found = None
    if found:
        for ti, t in enumerate(found.tables, 1):
            body = render_table(t)
            if body.strip():
                items.append((t.bbox[1], t.bbox[0], "table", f"[表格]\n{body}\n[/表格]"))
                ntab += 1
    items.sort(key=lambda x: (round(x[0], 1), x[1]))
    parts = []
    for _, _, kind, txt in items:
        parts.append(txt.rstrip() if kind == "text" else txt)
    return "\n".join(parts), ntab


def extract_pdf(pdf: Path) -> dict:
    doc = pymupdf.open(pdf)
    pages = []
    for i, page in enumerate(doc, 1):
        txt, ntab = page_flow(page)
        pages.append({"page": i, "text": txt, "n_tables": ntab})
    doc.close()
    return {"file": pdf.name, "n_pages": len(pages), "pages": pages}


def strip_headers_footers(pages: list[dict]) -> None:
    """在多页重复出现的短行 = 页眉/页脚，删掉。就地修改。"""
    cnt = Counter()
    for p in pages:
        lines = [l.strip() for l in p["text"].split("\n") if l.strip()]
        # 只看每页首 3 行与末 3 行
        for l in lines[:3] + lines[-3:]:
            if len(l) <= 40:
                cnt[l] += 1
    thr = max(3, int(len(pages) * 0.25))
    junk = {l for l, c in cnt.items() if c >= thr}
    if not junk:
        return
    for p in pages:
        keep = []
        for l in p["text"].split("\n"):
            s = l.strip()
            if s in junk or NOISE_RE.match(s) and s in junk:
                continue
            keep.append(l)
        p["text"] = "\n".join(keep)


def tag_sections(pages: list[dict]) -> None:
    """给每页打章节标记；页内出现新章节则记录。就地修改。"""
    cur = "封面/目录"
    for p in pages:
        for line in p["text"].split("\n"):
            m = SEC_RE.match(line)
            if m and len(line.strip()) < 50:
                cur = f"{m.group(1)} {m.group(2).strip()}".strip()
                break
        p["section"] = cur


def main():
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    if len(sys.argv) > 1:
        pdfs = [p for p in pdfs if sys.argv[1] in p.name]
    for pdf in pdfs:
        t0 = time.time()
        rec = extract_pdf(pdf)
        strip_headers_footers(rec["pages"])
        tag_sections(rec["pages"])
        stem = pdf.stem
        with open(PG_DIR / f"{stem}.jsonl", "w", encoding="utf-8") as f:
            for p in rec["pages"]:
                f.write(json.dumps({"file": stem, "page": p["page"],
                                    "section": p["section"], "n_tables": p["n_tables"],
                                    "text": p["text"]}, ensure_ascii=False) + "\n")
        with open(TXT_DIR / f"{stem}.txt", "w", encoding="utf-8") as f:
            f.write(f"# {stem}\n# 共 {rec['n_pages']} 页\n\n")
            for p in rec["pages"]:
                f.write(f"\n===== [P{p['page']}] {p['section']} =====\n{p['text']}\n")
        ntab = sum(p["n_tables"] for p in rec["pages"])
        nchar = sum(len(p["text"]) for p in rec["pages"])
        print(f"OK {stem[:44]:46} {rec['n_pages']:4d}页 {ntab:4d}表 {nchar/10000:6.1f}万字 {time.time()-t0:5.1f}s")


if __name__ == "__main__":
    main()
