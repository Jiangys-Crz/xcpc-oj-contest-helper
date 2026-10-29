from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
import secrets
import time
from urllib.parse import urlencode, urljoin, urlsplit

from .http import WebSession, form_values
from .accounts import refresh_api_credentials
from .models import Cell, Contest, Medals, Reference, Rules, Submission, TeamScore, VPError, StartRejected
from .originals import OriginalReplay, classify, load_metadata
from .parsing import ccpc_reveal_count, medals_from_html
from .scoring import verdict

def signed_parameters(method: str, params: dict, key: str, secret: str,
                      at: int | None = None, nonce: str | None = None) -> dict:
    p = {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in params.items()}
    p.update(apiKey=key, time=str(at if at is not None else int(time.time())))
    prefix = nonce or secrets.token_hex(3)
    canonical = "&".join(f"{k}={v}" for k, v in sorted(p.items()))
    p["apiSig"] = prefix + hashlib.sha512(f"{prefix}/{method}?{canonical}#{secret}".encode()).hexdigest()
    return p


class Gym:
    def __init__(self, url: str, account: dict):
        self.url = url
        self.id = urlsplit(url).path.rsplit("/", 1)[-1]
        self.platform = "gym" if urlsplit(url).path.startswith("/gym/") else "cf"
        self.origin = "https://codeforces.com"
        self.account = account
        self.handle = account.get("handle", "")
        self.web = WebSession(self.origin, account)
        self.start_time: float | None = None
        self._original = None
        self.submissions_complete = False
        self._replay = None
        self._next_replay_attempt = 0.0
        self._replay_error = ""

    def api(self, method: str, **params):
        refresh_api_credentials(self.account, self.platform)
        if not self.account.get("api_key") or not self.account.get("api_secret"):
            raise VPError("请先运行 xcpc api cf 设置 Codeforces API key 和 secret。")
        p = signed_parameters(method, params, self.account["api_key"], self.account["api_secret"])
        try:
            # Use the same cross-process rate gate as anonymous standings.
            from .codeforces_live import public_api
            return public_api(self.web, method, **p)
        except VPError as exc:
            comment = str(exc)
            credentials_error = any(word in comment.casefold() for word in ("apikey", "api key", "api_key", "apisig", "signature", "secret"))
            for field in ("api_key", "api_secret", "cookie"):
                value = self.account.get(field)
                if value:
                    comment = comment.replace(value, "[redacted]")
            raise VPError(comment + ("；可运行 xcpc api cf 重新设置密钥。" if credentials_error else "")) from None

    def prepare(self) -> Contest:
        data = self.api("contest.standings", contestId=self.id, showUnofficial=False, participantTypes="CONTESTANT")
        raw = data["contest"]
        if raw["type"] != "ICPC":
            raise VPError("This Gym contest does not use ICPC scoring.")
        if raw["phase"] != "FINISHED":
            raise VPError("Only completed original contests can be started as a native VP.")
        soup = self.web.soup(self.url, params={"locale": "en"})
        dashboard_html = str(soup)
        names = [raw["name"]] + [h.get_text(" ", strip=True) for h in soup.select("h1,h2")]
        name = next((title for title in names if isinstance(title, str) and title.strip()), "")
        if not name:
            raise VPError("Codeforces 未返回比赛名称。")
        pdfs = {}
        for a in soup.select("a[href]"):
            label = a.get_text(" ", strip=True)
            href = urljoin(self.url, a["href"])
            if re.search(r"statement|problem.*pdf|题面", label, re.I) and (".pdf" in href.lower() or "attachments" in href):
                pdfs["Statements.pdf"] = href
                break
        if not pdfs:
            try:
                attachments = self.web.soup(self.url + "/attachments", params={"locale": "en"}).select("a[href]")
            except VPError:
                attachments = []
            for a in attachments:
                href = urljoin(self.url, a["href"])
                if re.search(r"\.pdf(?:$|\?)", href, re.I) and re.search(r"statement|problem", a.get_text(" ", strip=True), re.I):
                    pdfs["Statements.pdf"] = href
                    break
        if not pdfs:
            for problem in data["problems"]:
                label = problem["index"]
                try:
                    p = self.web.soup(self.url + f"/problem/{label}", params={"locale": "en"})
                except VPError:
                    continue
                for a in p.select("a[href]"):
                    href = urljoin(self.url, a["href"])
                    if re.search(r"\.pdf(?:$|\?)", href, re.I):
                        pdfs[f"{label}.pdf"] = href
                        break
        duration = int(raw["durationSeconds"])
        freeze = raw.get("freezeDurationSeconds")
        medals = medals_from_html(dashboard_html, self.url)
        self._original = data
        return Contest(self.platform, self.id, self.url, name, name,
                       {p["index"]: p["index"] for p in data["problems"]},
                       Rules(duration, freeze_at=duration-int(freeze) if freeze else None,
                             problem_reveal_count=ccpc_reveal_count(name)),
                       pdfs, medals, raw.get("startTimeSeconds"), raw.get("websiteUrl"))

    def _virtual_party(self):
        data = self.api("contest.standings", contestId=self.id, handles=self.handle,
                        showUnofficial=True, participantTypes="VIRTUAL")
        parties = [r["party"] for r in data["rows"]
                   if r["party"]["participantType"] == "VIRTUAL"
                   and any(m["handle"].lower() == self.handle.lower() for m in r["party"]["members"])
                   and r["party"].get("startTimeSeconds") is not None]
        return max(parties, key=lambda p: p["startTimeSeconds"], default=None)

    def start(self, contest: Contest, requested_start: float | None = None, *, fresh: bool = False) -> float:
        soup = self.web.soup(self.url, params={"locale": "en"})
        profile = soup.select_one("#header")
        if not profile or not any(urlsplit(a.get("href", "")).path.lower() == f"/profile/{self.handle}".lower() for a in profile.select("a")):
            raise VPError("The Codeforces login cookie does not belong to the configured handle.")
        old = self._virtual_party()
        if old and time.time() < old["startTimeSeconds"] + contest.rules.duration:
            if fresh or requested_start is not None and abs(requested_start - old["startTimeSeconds"]) > 60:
                raise StartRejected("Codeforces 已有尚未结束或已预约的 VP，本次命令已取消。")
            self.start_time = float(old["startTimeSeconds"])
            return self.start_time
        registration = self.web.soup(self.origin + f"/contestRegistration/{self.id}/virtual/true", params={"locale": "en"})
        form = registration.select_one("form#registerForm")
        if form is None:
            form = next((f for f in registration.select("form") if f.select_one('[name="startTime"], [name="virtualStartTime"]')), None)
        if form is None:
            raise StartRejected("Codeforces did not allow a new virtual participation registration.")
        payload = form_values(form)
        submit = form.select_one('input[type="submit"][name], button[type="submit"][name]')
        if submit:
            payload[submit["name"]] = submit.get("value", "")
        if not payload.get("csrf_token"):
            raise VPError("Codeforces did not provide a registration CSRF token.")
        if requested_start is not None:
            from .scheduling import registration_time
            registration_time(form, payload, requested_start)
        # Preserve the platform's observed start/timezone fields. In particular,
        # never use a local-clock guess for the native VP start.
        self.native_post_sent = True
        response = self.web.post(urljoin(self.origin, form.get("action") or f"/contestRegistration/{self.id}/virtual/true"), payload)
        from bs4 import BeautifulSoup
        content = getattr(response, "content", None)
        errors = (BeautifulSoup(content, "html.parser").select(".error, .error__message, .alert-danger")
                  if isinstance(content, (str, bytes)) else [])
        message = " ".join(e.get_text(" ", strip=True) for e in errors).strip()
        party = self._virtual_party()
        if not party or (old and party["startTimeSeconds"] == old["startTimeSeconds"]):
            if message:
                raise StartRejected("Codeforces 拒绝启动 VP：" + message[:500])
            if old and party:
                raise StartRejected("Codeforces 未创建新的 VP，本次命令已取消。")
            raise VPError("Codeforces did not confirm a new native VIRTUAL participation and start time.")
        self.start_time = float(party["startTimeSeconds"])
        if requested_start is not None and abs(self.start_time - requested_start) > 60:
            raise StartRejected("Codeforces confirmed a different virtual start time. Check the original registration before retrying.")
        return self.start_time

    def submissions(self, contest: Contest) -> list[Submission]:
        if self.start_time is None:
            raise VPError("The native Gym VP start has not been verified.")
        result = {}
        offset = 1
        while True:
            data = self.api("contest.status", contestId=self.id, handle=self.handle, **{"from": offset, "count": 1000})
            reached_start = False
            for raw in data:
                author = raw["author"]
                if raw["creationTimeSeconds"] < self.start_time:
                    reached_start = True
                    continue
                if not any(m["handle"].lower() == self.handle.lower() for m in author["members"]):
                    continue
                native = author["participantType"] == "VIRTUAL" and author.get("startTimeSeconds") == self.start_time
                practice = (author["participantType"] == "PRACTICE"
                            and raw["creationTimeSeconds"] >= self.start_time + contest.rules.duration)
                local = contest.extra.get("local_attempt") and author.get("participantType") in ("VIRTUAL", "PRACTICE")
                if str(raw.get("contestId", self.id)) != self.id or author.get("ghost") or not (native or practice or local):
                    continue
                label = raw["problem"]["index"]
                if label not in contest.problems:
                    continue
                sid = str(raw["id"])
                relative = raw["creationTimeSeconds"] - self.start_time if local else raw["relativeTimeSeconds"] if native else raw["creationTimeSeconds"] - self.start_time
                result[sid] = Submission(sid, "me", label, float(relative), verdict(raw.get("verdict")))
            if len(data) < 1000 or reached_start:
                self.submissions_complete = True
                break
            offset += 1000
        return list(result.values())

    @staticmethod
    def rows_from_api(data: dict, contest: Contest) -> list[TeamScore]:
        labels = [p["index"] for p in data["problems"]]
        rows = []
        for raw in data["rows"]:
            party = raw["party"]
            if party["participantType"] != "CONTESTANT":
                continue
            members = ";".join(m["handle"] for m in party["members"])
            ident = "team:" + str(party["teamId"]) if "teamId" in party else "ghost:" + party.get("teamName", "") if party.get("ghost") else "user:" + members
            cells = {}
            for label, result in zip(labels, raw["problemResults"]):
                solved = result.get("points", 0) > 0
                t = result.get("bestSubmissionTimeSeconds")
                if solved and t is None:
                    raise VPError("The Gym original scoreboard lacks solve times needed for DOMjudge ties.")
                cells[label] = Cell(int(result.get("rejectedAttemptCount", 0)) + int(solved), 0,
                                    solved, int(t // 60) if t is not None else None)
            rows.append(TeamScore(ident, party.get("teamName") or members, cells,
                                  penalty_minutes=contest.rules.penalty_minutes,
                                  known_penalty=int(raw["penalty"])))
        return rows

    def reference(self, contest: Contest, elapsed: float) -> Reference:
        if self._original is None:
            self._original = self.api("contest.standings", contestId=self.id, showUnofficial=False, participantTypes="CONTESTANT")
        released = contest.rules.freeze_at is None
        if contest.extra.get("local_attempt") and elapsed >= contest.rules.duration:
            released = True
        if not released and elapsed >= contest.rules.duration:
            # Original historic 'frozen=false' cannot release the current VP.
            soup = self.web.soup(self.url + "/standings", params={"locale": "en"})
            text = soup.get_text(" ", strip=True)
            native_finished = bool(re.search(r"virtual (?:participation|contest).*?(?:has ended|finished|completed)", text, re.I))
            released = native_finished and not re.search(r"scoreboard (?:is |remains )?frozen", text, re.I)
        final = self._original
        if elapsed >= contest.rules.duration:
            final = self.api("contest.standings", contestId=self.id, showUnofficial=False, participantTypes="CONTESTANT")
            self._original = final
        complete = final["contest"]["phase"] == "FINISHED" and not final["contest"].get("frozen", False)
        rows = self.rows_from_api(final, contest)
        if any(raw["party"].get("ghost") for raw in final["rows"]):
            try:
                # Imported CONTESTANT ghosts can have lost starred-team flags.
                classify(rows, contest, load_metadata(contest))
            except VPError as exc:
                return Reference(released=released, message=str(exc))
        if elapsed < contest.rules.duration or contest.rules.frozen(elapsed, released):
            if complete and self._replay is None and time.monotonic() >= self._next_replay_attempt:
                self._next_replay_attempt = time.monotonic() + 120
                try:
                    self._replay = self._original_replay(contest, rows)
                except VPError as exc:
                    self._replay_error = str(exc)
            if self._replay is not None:
                return self._replay.at(contest, elapsed, released=released)
            return Reference(released=released, message=self._replay_error or "Original submission events unavailable; final scoreboard only.")
        return Reference(rows, complete, complete, released, "" if complete else "Waiting for original final standings.")

    def _original_replay(self, contest: Contest, rows: list[TeamScore]) -> OriginalReplay:
        from .originals import cache_archive_submissions, cache_path, load_archive_submissions
        from .storage import read_json, write_json
        from dataclasses import asdict
        if any(raw["party"].get("ghost") for raw in self._original["rows"]):
            metadata = load_metadata(contest)
            events = load_archive_submissions(contest, metadata)
            replay = OriginalReplay(contest, metadata["teams"], events, rows)
            cache_archive_submissions(metadata, events)
            return replay
        source = contest.url + "/original-submissions"
        cache = read_json(cache_path("cf-events", source))
        teams = [{"id": row.id, "name": row.name, "official": row.eligible} for row in rows]
        if cache.get("source") == source:
            try:
                return OriginalReplay(contest, teams, [Submission(**s) for s in cache["submissions"]], rows)
            except (KeyError, TypeError, VPError):
                pass
        submissions = []
        offset = 1
        while True:
            data = self.api("contest.status", contestId=self.id, **{"from": offset, "count": 1000})
            for raw in data:
                party = raw["author"]
                if party["participantType"] != "CONTESTANT":
                    continue
                members = ";".join(m["handle"] for m in party["members"])
                ident = "team:" + str(party["teamId"]) if "teamId" in party else "ghost:" + party.get("teamName", "") if party.get("ghost") else "user:" + members
                relative = float(raw["relativeTimeSeconds"])
                if not 0 <= relative < contest.rules.duration:
                    continue
                submissions.append(Submission(str(raw["id"]), ident, raw["problem"]["index"], relative, verdict(raw.get("verdict"))))
            if len(data) < 1000:
                break
            offset += 1000
        replay = OriginalReplay(contest, teams, submissions, rows)
        write_json(cache_path("cf-events", source), {"source": source, "submissions": [asdict(s) for s in submissions]})
        return replay
