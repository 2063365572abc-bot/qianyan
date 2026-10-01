"""Deterministic deadline candidates anchored to the user's message and timezone.

Candidates apply only to a new, unconfirmed goal. Ambiguous dates remain unset.
The user reviews the displayed absolute date before activating the plan.
"""
import calendar
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def deadline_hint(text, zone_name, anchor):
    zone = ZoneInfo(zone_name)
    local = anchor.astimezone(zone)
    dates = set()
    invalid = False
    pattern = r"(?<!\d)(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)|(?:(\d{4})年)?(\d{1,2})月(\d{1,2})(?:日|号)?"
    for match in re.finditer(pattern, text):
        year, month, day = match.groups()[:3] if match.group(1) else (match.group(4) or local.year, match.group(5), match.group(6))
        try:
            value = datetime(int(year), int(month), int(day)).date()
            if not match.group(1) and not match.group(4) and value < local.date():
                invalid = True  # Ask which year; never silently roll into next year.
            dates.add(value)
        except ValueError:
            invalid = True
    relative = r"大后天|后天|明天|今天|下个月(?:月)?底|下月底|本月底|这个月底|月底|\bday after tomorrow\b|\btomorrow\b|\btoday\b|\bend of(?: this| next)? month\b"
    for match in re.finditer(relative, text, re.I):
        token = match.group().lower()
        if token in ("大后天", "后天", "明天", "今天", "day after tomorrow", "tomorrow", "today"):
            offset = {"大后天": 3, "后天": 2, "明天": 1, "今天": 0, "day after tomorrow": 2, "tomorrow": 1, "today": 0}[token]
            dates.add(local.date() + timedelta(days=offset))
        else:
            month = local.month + int(token in ("下个月月底", "下个月底", "下月底", "end of next month"))
            year = local.year + (month == 13)
            month = 1 if month == 13 else month
            dates.add(datetime(year, month, calendar.monthrange(year, month)[1]).date())
    # A different relative reference is not ignored in favour of a convenient date.
    if re.search(r"下周|这周|本周|下个月(?!月底|底)|下个星期|下月(?!底)|过几天|周[一二三四五六日天]|星期[一二三四五六日天]|\bnext week\b|\bthis week\b", text, re.I):
        invalid = True
    if not dates:
        return {"status": "needs_clarification" if invalid else "not_found", "timezone": zone_name}
    if invalid or len(dates) != 1:
        return {"status": "needs_clarification", "dates": sorted(d.isoformat() for d in dates), "timezone": zone_name}
    clocks = set()
    if re.search(r"[零一二三四五六七八九十两]+点|(?<!\d)\d{1,2}(?::\d{2})?\s*(?:am|pm)(?![A-Za-z])|\d{1,2}:\d{2}:\d{2}", text, re.I):
        invalid = True
    for match in re.finditer(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)|(?:(上午|早上|中午|下午|晚上))?(\d{1,2})[点时](?:(\d{1,2})分?|(半))?", text):
        if match.group(1):
            hour, minute = int(match.group(1)), int(match.group(2))
        else:
            hour, minute = int(match.group(4)), 30 if match.group(6) else int(match.group(5) or 0)
            period = match.group(3)
            if hour == 12 and period in ("上午", "早上", "晚上"):
                invalid = True
            if period in ("下午", "晚上", "中午") and hour < 12:
                hour += 12
            if period in ("上午", "早上") and hour == 12:
                hour = 0
        if not (0 <= hour < 24 and 0 <= minute < 60):
            invalid = True
        clocks.add((hour, minute))
    if invalid or len(clocks) > 1:
        return {"status": "needs_clarification", "dates": sorted(d.isoformat() for d in dates), "timezone": zone_name}
    hour, minute = next(iter(clocks)) if clocks else (23, 59)
    date = next(iter(dates))
    value = datetime(date.year, date.month, date.day, hour, minute, 0 if clocks else 59, tzinfo=zone)
    # DST gaps/folds need a human choice rather than a silently invalid instant.
    if value.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != value.replace(tzinfo=None) or value.utcoffset() != value.replace(fold=1).utcoffset():
        return {"status": "needs_clarification", "dates": [date.isoformat()], "timezone": zone_name}
    return {"status": "candidate", "date": date.isoformat(), "timezone": zone_name,
            "local_datetime": value.isoformat(), "deadline": value.astimezone(timezone.utc).isoformat(),
            "time_explicit": bool(clocks), "requires_confirmation": True}


def candidate_text(hint):
    if hint.get("status") == "candidate":
        assumption = "你未指定具体时刻，草稿暂按当天23:59:59；可修改。 / No exact time was given; the draft proposes end of day and can be edited." if not hint["time_explicit"] else ""
        return f"截止时间候选 / Proposed deadline: {hint['local_datetime']} ({hint['timezone']})。{assumption} 请在激活计划前确认日期。 / Confirm the date before activating the plan."
    if hint.get("status") == "needs_clarification":
        return "截止日期仍需确认，请给出一个明确日期和时刻。 / Please provide one exact deadline date and time."
    return ""
