"""Choose and retain a contest's freeze interval, measured before its end."""
from __future__ import annotations

import math
import sys

from .models import Contest, VPError


def supported_formats(platform: str) -> tuple[str, ...]:
    return ('ICPC', 'CCPC', 'OI', 'IOI') if platform == 'qoj' else ('ICPC', 'CCPC')


def configure_format(contest: Contest, *, room_format: str | None = None) -> None:
    """Offer the platform's native VP rules; CCPC adds public-statistics rules."""
    formats = supported_formats(contest.platform)
    native = contest.extra.get('native_scoring', contest.extra.get('scoring', 'ICPC'))
    selected = room_format or contest.extra.get("contest_format")
    if selected is None:
        selected = native if native in formats else 'ICPC'
        if sys.stdin.isatty():
            while True:
                try:
                    value = input(f"VP 赛制（{' / '.join(f'{i+1}. {v}' for i, v in enumerate(formats))}；回车默认 {selected}）：").strip().upper()
                except EOFError:
                    raise VPError('未完成赛制选择，本次 VP 已取消。') from None
                choices = {str(i+1): v for i, v in enumerate(formats)}
                if value == '' or value in formats or value in choices:
                    selected = choices.get(value, value or selected)
                    break
                print('请选择：' + ' / '.join(formats))
    if selected not in formats:
        raise VPError('该平台 VP 支持的赛制：' + ' / '.join(formats))
    scoring = 'ICPC' if selected in ('ICPC', 'CCPC') else selected
    if native != scoring:
        raise VPError(f'该场原站 VP 使用 {native} 计分，不能改为 {selected}；未发送开始请求。')
    contest.extra.update(contest_format=selected, scoring=scoring, native_scoring=native)
    contest.rules.problem_reveal_count = 50 if selected == "CCPC" else None


def minutes_before_end(contest: Contest) -> float:
    return (contest.rules.duration - contest.rules.freeze_at) / 60 if contest.rules.freeze_at is not None else 0


def set_minutes(contest: Contest, minutes: int, *, source: str) -> None:
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 0 <= minutes <= contest.rules.duration / 60:
        raise VPError(f"封榜分钟数必须是 0 到 {math.floor(contest.rules.duration / 60)} 之间的整数；0 表示不封榜。")
    contest.rules.freeze_at = contest.rules.duration - minutes * 60 if minutes else None
    # An explicit local/room setting always releases at the contest's end.
    contest.rules.unfreeze_at = contest.rules.duration if minutes else None
    contest.extra.update(freeze_known=True, freeze_source=source, freeze_override_minutes=minutes)


def prompt_minutes(contest: Contest) -> int:
    maximum = math.floor(contest.rules.duration / 60)
    # Piped/noninteractive launches use the documented default. Native room
    # launches supply an explicit value and never depend on terminal input.
    if not sys.stdin.isatty():
        return 0
    while True:
        try:
            value = input("封榜时间（距离比赛结束的分钟数；0 不封榜，回车默认 0）：").strip()
        except EOFError:
            raise VPError('未完成封榜设置，本次命令已取消。') from None
        if not value:
            return 0
        if value.isascii() and value.isdecimal() and 0 <= int(value) <= maximum:
            return int(value)
        print(f"请输入 0 到 {maximum} 之间的整数。")


def configure(contest: Contest, *, room_minutes: int | None = None) -> None:
    """Run before creating a session; unknown ICPC freezes require user input."""
    if room_minutes is not None:
        set_minutes(contest, room_minutes, source="room")
    elif "freeze_override_minutes" in contest.extra:
        set_minutes(contest, contest.extra["freeze_override_minutes"], source=contest.extra.get("freeze_source", "manual"))
    elif contest.extra.get("freeze_known") or contest.rules.freeze_at is not None:
        # A native explicit zero and an actual native timestamp are both known.
        contest.extra.setdefault("freeze_source", "platform")
        contest.extra["freeze_known"] = True
    elif contest.extra.get("scoring", "ICPC") == "ICPC":
        set_minutes(contest, prompt_minutes(contest), source="manual")


def retain(contest: Contest, previous: Contest) -> None:
    """A refreshed official schedule must not erase a local/room selection."""
    if previous.extra.get("contest_format"):
        configure_format(contest, room_format=previous.extra["contest_format"])
    if "freeze_override_minutes" in previous.extra:
        minutes = previous.extra["freeze_override_minutes"]
        if minutes * 60 > contest.rules.duration:
            # A shortened official contest can freeze from its new start.
            contest.rules.freeze_at = 0 if minutes else None
            contest.rules.unfreeze_at = contest.rules.duration if minutes else None
            contest.extra.update(freeze_known=True, freeze_source=previous.extra.get("freeze_source", "manual"),
                                 freeze_override_minutes=minutes)
        else:
            set_minutes(contest, minutes, source=previous.extra.get("freeze_source", "manual"))
    elif previous.extra.get("freeze_known") and not contest.extra.get("freeze_known"):
        interval = minutes_before_end(previous) * 60
        contest.rules.freeze_at = max(0, contest.rules.duration - interval) if interval else None
        contest.rules.unfreeze_at = (contest.rules.duration + previous.rules.unfreeze_at - previous.rules.duration
                                     if previous.rules.unfreeze_at is not None else None)
        contest.extra.update(freeze_known=True, freeze_source=previous.extra.get("freeze_source", "platform"))
