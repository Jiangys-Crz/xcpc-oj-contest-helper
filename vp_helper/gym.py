from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
import secrets
import time
from urllib.parse import urlencode, urljoin, urlsplit

from .http import WebSession, form_values
from .accounts import refresh_api_credentials
from .platform_clock import platform_now
from .models import Cell, Contest, Medals, Reference, Rules, Submission, TeamScore, VPError, StartRejected
from .originals import OriginalReplay, classify, load_metadata
from .parsing import ccpc_reveal_count, medals_from_html
from .scoring import codeforces_verdict, verdict
from .cf_judgements import PendingJudgements

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
        self.public = WebSession(self.origin, {}, auto_refresh=False)
        self.start_time: float | None = None
        self._original = None
        self._next_original_attempt = 0.0
        self.submissions_complete = False
        self.pending_judgements = PendingJudgements()
        self._replay = None
        self._next_replay_attempt = 0.0
        self._replay_error = ""
        self._onsite_replay = None
        self._next_onsite_attempt = 0.0
        self._onsite_error = ""

    def api(self, method: str, **params):
        if self.platform == 'cf' and method == 'contest.standings':
            from .codeforces_live import public_api
            return public_api(self.public, method, contestId=params.get('contestId', self.id))
        refresh_api_credentials(self.account, self.platform)
        if not self.account.get("api_key") or not self.account.get("api_secret"):
            raise VPError("请先运行 xcpc api cf 设置 Codeforces API key 和 secret。")
        p = signed_parameters(method, params, self.account["api_key"], self.account["api_secret"], at=int(platform_now(self.url)))
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
        if raw["type"] not in ("ICPC", "CF", "IOI"):
            raise VPError("Codeforces returned an unsupported contest scoring rule.")
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
        from .codeforces_live import freeze_metadata
        freeze_known, freeze_at = freeze_metadata(raw, duration)
        medals = medals_from_html(dashboard_html, self.url)
        self._original = data
        self._next_original_attempt = time.monotonic() + 30
        # Codeforces registers native virtual participations using ICPC rules,
        # including Div./Educational rounds whose original scoreboard uses CF.
        extra = {"scoring": "ICPC", "original_scoring": raw["type"], "freeze_known": freeze_known,
                 "problem_info": data["problems"]}
        if freeze_known:
            extra["freeze_source"] = "platform"
        return Contest(self.platform, self.id, self.url, name, name,
                       {p["index"]: p["index"] for p in data["problems"]},
                       Rules(duration, freeze_at=freeze_at,
                             problem_reveal_count=ccpc_reveal_count(name)),
                       pdfs, medals, raw.get("startTimeSeconds"), raw.get("websiteUrl"), extra)

    def contest_name(self) -> str:
        if self.platform == 'cf':
            from .codeforces_live import CodeforcesLive
            return CodeforcesLive(self.url, self.account).contest_name()
        return self.api('contest.standings', contestId=self.id)['contest']['name']

    def _virtual_party(self):
        if self.platform == 'cf':
            records = self.api('contest.status', contestId=self.id, handle=self.handle, **{'from': 1, 'count': 100})
            parties = [record.get('author', {}) for record in records]
            parties = [party for party in parties if party.get('participantType') == 'VIRTUAL'
                       and any(member.get('handle', '').casefold() == self.handle.casefold() for member in party.get('members', []))
                       and party.get('startTimeSeconds') is not None]
        else:
            data = self.api("contest.standings", contestId=self.id, handles=self.handle,
                            showUnofficial=True, participantTypes="VIRTUAL")
            parties = [r["party"] for r in data["rows"]
                       if r["party"]["participantType"] == "VIRTUAL"
                       and any(m["handle"].lower() == self.handle.lower() for m in r["party"]["members"])
                       and r["party"].get("startTimeSeconds") is not None]
        latest = max(parties, key=lambda party: party['startTimeSeconds'], default=None)
        duration = getattr(self, '_native_duration', None)
        if latest and isinstance(duration, (int, float)) and platform_now(self.url) < latest['startTimeSeconds'] + duration:
            return latest
        # Before the first submission, CF and Gym APIs may expose no current
        # virtual party. Read the account's native countdown using the server's
        # HTTP Date, never the local clock or the requested time.
        native = self._countdown_party()
        if native is not None:
            parties.append(native)
        return max(parties, key=lambda party: party['startTimeSeconds'], default=None)

    def _countdown_party(self):
        from bs4 import BeautifulSoup
        from email.utils import parsedate_to_datetime
        from .codeforces_live import verify_login
        response = self.web.get(self.url, params={'locale': 'en'})
        soup = BeautifulSoup(response.text, 'html.parser')
        verify_login(soup, self.handle)
        try:
            server_time = parsedate_to_datetime(response.headers.get('Date', '')).timestamp()
        except (TypeError, ValueError, AttributeError, OverflowError):
            return None
        for phase in ('start', 'finish'):
            node = soup.select_one(f'.countdown.before-contest-{self.id}-{phase}')
            if node is None:
                continue
            match = re.fullmatch(r'(\d+):(\d{2}):(\d{2})', node.get_text(strip=True))
            if not match or any(int(value) > 59 for value in match.groups()[1:]):
                continue
            duration = getattr(self, '_native_duration', None)
            if phase == 'finish' and not isinstance(duration, (int, float)):
                continue
            remaining = int(match[1])*3600 + int(match[2])*60 + int(match[3])
            started = server_time + remaining - (duration if phase == 'finish' else 0)
            return {'participantType':'VIRTUAL','members':[{'handle':self.handle}], 'startTimeSeconds':started}
        return None

    def start(self, contest: Contest, requested_start: float | None = None, *, fresh: bool = False) -> float:
        self._native_duration = contest.rules.duration
        soup = self.web.soup(self.url, params={"locale": "en"})
        profile = soup.select_one("#header")
        if not profile or not any(urlsplit(a.get("href", "")).path.lower() == f"/profile/{self.handle}".lower() for a in profile.select("a")):
            raise VPError("The Codeforces login cookie does not belong to the configured handle.")
        old = self._virtual_party()
        if old and platform_now(self.url) < old["startTimeSeconds"] + contest.rules.duration:
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
        if form.select_one('[name="takePartAs"]'):
            if form.select_one('input[name="takePartAs"][value="personal"]:not([disabled])') is None:
                raise VPError("Codeforces 未提供个人参赛选项，本次未发送报名请求。")
            payload["takePartAs"] = "personal"
            for name in list(payload):
                if name.startswith("teamMember#"):
                    del payload[name]
            if form.select_one('select[name="teamId"] option[value="-1"]'):
                payload["teamId"] = "-1"
        submit = form.select_one('input[type="submit"][name], button[type="submit"][name]')
        if submit:
            payload[submit["name"]] = submit.get("value", "")
        if not payload.get("csrf_token"):
            raise VPError("Codeforces did not provide a registration CSRF token.")
        if requested_start is not None:
            from .scheduling import registration_time
            self.registration_start = registration_time(form, payload, requested_start)
        # The requested time is only a reservation until the platform confirms
        # the actual native VP start below.
        before_post = getattr(self, 'before_native_post', None)
        if callable(before_post):
            before_post(platform_now(self.url))
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
                raise VPError("Codeforces 尚未返回新的 VP 开始记录；请恢复原会话继续核验，不会重发报名请求。")
            raise VPError("Codeforces did not confirm a new native VIRTUAL participation and start time.")
        self.start_time = float(party["startTimeSeconds"])
        if requested_start is not None and abs(self.start_time - requested_start) > 60:
            raise StartRejected("Codeforces confirmed a different virtual start time. Check the original registration before retrying.")
        return self.start_time

    def seed_submissions(self, records) -> None:
        self.pending_judgements.seed(records)

    def submissions(self, contest: Contest) -> list[Submission]:
        if self.start_time is None:
            raise VPError("The native Gym VP start has not been verified.")
        result = {}
        self.submissions_complete = False
        self.submission_message = ''
        offset = 1
        while True:
            try:
                data = self.api("contest.status", contestId=self.id, handle=self.handle, **{"from": offset, "count": 1000})
            except VPError as exc:
                if offset == 1:
                    raise
                self.submission_message = 'Recent submissions synced; older history will be retried: ' + str(exc)
                break
            # HTTP Date + native countdown can differ from the API epoch by a
            # few seconds. Reconcile only this account's same-contest VP near
            # the confirmed reservation; never import a different participation.
            starts = {float(raw['author']['startTimeSeconds']) for raw in data
                      if str(raw.get('contestId', self.id)) == self.id
                      and raw.get('author', {}).get('participantType') == 'VIRTUAL'
                      and not raw['author'].get('ghost')
                      and any(m.get('handle', '').casefold() == self.handle.casefold()
                              for m in raw['author'].get('members', []))
                      and isinstance(raw['author'].get('startTimeSeconds'), (int, float))
                      and abs(raw['author']['startTimeSeconds'] - self.start_time) <= 60}
            if len(starts) > 1:
                raise VPError('Codeforces 返回多个相近的 VP 开始时间，暂不混合提交记录。')
            known_start = getattr(self, 'observed_start_time', None)
            if starts and isinstance(known_start, (int, float)) and known_start not in starts:
                raise VPError('Codeforces 返回了另一场 VP 的开始时间，原会话记录已保留。')
            native_start = next(iter(starts), self.start_time)
            if starts:
                self.observed_start_time = native_start
            reached_start = False
            for raw in data:
                author = raw["author"]
                if not any(m["handle"].lower() == self.handle.lower() for m in author["members"]):
                    continue
                native = author["participantType"] == "VIRTUAL" and author.get("startTimeSeconds") == native_start
                if raw["creationTimeSeconds"] < self.start_time and not native:
                    reached_start = True
                    continue
                practice = (author["participantType"] == "PRACTICE"
                            and raw["creationTimeSeconds"] >= self.start_time + contest.rules.duration)
                local = contest.extra.get("local_attempt") and author.get("participantType") in ("VIRTUAL", "PRACTICE")
                if str(raw.get("contestId", self.id)) != self.id or author.get("ghost") or not (native or practice or local):
                    continue
                label = raw["problem"]["index"]
                if label not in contest.problems:
                    continue
                sid = str(raw["id"])
                relative = (raw["relativeTimeSeconds"] + native_start - self.start_time if native and not local
                            else raw["creationTimeSeconds"] - self.start_time)
                parsed = codeforces_verdict(raw.get('verdict'))
                result[sid] = Submission(sid, "me", label, float(relative), 'UNAVAILABLE' if parsed == 'PARTIAL' else parsed)
            if len(data) < 1000 or reached_start:
                self.submissions_complete = True
                break
            offset += 1000
        from dataclasses import asdict
        events = self.pending_judgements.update(self, [asdict(s) for s in result.values()])
        return [Submission(**{**event, 'verdict': 'UNAVAILABLE' if event['verdict'] == 'PARTIAL' else event['verdict']})
                for event in events]

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
                if data["contest"]["type"] == "IOI":
                    maximum = next((p.get("points") for p in data["problems"] if p["index"] == label), None)
                    solved = maximum is not None and result.get("points", 0) >= maximum
                t = result.get("bestSubmissionTimeSeconds")
                if solved and t is None and data["contest"]["type"] == "ICPC":
                    raise VPError("The Gym original scoreboard lacks solve times needed for DOMjudge ties.")
                cells[label] = Cell(int(result.get("rejectedAttemptCount", 0)) + int(solved), 0,
                                    solved, int(t // 60) if t is not None else None)
            rows.append(TeamScore(ident, party.get("teamName") or members, cells,
                                  penalty_minutes=contest.rules.penalty_minutes,
                                  known_penalty=int(raw["penalty"]) if data["contest"]["type"] == "ICPC" else None))
        return rows

    def reference(self, contest: Contest, elapsed: float) -> Reference:
        if (self._original is None or
                not any(raw.get("party", {}).get("participantType") == "CONTESTANT"
                        for raw in self._original.get("rows", []))
                and time.monotonic() >= self._next_original_attempt):
            self._next_original_attempt = time.monotonic() + 30
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
        if not rows:
            # A mirror may expose only virtual/practice participants. Empty
            # original timelines also reproduce an empty final board, but
            # that does not make the local team first in the onsite contest.
            self._replay = None
            return self._onsite_reference(contest, elapsed, released)
        if final["contest"]["type"] != "ICPC":
            # The original CF/IOI ranking is not an ICPC ranking. Build the
            # native virtual comparison from actual original submissions;
            # never reinterpret a points total or zero API penalty as ICPC.
            if complete and self._replay is None and time.monotonic() >= self._next_replay_attempt:
                self._next_replay_attempt = time.monotonic() + 120
                try:
                    self._replay = self._original_replay(contest, rows)
                except VPError as exc:
                    self._replay_error = str(exc)
            if self._replay is not None:
                return self._replay.at(contest, elapsed, released=released)
            return Reference(released=released, message=self._replay_error or "Original submissions unavailable for the native ICPC virtual ranking.")
        if any(raw["party"].get("ghost") and raw["party"].get("participantType") == "CONTESTANT"
               for raw in final["rows"]):
            try:
                # Imported CONTESTANT ghosts can have lost starred-team flags.
                classify(rows, contest, load_metadata(contest))
            except VPError as exc:
                return self._onsite_reference(contest, elapsed, released, str(exc))
            if not any(row.eligible for row in rows):
                return Reference(rows, complete=False, statistics_complete=True, released=released,
                                 message="No verified official participants are available for the original ranking.")
        if complete and self._replay is None and time.monotonic() >= self._next_replay_attempt:
            self._next_replay_attempt = time.monotonic() + 120
            try:
                self._replay = self._original_replay(contest, rows)
            except VPError as exc:
                self._replay_error = str(exc)
        if self._replay is not None:
            return self._replay.at(contest, elapsed, released=released)
        if elapsed < contest.rules.duration or contest.rules.frozen(elapsed, released):
            return Reference(released=released, message=self._replay_error or "Original submission events unavailable; final scoreboard only.")
        # API penalties and accepted cells can validate a final original rank,
        # but rejectedAttemptCount omits CE and does not describe every submit.
        # Publish attempt statistics only after the actual events are verified.
        return Reference(rows, complete, False, released,
                         "Original rank available; original attempt statistics are unavailable."
                         if complete else "Waiting for original final standings.")

    def _onsite_reference(self, contest: Contest, elapsed: float, released: bool, error: str = "") -> Reference:
        if self._onsite_replay is None and time.monotonic() >= self._next_onsite_attempt:
            self._next_onsite_attempt = time.monotonic() + 120
            try:
                from .onsite import load_replay
                self._onsite_replay = load_replay(contest)
            except VPError as exc:
                self._onsite_error = str(exc)
        if self._onsite_replay is not None:
            return self._onsite_replay.at(contest, elapsed, released=released)
        return Reference(released=released, message=self._onsite_error or error or
                         "Original official participants are unavailable; original rank and public statistics are unavailable.")

    def _original_replay(self, contest: Contest, rows: list[TeamScore]) -> OriginalReplay:
        from .originals import cache_archive_submissions, cache_path, load_archive_submissions
        from .storage import read_json, write_json
        from dataclasses import asdict
        if not rows:
            raise VPError("The original contest has no verified participants to replay.")
        if any(raw["party"].get("ghost") and raw["party"].get("participantType") == "CONTESTANT"
               for raw in self._original["rows"]):
            metadata = load_metadata(contest)
            events = load_archive_submissions(contest, metadata)
            replay = OriginalReplay(contest, metadata["teams"], events, rows)
            cache_archive_submissions(metadata, events)
            return replay
        source = contest.url + "/original-submissions"
        cache = read_json(cache_path("cf-events", source))
        teams = [{"id": row.id, "name": row.name, "official": row.eligible} for row in rows]
        def replay(events):
            expected = rows
            if self._original["contest"]["type"] != "ICPC":
                from .scoring import score_submissions
                expected = []
                originals = {row.id: row for row in rows}
                for team in teams:
                    row = score_submissions(events, list(contest.problems), contest.rules, contest.rules.duration,
                                            released=True, team_id=team["id"], name=team["name"])
                    row.eligible = team["official"]
                    # For CF rounds positive final points mean an accepted
                    # solution. Check all such cells against the fetched
                    # timeline before trusting the converted ICPC ranking.
                    checked = (list(contest.problems) if self._original["contest"]["type"] == "CF" else
                               [p["index"] for p in self._original["problems"]
                                if isinstance(p.get("points"), (int, float)) and p["points"] > 0])
                    if any(
                            row.cells[label].solved != originals[team["id"]].cells[label].solved
                            for label in checked):
                        raise VPError("Original Codeforces submissions do not reproduce the final accepted problems.")
                    expected.append(row)
            return OriginalReplay(contest, teams, events, expected)
        if cache.get("source") == source:
            try:
                return replay([Submission(**s) for s in cache["submissions"]])
            except (KeyError, TypeError, VPError):
                pass
        submissions = []
        offset = 1
        while True:
            data = self.api("contest.status", contestId=self.id, **{"from": offset, "count": 1000})
            if not isinstance(data, list):
                raise VPError("The original submission API returned an invalid event list.")
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
        result = replay(submissions)
        write_json(cache_path("cf-events", source), {"source": source, "submissions": [asdict(s) for s in submissions]})
        return result
