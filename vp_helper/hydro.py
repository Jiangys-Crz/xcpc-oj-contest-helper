"""CWNUOJ's Hydro JSON views, using only the signed-in user's permissions."""
from __future__ import annotations

import ast
from html import escape
import json
import math
import re
import time
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from .http import WebSession
from .platform_clock import platform_now
from .models import Contest, Rules, VPError
from .parsing import CWNUOJ_HOST, contest_url, timestamp
from .receipts import valid_submission_id

ORIGIN = "https://" + CWNUOJ_HOST
SCORING = {"acm": "ICPC", "oi": "OI", "ioi": "IOI", "codeforces": "CF"}
VERDICTS = {0: "PENDING", 1: "CORRECT", 2: "WRONG-ANSWER", 3: "TIMELIMIT", 4: "MEMORY-LIMIT",
            5: "OUTPUT-LIMIT", 6: "RUN-ERROR", 7: "COMPILER-ERROR", 8: "SYSTEM-ERROR", 9: "CANCELLED",
            10: "UNAVAILABLE", 11: "HACKED", 20: "PENDING", 21: "PENDING", 22: "PENDING",
            30: "IGNORED", 31: "FORMAT-ERROR", 32: "HACK-SUCCESSFUL", 33: "HACK-UNSUCCESSFUL"}


def identity_from_html(html: str) -> dict:
    # Hydro serializes JSON into a quoted JS string. Parse literals only; never
    # execute the page or treat a link to another user's profile as the login.
    match = re.search(r'''\bwindow\.UserContext\s*=\s*("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')''', html)
    try:
        value = json.loads(ast.literal_eval(match[1])) if match else {}
    except (ValueError, SyntaxError, TypeError):
        value = {}
    if not isinstance(value, dict) or type(value.get("_id")) is not int or value["_id"] <= 0 or not value.get("uname"):
        raise VPError("CWNUOJ login expired. Log in at oj.cwnupaa.com in Firefox, then retry.")
    return {"uid": value["_id"], "handle": value["uname"]}


def plain(value) -> str:
    soup = BeautifulSoup(str(value or ""), "html.parser")
    for icon in soup.select(".icon-check"):
        icon.replace_with("+")
    for br in soup.select("br"):
        br.replace_with("\n")
    return soup.get_text().strip()


def numeric(value) -> float:
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise VPError("CWNUOJ returned an unrecognized score; no score will be guessed.") from None
    if not math.isfinite(result):
        raise VPError("CWNUOJ returned a non-finite score.")
    return result


class HydroLive:
    def __init__(self, url: str, account: dict):
        platform, self.id, self.url = contest_url(url)
        if platform != "cwnuoj":
            raise VPError("This adapter accepts CWNUOJ contest links only.")
        self.prefix = urlsplit(self.url).path.rsplit("/contest/", 1)[0]
        self.domain = self.prefix.split("/")[-1] if self.prefix else "system"
        self.account = account
        self.handle = account.get("handle", "")
        self.uid = account.get("uid")
        self._identified = False
        self._dynamic_records = None
        self.web = WebSession(ORIGIN, account)

    def identity(self) -> dict:
        response = self.web.get(ORIGIN + self.prefix + "/")
        if urlsplit(response.url).hostname != CWNUOJ_HOST:
            raise VPError("CWNUOJ returned an unexpected login destination.")
        identity = identity_from_html(response.text)
        if ((self.handle and identity["handle"].casefold() != self.handle.casefold())
                or (self.uid is not None and identity["uid"] != self.uid)):
            raise VPError("The CWNUOJ session belongs to another account. Restore this contest's login.")
        self.uid = identity["uid"]
        self.handle = identity["handle"]
        self.account.update(identity)
        self._identified = True
        return identity

    def get_json(self, url: str, **params) -> dict:
        if urlsplit(url).scheme != "https" or urlsplit(url).netloc != CWNUOJ_HOST:
            raise VPError("Refusing an unexpected CWNUOJ API destination.")
        for attempt in range(2):
            response = self.web.get(url, params=params, headers={"Accept": "application/json"})
            if urlsplit(response.url).netloc != CWNUOJ_HOST:
                raise VPError("CWNUOJ redirected to another site.")
            try:
                data = response.json()
            except ValueError:
                raise VPError("CWNUOJ did not return JSON. Refresh the browser login and retry.") from None
            if not isinstance(data, dict):
                raise VPError("CWNUOJ returned an invalid JSON response.")
            if data.get("url") and urlsplit(data["url"]).path in ("/login", "/user/login"):
                if attempt == 0 and self.web._refresh_browser(force=True):
                    continue
                raise VPError("CWNUOJ login expired. Log in to oj.cwnupaa.com in Firefox, then retry.")
            if data.get("error"):
                error = data["error"]
                raise VPError("CWNUOJ: " + str(error.get("message", error.get("name", "access denied")) if isinstance(error, dict) else error))
            return data
        raise VPError("CWNUOJ login could not be refreshed.")

    def details(self) -> dict:
        if not self._identified:
            self.identity()
        data = self.get_json(self.url)
        if not (data.get("tsdoc") or {}).get("attend"):
            if self.web._refresh_browser(force=True):
                self.identity()
                data = self.get_json(self.url)
        return data

    def contest_name(self) -> str:
        data = self.details().get('tdoc', {})
        if str(data.get('docId', data.get('_id', ''))) != self.id or data.get('domainId', 'system') != self.domain:
            raise VPError('CWNUOJ 返回了其他比赛的信息，原目录保留。')
        return data.get('title', '')

    def apply_metadata(self, contest: Contest, data: dict, *, require_registration: bool = True) -> None:
        tdoc, status = data.get("tdoc", {}), data.get("tsdoc") or {}
        if contest.extra.get("uid") is not None and contest.extra["uid"] != self.uid:
            raise VPError("The CWNUOJ user ID changed; restore the saved participation's account.")
        ident = str(tdoc.get("docId", tdoc.get("_id", "")))
        if ident != self.id or tdoc.get("domainId", "system") != self.domain or tdoc.get("rule") not in SCORING:
            raise VPError("CWNUOJ contest identity or scoring rule is unsupported. Use an ACM, OI, IOI or Codeforces contest.")
        if require_registration and not status.get("attend"):
            # A timed out/anonymous session must not be mistaken for an existing
            # participation, and no attend/start request is ever issued here.
            raise VPError("CWNUOJ did not confirm an existing registration. Log in and join this contest on CWNUOJ first.")
        if require_registration and tdoc.get("allowTeam"):
            raise VPError("CWNUOJ team contests are not supported yet; use the original contest page.")
        if not all(isinstance(tdoc.get(key), str) and tdoc[key] for key in ("title", "beginAt", "endAt")):
            raise VPError("CWNUOJ did not provide a valid contest title and schedule.")
        start, end = timestamp(tdoc["beginAt"]), timestamp(tdoc["endAt"])
        if tdoc.get("duration") and (require_registration or status.get("startAt")):
            if not status.get("startAt"):
                raise VPError("This timed participation has not started on CWNUOJ. Open it on the original site first, then reconnect.")
            start = timestamp(status["startAt"])
            end = min(end, start + numeric(tdoc["duration"]) * 3600,
                      timestamp(status["endAt"]) if status.get("endAt") else end)
        if end <= start:
            raise VPError("CWNUOJ returned an invalid contest time window.")
        pids = tdoc.get("pids", [])
        if not isinstance(pids, list) or any(type(pid) is not int or pid <= 0 for pid in pids) or len(set(pids)) != len(pids):
            raise VPError("CWNUOJ returned an invalid problem list.")
        # Hydro uses A..Z, AA..AZ, BA... for contest indices.
        def label(index):
            result = ""
            while index >= 0:
                result = chr(65 + index % 26) + result
                index = index // 26 - 1
            return result
        contest.name = tdoc["title"]
        contest.original_start = start
        contest.rules = Rules(int(end - start))
        contest.problems = {label(i): str(pid) for i, pid in enumerate(pids)}
        now = platform_now(self.url)
        phase = "BEFORE" if now < start else "CODING" if now < end else "FINISHED"
        frozen = bool(tdoc.get("lockAt") and timestamp(tdoc["lockAt"]) <= now and not tdoc.get("unlocked"))
        contest.extra.update(mode="live", scoring=SCORING[tdoc["rule"]], phase=phase, frozen=frozen,
                             hydro_rule=tdoc["rule"], domain=self.domain, uid=self.uid, end_time=end)

    def prepare(self) -> tuple[Contest, dict]:
        self.identity()
        data = self.details()
        contest = Contest("cwnuoj", self.id, self.url, "CWNUOJ", f"CWNUOJ {self.domain} {self.id}", {}, Rules(1))
        self.apply_metadata(contest, data)
        return contest, self.snapshot(contest, details=data)

    def snapshot(self, contest: Contest, *, details: dict | None = None) -> dict:
        data = details if details is not None else self.details()
        self.apply_metadata(contest, data)
        snapshot = {"own": None, "stats": {}, "phase": contest.extra["phase"],
                    "frozen": contest.extra["frozen"], "at": platform_now(self.url)}
        if snapshot["phase"] == "BEFORE":
            return snapshot
        try:
            board = self.get_json(self.url + "/scoreboard")
        except VPError as exc:
            snapshot["message"] = "Scoreboard unavailable: " + str(exc)
            return snapshot
        rows = board.get("rows")
        if not isinstance(rows, list) or not rows:
            snapshot["message"] = "CWNUOJ has not published a scoreboard."
            return snapshot
        header = rows[0]
        positions = {str(cell.get("raw")): index for index, cell in enumerate(header) if cell.get("type") == "problem"}
        if set(positions) != set(contest.problems.values()):
            raise VPError("CWNUOJ scoreboard problems do not match this contest.")
        uid_index = next((i for i, c in enumerate(header) if c.get("type") == "user"), None)
        rank_index = next((i for i, c in enumerate(header) if c.get("type") == "rank"), None)
        total_index = next((i for i, c in enumerate(header) if c.get("type") in ("solved", "total_score")), None)
        if None in (uid_index, rank_index, total_index):
            raise VPError("CWNUOJ returned an unrecognized scoreboard layout.")
        for row in rows[1:]:
            if len(row) != len(header):
                raise VPError("CWNUOJ returned an incomplete scoreboard row.")
            if str(row[uid_index].get("raw")) != str(self.uid):
                continue
            total = plain(row[total_index].get("value")).splitlines()
            own = {"rank": plain(row[rank_index].get("value")), "points": numeric(total[0]),
                   "penalty": total[1] if contest.extra["scoring"] == "ICPC" and len(total) > 1 else "",
                   "party": {"members": [{"handle": self.handle}], "teamName": plain(row[uid_index].get("value"))},
                   "problemResults": []}
            for pid in contest.problems.values():
                cell = row[positions[pid]]
                # IOI's "records" cells can include practice results. Only the
                # first cell is the official contest result.
                if cell.get("type") == "records" and isinstance(cell.get("raw"), list):
                    cell = cell["raw"][0] if cell["raw"] else {}
                own["problemResults"].append({"points": numeric(cell.get("score") or 0),
                                               "display": plain(cell.get("value")) or "—", "type": "FINAL"})
            snapshot["own"] = own
            break
        pdict = board.get("pdict", {})
        for name, field in (("submitted", "nSubmit"), ("accepted", "nAccept")):
            if all(type(pdict.get(pid, {}).get(field)) is int for pid in contest.problems.values()):
                snapshot["stats"][name] = {label: pdict[pid][field] for label, pid in contest.problems.items()}
        # CWNUOJ's custom view exposes exact attempted counts as well. Use its
        # visible summaries rather than inferring attempts from a score cell.
        custom = {str(p.get("pid")): p for p in (board.get("ratingBoard") or {}).get("problems", [])}
        if set(custom) == set(contest.problems.values()):
            for name in ("submitted", "attempted", "accepted"):
                if all(type(custom[pid].get(name)) is int and custom[pid][name] >= 0 for pid in contest.problems.values()):
                    snapshot["stats"][name] = {label: custom[pid][name] for label, pid in contest.problems.items()}
        return snapshot

    def submissions(self, contest: Contest) -> list[dict]:
        if contest.extra.get("practice"):
            return self._submissions(contest, correction=True)
        values = {record["id"]: record for record in self._submissions(contest)}
        if contest.extra.get("phase") == "FINISHED":
            values.update({record["id"]: record for record in self._submissions(contest, correction=True)})
        return list(values.values())

    def _submissions(self, contest: Contest, *, correction: bool = False) -> list[dict]:
        if not self._identified:
            self.identity()
        if contest.extra.get("uid") != self.uid:
            raise VPError("The CWNUOJ user ID changed; restore this contest's account.")
        result, seen, page = {}, set(), 1
        # CWNUOJ's rating plugin interprets uidOrName as a display-name keyword,
        # so its JSON view must be paged without that filter and then filtered by
        # exact numeric UID. The server still enforces contest visibility.
        dynamic = None if correction else self._dynamic_records
        while True:
            params = {"page": page} if correction else {"tid": self.id, "page": page}
            if dynamic is not True:
                params["uidOrName"] = str(self.uid)
            data = self.get_json(ORIGIN + self.prefix + "/record", **params)
            if dynamic is None and data.get("ratingDynamicRecords"):
                dynamic = True
                if not correction:
                    self._dynamic_records = True
                continue
            dynamic = bool(data.get("ratingDynamicRecords"))
            if not correction:
                self._dynamic_records = dynamic
            records = data.get("rdocs")
            if not isinstance(records, list):
                raise VPError("CWNUOJ did not return a submission list.")
            ids = {str(r.get("_id", "")) for r in records}
            if ids and not ids - seen:
                raise VPError("CWNUOJ repeated a submission page; history completeness could not be verified.")
            seen.update(ids)
            for raw in records:
                if raw.get("uid") != self.uid:
                    continue
                sid = str(raw.get("_id", ""))
                if not valid_submission_id(sid, "cwnuoj"):
                    raise VPError("CWNUOJ returned an invalid submission ID.")
                wanted_contests = (None, "") if correction else (self.id,)
                if raw.get("domainId", self.domain) != self.domain or raw.get("contest", None if correction else self.id) not in wanted_contests:
                    continue
                labels = {pid: label for label, pid in contest.problems.items()}
                label = labels.get(str(raw.get("pid")))
                if label is None:
                    continue
                created = int(sid[:8], 16)
                relative = created - contest.original_start
                if relative < 0:
                    continue
                verdict = VERDICTS.get(raw.get("status"), "HIDDEN" if raw.get("status") is None else "UNAVAILABLE")
                result[sid] = {"id": sid, "team": "me", "problem": label, "time": relative,
                               "verdict": verdict, "points": raw.get("score"), "practice": correction or created >= contest.extra["end_time"]}
            if correction and records and all(valid_submission_id(str(r.get("_id", "")), "cwnuoj")
                                               and int(str(r["_id"])[:8], 16) < contest.original_start for r in records):
                break
            if dynamic:
                if not data.get("ratingRecordsHasNext"):
                    break
                next_url = urlsplit(data.get("ratingRecordsNextUrl") or "")
                if next_url.netloc or next_url.path != self.prefix + "/record":
                    raise VPError("CWNUOJ returned an invalid next submission page.")
                query = parse_qs(next_url.query)
                try:
                    next_page = int(query["page"][0])
                except (KeyError, ValueError, IndexError):
                    raise VPError("CWNUOJ returned invalid submission pagination.") from None
                if query.get("tid") != (None if correction else [self.id]) or next_page <= page:
                    raise VPError("CWNUOJ changed contest identity while paging submissions.")
                page = next_page
            elif not records:
                break
            else:
                page += 1
        return list(result.values())

    def save_statements(self, contest: Contest, directory) -> None:
        for label, pid in contest.problems.items():
            target = directory / (label + ".html")
            if target.exists():
                continue
            response = self.web.get(ORIGIN + self.prefix + "/p/" + pid, params={"tid": self.id})
            content = BeautifulSoup(response.text, "html.parser").select_one(".problem-content, .problem-statement")
            if content is None:
                raise VPError("CWNUOJ has not exposed this statement. Use the original contest page.")
            target.write_text('<!doctype html><meta charset="utf-8">'
                              f'<base href="{ORIGIN}{self.prefix}/"><title>{escape(contest.name)} — {label}</title>'
                              '<style>body{max-width:960px;margin:2em auto;font:18px/1.6 sans-serif}pre{white-space:pre-wrap}</style>'
                              + str(content), encoding="utf8")
