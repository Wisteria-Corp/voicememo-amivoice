# voicememo-amivoice

話したメモが、**固有名詞を間違えずに**、予定とタスクになる小さな CLI。

```
録音 → AmiVoice API（単語登録つき）→ 曜日・時刻・金額・固有名詞の構造化 → Markdown メモ + .ics
```

- 文字起こしは [AmiVoice API](https://acp.amivoice.com/)（同期 HTTP・汎用エンジン）。**`profileWords` に「表記 読み」を渡す**ので、取引先名のような固有名詞が音どおりに確定します
- 単語辞書は取引先一覧 CSV から自動生成（`words_from_csv.py`）
- 曜日は「来週火曜」「今週金曜」「月末」を基準日から**計算**します（推測しません）
- 生成 AI は任意（OpenAI か Ollama）。ルール抽出と比較するための口です

## セットアップ

```bash
pip install -r requirements.txt            # 録音する場合のみ sounddevice が要ります
export AMIVOICE_APPKEY=...                 # マイページ「接続情報 → 共通接続情報」の API キー（無料枠: 毎月 60 分）
```

Windows PowerShell: `$env:AMIVOICE_APPKEY = "..."`

## 使い方

```bash
# 1) 取引先 CSV → 単語辞書
python words_from_csv.py customers.csv --name-col 社名 --yomi-col 読み > words.txt

# 2) 録音して一気に処理（Enter で録音停止）
python voicememo.py run --words words.txt

# 2') 既存の WAV（16kHz mono）で
python voicememo.py run --wav samples/s1.wav --words samples/words.txt --base 2026-11-07

# 個別に
python voicememo.py transcribe samples/s2.wav --words samples/words.txt
python voicememo.py structure memo.txt --base 2026-11-07 --words words.txt
```

出力は `memo/` に `YYYY-MM-DD_HHMMSS_<name>.md` / `.json` / 予定があれば `.ics`。

## サンプル

`samples/` に合成音声 6 文（固有名詞はすべて架空）と `words.txt` を同梱しています。`tests/` の期待値で再現できます。

```bash
python -m pytest -q
```

## ハマりどころ

1. `profileWords` の値は **URL エンコード必須**。しないとエラーにならず黙って無視されます
2. ログ保存なし（`loggingOptOut=True`）は無料枠の API キーでは「認証不正」になります
3. API キーは長い方（サービス ID ではない）。環境変数の末尾改行にも注意

## ライセンス

MIT
