# -*- coding: utf-8 -*-
"""检索 + 作答。两条后端：
  - extractive : 不用大模型，把最相关的句子摘出来并标注出处（离线、零成本、可复现）
  - llm        : 把召回片段交给大模型生成答案，强制标注出处

LLM 后端从 .env 读 key（.env 不进仓库）：
  ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL / ANTHROPIC_MODEL
或 OpenAI 兼容：
  OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL
"""
import os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

PROMPT = """你是财报分析助手。只根据下面的【资料】回答问题，不要使用任何外部知识。

要求：
1. 答案中每个数字后面必须紧跟出处，格式：[公司《报告》章节 P页码]
2. 资料中没有的信息，直接说「资料中未找到」，禁止推测
3. 涉及金额要注明单位

【资料】
{ctx}

【问题】{q}

【答案】"""


def load_dotenv():
    """.env 里的 key 只在这读，绝不进仓库/截图。"""
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def llm_available() -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return ""


def build_ctx(hits, searcher) -> str:
    parts = []
    for h in hits:
        m = h["meta"]
        parts.append(f"[{h['rank']}] {searcher.cite(m)}\n{h['text'][:1200]}")
    return "\n\n".join(parts)


def call_llm(prompt: str) -> str:
    backend = llm_available()
    if backend == "anthropic":
        import anthropic
        cli = anthropic.Anthropic(base_url=os.environ.get("ANTHROPIC_BASE_URL") or None)
        r = cli.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            max_tokens=1500, messages=[{"role": "user", "content": prompt}])
        return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    if backend == "openai":
        from openai import OpenAI
        cli = OpenAI(base_url=os.environ.get("OPENAI_BASE_URL") or None)
        r = cli.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "deepseek-chat"),
            messages=[{"role": "user", "content": prompt}], max_tokens=1500)
        return r.choices[0].message.content
    raise RuntimeError("没有配置 LLM key")


# 「金额形状」的数字：20,909,746,962.20 算，证券代码 300308 / 公告编号 2026-081 不算
MONEY_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d{2}")
# 问句在要一个数字/名次（而不是要一段解释）
WANT_NUM_RE = re.compile(r"是多少|多大|几位|多少钱|金额|占比|比例|百分|哪家|哪三|排名|前三|前五|最高|最低|最大|最快")
# 审计报告套话：含「财务报表」「营业收入」等高频词，但从来不是答案
BOILER = ("审计程序", "关键审计事项", "内部控制", "我们认为", "管理层负责", "持续经营",
          "企业会计准则", "公允反映", "抽样", "关键业绩指标", "我们的审计",
          # 披露模板的勾选框行："…作为承租方适用 □不适用…" —— 句子里全是关键词，什么答案都不是
          "□适用", "√适用", "□不适用", "√不适用")
# 章节标题/编号行：小节名不是答案，但和正文粘在一起时会因为词密而排到前面
SECTION_RE = re.compile(r"第[一二三四五六七八九十]+[节章]|^[（(][一二三四五六七八九十]+[）)]")
# 公告抬头/签字行：含数字（证券代码、编号）但同样不是答案
HEADER = ("证券代码", "证券简称", "公告编号", "法定代表人", "会计机构负责人", "主管会计工作负责人")

# 跨公司排名题（"增速最快的前三家"）：单次检索只会覆盖 3~4 家公司，
# 走 panorama 逐公司枚举 + 表行解析，把排名算出来
RANK_RE = re.compile(r"前三|前五|前\d|排名|哪三家|哪三家")
MARGIN_RE = re.compile(r"毛利率")
YOY_RANK_RE = re.compile(r"增速|增长|增幅|最快")
# 解析「营业收入」行时要排除的：分公司/分产品口径、母公司口径、利息类收入
ROW_SKIP = ("其中", "占", "母公司", "分产品", "分地区", "分解", "利息", "手续费", "已赚", "内部")
NEXT_ITEM_RE = re.compile(r"营业成本|营业利润|利润总额|营业总收入|销售费用|管理费用|财务费用|"
                          r"研发费用|研发投入|归属于|净利润|总资产|总负债|净资产|资产总计|"
                          r"每股|收益率|税金|投资收益|信用减值|资产减值|其他收益|公允价值|"
                          r"经营活动|公司是否|适用□不适用")
UNIT_MULT = {"千元": 1e3, "万元": 1e4, "亿元": 1e8, "元": 1.0}
# 逐公司检索时问句里的公司名已经没用（调用方逐家收窄过了），补上财报表头词，
# 把每家公司都拉向它的「主要会计数据」表 —— 那是跨公司唯一可比的口径；
# 不补的话各家只会返回各写各的第三节叙述段，取不到同一个科目。
PANORAMA_HINT = " 主要会计数据 本报告期 上年同期 同比增减 单位"

# 库外公司识别（Q10 应拒答题）：问句里出现「像公司名」的实体时先查一遍语料。
# 不做这一步，检索一定会返回「最像的那家」光模块公司的数字，读者根本看不出来。
ENTITY_RE = re.compile(r"[一-龥]{2,6}(?:公司|集团|股份|科技|时代|控股|电子|"
                       r"半导体|能源|材料|通信|光电)")
ENTITY_STOP = ("该", "这", "本", "上述", "相关", "标的", "同一", "各家", "一家")
# 「最大的前三家公司」这种修饰语也长得像实体名，但它们是"哪几家"的泛称
ENTITY_MOD_SKIP_RE = re.compile(r"[的之]|前[一二三四五六七八九十\d]|最大|最小|最快|最高|最低|哪|几家|哪些")
# 「光模块公司」「光通信公司」这类是行业词+公司，不是公司名
INDUSTRY_STOP = ("光模块", "光通信", "光器件", "光芯片", "通信", "电子", "半导体",
                 "材料", "设备", "器件", "网络", "数据", "科技", "模组", "芯片")


def out_of_corpus_entity(question: str, searcher) -> str:
    """返回问句里像公司名、又不在语料 12 家里的那个实体；没有则返回空串。

    纯启发式：认「汉字 + 常见后缀」，语料外的公司名如果没带这些后缀（比如「旭创」
    「Coherent」）会漏判 —— 漏判的后果是正常作答，不会拒答正常问题。
    """
    for m in ENTITY_RE.finditer(question):
        ent = m.group()
        if any(c in ent for c in searcher.COMPANIES):     # 「新易盛公司」也算命中
            continue
        if ent.startswith(ENTITY_STOP) or ENTITY_MOD_SKIP_RE.search(ent):
            continue
        mod = ent[:-2] if ent.endswith("公司") else ent
        if any(mod.endswith(w) for w in INDUSTRY_STOP):
            continue
        return ent
    return ""


def _chunk_unit(text: str) -> float:
    """块内声明的金额单位。—— 单位陷阱（德科立整份用千元）就靠它统一折算。"""
    m = re.search(r"单位[:：]\s*(千元|万元|亿元|元)", text)
    return UNIT_MULT.get(m.group(1), 1.0) if m else 1.0


def _cut_parent(text: str) -> str:
    """砍掉母公司报表段：同一份报告里母公司数可能大于合并数（新易盛 P42 就是）。"""
    cut = text.find("母公司利润表")
    if cut < 0:
        cut = text.find("母公司资产负债表")
    return text[:cut] if cut >= 0 else text


def _flatten(text: str):
    """把换行当单元格分隔符切开 —— 三种排版（管道式/拆行式/叙述式）统一成 token 流。"""
    flat = re.sub(r"[ \t]*\n[ \t]*", " | ", text)
    return [t.strip() for t in flat.split("|")]


def merge_lines(text: str) -> str:
    """把 PDF 抽取的硬换行接回去 —— 句子级摘录最怕半句。

    财报正文一行一个换行，"…领域通信连接的终端设备（包括\\n电信宽带、无线网络与边缘
    计算）以及高速光模块…" 这样一句话会被切成两半，只摘到前半句（Q8 就少了高速光模块那一半）。
    只接"两端都不像表格单元格"的换行：含 | 的行不接（那是一个科目一行），
    以数字/百分号/括号结尾或开头的行也不接。
    """
    out = []
    for ln in text.split("\n"):
        prev = out[-1].rstrip() if out else ""
        if (out and "|" not in prev and "|" not in ln
                and not re.search(r"[。；！？：:]$", prev)
                and not re.search(r"[\d%）》）]$", prev)
                and not re.match(r"^\s*[\d（(]", ln)):
            out[-1] += ln.strip()
        else:
            out.append(ln)
    return "\n".join(out)


def _norm_item(t: str) -> str:
    """科目名归一：「其中：营业成本」「减：营业成本」「二、营业总成本」「营业收入（元）」
    -> 裸科目名。利润表用的是「减：」前缀，不剥掉就一行都认不出来。"""
    t = re.sub(r"^(?:其中|减|加)[：:]\s*", "", t.strip())
    t = re.sub(r"^[一二三四五六七八九十]+[、.]\s*", "", t)
    t = re.sub(r"[（(][^）)]*[）)]\s*$", "", t)
    return re.sub(r"[\s|]+", "", t)


def _is_item(t: str, name: str) -> bool:
    n = _norm_item(t)
    return n in (name, name + "合计", name + "小计", name + "净额")


AMT_RE = re.compile(r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d{2})\s*(亿元|万元|千元|元)?(?!\s*%)")
PCT_INLINE_RE = re.compile(r"(?:同比|增长|增加|上升|下降)[^\d%]{0,8}(-?[\d.]+)\s*%")


def _amounts(s: str, default_unit: float):
    """抽出 s 里的金额（带单位词就用单位词，否则用块声明的默认单位）。"""
    out = []
    for m in AMT_RE.finditer(s):
        out.append(float(m.group(1).replace(",", "")) * UNIT_MULT.get(m.group(2), default_unit))
    return out


def _item_values(text: str, name: str):
    """取某科目行的本期值（第一个金额）。比 _rev_rows 宽松：认「其中：营业收入」这种
    子行，也认「营业收入合计」；但「营业总成本」不算「营业成本」，不会串行。"""
    unit = _chunk_unit(text)
    toks = _flatten(_cut_parent(text))
    for i, t in enumerate(toks):
        if not _is_item(t, name):
            continue
        vals = []
        for t2 in [t] + toks[i + 1:i + 4]:
            if t2 is not t and NEXT_ITEM_RE.search(t2):
                break
            vals.extend(_amounts(t2, unit))
        if vals:
            return vals
    return []


def _rev_rows(text: str):
    """从一块文本里解析「营业收入」行，返回 [(本期金额, 同比%, 行文本)]。

    同一份数据在库里有三种写法，都要认：
      管道式  '营业收入（元） | 20,909,746,962.20 | 10,437,170,265.50 | 100.34%'
      拆行式  '营业收入（元）\\n20,909,746,962.20\\n10,437,170,265.50\\n100.34%'
      叙述式  '2026 年上半年，公司实现营业收入27.05 亿元，同比增长32.92%；'
    统一成「用 | 切开」后按 token 往后看，遇到下一个科目名就停，避免串到下一行。
    """
    unit = _chunk_unit(text)
    text = _cut_parent(text)
    toks = _flatten(text)
    out = []
    for i, t in enumerate(toks):
        if "营业收入" not in t or any(k in t for k in ROW_SKIP):
            continue
        vals, raw, pct_cell, pct_inline = [], [], None, None
        # 窗口开到 24 个 token：三列式的表在「本期」前面还夹着空单元格
        # （中际旭创/天孚/联特），同比列落在 +8~+9；断行交给 NEXT_ITEM_RE，
        # 所以窗口宽一点不会串到下一行
        for j, t2 in enumerate([t] + toks[i + 1:i + 25]):
            if j > 0 and NEXT_ITEM_RE.search(t2):
                break
            if re.fullmatch(r"-?\d+(?:\.\d+)?%[；;。]?", t2):
                # 纯百分比单元格：取本行最后一个 —— 光迅那行前面有两个「占比重100%」，
                # 真正的同比 44.20% 在最后
                pct_cell = float(t2[:-1])
                continue
            vals.extend(_amounts(t2, unit))
            num = re.fullmatch(r"-?[\d,]+\.\d+", t2)
            if num:
                raw.append(float(num.group().replace(",", "")))
            if pct_inline is None:
                # 叙述式：「同比增长20.81%」，只认第一个 —— 同一句里后面还有
                # 「净利润…增长1.71%」，抢过来就把营收增速带错了（太辰光踩过）
                pm = PCT_INLINE_RE.search(t2)
                if pm:
                    pct_inline = float(pm.group(1))
        pct = pct_cell if pct_cell is not None else pct_inline
        if vals and pct is None and len(raw) >= 3 and abs(raw[2]) < 1000:
            pct = raw[2]      # 千元表的同比列不带 % 号（德科立 P7：… 545,069.47 433,355.28 25.78）
        if vals and pct is None:
            pm = PCT_INLINE_RE.search(text)   # 全块兜底：同比写在表格外的叙述句里
            if pm:
                pct = float(pm.group(1))
        if vals:
            out.append((vals[0], pct, t))
    return out


def _table_hits(searcher, company: str, reports, phrase: str = "主要会计数据",
                limit: int = None):
    """直接定位某公司某报告期内含指定表头词的块。"""
    where = {"$and": [{"company": company}, {"report": {"$in": reports}}]} if reports \
        else {"company": company}
    try:
        r = searcher.col.get(where=where, where_document={"$contains": phrase},
                             include=["documents", "metadatas"], limit=limit)
    except Exception:
        return []
    return [{"chunk_id": cid, "text": doc, "meta": meta, "rank": 0, "rrf": 0.0,
             "vrank": None, "brank": None, "src": "table"}
            for cid, doc, meta in zip(r["ids"], r["documents"], r["metadatas"])]


# 章节型问题（问"主要业务/主营业务"）：原文答案在「公司所属行业及主营业务」这一节，
# 但稠密检索会被「同业竞争」段抢走 —— 它们一样含"公司/业务/主营"这些词
# （Q8 实测前 6 句全是同业竞争）。这类题按标题词直接定位，比调权重可靠。
# 定位词用「主营业务」：报告里的标题和正文都是这个写法，"主要业务"只是子公司表的列名
# （剑桥科技 2026 半年报 P29），按"主要业务"定位会捞回一堆子公司明细。
LOCATE_PHRASES = {"主要业务": "主营业务", "主营业务": "主营业务", "主业": "主营业务"}


def locate_sections(hits, searcher, question: str, limit: int = 6):
    """按标题词定位章节，把定位到的块插到最前面（排名也跟着重排）。"""
    phrase = next((p for k, p in LOCATE_PHRASES.items() if k in question), None)
    coms = searcher.detect_companies(question)
    if not phrase or not coms:
        return hits
    period = searcher.detect_report(question)
    reports = sorted(searcher.report_names.get(period, [])) if period else []
    got = _table_hits(searcher, coms[0], reports, phrase=phrase)
    if not got:
        return hits
    # 问句没点名报告期时，历年年报里也有同一个标题词，而且年报的「主要业务」常常是
    # 子公司情况表的列名（剑桥科技 2025 年报 P37 就是）—— 按报告期从新到旧取，
    # 优先最新一期讲自家业务的那一节
    got.sort(key=lambda h: (-int(h["meta"].get("year") or 0), h["meta"]["page_start"]))
    got = got[:limit]
    ids = {h["chunk_id"] for h in hits}
    merged = [h for h in got if h["chunk_id"] not in ids] + hits
    for i, h in enumerate(merged, 1):
        h["rank"] = i
    return merged


def panorama_answer(question: str, searcher, per_company: int = 5):
    """跨公司排名题：逐公司定位科目表 → 解析 → 排序，答案里直接给结论。"""
    metric = "yoy" if YOY_RANK_RE.search(question) else "value"
    period = searcher.detect_report(question) or "半年度"
    reports = sorted(searcher.report_names.get(period, []))
    sub_q = question + PANORAMA_HINT

    best = {}
    for c in searcher.COMPANIES:
        # 先按表头词直接定位「主要会计数据」表 —— 跨公司要的是同一张可比口径的表，
        # 靠稠密检索碰运气只会返回各写各的叙述段（实测 12 家里只能碰到 7 家）。
        chunks = _table_hits(searcher, c, reports)
        if not chunks:                      # 定位不到才退回逐公司检索
            chunks = [dict(h, src="retrieval")
                      for h in searcher.search(sub_q, top_k=per_company,
                                               mode="hybrid", scope=c)]
        picked = None
        for h in chunks:
            for val, pct, line in _rev_rows(h["text"]):
                if (picked is None or val > picked[0]
                        or (val == picked[0] and picked[1] is None and pct is not None)):
                    picked = (val, pct, line, h)
        if picked:
            best[c] = picked

    if not best:
        return {"answer": "未召回到可解析的营业收入行", "hits": [], "backend": "panorama",
                "parsed": {}}

    # 逐家解析结果，评测/对账用（value 已按块内声明的单位折算成元）
    parsed = {c: {"value": v, "yoy": p, "cite": searcher.cite(h["meta"]), "line": line[:60]}
              for c, (v, p, line, h) in best.items()}

    # 排「增速」时同比没解析出来的公司只能排最后（拿金额当降级键会把它们顶到第一：
    # 中际旭创金额最大、同比未知，第一版就把它排在了增速榜第 1）
    def _key(kv):
        v, p = kv[1][0], kv[1][1]
        if metric == "yoy":
            return (0, -p) if p is not None else (1, -v)
        return (0, -v)

    ordered = sorted(best.items(), key=_key)
    head = "按 2026 上半年营业收入【同比增速】排序" if metric == "yoy" else "按 2026 上半年营业收入【规模】排序"
    lines = [f"{head}（{len(best)}/{len(searcher.COMPANIES)} 家取到数）："]
    for i, (c, (v, p, line, h)) in enumerate(ordered[:5], 1):
        yoy = f"，同比 {p:+.2f}%" if p is not None else "，同比未取到"
        lines.append(f"{i}. {c}　{v/1e8:.2f} 亿元{yoy}")
        lines.append(f"    —— {searcher.cite(h['meta'])}")
    miss = [c for c in searcher.COMPANIES if c not in best]
    if miss:
        lines.append(f"（未取到：{'、'.join(miss)}）")

    evidence = [best[c][3] for c, _ in ordered]
    for i, h in enumerate(evidence, 1):
        h["rank"] = i
    return {"answer": "\n".join(lines), "hits": evidence, "backend": "panorama",
            "parsed": parsed}


def compute_margin(hits, searcher, question: str):
    """问毛利率的题：营业收入、营业成本都在利润表里，但"毛利率"原文往往不给，
    摘录式后端只摘不算是答不出来的，这里补一步显式算术。

    两个数必须来自同一块（同一张表）—— 跨表取数会把分产品口径和合并口径混在一起；
    多块都算得出时取营业收入最大的那块（合并口径 ≥ 母公司口径，见 Q1 的母公司陷阱）。
    返回 (答案文本, 取值那块)；算不出来时 (None, None)。
    """
    def best_of(chunks):
        pick = None
        for h in chunks:
            t = h["text"]
            rev = _item_values(t, "营业收入") or _item_values(t, "营业总收入")
            cost = _item_values(t, "营业成本")
            if rev and cost and 0 < cost[0] < rev[0]:
                if pick is None or rev[0] > pick[0]:
                    pick = (rev[0], cost[0], h)
        return pick

    cand = list(hits)
    coms = searcher.detect_companies(question)
    if coms:
        # 池子里常常没有利润表（问的是「营业成本」，检索却捞回一堆成本分析叙述段），
        # 按科目名直接定位一遍
        period = searcher.detect_report(question)
        reports = sorted(searcher.report_names.get(period, [])) if period else []
        cand += _table_hits(searcher, coms[0], reports, phrase="营业成本")
    pick = best_of(cand)
    if pick is None:
        return None, None
    rev, cost, h = pick
    g = (rev - cost) / rev * 100
    return (f"计算：毛利率 =（营业收入 − 营业成本）÷ 营业收入\n"
            f"        =（{rev:,.2f} − {cost:,.2f}）÷ {rev:,.2f} = {g:.2f}%\n"
            f"  —— 两个数取自同一张表：{searcher.cite(h['meta'])}"), h


def extractive(hits, searcher, max_sent: int = 6, located_only: bool = False) -> str:
    """不依赖大模型：按「像不像答案」从召回片段里挑句子，逐句附出处。

    财报问答里这招比想象中能打 —— 答案往往就是原文的一句话，不需要改写。
    第一版只加总词面重合，结果审计套话（"管理层负责编制财务报表…"）因为含
    "财务报表/营业收入"这类高频词，把真正的数据行压了下去（见 _diag_zjxc.txt）。
    这一版补四个信号：
      1. 问数字的题，只认「金额形状」的数字，且优先只留带数字的句子
      2. 审计套话、公告抬头重罚
      3. 母公司报表段重罚 —— 同一份报告里母公司数可能大于合并数（新易盛 P42），
         默认要合并口径，除非问句点名母公司
      4. 名次权重 1/rank -> 3/rank，长度罚分 /4000 -> /400（原来几乎是摆设）

    located_only：定位通道（按标题词/科目名直接捞）拿到了块时，就只从这些块里摘句。
    问「主要业务」这种题，检索回来的全是同业竞争、子公司明细表 —— 一样含"公司+业务"，
    词面重合度还更高，混在一起必然把它们摘出来（Q8 实测）。
    """
    if located_only:
        loc = [h for h in hits if h.get("src") == "table"]
        hits = loc or hits
    import jieba
    q = searcher.last_query or ""
    qterms = {w for w in jieba.cut(q) if len(w) > 1}
    want_num = bool(WANT_NUM_RE.search(q))
    want_parent = "母公司" in q

    com = searcher.detect_companies(q)
    scored = []
    for h in hits:
        m = h["meta"]
        cite = searcher.cite(m)
        text = merge_lines(h["text"])
        # 母公司报表在本块中的起始位置，位置之后的句子默认不是合并口径
        cut = text.find("母公司利润表")
        if cut < 0:
            cut = text.find("母公司资产负债表")
        for mt in re.finditer(r"[^\n。；!?]+[。；!?]?", text):
            s = mt.group().strip()
            if len(s) < 12 or len(s) > 600:
                continue
            toks = {w for w in jieba.cut(s) if len(w) > 1}
            if not toks:
                continue
            hit_terms = qterms & toks
            overlap = len(hit_terms)
            # 答案句常常只说「公司/本公司」不重复公司名（剑桥科技那句就是
            # 「公司的主营业务为…」），问句里的公司名要算它命中，否则重合度为 0 直接被丢。
            # 只在定位块里这么算：定位块是按标题词捞出来的，块内「公司」必然指这家公司；
            # 自由检索的块没这个保证，放开就会把一堆含"公司"的句子全放进来（Q1 实测）。
            if com and "公司" in s and located_only:
                overlap += 1
            if overlap == 0:
                continue
            # 只跟公司名重合的句子讲的是"这家公司"，不是"这个科目"（新易盛 P77 的
            # 「美国新易盛 | 8.84%、21%」就是，还因为句子短排到了正解前面）
            if com and hit_terms and hit_terms <= set(com):
                score_adj = -1.0
            else:
                score_adj = 0.0
            has_money = bool(MONEY_RE.search(s))
            score = overlap * 1.0 + 3.0 / h["rank"] - len(s) / 400.0 + score_adj
            if want_num:
                score += 1.0 if has_money else -1.5
            if any(b in s for b in BOILER):
                score -= 1.5
            if SECTION_RE.search(s[:40]):
                score -= 0.8
            if any(b in s for b in HEADER):
                score -= 1.5
            # 母公司口径：位置法会被"表头在上一页"绕过去（太辰光 P42 就是），
            # 句子本身写了"母公司"的也算
            in_parent = (cut >= 0 and mt.start() > cut) or "母公司" in s
            if in_parent and not want_parent:
                score -= 1.0
            scored.append((score, cite, s, has_money, in_parent))

    scored.sort(key=lambda x: -x[0])

    # 两条硬排除（都带兜底：全被排掉就退回全量）：
    #   1. 问数字的题不留没有数字的句子（干掉"证券代码…""管理层负责…"）
    #   2. 母公司报表段的句子不留 —— 母公司口径不是"差一点的答案"，是另一个口径的
    #      答案，混进答案里会直接误导（新易盛 26,711,151,530.32 就是母公司数）
    #   3. 公告抬头（证券代码/编号）不留 —— 含数字也含公司名，但从来不是答案
    def keep(x):
        if want_num and not x[3]:
            return False
        if any(b in x[2] for b in HEADER):
            return False
        return not (x[4] and not want_parent)

    pool = [x for x in scored if keep(x)] or scored
    seen, picked = set(), []
    for score, cite, s, _, _ in pool:
        key = s[:40]
        if key in seen:
            continue
        seen.add(key)
        picked.append((cite, s))
        if len(picked) >= max_sent:
            break
    if not picked:
        return None
    lines = [f"· {s}\n  —— {c}" for c, s in picked]
    return "从召回原文中摘出的相关句子：\n\n" + "\n".join(lines)


POOL_K = 16      # 摘录用的候选池，比页面显示的 k 宽：单路强命中常被 RRF 挤到显示名次之外
                 # （实测 Q2 金标块在 hybrid 里排 13，k=8/12 都够不着）


def answer(question: str, searcher, top_k: int = 6, mode: str = "hybrid",
           backend: str = "auto"):
    # 库外公司：先拒答，不检索 —— 语料里没有的公司，检索只会返回"最像的"那家
    ent = out_of_corpus_entity(question, searcher)
    if ent:
        return {"answer": f"资料中未找到「{ent}」。本知识库只含 "
                          f"{len(searcher.COMPANIES)} 家光模块/光通信公司的财报"
                          f"（{'、'.join(searcher.COMPANIES)}），没有该公司的任何数据，"
                          f"不能给出数字。",
                "hits": [], "backend": "refuse"}

    # 跨公司排名题：单次检索只覆盖 3~4 家，改走逐公司枚举 + 解析
    if RANK_RE.search(question) and not searcher.detect_companies(question):
        return panorama_answer(question, searcher)

    hits = searcher.search(question, top_k=max(top_k, POOL_K), mode=mode)
    searcher.last_query = question
    if not hits:
        return {"answer": "未召回到任何片段", "hits": [], "backend": "none"}
    backend = backend if backend != "auto" else (llm_available() or "extractive")
    if backend == "extractive":
        hits = locate_sections(hits, searcher, question)
        calc = None
        if MARGIN_RE.search(question):          # 「表格+计算」类：先给算出来的结论
            calc, calc_hit = compute_margin(hits, searcher, question)
            if calc_hit and calc_hit["chunk_id"] not in {h["chunk_id"] for h in hits}:
                hits = [calc_hit] + hits        # 取值那块也进证据列表
                for i, h in enumerate(hits, 1):
                    h["rank"] = i
        txt = extractive(hits, searcher, located_only=True)
        if calc:
            txt = calc + ("\n\n" + txt if txt else "")
        return {"answer": txt, "hits": hits[:top_k], "backend": "extractive"}
    try:
        txt = call_llm(PROMPT.format(ctx=build_ctx(hits[:top_k], searcher), q=question))
        return {"answer": txt, "hits": hits[:top_k], "backend": backend}
    except Exception as e:
        return {"answer": f"[LLM 调用失败: {e}]", "hits": hits[:top_k],
                "backend": f"{backend}-error"}


if __name__ == "__main__":
    load_dotenv()
    from search import HybridSearcher
    s = HybridSearcher()
    q = sys.argv[1] if len(sys.argv) > 1 else "中际旭创2026年上半年营业收入是多少"
    r = answer(q, s)
    print(f"后端 {r['backend']}  问题: {q}\n" + "=" * 78)
    if r["answer"]:
        print(r["answer"])
    print("\n--- 召回出处 ---")
    for h in r["hits"]:
        print(f"  #{h['rank']} {s.cite(h['meta'])}  RRF={h['rrf']:.5f}")
