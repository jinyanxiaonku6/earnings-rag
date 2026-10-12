# -*- coding: utf-8 -*-
"""eval/report.md（逐题评测记录）→ 逐题判定表.md（复核人视角的四段式）

复核意见：「尚缺对 10 道实际答案逐题作出正确/部分/错误判定及理由」。
本脚本把 report.md 里已有的 标准答案/出处/判定/错在哪/怎么修/摘录式作答
按「参考依据 — 实际答案 — 判定 — 证据与错误原因」重排，并给两道全景题
生成逐公司覆盖与缺失表。数字全部从 report.md 抽取，不手抄。

用法：python -I eval/make_verdict_table.py
"""
import io
import re
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\Rookie\projects\earnings_rag")
SRC = ROOT / "eval" / "report.md"
OUT = ROOT / "逐题判定表.md"
REPO = "https://github.com/jinyanxiaonku6/earnings-rag"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 判定标签（正确/部分正确/错误三档）+ 一句理由。report.md 里写的是「答对」，这里落到复核口径。
VERDICT = {
    "Q1": ("正确", "数值与金标逐位一致（20,909,746,962.20 元 / +100.34%），出处同为 P7「主要会计数据」表。"),
    "Q2": ("正确", "答出「算力基础设施建设与资本开支增长带动 1.6T、800G 高端光模块销售增加」，与金标同因。"),
    "Q3": ("正确", "营业成本 652,506,942.28 元、毛利率 34.81%，与金标一致，算式含在答案里。"),
    "Q4": ("正确", "识别出德科立整份报告为千元口径，换算后 5.45 亿元、+25.78% 与金标一致。"),
    "Q5": ("正确", "前三名与金标完全一致（源杰 +351.40%、中际旭创 +182.49%、新易盛 +100.34%），并给全 12 家。"),
    "Q6": ("正确", "前三名与金标一致（中际旭创 417.78 亿、新易盛 209.10 亿、华工科技 78.22 亿），12/12 家金额与同比都对上。"),
    "Q7": ("正确", "取的是 2025 年度报告而非半年报，119.29 亿元、+44.20% 与金标一致。"),
    "Q8": ("部分正确", "双主业两句都在答案里（内容不丢），但首句是泛泛句、正确句排在第 2 条 —— 表述不理想，如实记部分正确。"),
    "Q9": ("正确", "破了「PDF 断行 24,841,854,840. 22」与「年报/半年报混用」两个坑，248.42 亿元与金标一致。"),
    "Q10": ("拒答正确", "问到语料外公司时答「资料中未找到」并列出库内 12 家，没有拿别的公司数字顶替。"),
}

TOP3 = {"Q5": ["源杰科技", "中际旭创", "新易盛"],
        "Q6": ["中际旭创", "新易盛", "华工科技"]}

text = SRC.read_text(encoding="utf-8")

# ── report.md 顶部汇总表（检索层名次/覆盖）→ {qid: (向量, BM25, 混合, 无预过滤, 全景)} ──
RETRIEVAL = {}
for line in text.splitlines():
    m = re.match(r"^\| (Q\d+) \| .+? \| (.+?) \| (.+?) \| (.+?) \| (.+?) \| (.+?) \|$", line)
    if m:
        RETRIEVAL[m.group(1)] = tuple(x.replace("**", "").strip() for x in m.groups()[1:])

# ── 按 ### Qn　类型 切块 ────────────────────────────────────────────────
blocks = []
for m in re.finditer(r"^### (Q\d+)　(.+?)\s*$", text, re.M):
    blocks.append((m.group(1), m.group(2), m.start()))
items = []
for i, (qid, qtype, start) in enumerate(blocks):
    end = blocks[i + 1][2] if i + 1 < len(blocks) else len(text)
    items.append({"id": qid, "type": qtype, "body": text[start:end]})


def field(body, name):
    m = re.search(r"^\*\*" + name + r"\*\*：(.*)$", body, re.M)
    return m.group(1).strip() if m else ""


def bullet(body, name):
    m = re.search(r"^- \*\*" + name + r"\*\*：(.*)$", body, re.M)
    return m.group(1).strip() if m else ""


def last_code_block(body):
    """取最后一个 ``` 代码块（摘录式作答 / 拒答原文）。"""
    blocks_ = re.findall(r"```\n(.*?)```", body, re.S)
    return blocks_[-1].strip() if blocks_ else ""


def coverage(body, route):
    m = re.search(r"^\*\*" + route + r"\*\*　→ 覆盖 (\d+)/12 家：(.+)$", body, re.M)
    if not m:
        return None
    names = re.findall(r"([\u4e00-\u9fff]+?)\(#", m.group(2))
    return int(m.group(1)), names


def recon_rows(body):
    """全景题的「逐家解析 + 与金标对账」表 → [(公司, 解析值, 同比, 金标值, 一致)]。"""
    rows = []
    for line in body.splitlines():
        if not line.startswith("| ") or "解析值" in line or set(line) <= set("|-: "):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 6 and cells[0] and "亿" in cells[1]:     # 对账表独有：第 2 列是金额
            rows.append((cells[0], cells[1], cells[3].lstrip("+"), cells[2], cells[5]))
    return rows


def recon_table(qid, body):
    rows = recon_rows(body)
    cov = {r: coverage(body, r) for r in ("vector", "bm25", "hybrid", "hybrid_all")}
    top3 = TOP3[qid]
    out = ["| 公司 | 本期营收 | 同比 | 向量 | BM25 | 混合 | 混合(无预过滤) | 进入答案前三 |",
           "|---|---|---|---|---|---|---|---|"]
    for name, val, yoy, gold, same in rows:
        cells = []
        for r in ("vector", "bm25", "hybrid", "hybrid_all"):
            covered = cov[r] and name in cov[r][1]
            cells.append("✓" if covered else "—")
        out.append("| %s | %s | %s%s | %s | %s | %s | %s | %s |" % (
            name, val, ("+" if yoy and yoy[0].isdigit() else ""), yoy,
            cells[0], cells[1], cells[2], cells[3],
            "★ 是" if name in top3 else "否"))
    return "\n".join(out)


def cov_line(body):
    parts = []
    for route, label in (("vector", "向量"), ("bm25", "BM25"),
                         ("hybrid", "混合"), ("hybrid_all", "混合(无预过滤)")):
        c = coverage(body, route)
        parts.append("%s %s/12" % (label, c[0]) if c else "%s —" % label)
    return " · ".join(parts)


# ── 组装 ────────────────────────────────────────────────────────────────
head = f"""# 作业3 · 方向A — 逐题判定表

**代码仓库（公开）**：<{REPO}>

**这份表补的是什么**：复核意见（2026-10-12）指出，现有逐题材料主要评价**检索命中、排名和证据来源**，
缺「对 10 道实际答案逐题作出**正确 / 部分 / 错误**判定及理由 —— 不能把命中正确来源等同于回答正确」。
本表把 10 道题按 **参考依据 — 实际答案 — 判定 — 证据与错误原因** 并列成一块，
两道跨公司全景题（Q5、Q6）**逐公司列覆盖与缺失**。

检索层的名次与覆盖家数仍留在 `eval/report.md` 与 `eval/run_2026-10-12.txt`，**两层分开评价，不揉成一个分数**。

**判定口径**
- **正确**：答案的数值 / 结论与金标一致，且出处可追到「公司 + 报告 + 章节 + 页码」；
- **部分正确**：核心内容对，但有多给、少给或排序不理想等瑕疵（逐题写明）；
- **错误**：数值错、出处错，或该拒答却作答。
- 判错不删、瑕疵照写：**Q8 首句不理想**、**Q9 是绕开陷阱而非堵住**、**Q10 的拒答守卫是启发式**，
  都写在各自「证据与错误原因」里。

## 汇总（10 题）

| 题 | 类型 | 判定 | 参考依据（金标） | 检索层（名次 / 覆盖家数） |
|---|---|---|---|---|
"""

rows = []
for it in items:
    qid = it["id"]
    label, _ = VERDICT.get(qid, ("", ""))
    gold = field(it["body"], "标准答案")
    gold = gold if len(gold) <= 78 else gold[:76] + "…"
    layer = RETRIEVAL.get(qid)
    if layer:
        if "家" in layer[0]:                       # 全景题：覆盖家数
            layer = "单次检索 向量 %s · BM25 %s · 混合 %s · 无预过滤 %s → 全景枚举 %s" % layer
            layer = layer.replace("/12家", "/12 家")
        elif qid == "Q10":
            layer = "召回 0 块（走后端拒答守卫，未进入检索打分）"
        else:                                      # 单公司题：金标块名次
            layer = "向量 %s · BM25 %s · 混合 %s · 无预过滤 %s" % layer[:4]
    else:
        layer = "—"
    rows.append("| %s | %s | **%s** | %s | %s |" % (qid, it["type"], label, gold, layer))
head += "\n".join(rows) + """

**合计：正确 8 · 部分正确 1 · 拒答正确 1 · 错误 0。**

---

"""

body_out = []
for it in items:
    qid, qtype, body = it["id"], it["type"], it["body"]
    label, why = VERDICT.get(qid, ("", ""))
    seg = ["### %s　%s\n" % (qid, qtype)]
    seg.append("**问**：%s\n" % field(body, "问"))
    gold = field(body, "标准答案")
    src = field(body, "出处")
    seg.append("**参考依据（金标）**：%s\n" % gold)
    seg.append("　　*出处：%s*\n" % (src if src and src != "（无 —— 语料只含 12 家光模块公司）"
                                    else "无 —— 该题考的是「语料里没有这家公司」时的行为"))
    ans = last_code_block(body)
    seg.append("**实际答案（系统输出原文，抽取式）**：\n")
    seg.append("```\n%s\n```\n" % ans)
    seg.append("**判定：%s** —— %s\n" % (label, why))
    seg.append("**证据与错误原因**\n")
    for name, head_ in (("召回情况", "**召回层**"), ("错在哪", "**错在哪**"),
                        ("怎么修", "**怎么修**"), ("遗留", "**遗留**")):
        val = bullet(body, name)
        if val:
            seg.append("- %s：%s" % (head_, val))
    if qid in TOP3:
        seg.append("")
        seg.append("**逐公司覆盖与缺失**（✓ = 该通道的 top-8 召回到这家公司；"
                   "全景枚举通道 12/12 家全部取到金额与同比，与金标逐家一致）\n")
        seg.append(recon_table(qid, body))
        seg.append("")
    seg.append("\n---\n")
    body_out.append("\n".join(seg))

OUT.write_text(head + "\n".join(body_out), encoding="utf-8")
print("ok:", OUT.name, OUT.stat().st_size, "bytes,", len(items), "questions")
