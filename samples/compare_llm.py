#!/usr/bin/env python3
"""compare_llm.py — 構造化を「ルール抽出」と「生成 AI」で比べる。

同じ 6 文（samples/expected.json と同じ文面）・同じ基準日で、
日付・時刻・金額・取引先がどれだけ正しく取れたか、誤った日付をいくつ出したか、1 文あたりの所要時間を測る。

    python samples/compare_llm.py                         # ルールのみ
    OLLAMA_HOST=http://<host>:11434 python samples/compare_llm.py --ollama gpt-oss:120b qwen3:8b
    OPENAI_API_KEY=... python samples/compare_llm.py --openai gpt-4o-mini

出力: 標準出力に Markdown 表、samples/compare_llm.json に生の結果。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voicememo as vm  # noqa: E402

HERE = Path(__file__).resolve().parent
BASE = dt.date(2026, 11, 7)  # 土曜
WORDS = vm.load_words(str(HERE / "words.txt"))

TEXTS = {
    "s1": "来週火曜の十四時から、青嵐精機さんと打ち合わせです。提案書は十六ページで確定しました。",
    "s2": "金曜の十七時までに、ノルディックベーカリーへ見積もりを送ります。金額は二百三十万円です。",
    "s3": "月曜と水曜はKTSラボの定例です。木曜は休みにしてください。",
    "s4": "日曜の午前九時に土曜の分のログをまとめて、青嵐精機の担当者へ共有します。",
    "s5": "ノルディックベーカリーの件は、三ページ目の表を直してから、火曜に再送します。",
    "s6": "KTSラボから金曜に請求書が届く予定です。金額は四十五万円、支払いは月末です。",
}

# 正解（基準日 2026-11-07 土・週は月曜始まり・修飾なしの曜日は「次に来るその曜日」）
# optional は出しても誤りにしない日付（解釈が割れるもの）
GOLD = {
    "s1": {"dates": ["2026-11-10"], "times": ["14:00"], "yen": [], "orgs": ["青嵐精機"]},
    "s2": {"dates": ["2026-11-13"], "times": ["17:00"], "yen": [2300000], "orgs": ["ノルディックベーカリー"]},
    "s3": {"dates": ["2026-11-09", "2026-11-11"], "times": [], "yen": [], "orgs": ["KTSラボ"], "optional": ["2026-11-12"]},
    "s4": {"dates": ["2026-11-08"], "times": ["09:00"], "yen": [], "orgs": ["青嵐精機"], "optional": ["2026-11-07"]},
    "s5": {"dates": ["2026-11-10"], "times": [], "yen": [], "orgs": ["ノルディックベーカリー"]},
    "s6": {"dates": ["2026-11-13", "2026-11-30"], "times": [], "yen": [450000], "orgs": ["KTSラボ"]},
}

DATE_RE = re.compile(r"20\d\d-\d\d-\d\d")
TIME_RE = re.compile(r"\b([01]\d|2[0-3]):[0-5]\d\b")


def _yen(v) -> int | None:
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        s = vm._z2h(v).replace(",", "").replace("円", "")
        m = re.fullmatch(r"(\d+)(万)?", s.strip())
        if m:
            return int(m.group(1)) * (10000 if m.group(2) else 1)
    return None


def score(sid: str, st: dict | None) -> dict:
    g = GOLD[sid]
    total = len(g["dates"]) + len(g["times"]) + len(g["yen"]) + len(g["orgs"])
    if not isinstance(st, dict):
        return {"hit": 0, "total": total, "wrong_dates": 0, "json_ok": False}
    plan = json.dumps({"events": st.get("events", []), "todos": st.get("todos", [])}, ensure_ascii=False)
    dates = set(DATE_RE.findall(plan))
    times = {m.group(0) for m in TIME_RE.finditer(plan)}
    yens = {y for a in (st.get("amounts") or []) for y in [_yen(a.get("yen") if isinstance(a, dict) else a)] if y}
    orgs = " ".join(str(o) for o in (st.get("orgs") or []))
    hit = (sum(d in dates for d in g["dates"]) + sum(t in times for t in g["times"])
           + sum(y in yens for y in g["yen"]) + sum(o in orgs for o in g["orgs"]))
    allowed = set(g["dates"]) | set(g.get("optional", []))
    return {"hit": hit, "total": total, "wrong_dates": len(dates - allowed), "json_ok": True,
            "dates": sorted(dates), "times": sorted(times), "yen": sorted(yens)}


def run(label: str, fn) -> dict:
    rows = {}
    for sid, text in TEXTS.items():
        t0 = time.perf_counter()
        st = fn(text)
        sec = time.perf_counter() - t0
        rows[sid] = {**score(sid, st), "sec": round(sec, 2), "raw": st}
        print(f"  {label} {sid}: {rows[sid]['hit']}/{rows[sid]['total']} wrong={rows[sid]['wrong_dates']} {sec:.1f}s", file=sys.stderr)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ollama", nargs="*", default=[])
    ap.add_argument("--openai", nargs="*", default=[])
    a = ap.parse_args()

    results = {"base_date": BASE.isoformat(), "runs": {}}
    results["runs"]["rules"] = run("rules", lambda t: vm.structure(t, BASE, WORDS))
    for m in a.ollama:
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ["OLLAMA_MODEL"] = m
        results["runs"][f"ollama:{m}"] = run(m, lambda t: vm.structure_llm(t, BASE))
    key = os.environ.get("OPENAI_API_KEY", "")
    for m in a.openai:
        if not key:
            print("OPENAI_API_KEY がないので skip", file=sys.stderr)
            break
        os.environ["OPENAI_API_KEY"] = key
        os.environ["OPENAI_MODEL"] = m
        results["runs"][f"openai:{m}"] = run(m, lambda t: vm.structure_llm(t, BASE))

    (HERE / "compare_llm.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("| 方式 | 正解項目 | 誤った日付 | JSON 失敗 | 1 文あたり |")
    print("|---|---|---|---|---|")
    for name, rows in results["runs"].items():
        hit = sum(r["hit"] for r in rows.values()); tot = sum(r["total"] for r in rows.values())
        wrong = sum(r["wrong_dates"] for r in rows.values()); bad = sum(not r["json_ok"] for r in rows.values())
        sec = sum(r["sec"] for r in rows.values()) / len(rows)
        print(f"| {name} | {hit}/{tot} | {wrong} | {bad} | {sec:.2f}s |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
