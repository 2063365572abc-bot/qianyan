from datetime import datetime, timezone
import pytest
from app.dates import deadline_hint, candidate_text


ANCHOR = datetime(2026, 10, 1, 16, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("text,zone,date,clock", [
    ("明天必须完成", "Asia/Shanghai", "2026-10-03", "23:59:59"),
    ("明天18:00前完成", "Asia/Shanghai", "2026-10-03", "18:00:00"),
    ("后天晚上8点半", "Asia/Shanghai", "2026-10-04", "20:30:00"),
    ("月底完成", "Asia/Shanghai", "2026-10-31", "23:59:59"),
    ("下个月底完成", "Asia/Shanghai", "2026-11-30", "23:59:59"),
    ("tomorrow at 10:30", "America/New_York", "2026-10-02", "10:30:00"),
    ("end of month", "UTC", "2026-10-31", "23:59:59"),
    ("2026-10-30 15:00", "Asia/Shanghai", "2026-10-30", "15:00:00"),
    ("10月30日下午3点", "Asia/Shanghai", "2026-10-30", "15:00:00"),
])
def test_candidate_uses_message_instant_and_personal_timezone(text, zone, date, clock):
    hint = deadline_hint(text, zone, ANCHOR)
    assert hint["status"] == "candidate" and hint["requires_confirmation"]
    assert hint["date"] == date and date + "T" + clock in hint["local_datetime"]
    assert zone in candidate_text(hint) and date in candidate_text(hint)
    if not hint["time_explicit"]:
        assert "No exact time" in candidate_text(hint)


@pytest.mark.parametrize("text", ["明天或月底完成", "下周五完成", "过几天再说", "2026-02-30完成",
    "9月30日完成", "明天下午25点", "明天上午八点", "明天8pm", "明天18:00:30", "明天晚上12点", "明天或下周完成"])
def test_ambiguous_invalid_or_unsupported_dates_are_not_guessed(text):
    hint = deadline_hint(text, "Asia/Shanghai", ANCHOR)
    assert hint["status"] == "needs_clarification" and "deadline" not in hint


def test_next_month_wraps_year_and_leap_month_is_real():
    anchor = datetime(2027, 12, 20, tzinfo=timezone.utc)
    assert deadline_hint("下月底", "UTC", anchor)["date"] == "2028-01-31"
    assert deadline_hint("2028年2月29日", "UTC", anchor)["date"] == "2028-02-29"


@pytest.mark.parametrize("text", ["2026-03-08 02:30", "2026-11-01 01:30"])
def test_dst_gap_or_fold_requires_a_user_choice(text):
    hint = deadline_hint(text, "America/New_York", ANCHOR)
    assert hint["status"] == "needs_clarification" and "deadline" not in hint
