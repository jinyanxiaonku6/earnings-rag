# -*- coding: utf-8 -*-
"""从巨潮资讯(cninfo)按股票批量下载年报/半年报 PDF。

用法: python scripts/fetch_reports.py
产物: data/pdfs/*.pdf  +  data/manifest.json
"""
import json, re, time, sys
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
PDF_DIR = ROOT / "data" / "pdfs"
PDF_DIR.mkdir(parents=True, exist_ok=True)

# 光模块 / 光通信板块
COMPANIES = [
    ("300308", "中际旭创"), ("300502", "新易盛"),  ("300394", "天孚通信"),
    ("002281", "光迅科技"), ("300548", "博创科技"), ("300570", "太辰光"),
    ("603083", "剑桥科技"), ("000988", "华工科技"), ("688498", "源杰科技"),
    ("688313", "仕佳光子"), ("688205", "德科立"),  ("301205", "联特科技"),
]
# (类别代码, 报告类型标签, 报告年份, 披露窗口)  —— 窗口按实际披露时间取，不是报告期
TARGETS = [
    ("category_ndbg_szsh",  "年度报告", "2025", "2026-01-01~2026-06-30"),
    ("category_bndbg_szsh", "半年度报告", "2026", "2026-07-01~2026-12-31"),
]

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                  "Referer": "http://www.cninfo.com.cn/", "X-Requested-With": "XMLHttpRequest"})
SKIP = re.compile(r"摘要|英文|English|更正|补充|问询|反馈|已取消|取消|H股|港股")


def title_ok(title: str, label: str) -> bool:
    """标题匹配。注意「年度报告」不含子串「年报」，必须按完整词匹配。"""
    t = (title or "").replace(" ", "")
    if SKIP.search(t):
        return False
    if label == "年度报告":                       # 半年报标题里也含「年度报告」，要排掉
        return "年度报告" in t and "半年度报告" not in t
    return "半年度报告" in t

def org_id(code):
    r = S.post("http://www.cninfo.com.cn/new/information/topSearch/query",
               data={"keyWord": code, "maxNum": "10"}, timeout=25)
    for it in r.json() or []:
        if it.get("code") == code:
            return it.get("orgId")
    return None

def query(code, orgid, cat, se_date):
    col = "sse" if code[0] == "6" else "szse"
    d = {"pageNum": "1", "pageSize": "30", "column": col, "tabName": "fulltext", "plate": "",
         "stock": f"{code},{orgid}", "searchkey": "", "secid": "", "category": cat,
         "trade": "", "seDate": se_date, "sortName": "", "sortType": "", "isHLtitle": "true"}
    r = S.post("http://www.cninfo.com.cn/new/hisAnnouncement/query", data=d, timeout=25)
    return (r.json() or {}).get("announcements") or []

manifest = []
for code, name in COMPANIES:
    oid = org_id(code)
    if not oid:
        print(f"!! {code} {name}: 拿不到 orgId");  continue
    for cat, label, year, window in TARGETS:
        try:
            anns = query(code, oid, cat, window)
        except Exception as e:
            print(f"!! {code} {name} {label}: 查询失败 {e}");  continue
        # 标题需含目标年份，排除摘要/更正等
        cand = [a for a in anns
                if year in (a.get("announcementTitle") or "")
                and title_ok(a.get("announcementTitle"), label)]
        if not cand:
            print(f"!! {code} {name} {year}{label}: 无匹配公告");  continue
        cand.sort(key=lambda a: a.get("announcementTime") or 0, reverse=True)
        a = cand[0]
        url = "http://static.cninfo.com.cn/" + a["adjunctUrl"]
        fn = f"{code}_{name}_{year}{label}.pdf"
        dst = PDF_DIR / fn
        if dst.exists() and dst.stat().st_size > 100_000:
            print(f"   已存在 {fn}");  manifest.append({"code":code,"name":name,"year":year,
                "type":label,"file":fn,"url":url,"title":a.get("announcementTitle")});  continue
        try:
            with S.get(url, timeout=180, stream=True) as r:
                r.raise_for_status()
                with open(dst, "wb") as f:
                    for buf in r.iter_content(1 << 16):
                        f.write(buf)
            print(f"OK {fn}  {dst.stat().st_size/1048576:.1f} MB")
            manifest.append({"code":code,"name":name,"year":year,"type":label,
                             "file":fn,"url":url,"title":a.get("announcementTitle")})
        except Exception as e:
            print(f"!! 下载失败 {fn}: {e}")
        time.sleep(0.4)

(ROOT / "data" / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\n=== 共 {len(manifest)} 份 ===")
