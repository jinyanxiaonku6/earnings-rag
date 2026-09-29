# -*- coding: utf-8 -*-
"""截问答页面图。用法：python shots.py [题目筛选]

需要 app.py 已在 8848 跑着。用无头 Chrome 打开带 ?q= 的链接，
加载后页面会自动问一次，等渲染完再截图。
"""
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "shots"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BASE = "http://127.0.0.1:8848/"

# (文件名, 题目, 取几块, 窗口高)
SHOTS = [
    ("q6_panorama", "2026年上半年营业收入规模最大的前三家公司是哪三家？", 6, 2000),
    ("q3_margin", "太辰光2026年上半年的营业成本是多少？据此计算的毛利率是多少？", 6, 1600),
    ("q7_annual", "光迅科技2025年全年营业收入是多少？", 6, 1500),
    ("q10_refuse", "宁德时代2026年上半年营业收入是多少？", 6, 1200),
]


def shot(name: str, q: str, k: int, height: int) -> Path:
    OUT.mkdir(exist_ok=True)
    url = BASE + "?" + urllib.parse.urlencode({"q": q, "mode": "hybrid", "k": k})
    png = OUT / f"{name}.png"
    if png.exists():
        png.unlink()
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    f"--screenshot={png}", "--window-size=1240,%d" % height,
                    "--virtual-time-budget=25000", url],
                   check=True, timeout=180,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return png


def main():
    want = sys.argv[1] if len(sys.argv) > 1 else ""
    for name, q, k, h in SHOTS:
        if want and want not in name:
            continue
        t0 = time.time()
        png = shot(name, q, k, h)
        print(f"{png.name:20s} {png.stat().st_size/1024:7.1f} KB  {time.time()-t0:5.1f}s")


if __name__ == "__main__":
    main()
