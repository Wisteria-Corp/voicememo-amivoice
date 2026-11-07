import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voicememo as vm  # noqa: E402

BASE = dt.date(2026, 11, 7)  # 土曜
WORDS = [("青嵐精機", "せいらんせいき"), ("ノルディックベーカリー", "のるでぃっくべーかりー"), ("KTSラボ", "けーてぃーえすらぼ")]


def test_weekday_next_week():
    # 2026-11-07 は土曜。来週火曜 = 11-10（週は月曜始まり: 今週の月曜 11-02 → 来週の火曜 11-10）
    assert vm.resolve_weekday(BASE, "火", "来週") == dt.date(2026, 11, 10)
    assert vm.resolve_weekday(BASE, "金", "今週") == dt.date(2026, 11, 6)
    assert vm.resolve_weekday(BASE, "金", None) == dt.date(2026, 11, 13)   # 次の金曜
    assert vm.resolve_weekday(BASE, "土", None) == BASE                      # 今日を含む


def test_kanji_numbers():
    assert vm.kanji_to_int("十四") == 14
    assert vm.kanji_to_int("二百三十万") == 2300000
    assert vm.kanji_to_int("45万") == 450000
    assert vm.kanji_to_int("16") == 16


def test_structure_event_and_amount():
    st = vm.structure("来週火曜の14時から、青嵐精機さんと打ち合わせです。提案書は16ページで確定しました。", BASE, WORDS)
    assert st["events"] and st["events"][0]["date"] == "2026-11-10" and st["events"][0]["time"] == "14:00"
    assert "青嵐精機" in st["orgs"]
    assert st["pages"][0]["page"] == 16


def test_structure_todo_with_due():
    st = vm.structure("金曜の17時までにノルディックベーカリーへ見積もりを送ります。金額は230万円です。", BASE, WORDS)
    # 「金曜の17時までに…送ります」は日時つき → 予定として拾う（締切の意味）
    assert st["events"][0]["date"] == "2026-11-13"
    assert st["amounts"][0]["yen"] == 2300000


def test_month_end():
    st = vm.structure("KTSラボから金曜に請求書が届く予定です。支払いは月末です。", BASE, WORDS)
    assert any(d["text"] == "月末" and d["date"] == "2026-11-30" for d in st["dates"])
    assert st["todos"]
