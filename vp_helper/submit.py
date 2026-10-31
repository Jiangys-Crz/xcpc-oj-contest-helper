"""Dispatch by the source directory, so old contest folders stay usable."""
from __future__ import annotations

import os
from pathlib import Path
import re
import sys
import time

from .platform_clock import platform_now
from .models import VPError
from .storage import read_json
from .submit_context import submission_configuration


def main() -> None:
    args = sys.argv[1:]
    if args in ([], ["-h"], ["--help"]):
        print("Usage: submit SOURCE [-p PROBLEM] [-l LANGUAGE] [-y]\nUses this source's contest and attempt; post-contest submissions are practice.")
        return
    try:
        source = Path(args[0]).expanduser().resolve()
        config_path, config = submission_configuration(source)
        from .cloud import check_submission
        check_submission(config)
        context = config.get("vp_context", {})
        if context.get("state_path"):
            state = read_json(Path(context["state_path"]))
            if state.get("status") in ("cancelled", "failed"):
                raise VPError("This VP did not start. Use bt CONTEST_URL for practice.")
            if state.get("status") == "scheduled" or platform_now(state) < state.get("start_time", 0):
                raise VPError("This VP has not started yet. Wait for the countdown.")
        platform = config.get("platform")
        if platform in ("cf", "gym") and config.get("mode") in ("live", "practice"):
            from .live_submit import main as run
        elif platform == "cwnuoj":
            from .hydro_submit import main as run
        elif platform == "nowcoder":
            from .nowcoder_submit import main as run
        elif platform in ("qoj", "gym", "cf"):
            script = Path(sys.prefix).parent / "third_party" / ("qoj" if platform == "qoj" else "cf_submit") / "submit"
            if not script.is_file():
                raise VPError("The bundled submitter is missing; reinstall this version.")
            # Upstream CF reads contest.json from cwd. Preserve the chosen
            # source as an absolute path when entering its own contest folder.
            os.chdir(config_path.parent)
            options = ["--prob" if p == "--problem" else p for p in args[1:]]
            if platform != "qoj" and not any(p in ("-p", "--prob") or p.startswith("--prob=") for p in options):
                label = source.stem.upper()
                if not re.fullmatch(r"[A-Z][0-9]*", label):
                    raise VPError("Name the source A.cpp or specify --problem A.")
                # The upstream parser infers a problem from the full argument,
                # so absolute paths need an explicit problem index.
                options += ["-p", label]
            if sys.platform == 'win32':
                from .bootstrap import launch_python
                launch_python(sys.executable, [str(script), str(source), *options])
            elif sys.platform == 'darwin':
                os.execv(sys.executable, [sys.executable, str(script), str(source), *options])
            else:
                os.execv(str(script), [str(script), str(source), *options])
            return
        else:
            raise VPError("Unknown contest platform in this source directory.")
        run()
    except (VPError, OSError) as exc:
        print("submit: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
