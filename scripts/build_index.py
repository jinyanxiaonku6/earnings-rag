# -*- coding: utf-8 -*-
"""data/chunks.jsonl -> 双索引
  1) 向量索引: Qwen3-Embedding-0.6B (GPU) -> Chroma
  2) 稀疏索引: jieba 分词 -> rank_bm25 (pickle)

用法: python scripts/build_index.py [--rebuild]
"""
import json, os, pickle, sys, time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

ROOT = Path(__file__).resolve().parents[1]
CHUNKS = ROOT / "data" / "chunks.jsonl"
DB_DIR = ROOT / "data" / "db"
MODEL = Path(r"C:\Users\Rookie\projects\embed_pipeline\models\Qwen3-Embedding-0.6B")
COLLECTION = "earnings"
BM25_PKL = ROOT / "data" / "bm25.pkl"

# 检索指令：非对称检索（问句找文档）必须带
INSTRUCT = "Given a Chinese financial report query, retrieve relevant passages that answer the query"
BATCH = 16
MAX_SEQ = 1024         # ★实测：这批财报 1 字≈1.1 token，块最长 964 token。设 512 会截断 65% 的块

# jieba 自定义词典：公司名 + 领域词。不加会把「光模块」切成「光/模块」，「中际旭创」切碎
USER_WORDS = ["中际旭创","新易盛","天孚通信","光迅科技","博创科技","太辰光","剑桥科技",
              "华工科技","源杰科技","仕佳光子","德科立","联特科技",
              "光模块","光器件","光芯片","硅光","相干","波分复用","数据中心","数通",
              "毛利率","净利率","营业收入","归母净利润","扣非净利润","研发费用",
              "销售费用","管理费用","财务费用","经营活动现金流","资本开支",
              "800G","400G","1.6T","100G","AOC","DAC","CPO","LPO","OSFP","QSFP",
              "磷化铟","铌酸锂","EML","VCSEL","DFB","AWG","PLC","TEC",
              "前五名客户","前五名供应商","境外收入","汇率波动","存货跌价准备"]


def load_chunks():
    rows = [json.loads(l) for l in CHUNKS.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows


def main():
    rebuild = "--rebuild" in sys.argv
    rows = load_chunks()
    print(f"载入 {len(rows)} 块")

    # ---------- 1. 向量索引 ----------
    import chromadb
    from sentence_transformers import SentenceTransformer
    import torch

    DB_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(DB_DIR))
    if rebuild:
        try: client.delete_collection(COLLECTION)
        except Exception: pass
    col = client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"设备 {dev}")
    model = SentenceTransformer(str(MODEL), device=dev)
    model.max_seq_length = MAX_SEQ
    if dev == "cuda":
        model.half()

    # 编码长度自检 + 全量 token 长度（排序分批要用）
    tok = model.tokenizer
    texts = [(INSTRUCT + "\n" + r["embed_text"]) for r in rows]
    print("统计 token 长度…")
    # 分批 tokenize（不 padding），否则一次吃 11543 条会爆内存
    all_lens = []
    for i in range(0, len(texts), 512):
        enc = tok(texts[i:i+512], add_special_tokens=True,
                  padding=False, truncation=False)["input_ids"]
        all_lens.extend(len(x) for x in enc)
    srt = sorted(all_lens)
    over = sum(1 for x in all_lens if x > MAX_SEQ)
    print(f"token 长度 中位 {srt[len(srt)//2]} 最大 {srt[-1]} 均值 {sum(srt)/len(srt):.0f} | 超 {MAX_SEQ} 的 {over} 块")

    already = col.count()
    if already and not rebuild:
        print(f"库里已有 {already} 块，跳过编码（--rebuild 强制重灌）")
    else:
        # ★ 按 token 长度排序后再分批：同批长度接近 → padding 浪费最小
        #   不排序时每批都按该批最长样本 padding，显存与算力都被浪费掉
        import numpy as np
        order = sorted(range(len(texts)), key=lambda i: all_lens[i])
        t0 = time.time()
        vecs = None
        for i in range(0, len(order), BATCH * 8):
            idx = order[i:i + BATCH * 8]
            e = model.encode([texts[j] for j in idx], batch_size=BATCH,
                             normalize_embeddings=True, show_progress_bar=False,
                             convert_to_numpy=True)
            if vecs is None:
                vecs = np.zeros((len(texts), e.shape[1]), dtype=np.float32)
            vecs[idx] = e
            done = min(i + BATCH * 8, len(order))
            el = time.time() - t0
            print(f"  编码 {done}/{len(order)}  {el:.0f}s  剩约 {el/done*(len(order)-done)/60:.0f} 分", flush=True)
        embs = vecs
        print(f"编码完成 {time.time()-t0:.0f}s  ({len(rows)/(time.time()-t0):.0f} 块/秒)  dim={embs.shape[1]}")
        B = 2000
        for i in range(0, len(rows), B):
            sub = rows[i:i+B]
            col.add(ids=[r["chunk_id"] for r in sub],
                    documents=[r["text"] for r in sub],
                    embeddings=[e.tolist() for e in embs[i:i+B]],
                    metadatas=[{"company": r["company"], "code": r["code"],
                                "report": r["report"], "year": r["year"], "rtype": r["rtype"],
                                "file": r["file"], "section": r["section"],
                                "page_start": r["page_start"], "page_end": r["page_end"]} for r in sub])
            print(f"  写入 {min(i+B, len(rows))}/{len(rows)}")
    print(f"向量库 {col.count()} 块 @ {DB_DIR}")

    # ---------- 2. BM25 稀疏索引 ----------
    import jieba
    from rank_bm25 import BM25Okapi
    for w in USER_WORDS:
        jieba.add_word(w, freq=1000)
    t0 = time.time()
    corpus_tokens = [list(jieba.cut_for_search(r["text"])) for r in rows]
    bm25 = BM25Okapi(corpus_tokens, k1=1.5, b=0.75)
    BM25_PKL.write_bytes(pickle.dumps({"bm25": bm25, "ids": [r["chunk_id"] for r in rows]}, protocol=4))
    print(f"BM25 建好 {time.time()-t0:.0f}s -> {BM25_PKL}  ({BM25_PKL.stat().st_size/1048576:.1f} MB)")


if __name__ == "__main__":
    main()
