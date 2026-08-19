#!/usr/bin/env python3
"""Convert trade dashboard JSON reports to clean Markdown for Claude Code consumption."""

import json
import sys
import os
import glob
import html2text

def convert(json_path: str, output_path: str | None = None) -> str:
    with open(json_path) as f:
        data = json.load(f)

    h = html2text.HTML2Text()
    h.body_width = 0        # no wrapping
    h.ignore_images = True
    h.ignore_links = False
    h.protect_links = True

    md = h.handle(data["html"])

    header = f"# {data['symbol']} 技术分析报告\n\n> 生成时间: {data['timestamp']}\n\n"
    content = header + md

    if output_path is None:
        output_path = json_path.replace(".json", ".md")

    with open(output_path, "w") as f:
        f.write(content)

    return output_path


def find_latest(symbol: str | None = None, reports_dir: str | None = None) -> str:
    if reports_dir is None:
        reports_dir = os.path.dirname(os.path.abspath(__file__))
        reports_dir = os.path.join(reports_dir, "reports")

    pattern = f"*_{symbol}.json" if symbol else "*.json"
    files = sorted(glob.glob(os.path.join(reports_dir, pattern)))
    if not files:
        raise FileNotFoundError(f"No reports found for pattern: {pattern}")
    return files[-1]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: report2md.py <json_file|symbol> [output.md]")
        print("  report2md.py reports/20260529_152456_BTC-USDT.json")
        print("  report2md.py BTC-USDT")
        sys.exit(1)

    arg = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else None

    if arg.endswith(".json"):
        json_path = arg
    else:
        json_path = find_latest(arg)

    result = convert(json_path, out)
    print(f"Written: {result}")
