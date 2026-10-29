"""VP start input and Codeforces virtual-registration time fields."""
from __future__ import annotations

from datetime import datetime, timedelta
import math
import os
import re
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import VPError


DEFAULT_START_DELAY = 3 * 60
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def user_zone():
    try:
        return ZoneInfo(os.environ.get("TZ", "Asia/Shanghai"))
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Shanghai")


def parse_start(value: str, now: float | None = None) -> float:
    now = time.time() if now is None else now
    value = value.strip()
    if not value:
        return now + DEFAULT_START_DELAY
    if value.lower() in ("now", "立即"):
        return now
    current = datetime.fromtimestamp(now, user_zone())
    try:
        if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", value):
            parts = [int(p) for p in value.split(":")]
            selected = current.replace(hour=parts[0], minute=parts[1],
                                       second=parts[2] if len(parts) == 3 else 0, microsecond=0)
            if selected.timestamp() <= now:
                selected += timedelta(days=1)
        else:
            selected = datetime.fromisoformat(value)
            if selected.tzinfo is None:
                selected = selected.replace(tzinfo=user_zone())
        if selected.timestamp() <= now:
            raise ValueError
        return selected.timestamp()
    except (ValueError, OverflowError):
        raise VPError("请输入 HH:MM、HH:MM:SS 或 YYYY-MM-DD HH:MM；完整日期必须在将来。") from None


def prompt_start() -> float:
    while True:
        value = input(f"VP 开始时间（{user_zone()}；回车三分钟后开始，或 HH:MM / YYYY-MM-DD HH:MM）：")
        try:
            return parse_start(value)
        except VPError as exc:
            print(str(exc))


def registration_time(form, payload: dict, when: float) -> None:
    """Fill the native datetime widget using its protocol, never OS locale."""
    field = next((n for n in ("startTime", "virtualStartTime")
                  if form.select_one(f'input[name="{n}"]')), None)
    if field is None:
        raise VPError("Codeforces did not offer a virtual start time field.")
    if field == "startTime" and form.select_one('input[name="startDay"]'):
        # Codeforces.setupFormDatetimePicker uses separate date/time inputs and
        # moment.utcOffset(): minutes EAST of UTC, unlike getTimezoneOffset().
        # initialDatetime is a server-side default, not the requested start.
        initial = str(payload.get("initialDatetime", ""))
        if not form.select_one('input[name="clientTimezoneOffset"]'):
            raise VPError("Codeforces did not offer the datetime widget's timezone field.")
        if re.fullmatch(r"[A-Za-z]{3}/\d{2}/\d{4} \d{2}:\d{2}", initial):
            date_format = "en"
        elif re.fullmatch(r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}", initial):
            date_format = "ru"
        else:
            raise VPError("Codeforces changed its start date format; no registration was sent.")
        selected = datetime.fromtimestamp(math.ceil(when / 60) * 60, user_zone())
        payload["startDay"] = (f"{MONTHS[selected.month-1]}/{selected.day:02d}/{selected.year}"
                               if date_format == "en" else selected.strftime("%d.%m.%Y"))
        payload["startTime"] = selected.strftime("%H:%M")
        payload["clientTimezoneOffset"] = str(int(selected.utcoffset().total_seconds() / 60))
        return
    raw = str(payload.get(field, ""))
    if re.fullmatch(r"[0-9]{10,13}", raw):
        payload[field] = str(int(when * (1000 if len(raw) == 13 else 1)))
        return
    offset_field = next((k for k in payload if k.lower() in ("timezoneoffset", "timezone")), None)
    offset = str(payload.get(offset_field, "")) if offset_field else ""
    from datetime import timezone
    if offset and "/" in offset:
        try:
            zone = ZoneInfo(offset)
        except ZoneInfoNotFoundError:
            raise VPError("Codeforces returned an unknown registration timezone.") from None
    elif re.fullmatch(r"[+-]\d{2}:?\d{2}", offset):
        sign = -1 if offset.startswith("-") else 1
        digits = offset[1:].replace(":", "")
        zone = timezone(timedelta(minutes=sign*(int(digits[:2])*60+int(digits[2:]))))
    elif offset_field and offset_field.lower() == "timezone" and re.fullmatch(r"[+-]?\d+(?:\.\d+)?", offset) and abs(float(offset)) <= 14:
        zone = timezone(timedelta(hours=float(offset)))
    elif re.fullmatch(r"-?\d+", offset):
        # JavaScript Date.getTimezoneOffset has the opposite sign.
        zone = timezone(timedelta(minutes=-int(offset)))
    else:
        raise VPError("Codeforces did not expose its registration timezone; no start time was guessed.")
    if raw.count(":") == 1:
        # A minute-only widget must not round "now" down into the past.
        when = math.ceil(when/60)*60
    selected = datetime.fromtimestamp(when, zone)
    if re.fullmatch(r"[A-Za-z]{3}/\d{2}/\d{4} \d{2}:\d{2}(?::\d{2})?", raw):
        value = f"{MONTHS[selected.month-1]}/{selected.day:02d}/{selected.year} {selected:%H:%M}"
        payload[field] = value + (f":{selected.second:02d}" if raw.count(":") == 2 else "")
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?", raw):
        payload[field] = selected.strftime("%Y-%m-%d" + ("T" if "T" in raw else " ") + "%H:%M" + (":%S" if raw.count(":") == 2 else ""))
    elif re.fullmatch(r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}(?::\d{2})?", raw):
        payload[field] = selected.strftime("%d.%m.%Y %H:%M" + (":%S" if raw.count(":") == 2 else ""))
    else:
        raise VPError("Codeforces changed its start time format; no registration was sent.")
