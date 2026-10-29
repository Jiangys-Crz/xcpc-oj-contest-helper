"""Nowcoder's participant-facing contest APIs and authenticated page identity."""
from __future__ import annotations

import ast
from html import escape
import re
import time
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .http import WebSession
from .platform_clock import platform_now
from .models import Contest, Rules, VPError
from .parsing import NOWCODER_HOST, contest_url, js_value

ORIGIN = "https://" + NOWCODER_HOST
INFO = "/acm/contest/contest-info"
PROBLEMS = "/acm/contest/problem-list"
RANK = "/acm-heavy/acm/contest/real-time-rank-data"
STATUS = "/acm-heavy/acm/contest/status-list"
SCORING = {1: "ICPC", 2: "OI", 3: "IOI", 4: "WEEKLY"}
VERDICTS = {"答案正确": "CORRECT", "已通过": "CORRECT", "通过": "CORRECT", "Accepted": "CORRECT",
            "答案错误": "WRONG-ANSWER", "Wrong Answer": "WRONG-ANSWER", "编译错误": "COMPILER-ERROR",
            "运行错误": "RUN-ERROR", "执行出错": "RUN-ERROR", "运行超时": "TIMELIMIT", "运行时间超限": "TIMELIMIT",
            "部分正确": "PARTIAL", "部分通过": "PARTIAL",
            "内存超限": "MEMORY-LIMIT", "运行内存超限": "MEMORY-LIMIT", "输出超限": "OUTPUT-LIMIT",
            "格式错误": "FORMAT-ERROR", "等待评测": "PENDING", "等待编译": "PENDING", "正在评测": "PENDING",
            "评测中": "PENDING", "正在编译": "PENDING", "编译中": "PENDING", "运行中": "PENDING",
            "正在运行": "PENDING", "排队中": "PENDING", "提交中": "PENDING", "已提交": "PENDING",
            "未判定": "PENDING", "结果隐藏": "HIDDEN", "系统错误": "SYSTEM-ERROR"}


def identity_from_html(html: str) -> dict:
    # pageInfo.uid is the contest author, not the current account. Only trust
    # the site's own login context or its authenticated navigation profile.
    logged = js_value(html, "window.isLogin")
    match = re.search(r"\bwindow\.globalInfo\s*=\s*\{([\s\S]*?)\};", html)
    owner = re.search(r'''(?:^|[,\s])(?:["']?ownerId["']?)\s*:\s*["']?(\d+)''', match[1]) if match else None
    soup = BeautifulSoup(html, "html.parser")
    ids = {m[1] for a in soup.select('.acm-header a[href]')
           if (m := re.fullmatch(r"/acm/contest/profile/([0-9]+)/?", urlsplit(a["href"]).path))}
    if owner and int(owner[1]) > 0:
        if ids and ids != {owner[1]}:
            raise VPError("Nowcoder returned conflicting account identities.")
        ids = {owner[1]}
    if logged is False or len(ids) != 1 or not int(next(iter(ids))):
        raise VPError("Nowcoder login expired. Log in at ac.nowcoder.com in Firefox and retry.")
    uid = int(ids.pop())
    return {"uid": uid, "handle": str(uid)}


def problem_config(html: str) -> dict:
    start = html.find("window.pageInfo")
    if start < 0:
        raise VPError("Nowcoder did not expose the problem submission configuration.")
    block = html[start:html.find("</script>", start)]
    result = {}
    for key in ("contestId", "questionId", "problemId", "tagId", "subTagId", "doneQuestionId", "codeJudgeType", "supportLang"):
        match = re.search(r"(?m)^\s*" + key + r'''\s*:\s*("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\d+)''', block)
        if match:
            result[key] = str(ast.literal_eval(match[1]))
    return result


class NowcoderLive:
    def __init__(self, url: str, account: dict):
        platform, self.id, self.url = contest_url(url)
        if platform != "nowcoder":
            raise VPError("This adapter accepts ac.nowcoder.com contest links only.")
        self.account = account
        self.handle = account.get("handle", "")
        self.uid = account.get("uid")
        self.web = WebSession(ORIGIN, account)
        self._identified = False

    def identity(self) -> dict:
        for attempt in range(2):
            response = self.web.get(ORIGIN + "/", headers={"Cache-Control": "no-cache"})
            if urlsplit(response.url).netloc != NOWCODER_HOST:
                raise VPError("Nowcoder returned an unexpected login destination.")
            try:
                who = identity_from_html(response.text)
                break
            except VPError:
                if attempt == 0 and self.web._refresh_browser(force=True):
                    continue
                raise
        if (self.handle and self.handle != who["handle"]) or (self.uid is not None and self.uid != who["uid"]):
            raise VPError("The Nowcoder session belongs to another account. Use the configured numeric user ID.")
        self.uid, self.handle = who["uid"], who["handle"]
        self.account.update(who)
        self._identified = True
        return who

    def get_json(self, path: str, **params):
        if not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path:
            raise VPError("Invalid Nowcoder API path.")
        for attempt in range(2):
            response = self.web.get(ORIGIN + path, params={**params, "_": int(time.time() * 1000)},
                                    headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest", "Cache-Control": "no-cache"})
            if urlsplit(response.url).netloc != NOWCODER_HOST:
                raise VPError("Nowcoder redirected outside the contest site.")
            try:
                data = response.json()
            except ValueError:
                raise VPError("Nowcoder did not return JSON. Refresh the browser login and retry.") from None
            if not isinstance(data, dict):
                raise VPError("Nowcoder returned an invalid API response.")
            if data.get("code") == 0:
                if not isinstance(data.get("data"), dict):
                    raise VPError("Nowcoder returned an invalid API data object.")
                return data["data"]
            expired = data.get("code") == 999 or data.get("code") == 1 and data.get("msg") == "请先登录"
            if expired and attempt == 0 and self.web._refresh_browser(force=True):
                self.identity()
                continue
            if expired or data.get("code") in (1125, 499):
                raise VPError("Nowcoder requires browser login or verification. Complete it on the original site, then retry; no registration was changed.")
            raise VPError("Nowcoder: " + str(data.get("msg", "request failed")))
        raise VPError("Nowcoder login could not be refreshed.")

    def details(self) -> dict:
        if not self._identified:
            self.identity()
        data = self.get_json(INFO, id=self.id)
        if not isinstance(data, dict):
            raise VPError("Nowcoder did not return contest information.")
        return data

    def contest_name(self) -> str:
        data = self.details()
        if str(data.get('contestId')) != self.id:
            raise VPError('牛客返回了其他比赛的信息，原目录保留。')
        return data.get('name', '')

    def check_own_filter(self, basic: dict) -> None:
        # An expired cookie can still get code=0 with an empty "only mine"
        # list. Such a guest response must never finalize the local history.
        if basic.get("basicUid") != self.uid:
            self._identified = False
            self.web._refresh_browser(force=True)
            raise VPError("Nowcoder did not confirm this account's private view. Refresh the Firefox login and retry.")

    def apply_metadata(self, contest: Contest, data: dict, *, require_registration: bool = True) -> None:
        if (str(data.get("contestId")) != self.id or data.get("type") not in SCORING
                or not isinstance(data.get("name"), str)):
            raise VPError("Nowcoder returned an unsupported contest identity or scoring rule.")
        if contest.extra.get("uid") is not None and contest.extra["uid"] != self.uid:
            raise VPError("The Nowcoder user ID changed. Restore this contest's account.")
        if require_registration and data.get("isTeamSignUp"):
            raise VPError("Nowcoder team contests are not supported yet.")
        if require_registration and not data.get("isSignUp"):
            raise VPError("Nowcoder did not confirm an existing registration. Join this contest on the website first.")
        if data.get("contestType") == 1:
            raise VPError("This link is a replay contest. Live mode requires the original contest registration.")
        setting = data.get("settingInfo") or {}
        if setting.get("needPassword") and not data.get("filledPassword"):
            raise VPError("Unlock this contest on Nowcoder first, then reconnect with live URL.")
        start, end = data.get("startTime"), data.get("endTime")
        if not all(type(t) in (int, float) for t in (start, end)) or start <= 0 or end <= start:
            raise VPError("Nowcoder returned an invalid official schedule.")
        server_ms = start - data["countDownTime"] if type(data.get("countDownTime")) in (int, float) else platform_now(self.url) * 1000
        phase = "BEFORE" if server_ms < start else "CODING" if server_ms < end else "FINISHED"
        frozen_ms, after_ms = float(setting.get("rankFrozenMillisecond") or 0), float(setting.get("rankEndFrozenMillisecond") or 0)
        frozen = bool(setting.get("needRankFrozen") and frozen_ms > 0 and end - frozen_ms <= server_ms <= end + after_ms)
        contest.name = data["name"]
        contest.original_start = start / 1000
        contest.rules = Rules(int((end - start) / 1000))
        contest.extra.update(mode="live", scoring=SCORING[data["type"]], phase=phase, frozen=frozen, uid=self.uid,
                             end_time=end / 1000, ranking_visible=data.get("rankingVisible", True),
                             participation_type=data.get("participationType", ""))

    def problem_list(self, contest: Contest) -> list[dict]:
        result, page, seen, labels = [], 1, set(), set()
        while True:
            data = self.get_json(PROBLEMS, id=self.id, page=page, pageSize=50)
            basic = data.get("basicInfo", {})
            if str(basic.get("contestId")) != self.id or basic.get("pageCurrent") != page:
                raise VPError("Nowcoder changed contest identity or problem pagination.")
            for problem in data.get("data", []):
                if (problem["problemId"] in seen or problem.get("index") in labels
                        or not re.fullmatch(r"[A-Z]+[0-9]*", str(problem.get("index", "")))):
                    raise VPError("Nowcoder returned duplicate or invalid problem labels.")
                seen.add(problem["problemId"])
                labels.add(problem["index"])
                result.append(problem)
            if page >= basic.get("pageCount", 0):
                break
            if not data.get("data") or page > 10000:
                raise VPError("Nowcoder returned an incomplete problem list.")
            page += 1
        if len(result) != basic.get("problemCount"):
            raise VPError("Nowcoder did not return the complete contest problem list.")
        contest.problems = {p["index"]: str(p["problemId"]) for p in result}
        return result

    def prepare(self) -> tuple[Contest, dict]:
        self.identity()
        contest = Contest("nowcoder", self.id, self.url, "Nowcoder", "Nowcoder " + self.id, {}, Rules(1))
        return contest, self.snapshot(contest)

    def snapshot(self, contest: Contest) -> dict:
        self.apply_metadata(contest, self.details())
        snapshot = {"own": None, "stats": {}, "phase": contest.extra["phase"], "frozen": contest.extra["frozen"], "at": platform_now(self.url)}
        if snapshot["phase"] == "BEFORE":
            return snapshot
        problems = self.problem_list(contest)
        if not contest.extra["ranking_visible"] or (contest.extra["scoring"] == "OI" and snapshot["phase"] != "FINISHED"):
            snapshot["message"] = "Nowcoder hides the scoreboard during this contest."
            return snapshot
        try:
            data = self.get_json(RANK, id=self.id, page=1, rankScope="ALL")
            basic = data.get("basicInfo", {})
            if snapshot["phase"] == "FINISHED" and not snapshot["frozen"]:
                if not basic.get("postContestRankSupported"):
                    snapshot["message"] = "Nowcoder cannot verify a contest-only final scoreboard; practice scores are excluded locally."
                    return snapshot
                if not basic.get("onlyContestRankApplied"):
                    data = self.get_json(RANK, id=self.id, page=1, rankScope="ALL", onlyContestRank="true")
                    basic = data.get("basicInfo", {})
                    if not basic.get("onlyContestRankApplied"):
                        raise VPError("Nowcoder did not confirm a contest-only final scoreboard.")
            if str(basic.get("contestId")) != self.id:
                raise VPError("Nowcoder returned a scoreboard for another contest.")
        except VPError as exc:
            snapshot["message"] = str(exc)
            return snapshot
        rows = [r for r in [data.get("myRankData"), *data.get("rankData", [])] if r and r.get("uid") == self.uid and not r.get("team")]
        if rows:
            raw = rows[0]
            scores = {str(p["problemId"]): p for p in raw.get("scoreList", [])}
            own = {"rank": raw["ranking"], "points": raw["acceptedCount"] if contest.extra["scoring"] == "ICPC" else raw["totalScore"],
                   "penalty": f"{raw['penaltyTime'] / 60000:g}", "party": {"members": [{"handle": str(self.uid)}], "teamName": raw.get("userName", str(self.uid))},
                   "problemResults": []}
            for pid in contest.problems.values():
                cell = scores.get(pid, {})
                points = cell.get("score", 0)
                if contest.extra["scoring"] == "ICPC":
                    text = ("+" + (str(cell.get("failedCount")) if cell.get("failedCount") else "")) if cell.get("accepted") else "−" + str(cell["failedCount"]) if cell.get("failedCount") else "—"
                else:
                    text = f"{points:g}" if cell.get("submit") else "—"
                if cell.get("waitingJudgeCount"):
                    text += " +?"
                own["problemResults"].append({"points": int(bool(cell.get("accepted"))) if contest.extra["scoring"] == "ICPC" else points,
                                              "display": text, "type": "PRELIMINARY" if cell.get("waitingJudgeCount") else "FINAL"})
            snapshot["own"] = own
        stats_source = {str(p["problemId"]): p for p in data.get("problemData", [])}
        for p in problems:
            pid = str(p["problemId"])
            stats_source[pid] = {**p, **stats_source.get(pid, {})}
        for name, field in (("submitted", "submitCount"), ("attempted", "submitPersonCount"), ("accepted", "acceptedCount")):
            if all(type(stats_source.get(pid, {}).get(field)) is int for pid in contest.problems.values()):
                snapshot["stats"][name] = {label: stats_source[pid][field] for label, pid in contest.problems.items()}
        return snapshot

    def submissions(self, contest: Contest) -> list[dict]:
        if not self._identified:
            self.identity()
        if self.uid != contest.extra.get("uid"):
            raise VPError("The Nowcoder user ID changed. Restore this contest's account.")
        result, page, seen = {}, 1, set()
        while True:
            data = self.get_json(STATUS, id=self.id, page=page, pageSize=20, onlyMyStatusFilter="true")
            basic = data.get("basicInfo", {})
            self.check_own_filter(basic)
            if str(basic.get("contestId")) != self.id or basic.get("pageCurrent") != page:
                raise VPError("Nowcoder returned inconsistent submission pagination.")
            records = data.get("data", [])
            for raw in records:
                if raw.get("userId") != self.uid or raw.get("isTeam"):
                    continue
                sid = str(raw.get("submissionId", ""))
                if not re.fullmatch(r"[0-9]+", sid) or sid in seen:
                    raise VPError("Nowcoder repeated or omitted a valid submission ID.")
                seen.add(sid)
                label = next((label for label, pid in contest.problems.items() if pid == str(raw.get("problemId"))), None)
                if label is None:
                    continue
                submitted_at = raw["submitTime"] / 1000
                if submitted_at < contest.original_start:
                    continue
                value = raw.get("statusMessage")
                verdict = VERDICTS.get(value, "HIDDEN" if value in (None, "", "--", "-") else "UNAVAILABLE")
                if (contest.extra["scoring"] != "ICPC" and verdict == "WRONG-ANSWER"
                        and type(raw.get("score")) in (int, float) and type(raw.get("fullScore")) in (int, float)
                        and 0 < raw["score"] < raw["fullScore"]):
                    verdict = "PARTIAL"
                result[sid] = {"id": sid, "team": "me", "problem": label, "time": submitted_at - contest.original_start,
                               "verdict": verdict, "points": raw.get("score"), "practice": submitted_at >= contest.extra["end_time"]}
            if page >= basic.get("pageCount", 0):
                break
            if not records or page > 10000:
                raise VPError("Nowcoder ended the submission list before its declared last page.")
            page += 1
        if basic.get("statusCount") is not None and len(seen) != basic["statusCount"]:
            raise VPError("Nowcoder did not return a complete own-submission history.")
        return list(result.values())

    def save_statements(self, contest: Contest, directory) -> None:
        for label in contest.problems:
            target = directory / (label + ".html")
            if target.exists():
                continue
            page = self.web.soup(self.url + "/" + label)
            statement = page.select_one(".subject-describe, .question-content, .question-main")
            if statement is None:
                raise VPError("Nowcoder did not expose an HTML statement. Use the original contest page.")
            target.write_text('<!doctype html><meta charset="utf-8">'
                              f'<base href="{ORIGIN}/"><title>{escape(contest.name)} — {label}</title>'
                              '<style>body{max-width:960px;margin:2em auto;font:18px/1.6 sans-serif}pre{white-space:pre-wrap}</style>'
                              + str(statement), encoding="utf8")
