#!/usr/bin/env python3
"""Repair installed VP recovery and anonymous CCPC counts without reinstalling."""
from __future__ import annotations

from datetime import datetime
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile


OLD = '            if old.get("url") == canonical and tmux_exists(old.get("tmux_session", "")):'
NEW = '            if old.get("url") == canonical:'
STATISTICS_REPLACEMENTS = (
    ('        # Unknown columns are grouped, not left at A..M positions. Their values\n'
     '        # are also hidden so the column order cannot expose the problem mapping.\n'
     '        columns = ordered + [None] * (len(self.contest.problems) - len(ordered))',
     '        # Keep anonymous counts together, ordered by public activity rather than\n'
     '        # A..M, so their positions do not expose the hidden problem labels.\n'
     '        hidden = [p for p in self.contest.problems if p not in visible]\n'
     '        if self.stats:\n'
     '            hidden.sort(key=lambda p: tuple(self.stats[key][p] for key in\n'
     '                                            ("accepted", "attempted", "submitted")), reverse=True)\n'
     '        columns = ordered + hidden'),
    ('            table.add_column(problem or "?", justify="center")',
     '            table.add_column(problem if problem in visible else "?", justify="center", ratio=1,\n'
     '                             min_width=1, no_wrap=False, overflow="fold")'),
    ('            table.add_row(key, *("?" if p is None else str(self.stats[key][p]) if self.stats else "—" for p in columns))',
     '            table.add_row(key, *(str(self.stats[key][p]) if self.stats else "—" for p in columns))'),
)


def updated_source(path: Path, original: bytes, replacements: tuple) -> str:
    source = original.decode("utf8")
    if replacements == STATISTICS_REPLACEMENTS:
        # Accept the previous repaired layout as well as the original release.
        for previous_column in (
            '            table.add_column(problem if problem in visible else "?", justify="center")',
            '            table.add_column(problem if problem in visible else "?", justify="center", ratio=1)',
            STATISTICS_REPLACEMENTS[1][1].replace(', ratio=1', ''),
        ):
            source = source.replace(previous_column + '\n', STATISTICS_REPLACEMENTS[1][1] + '\n')
    for old, new in replacements:
        if old not in source:
            if "\n" + new + "\n" in "\n" + source + "\n":
                continue
            raise RuntimeError(f"Unrecognized code in {path}; no files were changed.")
        if source.count(old) != 1:
            raise RuntimeError(f"Ambiguous code in {path}; no files were changed.")
        source = source.replace(old, new)
    compile(source, str(path), "exec")
    return source


def repair(path: Path, replacements: tuple = ((OLD, NEW),)) -> Path | None:
    original = path.read_bytes()
    fixed = updated_source(path, original, replacements)
    if fixed.encode("utf8") == original:
        print(f"Already repaired: {path}")
        return None
    backup = path.with_name(path.name + ".before-vp-fix-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    shutil.copy2(path, backup)
    fd, temporary = tempfile.mkstemp(prefix=".vp-resume-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(fixed.encode("utf8"))
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777)
        if path.read_bytes() != original:
            raise RuntimeError("Source changed during repair; retry after other updates finish.")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"Repaired: {path}\nBackup: {backup}")
    return backup


def main() -> None:
    spec = importlib.util.find_spec("vp_helper.cli")
    if spec is None or spec.origin is None:
        raise RuntimeError("VP Helper was not found. Run this script with the Python used by vp.")
    cli_path = Path(spec.origin).resolve()
    targets = ((cli_path, ((OLD, NEW),)), (cli_path.with_name("tui.py"), STATISTICS_REPLACEMENTS))
    # Check both installed files before changing either one.
    for path, replacements in targets:
        updated_source(path, path.read_bytes(), replacements)
    for path, replacements in targets:
        repair(path, replacements)
    print("Repairs complete. Run: vp qoj 4538")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ImportError, SyntaxError, UnicodeError) as error:
        raise SystemExit(f"Repair failed: {error}") from None
