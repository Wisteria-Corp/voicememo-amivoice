#!/usr/bin/env python3
"""voicememo — 話したメモが、固有名詞を間違えずに、予定とタスクになる。

録音 → AmiVoice API（単語登録つき）→ 曜日・時刻・金額・固有名詞の構造化 → Markdown メモ + .ics

    python voicememo.py record                       # マイクで録音（Enter で停止）→ memo.wav
    python voicememo.py transcribe memo.wav --words words.txt
    python voicememo.py structure memo.txt --base 2026-11-07
    python voicememo.py run --words words.txt         # 録音 → 文字起こし → 構造化 → 出力まで一気に
    python voicememo.py run --wav samples/s1.wav --words samples/words.txt   # 既存 WAV で

必要なもの: Python 3.10+ / 環境変数 AMIVOICE_APPKEY（AmiVoice API マイページ「接続情報 → 共通接続情報」の API キー）
任意: sounddevice（録音）/ OPENAI_API_KEY または OLLAMA_MODEL（生成 AI による構造化の比較用）
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.request
import uuid
import wave
from pathlib import Path
from urllib.parse import quote

AMIVOICE_URL = "https://acp-api.amivoice.com/v1/recognize"
WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]
KANJI_NUM = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


# ----------------------------------------------------------------------------- 単語辞書

def load_words(path: str | None) -> list[tuple[str, str]]:
    """words.txt: 1 行 1 語「表記 読み」（読みはひらがな）。# 行と空行は無視。"""
    if not path:
        return []
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            out.append((parts[0], parts[1]))
        else:
            out.append((parts[0], ""))
    return out


# ----------------------------------------------------------------------------- 録音

def record(out_wav: str, rate: int = 16000) -> str:
    try:
        import sounddevice as sd
    except ImportError:
        sys.exit("録音には `pip install sounddevice` が必要です（または --wav で既存ファイルを渡してください）")
    import array
    print("録音中… Enter で停止")
    chunks: list[bytes] = []

    def cb(indata, frames, t, status):  # noqa: ARG001
        chunks.append(bytes(indata))

    with sd.RawInputStream(samplerate=rate, channels=1, dtype="int16", callback=cb):
        input()
    data = b"".join(chunks)
    with wave.open(out_wav, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(data)
    print(f"保存: {out_wav} ({len(array.array('h', data)) / rate:.1f} 秒)")
    return out_wav


# ----------------------------------------------------------------------------- 文字起こし（AmiVoice）

def transcribe(wav: str, words: list[tuple[str, str]], appkey: str | None = None, engine: str = "-a-general") -> dict:
    """AmiVoice 同期 HTTP API。単語登録は profileWords（表記 読み|表記 読み・URL エンコード必須）。"""
    key = (appkey or os.environ.get("AMIVOICE_APPKEY", "")).strip()
    if not key:
        sys.exit("環境変数 AMIVOICE_APPKEY を設定してください")
    d = f"grammarFileNames={engine}"
    if words:
        pw = "|".join(f"{w} {y}".strip() for w, y in words)
        d += " profileWords=" + quote(pw, safe="")  # ← これを忘れると黙って無視される
    boundary = uuid.uuid4().hex
    body = b""
    for k, v in (("u", key), ("d", d)):
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8")
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"a\"; filename=\"{Path(wav).name}\"\r\n"
             f"Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
    body += Path(wav).read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(AMIVOICE_URL, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.loads(r.read().decode("utf-8"))
    if res.get("code") not in (None, "", "0"):
        raise RuntimeError(f"AmiVoice error: code={res.get('code')} message={res.get('message')}")
    return {"text": res.get("text", ""), "latency_s": round(time.time() - t0, 2), "raw": res}


# ----------------------------------------------------------------------------- 構造化（ルール）

def kanji_to_int(s: str) -> int:
    n = 0; cur = 0
    for ch in s:
        if ch.isdigit():
            cur = cur * 10 + int(ch)
        elif ch in KANJI_NUM:
            cur = KANJI_NUM[ch]
        elif ch == "十":
            n += (cur or 1) * 10; cur = 0
        elif ch == "百":
            n += (cur or 1) * 100; cur = 0
        elif ch == "千":
            n += (cur or 1) * 1000; cur = 0
        elif ch == "万":
            n = (n + cur) * 10000; cur = 0
    return n + cur


NUM = r"[0-9０-９〇零一二三四五六七八九十百千万]+"


def _z2h(s: str) -> str:
    return s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def resolve_weekday(base: dt.date, word: str, modifier: str | None) -> dt.date:
    """『来週火曜』『今週金曜』『火曜』を日付にする。週は月曜始まり。修飾なしは「次に来るその曜日（今日を含む）」。"""
    target = WEEKDAYS.index(word)
    monday = base - dt.timedelta(days=base.weekday())
    if modifier == "今週":
        return monday + dt.timedelta(days=target)
    if modifier == "来週":
        return monday + dt.timedelta(days=7 + target)
    if modifier == "再来週":
        return monday + dt.timedelta(days=14 + target)
    delta = (target - base.weekday()) % 7
    return base + dt.timedelta(days=delta)


def structure(text: str, base: dt.date, words: list[tuple[str, str]] | None = None) -> dict:
    t = _z2h(text)
    out: dict = {"base_date": base.isoformat(), "dates": [], "times": [], "amounts": [], "pages": [], "orgs": [], "todos": [], "events": []}

    # 相対日
    for m in re.finditer(r"(再来週|来週|今週)?(月|火|水|木|金|土|日)曜", t):
        d = resolve_weekday(base, m.group(2), m.group(1))
        out["dates"].append({"text": m.group(0), "date": d.isoformat(), "weekday": WEEKDAYS[d.weekday()], "pos": m.start()})
    for word, days in (("明日", 1), ("明後日", 2), ("明々後日", 3), ("あさって", 2), ("あした", 1)):
        for m in re.finditer(word, t):
            d = base + dt.timedelta(days=days)
            out["dates"].append({"text": word, "date": d.isoformat(), "weekday": WEEKDAYS[d.weekday()], "pos": m.start()})
    for m in re.finditer(r"月末", t):
        last = calendar.monthrange(base.year, base.month)[1]
        d = dt.date(base.year, base.month, last)
        out["dates"].append({"text": "月末", "date": d.isoformat(), "weekday": WEEKDAYS[d.weekday()], "pos": m.start()})
    for m in re.finditer(rf"({NUM})月({NUM})日", t):
        mo, da = kanji_to_int(m.group(1)), kanji_to_int(m.group(2))
        y = base.year + (1 if (mo, da) < (base.month, base.day) else 0)
        try:
            d = dt.date(y, mo, da)
            out["dates"].append({"text": m.group(0), "date": d.isoformat(), "weekday": WEEKDAYS[d.weekday()], "pos": m.start()})
        except ValueError:
            pass
    out["dates"].sort(key=lambda x: x["pos"])

    # 時刻
    for m in re.finditer(rf"(午前|午後|朝|夜)?({NUM})時(?:({NUM})分|半)?", t):
        h = kanji_to_int(m.group(2)); mi = kanji_to_int(m.group(3)) if m.group(3) else (30 if "半" in m.group(0) else 0)
        if m.group(1) in ("午後", "夜") and h < 12:
            h += 12
        if 0 <= h <= 23:
            out["times"].append({"text": m.group(0), "time": f"{h:02d}:{mi:02d}", "pos": m.start()})

    # 金額・ページ
    for m in re.finditer(rf"({NUM})円", t):
        out["amounts"].append({"text": m.group(0), "yen": kanji_to_int(m.group(1))})
    for m in re.finditer(rf"({NUM})ページ", t):
        out["pages"].append({"text": m.group(0), "page": kanji_to_int(m.group(1))})

    # 固有名詞（辞書に載っている表記）
    for w, _ in (words or []):
        if w and w in t:
            out["orgs"].append(w)

    # 文ごとの TODO / 予定
    sentences = [s.strip() for s in re.split(r"[。\n]", t) if s.strip()]
    action = re.compile(r"(送り|送る|送信|再送|提出|確認|共有|作成|連絡|直し|修正|まとめ|届く|打ち合わせ|定例|会議|ミーティング|請求|支払|入金|納品|発注|予約)")
    for s in sentences:
        has_date = [d for d in out["dates"] if d["text"] in s]
        has_time = [x for x in out["times"] if x["text"] in s]
        # 「月曜と水曜」のように並列でつながる日付は全部使う（「日曜に土曜の分を」は先頭だけ）
        group = has_date[:1]
        for d in has_date[1:]:
            if any(f"{group[-1]['text']}{c}{d['text']}" in s for c in ("と", "・", "、", "や")):
                group.append(d)
        if group and has_time:
            for d in group:
                out["events"].append({"summary": s, "date": d["date"], "time": has_time[0]["time"]})
        elif action.search(s):
            for d in (group or [None]):
                out["todos"].append({"text": s, "due": d["date"] if d else None})
    out["title"] = sentences[0][:40] if sentences else ""
    return out


# ----------------------------------------------------------------------------- 構造化（生成 AI・任意）

SCHEMA_HINT = ("次の音声メモを JSON にしてください。キー: title(文字列), events(配列: {summary, date(YYYY-MM-DD), time(HH:MM)}), "
               "todos(配列: {text, due(YYYY-MM-DD か null)}), orgs(配列), amounts(配列: {text, yen}). "
               "日付は基準日 {base} から計算（週は月曜始まり・『来週火曜』は翌週の火曜）。JSON 以外は出力しない。")


def structure_llm(text: str, base: dt.date) -> dict | None:
    prompt = SCHEMA_HINT.replace("{base}", base.isoformat()) + "\n\n" + text
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("OLLAMA_MODEL", "").strip()
    try:
        if key:
            body = json.dumps({"model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"), "temperature": 0,
                               "response_format": {"type": "json_object"},
                               "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
            req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=body,
                                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(json.loads(r.read())["choices"][0]["message"]["content"])
        if model:
            host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
            body = json.dumps({"model": model, "prompt": prompt, "format": "json", "stream": False, "options": {"temperature": 0}}).encode("utf-8")
            req = urllib.request.Request(f"{host}/api/generate", data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(json.loads(r.read())["response"])
    except Exception as e:  # noqa: BLE001
        print(f"[llm] skip: {e!r}", file=sys.stderr)
    return None


# ----------------------------------------------------------------------------- 出力

def to_markdown(text: str, st: dict, latency: float | None) -> str:
    L = [f"# {st.get('title') or 'メモ'}", "", f"- 基準日: {st['base_date']}" + (f"／文字起こし {latency}s" if latency else ""), ""]
    if st["events"]:
        L += ["## 予定"] + [f"- {e['date']} {e['time']} — {e['summary']}" for e in st["events"]] + [""]
    if st["todos"]:
        L += ["## TODO"] + [f"- [ ] {x['text']}" + (f" → 期限 {x['due']}" if x.get("due") else "") for x in st["todos"]] + [""]
    if st["orgs"] or st["amounts"]:
        L += ["## 抽出"]
        if st["orgs"]:
            L.append("- 固有名詞: " + "・".join(dict.fromkeys(st["orgs"])))
        for a in st["amounts"]:
            L.append(f"- 金額: {a['text']}（{a['yen']:,} 円）")
        L.append("")
    L += ["## 文字起こし", "", text, ""]
    return "\n".join(L)


def to_ics(st: dict, tz: str = "Asia/Tokyo") -> str | None:
    if not st["events"]:
        return None
    L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//voicememo//JP"]
    for i, e in enumerate(st["events"]):
        start = dt.datetime.fromisoformat(f"{e['date']}T{e['time']}:00")
        end = start + dt.timedelta(hours=1)
        L += ["BEGIN:VEVENT", f"UID:voicememo-{uuid.uuid4().hex[:12]}-{i}",
              f"DTSTAMP:{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
              f"DTSTART;TZID={tz}:{start.strftime('%Y%m%dT%H%M%S')}", f"DTEND;TZID={tz}:{end.strftime('%Y%m%dT%H%M%S')}",
              f"SUMMARY:{e['summary'][:60]}", "END:VEVENT"]
    L.append("END:VCALENDAR")
    return "\r\n".join(L) + "\r\n"


# ----------------------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser(description="voicememo — AmiVoice で音声メモを予定とタスクに")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("record"); p.add_argument("--out", default="memo.wav")
    p = sub.add_parser("transcribe"); p.add_argument("wav"); p.add_argument("--words"); p.add_argument("--engine", default="-a-general")
    p = sub.add_parser("structure"); p.add_argument("txt"); p.add_argument("--base"); p.add_argument("--words"); p.add_argument("--llm", action="store_true")
    p = sub.add_parser("run"); p.add_argument("--wav"); p.add_argument("--words"); p.add_argument("--base"); p.add_argument("--out-dir", default="memo"); p.add_argument("--llm", action="store_true")
    a = ap.parse_args()

    base = dt.date.fromisoformat(a.base) if getattr(a, "base", None) else dt.date.today()
    words = load_words(getattr(a, "words", None))

    if a.cmd == "record":
        record(a.out); return 0
    if a.cmd == "transcribe":
        r = transcribe(a.wav, words, engine=a.engine); print(r["text"]); print(f"[{r['latency_s']}s]", file=sys.stderr); return 0
    if a.cmd == "structure":
        text = Path(a.txt).read_text(encoding="utf-8")
        st = structure(text, base, words)
        print(json.dumps(st, ensure_ascii=False, indent=1))
        if a.llm:
            print(json.dumps({"llm": structure_llm(text, base)}, ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "run":
        wav = a.wav or record("memo.wav")
        r = transcribe(wav, words)
        st = structure(r["text"], base, words)
        out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
        stem = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S") + "_" + Path(wav).stem
        (out / f"{stem}.md").write_text(to_markdown(r["text"], st, r["latency_s"]), encoding="utf-8")
        (out / f"{stem}.json").write_text(json.dumps({"transcript": r["text"], "structure": st, "llm": structure_llm(r["text"], base) if a.llm else None}, ensure_ascii=False, indent=1), encoding="utf-8")
        ics = to_ics(st)
        if ics:
            (out / f"{stem}.ics").write_text(ics, encoding="utf-8")
        print(to_markdown(r["text"], st, r["latency_s"]))
        print(f"→ {out / (stem + '.md')}" + (f" / {stem}.ics" if ics else ""), file=sys.stderr)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
