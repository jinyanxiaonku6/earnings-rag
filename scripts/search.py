# -*- coding: utf-8 -*-
"""混合检索：向量(Qwen3-Embedding + Chroma) ⊕ BM25(jieba) → RRF 融合。

三路可单独走，也可融合：
    vector  语义相近，问法随意时强
    bm25    字面命中，精确数字/专有名词时强
    hybrid  两路各取 top-n，用 RRF 重排
"""
import os, pickle, re, sys
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

ROOT = Path(__file__).resolve().parents[1]
DB_DIR = ROOT / "data" / "db"
BM25_PKL = ROOT / "data" / "bm25.pkl"
MODEL = Path(r"C:\Users\Rookie\projects\embed_pipeline\models\Qwen3-Embedding-0.6B")
COLLECTION = "earnings"

INSTRUCT = "Given a Chinese financial report query, retrieve relevant passages that answer the query"
RRF_K = 60           # RRF 平滑常数，标准取 60
CAND = 30            # 每路先取多少候选再融合

# 报告期识别：注意顺序 —— 「半年度」里含「年度」，必须先判半年报。
RE_PERIOD_HALF = re.compile(r"上半年|半年度|半年报|中报|H1|Q2")
RE_PERIOD_FULL = re.compile(r"全年|年度|年报|Q4")

USER_WORDS = ["中际旭创","新易盛","天孚通信","光迅科技","博创科技","太辰光","剑桥科技",
              "华工科技","源杰科技","仕佳光子","德科立","联特科技",
              "光模块","光器件","光芯片","硅光","相干","波分复用","数据中心","数通",
              "毛利率","净利率","营业收入","归母净利润","扣非净利润","研发费用",
              "销售费用","管理费用","财务费用","经营活动现金流","资本开支",
              "800G","400G","1.6T","100G","AOC","DAC","CPO","LPO","OSFP","QSFP",
              "磷化铟","铌酸锂","EML","VCSEL","DFB","AWG","PLC","TEC",
              "前五名客户","前五名供应商","境外收入","汇率波动","存货跌价准备"]


class HybridSearcher:
    COMPANIES = ["中际旭创", "新易盛", "天孚通信", "光迅科技", "博创科技", "太辰光",
                 "剑桥科技", "华工科技", "源杰科技", "仕佳光子", "德科立", "联特科技"]

    def __init__(self, device=None):
        import chromadb, jieba, torch
        from rank_bm25 import BM25Okapi
        from sentence_transformers import SentenceTransformer

        for w in USER_WORDS:
            jieba.add_word(w, freq=1000)
        self.jieba = jieba

        self.client = chromadb.PersistentClient(path=str(DB_DIR))
        self.col = self.client.get_collection(COLLECTION)

        blob = pickle.loads(BM25_PKL.read_bytes())
        self.bm25, self.bm25_ids = blob["bm25"], blob["ids"]
        self.id2row = {}                       # 用于 BM25 命中取回原文

        # chunk_id 形如 000988_华工科技_2025年度报告#0000 -> 取公司名，建 BM25 侧的公司索引
        self.company_idx = {}
        self.period_idx = {}      # "半年度"/"年度" -> [BM25 行号]
        self.report_names = {}    # "半年度"/"年度" -> {报告全名}，给 Chroma 的 where 用
        for i, cid in enumerate(self.bm25_ids):
            parts = cid.split("_")
            if len(parts) >= 2 and parts[1] in self.COMPANIES:
                self.company_idx.setdefault(parts[1], []).append(i)
            if len(parts) >= 3:
                rep = parts[2].split("#")[0]
                tag = "半年度" if "半年度" in rep else "年度"
                self.period_idx.setdefault(tag, []).append(i)
                self.report_names.setdefault(tag, set()).add(rep)

        dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = SentenceTransformer(str(MODEL), device=dev)
        self.model.max_seq_length = 1024
        self.device = dev

    # ---------- 公司元数据预过滤 ----------
    # 问句点名了公司时，先在元数据上把候选收窄到该公司再排序。
    # 实测（eval/questions.json 的 7 道单公司题）：BM25 中位名次 54 -> 11，进 top-8 由 0/7 升到 3/7。
    # 原因是 2026/年/营业收入/上半年 这类词在库里覆盖 981/3567/621/76 个块，
    # 短问句的信息量被高频词稀释；先收窄范围等于把"哪家公司"这一维从排序里摘掉。
    def detect_companies(self, query: str):
        return [c for c in self.COMPANIES if c in query]

    # 报告期预过滤，理由同公司预过滤：问「2026年上半年」时，同一家公司
    # 2025 年报里的「营业收入」与半年报里的高度相似，不收窄就会互相挤占，
    # 实测 Q1「新易盛2026上半年营业收入」原来摘到的是 2025 年报的句子。
    def detect_report(self, query: str):
        if RE_PERIOD_HALF.search(query):
            return "半年度"
        if RE_PERIOD_FULL.search(query):
            return "年度"
        return None

    def _where(self, company: str = None, period: str = None):
        """把公司/报告期两个元数据条件合成 Chroma 的 where 子句。"""
        conds = []
        if company:
            conds.append({"company": company})
        if period and self.report_names.get(period):
            conds.append({"report": {"$in": sorted(self.report_names[period])}})
        if not conds:
            return {}
        return {"$and": conds} if len(conds) > 1 else conds[0]

    def vector(self, query: str, k: int = CAND, company: str = None, period: str = None):
        emb = self.model.encode([INSTRUCT + "\n" + query], normalize_embeddings=True,
                                convert_to_numpy=True)[0]
        kw = {"where": self._where(company, period)} if (company or period) else {}
        r = self.col.query(query_embeddings=[emb.tolist()], n_results=k,
                           include=["documents", "metadatas", "distances"], **kw)
        out = []
        for cid, doc, meta, dist in zip(r["ids"][0], r["documents"][0],
                                        r["metadatas"][0], r["distances"][0]):
            out.append({"chunk_id": cid, "text": doc, "meta": meta,
                        "vscore": 1.0 - dist})
        return out

    def bm25_search(self, query: str, k: int = CAND, company: str = None, period: str = None):
        toks = list(self.jieba.cut_for_search(query))
        scores = self.bm25.get_scores(toks)
        pool = None
        if company:
            pool = set(self.company_idx.get(company, []))
        if period:
            p = set(self.period_idx.get(period, []))
            pool = p if pool is None else (pool & p)
        idx = sorted(pool) if pool is not None else range(len(scores))
        idx = sorted(idx, key=lambda i: -scores[i])[:k]
        return [{"chunk_id": self.bm25_ids[i], "bscore": float(scores[i])} for i in idx]

    # ---------- 融合 ----------
    def search(self, query: str, top_k: int = 6, mode: str = "hybrid", cand: int = CAND,
               scope: str = "auto"):
        """返回 top_k 条，每条带 text/meta/各路得分/融合分。

        scope="auto"   问句里恰好出现 1 家公司 -> 收窄到该公司；0 家或 ≥2 家 -> 全库
        scope="all"    强制全库（对比用）
        scope="<公司名>" 强制收窄到指定公司
        """
        if scope == "auto":
            named = self.detect_companies(query)
            company = named[0] if len(named) == 1 else None
        elif scope == "all":
            company = None
        else:
            company = scope
        self.last_company = company
        # scope="all" 是评测里的对照组（故意不加任何元数据过滤），此处一并关掉报告期过滤
        period = self.detect_report(query) if scope != "all" else None
        self.last_period = period

        if mode in ("vector", "hybrid"):
            vhits = self.vector(query, cand, company, period)
        else:
            vhits = []
        if mode in ("bm25", "hybrid"):
            bhits = self.bm25_search(query, cand, company, period)
        else:
            bhits = []

        pool = {}
        for rank, h in enumerate(vhits):
            pool.setdefault(h["chunk_id"], {"chunk_id": h["chunk_id"]})["vrank"] = rank + 1
            pool[h["chunk_id"]]["text"] = h["text"]
            pool[h["chunk_id"]]["meta"] = h["meta"]
            pool[h["chunk_id"]]["vscore"] = h.get("vscore")
        for rank, h in enumerate(bhits):
            cid = h["chunk_id"]
            pool.setdefault(cid, {"chunk_id": cid})["brank"] = rank + 1
            pool[cid]["bscore"] = h.get("bscore")

        # BM25 命中但向量没覆盖的，从 Chroma 取回原文
        miss = [cid for cid, v in pool.items() if "text" not in v]
        if miss:
            got = self.col.get(ids=miss, include=["documents", "metadatas"])
            for cid, doc, meta in zip(got["ids"], got["documents"], got["metadatas"]):
                pool[cid]["text"] = doc
                pool[cid]["meta"] = meta

        for cid, v in pool.items():
            s = 0.0
            if v.get("vrank"): s += 1.0 / (RRF_K + v["vrank"])
            if v.get("brank"): s += 1.0 / (RRF_K + v["brank"])
            v["rrf"] = s
        ranked = sorted(pool.values(), key=lambda x: -x["rrf"])[:top_k]
        for i, h in enumerate(ranked, 1):
            h["rank"] = i
        return ranked

    def panorama(self, query: str, per_company: int = 1, mode: str = "hybrid"):
        """跨公司全景题：逐家公司各取 top-n，再按名次交错汇总。

        单次全库检索必然只覆盖少数几家公司（实测 3~4/12），
        因为 12 家公司的同类科目块彼此极像，排序器只会挑出其中几家。
        逐公司检索把"覆盖 12 家"从排序问题变成枚举问题。
        """
        out = []
        for c in self.COMPANIES:
            hits = self.search(query, top_k=per_company, mode=mode, scope=c)
            for h in hits:
                h["company_scoped"] = c
                out.append(h)
        for i, h in enumerate(out, 1):
            h["rank"] = i
        return out

    @staticmethod
    def cite(meta: dict) -> str:
        return (f"{meta['company']}《{meta['report']}》{meta['section']} "
                f"P{meta['page_start']}" + (f"-{meta['page_end']}" if meta['page_end'] != meta['page_start'] else ""))


def main():
    q = sys.argv[1] if len(sys.argv) > 1 else "中际旭创2026年上半年营业收入是多少"
    mode = sys.argv[2] if len(sys.argv) > 2 else "hybrid"
    s = HybridSearcher()
    print(f"\n问题: {q}   [模式 {mode}]\n" + "=" * 78)
    for h in s.search(q, top_k=5, mode=mode):
        m = h["meta"]
        print(f"\n#{h['rank']} RRF={h['rrf']:.5f}  vrank={h.get('vrank')} brank={h.get('brank')}")
        print(f"    {s.cite(m)}")
        print(f"    {h['text'][:180].replace(chr(10), ' / ')}")


if __name__ == "__main__":
    main()
