"""Submit one explicitly selected source file to an existing live participation."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
import time
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .browser_login import refresh_submission_login
from .codeforces_live import CodeforcesLive, verify_login, verify_practice
from .http import _RequestException, form_values
from .models import VPError
from .receipts import cf_submission_id, cf_submission_ids, record_receipt
from .storage import read_json, state_directory
from .submit_context import submission_configuration
from .submission_confirmation import confirm_submission


def compiler_id(form, source: Path, requested: str | None) -> str:
    options = {o["value"]: o.get_text(" ", strip=True)
               for o in form.select('select[name="programTypeId"] option[value]') if o["value"]}
    if requested:
        if requested in options:
            return requested
        matches = [value for value, label in options.items() if requested.lower() in label.lower()]
        if len(matches) == 1:
            return matches[0]
        raise VPError("Compiler name is missing or ambiguous; specify its numeric ID with --lang.")
    prefixes = {".cpp": ("GNU G++23", "GNU G++20", "GNU G++17"), ".cc": ("GNU G++23", "GNU G++20", "GNU G++17"),
                ".cxx": ("GNU G++23", "GNU G++20", "GNU G++17"), ".c": ("GNU GCC",),
                ".py": ("PyPy 3", "Python 3"), ".java": ("Java 21", "Java 17", "Java"),
                ".rs": ("Rust",), ".go": ("Go",), ".kt": ("Kotlin",)}
    for prefix in prefixes.get(source.suffix.lower(), ()):
        for value, label in options.items():
            if label.startswith(prefix):
                return value
    raise VPError("No compatible compiler was found; use --lang with a compiler offered by this contest.")


def submit(source: Path, problem: str | None = None, language: str | None = None) -> str | None:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise VPError("Source file not found: " + str(source))
    config_path, config = submission_configuration(source)
    practice_mode = config.get("mode") == "practice"
    if config.get("platform") not in ("cf", "gym") or config.get("mode") not in ("live", "practice"):
        raise VPError("Run submit from a Codeforces live contest directory prepared by live URL.")
    api = CodeforcesLive(config["base_url"], config, **({"allow_gym": True} if practice_mode else {}))
    if str(config.get("contest_id")) != api.id:
        raise VPError("The submission configuration contains conflicting contest IDs.")
    label = (problem or source.stem).upper()
    if not re.fullmatch(r"[A-Z][0-9]*", label):
        raise VPError("Use a filename such as A.cpp or specify the problem with --problem A.")
    refresh_submission_login(config_path, config, "cf")
    api = CodeforcesLive(config["base_url"], config, **({"allow_gym": True} if practice_mode else {}))
    context = config.get("vp_context", {})
    if not context.get("state_path") or Path(context["state_path"]).resolve().parent != state_directory().resolve():
        raise VPError("This submission context is outside the live state directory. Reconnect with live URL.")
    state = read_json(Path(context["state_path"]))
    if (state.get("mode") != config["mode"] or state.get("url") != api.url
            or state.get("handle", "").lower() != api.handle.lower()):
        raise VPError("This directory no longer has a valid live participation; run live URL for this contest again.")
    # After coding ends the original form becomes a practice form.
    snapshot = api.api("contest.standings", contestId=api.id)
    raw = snapshot["contest"]
    if practice_mode and raw.get("phase") != "FINISHED":
        raise VPError("This practice workspace requires a finished contest.")
    if practice_mode:
        verify_practice(api, raw["durationSeconds"])
    if (str(raw.get("id")) != api.id or raw.get("phase") == "BEFORE" or
            not raw.get("startTimeSeconds") or
            time.time() < raw["startTimeSeconds"]
            or raw.get("phase") != "CODING" and time.time() < raw["startTimeSeconds"] + raw["durationSeconds"]):
        raise VPError("The coding phase has not started or practice is not open. No code was submitted.")
    if label not in {p["index"] for p in snapshot["problems"]}:
        raise VPError("This problem is not available in the current contest.")
    # Validate context before the irreversible POST, even if no receipt can be
    # recovered afterwards. Account/contest conflicts must not be discovered late.
    if (context.get("url") != state.get("url") or context.get("handle") != state.get("handle")
            or context.get("start_time") != state.get("start_time")
            or not practice_mode and state["start_time"] != raw["startTimeSeconds"]):
        raise VPError("The official start time changed; reconnect with live URL before submitting.")
    previous_page = api.web.soup(api.url + "/my", params={"locale": "en"})
    verify_login(previous_page, api.handle)
    previous = cf_submission_ids(str(previous_page))
    page = api.web.soup(api.url + "/submit", params={"locale": "en"})
    verify_login(page, api.handle)
    form = page.select_one("form.submit-form")
    if form is None:
        raise VPError("Codeforces did not offer a submission form for this participation.")
    payload = form_values(form)
    if not payload.get("csrf_token"):
        raise VPError("The Codeforces submission form has no CSRF token.")
    payload.update(submittedProblemIndex=label, programTypeId=compiler_id(form, source, language),
                   source=source.read_text(encoding="utf8"))
    target = urljoin(api.url + "/submit", form.get("action") or api.url + "/submit")
    base_path = urlsplit(api.url).path.rstrip("/")
    if (urlsplit(target).scheme != "https" or urlsplit(target).netloc != "codeforces.com"
            or urlsplit(target).path.rstrip("/") != base_path + "/submit"):
        raise VPError("The platform returned an unexpected submission destination.")
    language_name = form.select_one(f'select[name="programTypeId"] option[value="{payload["programTypeId"]}"]').get_text(' ', strip=True)
    if not confirm_submission(source, config, label, language_name, target, config_path):
        return None
    submitted_at = time.time()
    try:
        # A POST is sent once. A timeout can mean the server accepted it.
        response = api.web.session.post(target, data=payload, timeout=(10, 40))
    except _RequestException:
        raise VPError("Submission outcome unknown. Check Codeforces My submissions before retrying; the request was not repeated.") from None
    if (response.status_code != 200 or urlsplit(response.url).netloc != "codeforces.com"
            or urlsplit(response.url).path.rstrip("/") != base_path + "/my"):
        soup = BeautifulSoup(response.text, "html.parser")
        error = soup.select_one(".error, .error-message")
        detail = error.get_text(" ", strip=True) if error else f"HTTP {response.status_code}; check My submissions before retrying"
        raise VPError("Codeforces did not confirm this submission: " + detail)
    sid = cf_submission_id(BeautifulSoup(response.text, "html.parser"), previous, api.id, label, api.handle)
    if sid is None:
        print("Codeforces returned My submissions; the new ID is not yet identifiable. Check the live panel before retrying.")
        return None
    print("Submission #" + sid + ": PENDING", flush=True)
    try:
        record_receipt(config, sid, label, submitted_at)
    except (VPError, OSError) as exc:
        print("Submitted; the panel will recover it from Codeforces. Notification: " + str(exc))
    return sid


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit to an existing Codeforces live participation")
    parser.add_argument("source", type=Path)
    parser.add_argument("-p", "--problem")
    parser.add_argument("-l", "--lang", help="compiler ID or an unambiguous compiler name")
    args = parser.parse_args()
    try:
        submit(args.source, args.problem, args.lang)
    except (VPError, OSError, UnicodeError) as exc:
        print("submit: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
