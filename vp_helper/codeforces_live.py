"""Regular Codeforces contests: official standings, never a virtual start."""
from __future__ import annotations

import re
import math
import time
from urllib.parse import urljoin, urlsplit

from .portable import login_browser
from .http import WebSession
from .platform_clock import platform_now
from .models import Contest, Rules, VPError
from .submission_languages import language_fields
from .accounts import refresh_api_credentials
from .parsing import contest_url
from .storage import file_lock, read_json, state_directory, write_json
from .scoring import codeforces_verdict
from .cf_judgements import PendingJudgements

PHASES = {"BEFORE", "CODING", "PENDING_SYSTEM_TEST", "SYSTEM_TEST", "FINISHED"}
LIVE_TYPES = {"CONTESTANT", "OUT_OF_COMPETITION"}


def freeze_metadata(raw: dict, duration: int) -> tuple[bool, int | None]:
    # `frozen` is a current state, not the freeze duration. In particular a
    # historic final board saying frozen=false does not disable a VP freeze.
    if "freezeDurationSeconds" not in raw:
        return False, None
    value = raw["freezeDurationSeconds"]
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= duration:
        raise VPError("Codeforces returned an invalid scoreboard freeze duration.")
    return True, duration - int(value) if value else None


def public_api(web: WebSession, method: str, **params):
    # Share the limit between the monitor and submit processes. Do not retry
    # inside WebSession: every network attempt must pass through this gate.
    root = state_directory()
    with file_lock(root / "cf-api.lock"):
        last = read_json(root / "cf-api.json").get("at", 0)
        time.sleep(max(0.0, min(2.1, 2.1 - (time.time() - last))))
        try:
            options = {'headers': {'Cache-Control': 'no-cache'}} if method == 'contest.status' else {}
            response = web.get("https://codeforces.com/api/" + method, params=params, retry=False, **options)
        finally:
            write_json(root / "cf-api.json", {"at": time.time()})
    try:
        data = response.json()
    except ValueError:
        raise VPError("Codeforces API did not return JSON.") from None
    if not isinstance(data, dict) or data.get("status") != "OK":
        raise VPError("Codeforces API: " + str(data.get("comment", "request failed") if isinstance(data, dict) else "invalid response"))
    return data["result"]


def verify_login(soup, handle: str) -> None:
    if not any(urlsplit(a.get("href", "")).path.rstrip("/").lower() == "/profile/" + handle.lower()
               for a in soup.select("#header a[href]")):
        raise VPError(f"Codeforces login expired or belongs to another account. Log in to the configured account in {login_browser()}.")


def verify_practice(api, duration: int) -> None:
    from .sessions import attempts, boundary, locally_ended
    for path, state in attempts():
        cutoff = boundary(path)
        if (state.get("mode", "vp") == "vp" and state["url"] == api.url
                and state["handle"].casefold() == api.handle.casefold()
                and state.get("status") not in ("complete", "archived", "failed", "cancelled")
                and (cutoff is None or platform_now(state) < cutoff)
                and platform_now(state) < state["start_time"] + state["contest"]["rules"]["duration"]):
            raise VPError("这场比赛的本地 VP 尚未结束。请先运行 xcpc end 或等待结束。")
    if api.platform == "cf":
        # Regular contest standings no longer accept handles/showUnofficial.
        # Own submission parties still expose a verified native virtual start.
        records = api.api("contest.status", contestId=api.id, handle=api.handle, **{"from": 1, "count": 100})
        parties = [record.get("author", {}) for record in records]
    else:
        data = api.api("contest.standings", contestId=api.id, handles=api.handle, showUnofficial=True)
        parties = [row.get("party", {}) for row in data.get("rows", [])]
    for party in parties:
        if (party.get("participantType") == "VIRTUAL"
                and any(m.get("handle", "").casefold() == api.handle.casefold() for m in party.get("members", []))
                and party.get("startTimeSeconds") is not None
                and platform_now(api.url) < party["startTimeSeconds"] + duration):
            if locally_ended(api.url, api.handle, party["startTimeSeconds"]):
                continue
            raise VPError("Codeforces still has an active or scheduled virtual participation. Use bt after it ends so practice cannot change that VP.")


def own_row(rows: list[dict], handle: str) -> dict | None:
    matches = [row for row in rows if row["party"].get("participantType") == "CONTESTANT"
               and not row["party"].get("ghost")
               and any(m["handle"].lower() == handle.lower() for m in row["party"]["members"])]
    if len(matches) > 1:
        raise VPError("Codeforces returned more than one official participation for this handle.")
    return matches[0] if matches else None


def apply_metadata(contest: Contest, raw: dict, problems: list[dict] | None = None) -> None:
    if (str(raw.get("id")) != contest.contest_id or raw.get("type") not in ("CF", "ICPC", "IOI")
            or raw.get("phase") not in PHASES or not raw.get("startTimeSeconds")
            or raw.get("durationSeconds", 0) <= 0):
        raise VPError("Codeforces returned invalid contest timing or scoring information.")
    contest.name = raw["name"]
    contest.original_start = float(raw["startTimeSeconds"])
    duration = int(raw["durationSeconds"])
    known, freeze = freeze_metadata(raw, duration)
    contest.rules = Rules(duration, freeze_at=freeze)
    contest.extra.update(mode="live", scoring=raw["type"], phase=raw["phase"], frozen=bool(raw.get("frozen")))
    contest.extra["score_policy"] = "native" if raw["type"] == "CF" else "best"
    if contest.extra.get("freeze_source") not in ("manual", "room"):
        contest.extra["freeze_known"] = known
        if known:
            contest.extra["freeze_source"] = "platform"
        else:
            contest.extra.pop("freeze_source", None)
    if problems is not None:
        contest.problems = {p["index"]: p["index"] for p in problems}
        contest.extra["problem_info"] = problems
        contest.extra["problem_max_scores"] = {
            p["index"]: p["points"] for p in problems
            if type(p.get("points")) in (int, float) and math.isfinite(p["points"]) and p["points"] > 0
        }


class CodeforcesLive:
    def __init__(self, url: str, account: dict, *, allow_gym: bool = False):
        platform, self.id, self.url = contest_url(url)
        if platform != "cf" and not (allow_gym and platform == "gym"):
            raise VPError("Live mode currently accepts regular Codeforces /contest/ links only.")
        self.platform = platform
        self.account = dict(account)
        # Workspace files contain cookies, never API secrets. Resolve the pair
        # from this same user's shared configuration, including older folders.
        refresh_api_credentials(self.account, platform)
        self.handle = account.get("handle", "")
        self.pending_judgements = PendingJudgements()
        self.web = WebSession("https://codeforces.com", self.account)
        # The public standings endpoint must receive exactly contestId and no
        # account cookies, API authentication, locale, or filter parameters.
        self.public = WebSession("https://codeforces.com", {}, auto_refresh=False)

    def api(self, method: str, **params):
        if self.platform == "cf" and method == "contest.standings":
            return public_api(self.public, method, contestId=params.get("contestId", self.id))
        refresh_api_credentials(self.account, self.platform)
        if self.account.get("api_key") and self.account.get("api_secret"):
            from .gym import Gym
            return Gym(self.url, self.account).api(method, **params)
        return public_api(self.public, method, **params)

    def prepare(self) -> tuple[Contest, dict]:
        page = self.web.soup(self.url, params={"locale": "en"})
        verify_login(page, self.handle)
        if self.platform == "gym":
            data = self.api("contest.standings", contestId=self.id, showUnofficial=True)
            raw = data.get("contest")
        else:
            data = self.api("contest.list", gym="false")
            raw = next((c for c in data if str(c["id"]) == self.id), None)
        if raw is None:
            raise VPError("This contest is not available in the public Codeforces contest list.")
        contest = Contest(self.platform, self.id, self.url, raw["name"], f"{self.platform.upper()} {self.id}", {}, Rules(1))
        apply_metadata(contest, raw)
        snapshot = self.snapshot(contest, allow_unavailable=True) if raw["phase"] != "BEFORE" else {}
        if not snapshot.get("own"):
            self.verify_registration(page, contest)
        if not contest.problems:
            # A temporarily unavailable public board must not prevent an
            # authenticated, registered contestant from joining mid-contest.
            # Read only this contest's published problem links; later board
            # polls will populate full native problem/scoring metadata.
            prefix = urlsplit(self.url).path + "/problem/"
            for link in page.select("table.problems a[href]"):
                target = urlsplit(urljoin(self.url + "/", link["href"]))
                if (target.scheme != "https" or target.netloc.removeprefix("www.") != "codeforces.com"
                        or not target.path.startswith(prefix)):
                    continue
                label = target.path[len(prefix):]
                if re.fullmatch(r"[A-Z][A-Z0-9]*", label):
                    contest.problems[label] = label
        return contest, snapshot

    def contest_name(self) -> str:
        if self.platform == 'gym':
            return self.api('contest.standings', contestId=self.id)['contest']['name']
        data = self.api('contest.list', gym='false')
        raw = next((c for c in data if str(c['id']) == self.id), None)
        if raw is None:
            raise VPError('Codeforces 未返回这场比赛的名称，原目录保留。')
        return raw['name']

    def verify_registration(self, page, contest: Contest) -> None:
        # A missing scoreboard row is not evidence of missing registration:
        # registered users may not have submitted, or may be out of competition.
        text = " ".join(node.get_text(" ", strip=True) for node in
                        page.select(".contest-state-phase, .contest-registration, #pageContent > .notice"))
        if re.search(r"\byou (?:are|have been|have already been) registered\b", text, re.I):
            return
        try:
            registrants = self.web.soup(f"https://codeforces.com/contestRegistrants/{self.id}/friends/true",
                                        params={"locale": "en"})
            verify_login(registrants, self.handle)
            for row in registrants.select("table.registrants tr"):
                if any(urlsplit(a["href"]).path.lower() == "/profile/" + self.handle.lower()
                       for a in row.select("td a[href]")):
                    return
        except VPError:
            pass  # Some rounds do not expose a registrants/friends page.
        if contest.extra["phase"] == "CODING":
            submit = self.web.soup(self.url + "/submit", params={"locale": "en"})
            verify_login(submit, self.handle)
            form = submit.select_one("form.submit-form")
            if form and form.select_one('[name="submittedProblemIndex"]') and form.select_one('[name="csrf_token"]'):
                return
        raise VPError("Cannot verify an existing registration for this contest. Register on Codeforces first, then retry; no registration was changed.")

    def snapshot(self, contest: Contest, *, allow_unavailable: bool = False) -> dict:
        try:
            data = self.api("contest.standings", contestId=self.id)
        except VPError as exc:
            if not allow_unavailable:
                raise
            return {"own": None, "stats": {}, "phase": contest.extra["phase"],
                    "frozen": contest.extra["frozen"], "at": platform_now(self.url),
                    "available": False, "message": "Scoreboard unavailable; retrying: " + str(exc)}
        apply_metadata(contest, data["contest"], data["problems"])
        row = own_row(data["rows"], self.handle)
        if row is not None:
            row = {**row, "problemResults": [dict(result) for result in row.get("problemResults", [])]}
            for label, result in zip(contest.problems, row["problemResults"]):
                points = result.get("points", 0)
                maximum = contest.extra["problem_max_scores"].get(label)
                if contest.extra["scoring"] != "IOI":
                    result["accepted"] = points > 0
                elif maximum is not None:
                    result["accepted"] = points >= maximum
                result["attempted"] = bool(points or result.get("rejectedAttemptCount"))
        stats = {"scored": dict.fromkeys(contest.problems, 0), "attempted": dict.fromkeys(contest.problems, 0)}
        for public in data["rows"]:
            if public["party"].get("participantType") != "CONTESTANT":
                continue
            for problem, result in zip(contest.problems, public["problemResults"]):
                scored = result.get("points", 0) > 0
                stats["scored"][problem] += int(scored)
                stats["attempted"][problem] += int(scored or result.get("rejectedAttemptCount", 0) > 0)
        return {"own": row, "stats": stats, "phase": contest.extra["phase"],
                "frozen": contest.extra["frozen"], "at": platform_now(self.url)}

    def seed_submissions(self, records) -> None:
        self.pending_judgements.seed(records)

    def submissions(self, contest: Contest) -> list[dict]:
        result = {}
        self.submissions_complete = False
        self.submission_message = ''
        offset = 1
        while True:
            try:
                data = self.api("contest.status", contestId=self.id, handle=self.handle,
                                **{"from": offset, "count": 1000})
            except VPError as exc:
                if offset == 1:
                    raise
                self.submission_message = 'Recent submissions synced; older history will be retried: ' + str(exc)
                break
            for raw in data:
                author = raw["author"]
                kind = author.get("participantType")
                if (str(raw.get("contestId")) != self.id or author.get("ghost")
                        or not any(m["handle"].lower() == self.handle.lower() for m in author["members"])
                        or kind not in LIVE_TYPES | {"PRACTICE"} | ({"VIRTUAL"} if contest.extra.get("practice") else set())):
                    continue
                relative = float(raw["creationTimeSeconds"]) - contest.original_start
                if ((relative < 0 and not contest.extra.get("practice"))
                        or (kind == "PRACTICE" and relative < contest.rules.duration and not contest.extra.get("practice"))):
                    continue
                value = codeforces_verdict(raw.get('verdict'))
                result[str(raw["id"])] = {
                    "id": str(raw["id"]), "team": "me", "problem": raw["problem"]["index"],
                    "time": relative, "verdict": value, "testset": raw.get("testset", ""),
                    "points": raw.get("points"), "practice": kind == "PRACTICE", **language_fields(raw),
                }
            if len(data) < 1000:
                self.submissions_complete = True
                break
            offset += 1000
        return self.pending_judgements.update(self, list(result.values()))

    def save_statements(self, contest: Contest, directory) -> None:
        # HTML is the native statement format for ordinary rounds. Skip files
        # already present so reconnecting never overwrites local edits.
        if not contest.problems or all((directory / f"{label}.html").exists() for label in contest.problems):
            return
        soup = self.web.soup(self.url + "/problems", params={"locale": "en"})
        statements = soup.select(".problem-statement")
        if len(statements) != len(contest.problems):
            raise VPError("The complete statements are not available yet; use the original contest page.")
        for label, statement in zip(contest.problems, statements):
            target = directory / f"{label}.html"
            if target.exists():
                continue
            from html import escape
            target.write_text('<!doctype html><meta charset="utf-8"><base href="https://codeforces.com/">'
                              f'<title>{escape(contest.name)} — {escape(label)}</title>'
                              '<style>body{max-width:960px;margin:2em auto;font:18px/1.6 sans-serif}'
                              'pre{white-space:pre-wrap}.title{font-size:1.5em;font-weight:bold}</style>'
                              + str(statement), encoding="utf8")


def submission_records(events: list[dict], phase: str) -> list[dict]:
    records = []
    for event in sorted(events, key=lambda s: (s["time"], str(s["id"]).zfill(24)), reverse=True):
        item = dict(event)
        if item["verdict"] == "CORRECT" and item.get("testset") == "PRETESTS" and phase != "FINISHED":
            item["verdict"] = "PRETESTS-PASSED"
        if item["verdict"] == "PARTIAL" and item.get("points") is not None:
            item["verdict"] = f"PARTIAL ({item['points']:g})"
        records.append(item)
    return records
