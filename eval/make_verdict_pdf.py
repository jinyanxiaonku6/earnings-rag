# -*- coding: utf-8 -*-
"""逐题判定表.md → HTML（复核对照表同款样式）→ Chrome 无头打印 PDF。
中文一律走 Python 内部的 Unicode，不经过 bash / 命令行参数。"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\Rookie\projects\earnings_rag")
MD = ROOT / "逐题判定表.md"
HTML = ROOT / "逐题判定表.html"
PDF = ROOT / "逐题判定表.pdf"
TMP_PDF = ROOT / "_scratch" / "verdict_tmp.pdf"
PANDOC = r"C:\Users\Rookie\AppData\Local\Pandoc\pandoc"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
md = MD.read_text(encoding="utf-8")

body = subprocess.run(
    [PANDOC, "-f", "gfm", "-t", "html5"],
    input=md.encode("utf-8"), capture_output=True,
).stdout.decode("utf-8")

CSS = """
  @page { size: A4; margin: 12mm 12mm; }
  * { box-sizing: border-box; }
  body { margin: 0; color: #16181d; background: #fff;
         font: 9.2pt/1.5 "Source Han Sans CN", "Noto Sans SC", "Microsoft YaHei", sans-serif; }
  h1 { font-size: 15pt; margin: 0 0 3px; letter-spacing: -.2px; }
  h2 { font-size: 10.6pt; margin: 12px 0 5px; padding-left: 7px;
       border-left: 3px solid #2563eb; line-height: 1.3; }
  h3 { font-size: 10pt; margin: 11px 0 4px; padding: 3px 7px; background: #f2f5fa;
       border-radius: 3px; }
  p { margin: 5px 0; }
  ul { margin: 5px 0 5px 16px; padding: 0; }
  li { margin: 2px 0; }
  code { font-family: Consolas, "Courier New", monospace; font-size: 8.4pt;
         background: #f2f4f8; padding: 0 2px; border-radius: 2px; }
  pre { background: #fafbfd; border: 1px solid #e6e9ef; border-radius: 3px;
        padding: 6px 8px; margin: 5px 0; font-size: 8.2pt; line-height: 1.45;
        white-space: pre-wrap; word-break: break-word; }
  pre code { background: none; padding: 0; font-size: 8.2pt; }
  table { width: 100%; border-collapse: collapse; font-size: 8.2pt; margin: 6px 0 4px; }
  th, td { border: 1px solid #e3e6ec; padding: 3px 5px; text-align: left;
           vertical-align: top; word-break: break-word; }
  td:nth-child(3), th:nth-child(3) { white-space: nowrap; }   /* 判定列别折行 */
  th { background: #f2f5fa; font-weight: 700; }
  a { color: #2563eb; text-decoration: none; }
  blockquote { margin: 6px 0; padding: 6px 9px; background: #fafbfd;
               border-left: 3px solid #d7dbe3; color: #374151; font-size: 8.8pt; }
  hr { border: none; border-top: 1px solid #e6e9ef; margin: 10px 0; }
"""

html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>作业3 · 方向A — 逐题判定表</title>
<style>{CSS}</style></head>
<body>
{body}
</body></html>
"""
HTML.write_text(html, encoding="utf-8")

if TMP_PDF.exists():
    TMP_PDF.unlink()
subprocess.run([
    CHROME, "--headless=new", f"--print-to-pdf={TMP_PDF}",
    "--no-pdf-header-footer", "--virtual-time-budget=20000",
    "--window-size=1280,1696", HTML.as_uri(),
], capture_output=True, timeout=180)
if not TMP_PDF.exists():
    sys.exit("PDF 未生成")
TMP_PDF.replace(PDF)
print("ok:", HTML.name, PDF.name, PDF.stat().st_size, "bytes")
