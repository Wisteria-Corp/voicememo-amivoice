#!/usr/bin/env python3
"""robustness.py — 合成音声にノイズ・速度変化を加えて、単語登録あり/なしの AmiVoice を再測する。

肉声を公開できない環境でも「条件が崩れたときに何が壊れるか」を再現可能な形で見るための補助。
    python samples/robustness.py            # 変種 WAV を生成 → AmiVoice（登録なし／あり）→ 表
出力: samples/variants/*.wav と標準出力の Markdown 表。期待語のチェックは samples/expected.json。
"""
from __future__ import annotations

import array
import json
import math
import random
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voicememo as vm  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "variants"
EXPECTED = json.loads((HERE / "expected.json").read_text(encoding="utf-8"))
WORDS = vm.load_words(str(HERE / "words.txt"))


def read(p: Path):
    with wave.open(str(p), "rb") as w:
        return w.getframerate(), array.array("h", w.readframes(w.getnframes()))


def write(p: Path, sr: int, samples):
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(array.array("h", samples).tobytes())


def add_noise(samples, snr_db: float, seed: int = 0):
    rnd = random.Random(seed)
    sig = math.sqrt(sum(s * s for s in samples) / len(samples))
    noise_rms = sig / (10 ** (snr_db / 20))
    out = []
    for s in samples:
        v = s + rnd.gauss(0, noise_rms)
        out.append(max(-32768, min(32767, int(v))))
    return out


def change_speed(samples, factor: float):
    """線形補間の簡易リサンプル（factor 1.15 = 15% 速く）。"""
    n = int(len(samples) / factor)
    out = []
    for i in range(n):
        x = i * factor; j = int(x); t = x - j
        a = samples[j]; b = samples[min(j + 1, len(samples) - 1)]
        out.append(int(a + (b - a) * t))
    return out


def normalize(s: str) -> str:
    t = s.replace(" ", "").replace("　", "").replace("、", "").replace("。", "").replace("・", "").lower()
    return t.translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def check(sid: str, text: str) -> tuple[int, int]:
    exp = EXPECTED[sid]
    hit = sum(1 for w in exp if normalize(w) in normalize(text))
    return hit, len(exp)


def main() -> int:
    OUT.mkdir(exist_ok=True)
    conds = {"clean": lambda sr, s: s, "snr20": lambda sr, s: add_noise(s, 20), "snr10": lambda sr, s: add_noise(s, 10),
             "fast115": lambda sr, s: change_speed(s, 1.15)}
    rows = []
    for cond, fn in conds.items():
        for reg in (False, True):
            hits = tot = 0; lat = []
            for sid in sorted(EXPECTED):
                src = HERE / f"{sid}.wav"
                dst = OUT / f"{sid}_{cond}.wav"
                if not dst.exists():
                    sr, s = read(src); write(dst, sr, fn(sr, s))
                r = vm.transcribe(str(dst), WORDS if reg else [])
                h, t = check(sid, r["text"]); hits += h; tot += t; lat.append(r["latency_s"])
                print(f"[{cond}{'+words' if reg else ''}] {sid} {h}/{t} {r['latency_s']}s | {r['text']}", file=sys.stderr)
            rows.append((cond, reg, hits, tot, sum(lat) / len(lat)))
    print("| 条件 | 単語登録 | 期待語の生存 | 応答/文 |")
    print("|---|---|---|---|")
    for cond, reg, h, t, l in rows:
        print(f"| {cond} | {'あり' if reg else 'なし'} | {h}/{t} | {l:.2f}s |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
