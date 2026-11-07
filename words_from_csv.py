#!/usr/bin/env python3
"""words_from_csv.py — 取引先一覧 CSV から AmiVoice 単語登録ファイル（words.txt）を作る。

    python words_from_csv.py customers.csv --name-col 社名 --yomi-col 読み > words.txt

- CSV に読みの列があればそれを使う。無い行は pykakasi（任意依存）で仮読みを付け、末尾に `# 要確認` を付ける
- 法人格（株式会社・有限会社・合同会社）は識別部分だけ残す（AmiVoice の辞書は短い表記のほうが効く）
- 出力は 1 行「表記 読み」。voicememo.py --words で読む
"""
from __future__ import annotations

import argparse
import csv
import re
import sys

LEGAL = re.compile(r"(株式会社|有限会社|合同会社|一般社団法人|\(株\)|（株）|㈱)")


def to_hira(s: str) -> str:
    try:
        import pykakasi
    except ImportError:
        return ""
    kks = pykakasi.kakasi()
    return "".join(x["hira"] for x in kks.convert(s))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--name-col", default="社名")
    ap.add_argument("--yomi-col", default="読み")
    ap.add_argument("--encoding", default="utf-8-sig")
    a = ap.parse_args()
    seen = set()
    with open(a.csv, encoding=a.encoding, newline="") as f:
        for row in csv.DictReader(f):
            name = LEGAL.sub("", (row.get(a.name_col) or "")).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            yomi = (row.get(a.yomi_col) or "").strip()
            flag = ""
            if not yomi:
                yomi = to_hira(name)
                flag = "  # 要確認（自動読み）" if yomi else "  # 要確認（読みなし）"
            print(f"{name} {yomi}{flag}".rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
