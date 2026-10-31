"""One-shot Nowcoder submissions using the original contest page's form fields.

Protocol reference: Handy2233/vscode-nowcoderac src/services/nowcoderService.ts
and Nowcoder's own acm lib/env.js. Authentication stays in browser_login.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

from .portable import login_browser
from .browser_login import refresh_submission_login
from .http import _RequestException
from .platform_clock import platform_now
from .models import Contest, VPError
from .nowcoder import NowcoderLive, ORIGIN, identity_from_html, problem_config
from .receipts import record_receipt
from .storage import read_json, state_directory
from .submit_context import submission_configuration
from .submission_confirmation import confirm_submission
from .submission_results import print_judging_link
from .session_cookies import honor_judge_response_cookies

# Standard input/output judge (codeJudgeType 0). These IDs and names are also
# present in the site's editor. Other judge types need their own templates.
LANGUAGES = {"2": "C++（clang++18）", "38": "C++(g++ 13)", "39": "C(gcc 10)",
             "1": "C", "4": "Java", "11": "Python3", "25": "pypy3",
             "17": "Go", "27": "Rust", "29": "Kotlin"}


def language_id(source: Path, requested: str | None) -> str:
    if requested:
        if requested in LANGUAGES:
            return requested
        aliases = {"cpp": "2", "clang": "2", "gcc": "38", "g++": "38", "python3": "11", "pypy3": "25"}
        selected = aliases.get(requested.casefold())
        matches = [key for key, label in LANGUAGES.items() if label.casefold() == requested.casefold()]
        if selected or len(matches) == 1:
            return selected or matches[0]
        raise VPError("Unknown Nowcoder compiler. Use --lang with a supported compiler ID.")
    selected = {".cpp": "2", ".cc": "2", ".cxx": "2", ".c": "1", ".py": "11", ".java": "4",
                ".go": "17", ".rs": "27", ".kt": "29"}.get(source.suffix.lower())
    if not selected:
        raise VPError("No default Nowcoder compiler for this file. Specify --lang.")
    return selected


def submit(source: Path, problem: str | None = None, language: str | None = None, *, assume_yes: bool = False) -> str | None:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise VPError("Source file not found: " + str(source))
    config_path, config = submission_configuration(source)
    practice_mode = config.get("mode") == "practice"
    if config.get("platform") != "nowcoder" or config.get("mode") not in ("live", "practice"):
        raise VPError("Run submit from a Nowcoder contest directory prepared by live URL.")
    context = config.get("vp_context", {})
    if not context.get("state_path") or Path(context["state_path"]).resolve().parent != state_directory().resolve():
        raise VPError("This submission context is outside the live state directory. Reconnect with live URL.")
    state = read_json(Path(context["state_path"]))
    if (state.get("mode") != config["mode"] or any(context.get(key) != state.get(key) for key in ("url", "handle", "start_time"))
            or state.get("url") != config.get("base_url") or state.get("handle") != config.get("handle")):
        raise VPError("The Nowcoder account or contest context changed. Reconnect with live URL.")
    refresh_submission_login(config_path, config, "nowcoder")
    api = NowcoderLive(config["base_url"], config)
    if str(config.get("contest_id")) != api.id or config.get("uid") != state.get("contest", {}).get("extra", {}).get("uid"):
        raise VPError("The Nowcoder contest or user ID changed. Reconnect with live URL.")
    api.identity()
    contest = Contest.from_dict(state["contest"])
    api.apply_metadata(contest, api.details(), **({"require_registration": False} if practice_mode else {}))
    if practice_mode and contest.extra["phase"] != "FINISHED":
        raise VPError("This practice workspace requires a finished contest.")
    if contest.extra["phase"] == "BEFORE":
        raise VPError("The Nowcoder contest has not started. No code was sent.")
    if not practice_mode and contest.original_start != state["start_time"]:
        raise VPError("The official start time changed. Reconnect with live URL before submitting.")
    label = (problem or source.stem).upper()
    # Refresh the authoritative mapping before reading the problem's submit IDs.
    api.problem_list(contest)
    if label not in contest.problems:
        raise VPError("This problem label is not available. Use a filename such as A.cpp or --problem A.")
    pid = contest.problems[label]
    if config.get("problem_ids", {}).get(label) != pid:
        raise VPError("Nowcoder changed the problem list. Reconnect with live URL before submitting.")
    url = api.url + "/" + label
    page = api.web.get(url)
    if urlsplit(page.url)._replace(query="", fragment="").geturl().rstrip("/") != url:
        raise VPError("Nowcoder returned an unexpected problem page.")
    if identity_from_html(page.text)["uid"] != config["uid"]:
        raise VPError("Nowcoder returned a problem page for another account.")
    fields = problem_config(page.text)
    if (fields.get("contestId") != api.id or fields.get("problemId") != pid
            or fields.get("doneQuestionId") != api.id
            or any(not re.fullmatch(r"[0-9]+", fields.get(key, "")) for key in ("questionId", "tagId", "subTagId"))):
        raise VPError("The Nowcoder submission fields do not match this problem and contest.")
    if fields.get("codeJudgeType") != "0":
        raise VPError("This Nowcoder judge requires a special code template. Submit on the original site.")
    lang = language_id(source, language)
    payload = {key: fields[key] for key in ("questionId", "tagId", "subTagId", "doneQuestionId")}
    payload.update(content=source.read_text(encoding="utf8"), language=lang, languageName=LANGUAGES[lang])
    if not confirm_submission(source, config, label, LANGUAGES[lang], url, config_path,
                              **({'assume_yes': True} if assume_yes else {})):
        return None
    submitted_at = platform_now(api.url)
    if submitted_at < contest.original_start:
        raise VPError("The Nowcoder contest has not started. No code was sent.")
    try:
        response = api.web.session.post(ORIGIN + "/nccommon/submit_cd", data=payload,
                                       headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                                                "Accept": "application/json", "Origin": ORIGIN, "Referer": url,
                                                "X-Requested-With": "XMLHttpRequest"},
                                       timeout=(10, 40), allow_redirects=False)
    except _RequestException:
        print_judging_link(api.url)
        raise VPError("Submission outcome unknown. Check Nowcoder records before retrying; the request was not repeated.") from None
    honor_judge_response_cookies(api.web.session, response)
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code != 200 or not isinstance(data, dict):
        print_judging_link(api.url)
        raise VPError("Nowcoder did not confirm the submission. Check original records before retrying.")
    if data.get("code") != 0:
        print_judging_link(api.url)
        if data.get("code") in (999, 1125, 499) or data.get("msg") == "请先登录":
            raise VPError(f"Nowcoder requires browser login or verification. Complete it in {login_browser()}, then check records before retrying.")
        raise VPError(f"Nowcoder rejected the submission (code {data.get('code')}). Check the result link before retrying.")
    # The reference client receives a numeric data value. Some site versions
    # wrap the same ID; never infer success merely from code=0 or local time.
    value = data.get("data")
    sid = str(value.get("submissionId", "") if isinstance(value, dict) else value)
    if not re.fullmatch(r"[1-9][0-9]*", sid):
        print_judging_link(api.url)
        raise VPError("Nowcoder did not expose a new submission ID. Check original records before retrying; the request was not repeated.")
    print("Submission #" + sid + ": PENDING", flush=True)
    print_judging_link(api.url, sid)
    try:
        record_receipt(config, sid, label, submitted_at)
    except (VPError, OSError) as exc:
        print("Submitted; the panel will recover it from Nowcoder. Notification: " + str(exc))
    return sid


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit to an existing Nowcoder contest participation")
    parser.add_argument("source", type=Path)
    parser.add_argument("-p", "--problem")
    parser.add_argument("-y", "--yes", action="store_true", help="confirm the submission without prompting")
    parser.add_argument("-l", "--lang", help="Nowcoder compiler ID: 2 clang++, 38 g++, 11 Python3, 25 PyPy3, 4 Java")
    args = parser.parse_args()
    try:
        submit(args.source, args.problem, args.lang, **({'assume_yes': True} if args.yes else {}))
    except (VPError, OSError, UnicodeError) as exc:
        print("submit: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
