"""One-shot submissions to a verified CWNUOJ contest participation."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlencode, urljoin, urlsplit

from .browser_login import refresh_submission_login
from .http import _RequestException, form_values
from .hydro import HydroLive, ORIGIN, identity_from_html
from .models import Contest, VPError
from .receipts import record_receipt, valid_submission_id
from .storage import read_json, state_directory
from .submit_context import submission_configuration


def language_id(options: dict, source: Path, requested: str | None) -> str:
    if requested:
        if requested in options:
            return requested
        matches = [key for key, label in options.items() if requested.casefold() in str(label).casefold()]
        if len(matches) == 1:
            return matches[0]
        raise VPError("Compiler name is missing or ambiguous. Use --lang with a CWNUOJ compiler ID.")
    preferred = {".cpp": ("cc.cc23o2", "cc.cc20o2", "cc.cc17o2", "cc"),
                 ".cc": ("cc.cc23o2", "cc.cc20o2", "cc.cc17o2", "cc"),
                 ".cxx": ("cc.cc23o2", "cc.cc20o2", "cc.cc17o2", "cc"), ".c": ("c",),
                 ".py": ("py.pypy3", "py.py3", "py"), ".java": ("java",),
                 ".rs": ("rs", "rust"), ".go": ("go",), ".kt": ("kt",)}
    for key in preferred.get(source.suffix.lower(), ()):
        if key in options:
            return key
    raise VPError("CWNUOJ does not offer a default compiler for this file. Specify an available ID with --lang.")


def submission_id(data: dict, location: str, prefix: str) -> str | None:
    if valid_submission_id(str(data.get("rid", "")), "cwnuoj"):
        return data["rid"]
    path = urlsplit(urljoin(ORIGIN, location or data.get("url", "")))
    match = re.fullmatch(re.escape(prefix) + r"/record/([a-f0-9]{24})/?", path.path)
    if path.scheme == "https" and path.netloc == urlsplit(ORIGIN).netloc and match:
        return match[1]
    return None


def submit(source: Path, problem: str | None = None, language: str | None = None) -> str | None:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise VPError("Source file not found: " + str(source))
    config_path, config = submission_configuration(source)
    practice_mode = config.get("mode") == "practice"
    if config.get("platform") != "cwnuoj" or config.get("mode") not in ("live", "practice"):
        raise VPError("Run submit from a CWNUOJ contest directory prepared by live URL.")
    context = config.get("vp_context", {})
    if not context.get("state_path") or Path(context["state_path"]).resolve().parent != state_directory().resolve():
        raise VPError("This submission context is outside the live state directory. Reconnect with live URL.")
    state = read_json(Path(context["state_path"]))
    if (state.get("mode") != config["mode"] or any(context.get(key) != state.get(key) for key in ("url", "handle", "start_time"))
            or state.get("url") != config.get("base_url") or state.get("handle") != config.get("handle")):
        raise VPError("The CWNUOJ account or contest context changed. Reconnect with live URL.")
    refresh_submission_login(config_path, config, "cwnuoj")
    api = HydroLive(config["base_url"], config)
    if str(config.get("contest_id")) != api.id or config.get("uid") != state.get("contest", {}).get("extra", {}).get("uid"):
        raise VPError("The CWNUOJ contest or user ID changed. Reconnect with live URL.")
    api.identity()
    contest = Contest.from_dict(state["contest"])
    api.apply_metadata(contest, api.details(), **({"require_registration": False} if practice_mode else {}))
    if practice_mode and contest.extra["phase"] != "FINISHED":
        raise VPError("This practice workspace requires a finished contest.")
    if contest.extra["phase"] == "BEFORE":
        raise VPError("The CWNUOJ contest has not started. No code was sent.")
    if not practice_mode and contest.original_start != state["start_time"]:
        raise VPError("The official start time changed. Reconnect with live URL before submitting.")
    label = (problem or source.stem).upper()
    if label not in contest.problems:
        raise VPError("This problem label is not available. Use a filename such as A.cpp or --problem A.")
    pid = contest.problems[label]
    if config.get("problem_ids", {}).get(label) != pid:
        raise VPError("CWNUOJ changed the problem list. Reconnect with live URL before submitting.")
    path = api.prefix + "/p/" + pid + "/submit"
    url = ORIGIN + path
    correction = contest.extra["phase"] == "FINISHED"
    query = {} if correction else {"tid": api.id}
    try:
        options = api.get_json(url, **query).get("langRange")
    except VPError as exc:
        raise VPError("读取 CWNUOJ " + ("补题" if correction else "比赛") + "提交页失败：" + str(exc)) from None
    if not isinstance(options, dict) or not options:
        raise VPError("CWNUOJ did not offer any submission languages for this participation.")
    page = api.web.soup(url, params=query)
    if identity_from_html(str(page))["uid"] != config["uid"]:
        raise VPError("CWNUOJ returned a submission form for another account.")
    code_field = page.select_one('textarea[name="code"]')
    form = code_field.find_parent("form") if code_field else None
    if form is None:
        raise VPError("CWNUOJ did not offer the expected source submission form.")
    target = urlsplit(urljoin(url, form.get("action") or url))
    if target.scheme != "https" or target.netloc != urlsplit(ORIGIN).netloc or target.path.rstrip("/") != path:
        raise VPError("CWNUOJ returned an unexpected submission destination.")
    payload = form_values(form)
    payload.update(lang=language_id(options, source, language), code=source.read_text(encoding="utf8"))
    if correction:
        payload.pop("tid", None)
    else:
        payload["tid"] = api.id
    payload.pop("pretest", None)
    submitted_at = time.time()
    if submitted_at < contest.original_start:
        raise VPError("The CWNUOJ contest has not started. No code was sent.")
    submit_url = url + ("?" + urlencode(query) if query else "")
    try:
        response = api.web.session.post(submit_url, data=payload,
                                        headers={"Accept": "application/json", "Origin": ORIGIN,
                                                 "Referer": submit_url},
                                        timeout=(10, 40), allow_redirects=False)
    except _RequestException:
        raise VPError("Submission outcome unknown. Check CWNUOJ records before retrying; the request was not repeated.") from None
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code not in (200, 302, 303) or not isinstance(data, dict) or data.get("error"):
        raise VPError(f"CWNUOJ 提交未获确认（HTTP {response.status_code}）。请先检查原站记录；请求不会自动重发。")
    sid = submission_id(data, response.headers.get("Location", ""), api.prefix)
    if sid is None:
        if data.get("tid") == api.id and response.status_code == 200:
            print("CWNUOJ accepted the submission; this contest hides judging records. Check the original site before retrying.")
            return None
        raise VPError("CWNUOJ did not expose a new submission ID. Check the original records before retrying; the request was not repeated.")
    # Confirm the assignment without reading or printing anyone's source code.
    try:
        record = api.get_json(ORIGIN + api.prefix + "/record/" + sid).get("rdoc", {})
    except VPError as exc:
        raise VPError("提交已返回编号 #" + sid + "，但读取判定详情失败：" + str(exc) +
                      "。请先查看原站记录；代码不会自动重发。") from None
    if (record.get("_id") != sid or record.get("uid") != config["uid"] or str(record.get("pid")) != pid
            or record.get("contest") not in ((None, "") if correction else (api.id,))
            or record.get("domainId", api.domain) != api.domain):
        raise VPError("The returned record does not match this submission. Check CWNUOJ before retrying.")
    print("Submission #" + sid + ": PENDING", flush=True)
    try:
        record_receipt(config, sid, label, submitted_at)
    except (VPError, OSError) as exc:
        print("Submitted; the panel will recover it from CWNUOJ. Notification: " + str(exc))
    return sid


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit to an existing CWNUOJ contest participation")
    parser.add_argument("source", type=Path)
    parser.add_argument("-p", "--problem")
    parser.add_argument("-l", "--lang", help="CWNUOJ compiler ID, e.g. cc.cc20o2 or py.pypy3")
    args = parser.parse_args()
    try:
        submit(args.source, args.problem, args.lang)
    except (VPError, OSError, UnicodeError) as exc:
        print("submit: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
