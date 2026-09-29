# -*- coding: utf-8 -*-
"""results.json -> report.md（召回的机械表 + 人写的逐题判定）。

下面的 COMMENT 是手写的：每题「答对没有 / 错在哪 / 怎么修」。
机械部分（召回名次、命中页、逐家对账）由 results.json 生成，不手抄。
"""
import json
from pathlib import Path

EVAL = Path(__file__).resolve().parent
R = json.loads((EVAL / "results.json").read_text(encoding="utf-8"))
recs = R["records"]
ORDER = R["modes"] + ["hybrid_all"]

# 逐题判定。verdict 三选一：答对 / 答对（不理想） / 拒答正确
COMMENT = {
    "Q1": {
        "verdict": "答对",
        "recall": "混合 #3，向量 #3，**BM25 未命中**（做题时预期它该直接命中）。",
        "wrong": "同表里「归属于上市公司股东的净利润/净资产/扣非净利润」三行与问句共享了"
                 "几乎所有词，BM25 分不出「营业收入（元）」这个行首才是答案。",
        "fix": "行首科目名精确匹配加权（`_rev_rows` 先归一化「其中：/减：/加：」前缀）+ "
               "硬排除勾选框和表头行（`BOILER` / `HEADER`）+ 截掉合并利润表后半段的母公司列"
               "（`_cut_parent`，新易盛母公司营业收入 267.11 亿反而比合并数大，不截就会答错）。",
        "left": "答案后面还跟着 4 条同表其它科目行（净利润/净资产等）。属于「多给」而非「答错」，未再压。",
    },
    "Q2": {
        "verdict": "答对",
        "recall": "BM25 #3，向量未命中，**混合未命中**。",
        "wrong": "融合反而把 BM25 单独能排到 #3 的块挤出了 top-8 —— RRF 在这题上是负贡献。"
                 "问句里没有「1.6T」「800G」「算力」这些答案用词，纯字面检索本来就难。",
        "fix": "作答真正指望的是元数据预过滤（公司=中际旭创 + 报告期=2026 半年度）把候选压到"
               "一百多块，再从中挑「增长/原因/受益」这类词密度最高的句子。",
        "left": "答案引 P23 的公司自述句，金标指的是 P25 表里「变动原因」列的同一句话（截断版）。"
                "两处表述等价，但**引用页与金标页不同**，逐题记录里如实标出。",
    },
    "Q3": {
        "verdict": "答对",
        "recall": "三路全中，混合 #1。",
        "wrong": "最初完全答不出：返回的是营业利润、营业外支出行，而且不做算术 —— 抽取式后端"
                 "只会摘句、不会算。另外利润表里这一行的原文是「**减：**营业成本」，"
                 "行首归一化没剥掉这个前缀就永远匹配不上。",
        "fix": "① `_norm_item` 剥掉「减：/其中：/加：」；② 利润表块压根不在召回池里，"
               "补一路 `_table_hits(phrase=\"营业成本\")` 直接按表内文字定位；"
               "③ `compute_margin` 显式输出算式「=(1,000,911,536.29 − 652,506,942.28) ÷ 1,000,911,536.29 = 34.81%」，"
               "两个数取自**同一个块**（避免跨表拼数）。",
        "left": "算出的 34.81% 与金标一致，引 P14（金标同页）。第 2、3 条摘录是财务费用等噪声行。",
    },
    "Q4": {
        "verdict": "答对（单位陷阱已堵）",
        "recall": "混合 #1（向量 #3、BM25 #2）。",
        "wrong": "全库只有德科立用「单位：千元」，其余 11 家用元。不读表头单位就会答成 5450 亿或 54.5 亿，差 100 倍。",
        "fix": "`_chunk_unit()` 按块解析「单位：千元/万元/亿元」并随答案带出，不靠全局假设。",
        "left": "首条即金标行（545,069.47 千元，同比 25.78）；第 5 条 P27 叙述句自带「千元」字样，"
                "作为交叉印证保留在答案里。",
    },
    "Q5": {
        "verdict": "答对（12/12 家全部取到数）",
        "recall": "**单次检索只覆盖 3/12 家**（向量 4/12、BM25 4/12）。这是本题的核心结论："
                  "跨 12 份文档的取数，稠密检索+BM25 怎么调都覆盖不全，必须换通道。",
        "wrong": "修过两个 bug：① 中际旭创/天孚/联特是「三列式」表（本期/上年同期/同比 三列中还夹着空单元格），"
                 "行扫描窗口开 8 格差一格够不到同比，开 25 格后取到；"
                 "② 同比解析失败的公司退化成按金额排序，把中际旭创顶到第 1 —— 改成同比未知者一律排最后。",
        "fix": "改走**逐公司枚举**：对 12 家逐一 `$contains=\"主要会计数据\"` 定位同一张可比口径的表，"
               "再解析排序。逐家与金标对账 12/12。",
        "left": "答案给全 12 家排名（金标只要求前三），源杰 +351.40% / 中际旭创 +182.49% / 新易盛 +100.34% 与金标一致。",
    },
    "Q6": {
        "verdict": "答对（12/12 家全部取到数）",
        "recall": "单次检索覆盖 5/12 家（三路里最好的），逐公司枚举 12/12。",
        "wrong": "跨文档比较 + 单位换算两个坑叠在一起：德科立那份是千元口径，换算后 5.45 亿垫底；"
                 "**不换算会顶到第 1，直接颠覆排名**。",
        "fix": "同 Q5 的逐公司枚举 + 逐块单位解析。逐家与金标对账 12/12。",
        "left": "中际旭创 417.78 亿 / 新易盛 209.10 亿 / 华工科技 78.22 亿，与金标一致。"
                "金标出处指向各公司不同页（如中际旭创金标在 P25），作答引 P8「第二节 主要会计数据」，"
                "**数值相同、出处等价**，如实标出。",
    },
    "Q7": {
        "verdict": "答对",
        "recall": "向量 #3，**BM25 未命中，混合也未命中**。",
        "wrong": "同一家公司库里有年报和半年报两份，取错报告就会答成 66.10 亿（2026H1 的数）。"
                 "而混合检索恰好把 2025 年报的金标页漏了。",
        "fix": "`detect_report(\"2025年全年\")` 把候选锁到 2025 年度报告，半年报直接挡在检索之外。"
               "作答引的是审计报告段落里「2025 年度…实现营业收入1,192,869.71 万元，较上年增长44.20%」"
               "这句自带单位、自带同比的完整叙述句。",
        "left": "后面几条摘录（存货余额、研发投入占比）是噪声行。119.29 亿元与金标一致。",
    },
    "Q8": {
        "verdict": "答对（**首句不理想**）",
        "recall": "**三路全未命中**。金标页在三种检索下都没进 top-8，全被「同业竞争」段落占了位"
                  "（那些段落含「公司/业务/主营」等词，词面高度重合）。",
        "wrong": "① 稠密检索被同业竞争段抢走；② 金标原句「公司的主营业务为……」在 PDF 抽取时被硬换行切成两半，"
                 "后半截「高速光模块产品的研发、生产与销售」单独成行，只召回前半句会漏掉双主业里的「光模块」这一半；"
                 "③ 定位词若用「主要业务」，命中的是子公司情况表的列名（剑桥科技 P29），捞回一堆子公司明细。",
        "fix": "① 加标题词定位通道 `locate_sections`，短语用「主营业务」（报告的标题和正文都是这个写法）；"
               "② `merge_lines` 把硬换行接回来（只接非表格行、且不接数字/百分号/括号结尾的行）；"
               "③ 该通道下把「公司」也算作公司名命中词，否则「公司的主营业务为…」这句话与问句的 overlap 为 0。",
        "left": "**正确句排在第 2 条**，第 1 条是「报告期内，公司所处行业情况未发生重大变化」这类泛泛句 —— "
                "打分公式里短句占便宜，标题词「主营业务」的权重没有单独抬起来。内容不丢（双主业两句都在答案里），"
                "但首句该更准。这是全部 10 题里最弱的一处。",
    },
    "Q9": {
        "verdict": "答对",
        "recall": "混合 #1、向量 #1，BM25 未命中。",
        "wrong": "金标数在 P7 表里被 PDF 抽成「24,841,854,840. 22」—— 小数点后嵌了个空格，"
                 "BM25 按「24841854840」检索失配，读的人也容易看错量级（248 亿 vs 2.48 亿）。",
        "fix": "`MONEY_RE` 的金额形状兜底只是缓解；本题真正绕开坑的办法是引用 P64 的叙述句"
               "「2025 年度新易盛营业收入为人民币2,484,185.48 万元」—— 原文里少数完整写出来的句子。",
        "left": "结论：这是**换了条路走，不是把坑填了**。若问的是只有 P7 表里才有的口径，仍会踩到那个断行数字。",
    },
    "Q10": {
        "verdict": "拒答正确",
        "recall": "召回 0 块，`backend=refuse`，没有进入检索打分。",
        "wrong": "库里 11543 块全是高度相似的财报文本，「找到个长得像的」蒙混过关是最容易犯的错。",
        "fix": "`out_of_corpus_entity` 启发式守卫：识别「XX公司/集团/股份/科技/时代…」后缀的实体，"
               "排除行业通用词和「最大的/前三家」这类修饰语，命中即直接拒答并列出库内 12 家。",
        "left": "**这是启发式，不是模型判断**：对没有这些后缀的公司名（英文名如 Coherent，"
                "或「宁王」这类简称）会失效（fail open），届时会把检索到的某家光模块公司数字当答案。"
                "README 里如实写明。",
    },
}


def cell(m):
    if "gold_rank" in m:
        return f"#{m['gold_rank']}" if m["gold_rank"] else "**未命中**"
    if "coverage_n" in m:
        return f"{m['coverage_n']}/{m['coverage_total']}家"
    return "—"


L = []
L.append("# 逐题评测记录\n")
L.append("- 语料：12 家光模块/光通信公司 × (2025 年度报告 + 2026 半年度报告) = 24 份，11543 块")
L.append(f"- 每次检索取 top-{R['k']}，命中判定：检索结果中出现「金标公司 + 金标报告 + 金标页 ±{R['tolerance_pages']} 页」的块")
L.append("- 三路：vector（Qwen3-Embedding-0.6B → Chroma，cosine）/ bm25（jieba + BM25Okapi）/ hybrid（RRF, k=60）")
L.append("- `hybrid_all` 是对照组：**关掉公司元数据预过滤**，强制全库排序，用来看修复值多少\n")

L.append("## 汇总\n")
L.append("| 题 | 类型 | 向量 | BM25 | 混合 | 混合(关预过滤) | 全景枚举 |")
L.append("|---|---|---|---|---|---|---|")
for r in recs:
    row = [cell(r["modes"].get(m, {})) for m in ORDER]
    pano = r.get("panorama")
    row.append(f"{pano['coverage_n']}/{pano['coverage_total']}家" if pano else "—")
    L.append(f"| {r['id']} | {r['type']} | " + " | ".join(row) + " |")

L.append("\n---\n\n## 逐题明细\n")
for r in recs:
    L.append(f"### {r['id']}　{r['type']}\n")
    L.append(f"**问**：{r['question']}\n")
    L.append(f"**标准答案**：{r['gt']}\n")
    L.append(f"**出处**：{r['source']}\n")
    if r["trap"]:
        L.append(f"**考点/坑**：{r['trap']}\n")

    c = COMMENT.get(r["id"])
    if c:
        L.append(f"\n**判定**：{c['verdict']}\n")
        L.append(f"- **召回情况**：{c['recall']}")
        L.append(f"- **错在哪**：{c['wrong']}")
        L.append(f"- **怎么修**：{c['fix']}")
        L.append(f"- **遗留**：{c['left']}\n")

    for mode in ORDER:
        m = r["modes"].get(mode)
        if not m:
            continue
        tag = ""
        if "gold_rank" in m:
            tag = f"　→ 金标块 #{m['gold_rank']}" if m["gold_rank"] else "　→ **金标块未召回**"
        elif "coverage_n" in m:
            tag = (f"　→ 覆盖 {m['coverage_n']}/{m['coverage_total']} 家：" +
                   "、".join(f"{k}(#{v})" for k, v in sorted(m["coverage"].items(), key=lambda x: x[1])))
        L.append(f"\n**{mode}**{tag}\n")
        L.append("| 名次 | 出处 | RRF | 向量名次 | BM25名次 | 片段开头 |")
        L.append("|---|---|---|---|---|---|")
        for h in m["retrieved"]:
            L.append(f"| {h['rank']} | {h['cite']} | {h.get('rrf', '—')} | "
                     f"{h.get('vrank') or '—'} | {h.get('brank') or '—'} | "
                     f"{h['snip'].replace('|', '/')} |")
        L.append("")

    pano = r.get("panorama")
    if pano:
        L.append(f"\n**panorama（逐公司枚举）**　→ 覆盖 {pano['coverage_n']}/{pano['coverage_total']} 家\n")
        L.append("| 名次 | 出处 | 片段开头 |")
        L.append("|---|---|---|")
        for h in pano["retrieved"]:
            L.append(f"| {h['rank']} | {h['cite']} | {h['snip'].replace('|', '/')} |")
        L.append("")

    pa = r.get("panorama_answer")
    if pa:
        L.append(f"\n**全景作答（逐家解析 + 与金标对账）**　后端={pa['backend']}　"
                 f"→ **{pa['n_ok']}/{pa['n_total']} 家金额与同比都对上**\n")
        L.append(f"```\n{pa['answer']}\n```\n")
        L.append("| 公司 | 解析值 | 金标值 | 解析同比 | 金标同比 | 一致 | 解析出处 |")
        L.append("|---|---|---|---|---|---|---|")
        for c, v in pa["checked"].items():
            gv = v["gold_value"] / 1e8
            got = "—" if "value" not in v else f"{v['value']/1e8:.2f} 亿"
            yoy = "—" if v.get("yoy") is None else f"{v['yoy']:+.2f}%"
            L.append(f"| {c} | {got} | {gv:.2f} 亿 | {yoy} | {v['gold_yoy']:+.2f}% | "
                     f"{'✓' if v['ok'] else '✗ ' + v.get('why', '')} | {v.get('cite', '—')} |")
        L.append("")

    L.append(f"\n**摘录式作答**：\n\n```\n{(r['answer_extractive'] or '（无）')[:2500]}\n```\n")
    if r.get("answer_llm"):
        L.append(f"\n**大模型作答**：\n\n```\n{r['answer_llm'][:2500]}\n```\n")
    L.append("\n---\n")

(EVAL / "report.md").write_text("\n".join(L), encoding="utf-8")
print(f"写出 {EVAL/'report.md'}")
