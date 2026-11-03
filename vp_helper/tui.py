from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import re

from rich import box
from rich.cells import cell_len, set_cell_size
from rich.console import Console, Group
from rich.measure import Measurement
from rich.padding import Padding
from rich.segment import Segment
from rich.style import Style
from rich.table import Table
from rich.text import Text

from .models import Cell, Contest, Rules, Submission, TeamScore
from .scheduling import user_zone
from .scoring import NON_ATTEMPT_VERDICTS, UNRESOLVED_VERDICTS


PRACTICE_ACCEPTED = "white on #8b5cf6"
PENDING_STYLE = "white on #2563eb"
SCORE_BOX = box.Box("    \n    \n ── \n    \n ── \n ── \n    \n    \n")


@dataclass
class ProblemBox:
    """Keep every problem's colored area the same size within a score row."""
    value: Text
    width: int
    height: int
    content_height: int = 0
    details_below: bool = False
    detail_width: int = 0

    @property
    def plain(self):
        return self.value.plain

    def __rich_measure__(self, console, options):
        return Measurement(self.width, self.width)

    def __rich_console__(self, console, options):
        text_width = (self.detail_width or self.width) if self.details_below else self.width
        lines = self.value.wrap(console, text_width, overflow="fold", no_wrap=False)
        content = []
        text_margin = max(0, options.max_width - text_width)
        for line in lines:
            remaining = text_width - cell_len(line.plain)
            left = (remaining + text_margin % 2) // 2
            content.append(" " * left + line.plain + " " * (remaining - left))
        # Reserve the same content band for every cell so a one-line pending
        # result aligns with the attempts line of a two-line accepted result.
        content.extend([" " * text_width] * max(0, self.content_height - len(content)))
        if self.details_below:
            body = [" " * self.width] * self.height
        else:
            extra = max(0, self.height - len(content))
            body = ([" " * self.width] * (extra // 2) + content
                    + [" " * self.width] * (extra - extra // 2))
        margin = max(0, options.max_width - self.width)
        style = console.get_style(self.value.style)
        for line in body:
            # Text's automatic justification extends its background into the
            # extra character assigned to some columns. Explicit plain margins
            # preserve equal colored widths when division has a remainder.
            yield Segment(" " * (margin // 2))
            # A single character column cannot contain a whole-cell square
            # background. Its square glyph keeps the state marker square too.
            if self.width == 1 and self.height == 1 and self.details_below:
                colored = style.bgcolor is not None and not style.bgcolor.is_default
                yield Segment("■" if colored else " ", Style(color=style.bgcolor) if colored else None)
            else:
                yield Segment(line, style)
            yield Segment(" " * (margin - margin // 2))
            yield Segment.line()
        if self.details_below:
            for line in content:
                yield Segment(" " * (text_margin // 2) + line
                              + " " * (text_margin - text_margin // 2))
                yield Segment.line()


def score_table(columns, problems, rows, width, *, separate_summary=False, _allow_split=True,
                _allow_viewport=True, _minimum_problem_width=1, _minimum_box_height=0):
    problems = list(problems)

    def content_width(value):
        text = value if isinstance(value, Text) else Text.from_markup(str(value))
        return max((cell_len(line) for line in text.plain.splitlines()), default=0)

    minimum = [content_width(label) for label in columns]
    sizes = [max([minimum[index]] + [content_width(row[index]) for row in rows])
             for index in range(len(columns))]
    if "team" in columns:
        index = columns.index("team")
        sizes[index] = min(sizes[index], max(minimum[index], 24))
    count = len(columns) + len(problems)
    # Every problem header stays visible on one row. Full attempt wording is
    # retained when it fits; narrow cells keep exact counts and fold their
    # native time/score beneath them without hiding another problem.
    first_cells = rows[0][len(columns):] if rows else []
    problem_minimum = max([_minimum_problem_width, *[content_width(label) for label in problems]])
    full_problem_width = max([problem_minimum, *[content_width(cell) for row in rows
                                                for cell in row[len(columns):]]])
    problem_space = len(problems) * problem_minimum
    active = _minimum_box_height > 0 or any(
        (cell.plain if isinstance(cell, Text) else str(cell)).strip() not in ("", "—")
        for cell in first_cells)
    readable_width = sum(sizes) + len(problems) * full_problem_width + max(0, count - 1)
    if _allow_split and columns and problems and rows and readable_width > width:
        # Stack only the presentation. The first block retains every native
        # summary value; the second gets the full pane for the problem cells.
        metadata = score_table(columns, [], [rows[0][:len(columns)]], width, _allow_split=False)
        if isinstance(metadata, Table):
            metadata.expand = True
            if "rank" in columns:
                rank = metadata.columns[columns.index("rank")]
                rank.width = (rank.width or minimum[columns.index("rank")]) + 2
            if "team" in columns:
                team = metadata.columns[columns.index("team")]
                team.width = None
                team.ratio = 1
                team.min_width = minimum[columns.index("team")]
        summaries = rows[1:]
        problem_rows = [[*([""] if summaries else []), *first_cells]]
        for row in summaries:
            labels = [(cell.plain if isinstance(cell, Text) else str(cell)).strip()
                      for cell in row[:len(columns)]]
            problem_rows.append([" ".join(label for label in labels if label), *row[len(columns):]])
        problem_table = score_table([""] if summaries else [], problems, problem_rows, width,
                                    separate_summary=separate_summary, _allow_split=False)
        return Group(metadata, Text(""), problem_table)
    padding = 1 if sum(sizes) + len(problems) * full_problem_width + 2 * count <= width else 0
    available = width - problem_space - (2 * count if padding else max(0, count - 1))
    if available < sum(minimum):
        required = sum(minimum) + problem_space + max(0, count - 1)
        return Text(f"Enlarge the score pane to at least {required} columns to keep all headers on one row.",
                    style="#555555", overflow="fold")
    excess = max(0, sum(sizes) - available)
    order = sorted(range(len(columns)), key=lambda index: columns[index] != "team")
    for index in order:
        reduction = min(excess, sizes[index] - minimum[index])
        sizes[index] -= reduction
        excess -= reduction
    table = Table(box=SCORE_BOX, padding=(0, padding), style="default",
                  header_style="bold black on #eeeeee", border_style="#bbbbbb",
                  show_edge=False, expand=bool(problems), collapse_padding=True)
    for label, size in zip(columns, sizes):
        table.add_column(label, width=size, justify="center", no_wrap=False, overflow="fold")
    if padding and columns:
        # Account for Rich versions that draw, but do not measure, the leading
        # collapsed padding of the first fixed column.
        table.columns[0].width += max(0, 2 * padding - table._get_padding_width(0))
    for label in problems:
        table.add_column(label, justify="center", ratio=1, min_width=problem_minimum, overflow="fold")
    if active and problems:
        layout = Console(width=width, height=100, legacy_windows=False, safe_box=False)
        widths = table._calculate_column_widths(layout, layout.options.update_width(width - table._extra_width))
        problem_width = min(widths[index] - table._get_padding_width(index)
                            for index in range(len(columns), count))
        # The square rounds odd column widths down. Compact against its actual
        # width so "1 try" cannot fit the column but wrap inside a 4-cell box,
        # pushing otherwise short attempts and minutes below every square.
        box_width = max(1, min(16, problem_width // 2 * 2))
        height = max(1, box_width // 2)
        values = [cell if isinstance(cell, Text) else Text(str(cell)) for cell in first_cells]
        for index, value in enumerate(values):
            if content_width(value) > box_width:
                lines = value.plain.split("\n")
                lines[0] = re.sub(r"\s+tr(?:y|ies)\b", "", lines[0]).replace(" ", "")
                lines[0] = re.sub(r"pending\b", "?", lines[0])
                values[index] = Text("\n".join(lines), style=value.style)
        # One terminal row is about twice one character column's width. Use
        # exact 2:1 dimensions rather than rounding an odd width or stretching
        # the background to accommodate the text. Narrow cells put the full
        # count and time below the square, keeping every problem visible.
        content_height = max(len(value.wrap(layout, box_width, overflow="fold", no_wrap=False))
                             for value in values)
        details_below = content_height > height or box_width == 1
        if details_below:
            content_height = max(len(value.wrap(layout, problem_width, overflow="fold", no_wrap=False))
                                 for value in values)
        rows = [[*rows[0][:len(columns)], *(ProblemBox(value, box_width, height, content_height,
                                                       details_below, problem_width) for value in values)],
                *rows[1:]]
    for index, row in enumerate(rows):
        table.add_row(*row, end_section=separate_summary and index == 0)
    return table


@dataclass
class ProblemViewport:
    """One complete problem matrix, displayed through a cell-safe viewport."""
    table: Table
    natural_width: int
    offset: int = 0
    column_starts: tuple[int, ...] = ()
    problem_width: int = 0
    problem_labels: tuple[str, ...] = ()

    def visible_count(self, width):
        if not self.column_starts:
            return 0
        available = width - self.column_starts[0]
        return min(len(self.column_starts), max(0, (available + 1) // (self.problem_width + 1)))

    def max_offset(self, width):
        if self.column_starts:
            visible = self.visible_count(width)
            return max(0, len(self.column_starts) - visible) if visible else 0
        return max(0, self.natural_width - width)

    @staticmethod
    def slice_line(line, start, end):
        parts = list(Segment.divide(line, [start, end]))
        return parts[1] if len(parts) > 1 else []

    def __rich_measure__(self, console, options):
        return Measurement(options.max_width, options.max_width)

    def __rich_console__(self, console, options):
        width = options.max_width
        offset = min(max(0, self.offset), self.max_offset(width))
        full_options = options.update(width=self.natural_width, height=None)
        if self.column_starts:
            visible = self.visible_count(width)
            if not visible:
                required = self.column_starts[0] + self.problem_width
                yield Text(f"Enlarge the score pane to at least {required} columns for a complete problem cell.",
                           style="#777777", overflow="fold")
                return
            prefix = self.column_starts[0]
            start = self.column_starts[offset]
            end = start + visible * (self.problem_width + 1) - 1
            for line in console.render_lines(self.table, full_options, pad=True):
                yield from self.slice_line(line, 0, prefix)
                yield from self.slice_line(line, start, end)
                yield Segment(" " * max(0, width - prefix - (end - start)))
                yield Segment.line()
            hint = f"Problems {offset + 1}–{offset + visible} / {len(self.column_starts)} · ←/→ in dashboard"
        else:
            for line in console.render_lines(self.table, full_options, pad=True):
                # Rich replaces a sliced half of a wide Unicode character with
                # a space, preserving terminal positions and segment styles.
                yield from self.slice_line(line, offset, offset + width)
                yield Segment.line()
            hint = f"Problems {offset + 1}–{min(offset + width, self.natural_width)} / {self.natural_width} columns · ←/→ in dashboard"
        yield Text(hint, style="#777777", overflow="fold")


def problem_viewports(renderable):
    if isinstance(renderable, ProblemViewport):
        yield renderable
    elif isinstance(renderable, Group):
        for item in renderable.renderables:
            yield from problem_viewports(item)


def participant_text(title: str, subtitle: str, fallback: str) -> Text:
    return Text(title + ('\n' + subtitle if subtitle else '') if title else fallback,
                overflow='fold', no_wrap=False)


def clock(value: float) -> str:
    value = max(0, int(value))
    return f"{value // 3600:02d}:{value // 60 % 60:02d}:{value % 60:02d}"


def recorded_cells(contest: Contest, records: list[dict], *, practice: bool, all_attempts: bool = False):
    from .scoring import score_practice_submissions, score_submissions
    events = [Submission(str(r['id']), 'me', r['problem'], r['time'], r['verdict'],
                         score=r.get('score') if r.get('score') is not None else r.get('points'))
              for r in records if bool(r.get('practice')) == practice]
    duration = max([1] + [int(r.time) + 1 for r in events])
    if practice or all_attempts:
        return score_practice_submissions(events, list(contest.problems), duration).cells
    rules = Rules(duration, penalty_minutes=contest.rules.penalty_minutes,
                  compile_penalty=contest.rules.compile_penalty)
    return score_submissions(events, list(contest.problems), rules, duration,
                             scoring=contest.extra.get('scoring', 'ICPC'),
                             problem_max_scores=contest.extra.get('problem_max_scores')).cells


def practice_cell(original: Cell, correction: Cell, attempts: Cell) -> tuple[Cell, bool]:
    """Combine display counts without changing a saved competition score."""
    green = original.solved or attempts.solved
    cell = replace(original, tries=max(original.tries, attempts.tries) + correction.tries,
                   pending=max(original.pending, attempts.pending) + correction.pending,
                   unresolved=attempts.unresolved + correction.unresolved,
                   solved=green or correction.solved, solve_time=None, score=None)
    return cell, correction.solved and not green


def attempt_text(cell: Cell, *, practice: bool = False) -> str:
    judged = cell.tries
    if cell.pending:
        return f"{judged} + {cell.pending} tries"
    if cell.unresolved and not practice:
        return f"{cell.tries}+?" if cell.tries else "?"
    return f"{cell.tries} {'try' if cell.tries == 1 else 'tries'}" if cell.tries else "—"


@dataclass
class Dashboard:
    contest: Contest
    elapsed: float
    me: TeamScore
    rank: str
    prize: str
    stats: dict | None
    submissions: list[dict]
    message: str = ""
    revealed: frozenset[str] | None = None
    practice: bool = False
    start_time: float | None = None
    participant_title: str = ''
    participant_subtitle: str = ''

    def fixed(self, width: int):
        # Equal side columns keep the contest title at the screen's center.
        sides = min(30, max(19, (width - 8) // 3))
        prize = {"Gold": "Gold Prize", "Silver": "Silver Prize", "Bronze": "Bronze Prize", "Unknown": "Prize unavailable"}.get(self.prize, "")
        if self.prize == "None" and self.contest.medals is not None:
            prize = "No Prize"
        result = str(self.rank).lstrip('=') + (f"\n{prize}" if prize else "")
        timer = "Starts in " + clock(-self.elapsed) if self.elapsed < 0 else f"{clock(min(self.elapsed, self.contest.rules.duration))} / {clock(self.contest.rules.duration)}"
        if self.practice:
            timer = ""
        from .workspaces import display_name
        label = display_name(self.contest) + (f" · VP #{self.contest.extra['attempt']}" if self.contest.extra.get("attempt") else "")
        if self.practice:
            label += " · BT"
        if self.contest.extra.get("local_attempt") and not self.practice:
            label += " · local timer"
        heading = Table.grid(expand=True, padding=0)
        heading.style = "default"
        if self.practice or cell_len(label) > width - 2 * sides:
            heading.add_column(ratio=1, justify="center", no_wrap=False, overflow="fold")
            heading.add_row(Text(label, style="bold"))
            if timer:
                heading = Group(heading, Text(" " + timer, style="bold default"))
        else:
            heading.add_column(width=sides, no_wrap=False, overflow="fold")
            heading.add_column(ratio=1, justify="center", no_wrap=False, overflow="fold")
            heading.add_column(width=sides, justify="right", no_wrap=True)
            heading.add_row(Text(" " + timer, style="bold default"), Text(label, style="bold"), Text(""))
        cells = []
        corrections = recorded_cells(self.contest, self.submissions, practice=True)
        originals = recorded_cells(self.contest, self.submissions, practice=False, all_attempts=True) if self.practice else {}
        scoring = self.me.scoring
        saved_originals = recorded_cells(self.contest, self.submissions, practice=False) if self.practice else {}
        for label in self.contest.problems:
            c = self.me.cells[label]
            original_time = c.solve_time
            correction = corrections[label]
            if self.practice:
                # A standalone BT has no original competition cells.
                original = Cell() if self.contest.extra.get('practice') else c
                original_time = (original.solve_time if original.solve_time is not None else
                                 saved_originals[label].solve_time if original.solved else None)
                c, blue = practice_cell(original, correction, originals[label])
            else:
                blue = correction.solved and not c.solved
            if blue and not self.practice:
                c = correction
            tries = attempt_text(c, practice=self.practice)
            detail = (f"\n{original_time}" if self.practice and original_time is not None else
                      "" if self.practice else f"\n{c.score:g}" if scoring != 'ICPC' and c.score is not None
                      else f"\n{c.solve_time}" if c.solved and c.solve_time is not None else "")
            if c.solved:
                cells.append(Text(tries + detail, style=PRACTICE_ACCEPTED if blue else "black on #60e760"))
            elif c.pending:
                cells.append(Text(tries, style=PENDING_STYLE))
            elif not self.practice and c.score:
                cells.append(Text(tries + detail, style="black on #f1db71"))
            elif c.tries:
                cells.append(Text(tries + detail, style="black on #e87272"))
            else:
                cells.append(Text(tries))
        columns = ["rank", "team"]
        values = [Text(result, style="bold default"),
                  participant_text(self.participant_title, self.participant_subtitle, self.me.name)]
        known_summary = not self.contest.extra.get('practice') and scoring != 'PRACTICE'
        shown_scoring = 'ICPC' if scoring == 'PRACTICE' else scoring
        columns.append('solved' if shown_scoring == 'ICPC' else 'points')
        values.append('—' if self.practice and not known_summary else str(self.me.solved) if scoring == 'ICPC' else
                      f'{self.me.points:g}' if self.me.points is not None else '?')
        if shown_scoring == 'ICPC':
            columns.append('penalty')
            values.append('—' if self.practice and not known_summary else str(self.me.penalty) if self.me.results_complete else '?')
        rows = [[*values, *cells]]
        # A completed VP keeps its original scoreboard while upsolving. Only a
        # standalone practice workspace has no original public statistics.
        show_statistics = not self.practice or known_summary
        if show_statistics and not self.contest.rules.problem_reveal_count:
            for key in ("submitted", "attempted", "accepted"):
                rows.append(["", key, *([""] * (len(columns) - 2)), *(str(self.stats[key][p]) if self.stats else "—"
                                                   for p in self.contest.problems)])
        table = score_table(columns, self.contest.problems,
                            rows, width, separate_summary=True)
        labels = self.record_heading(width, self.record_rows(self))
        if show_statistics and self.contest.rules.problem_reveal_count:
            return Group(heading, Text(" "), table, self.public_statistics(), Text(" "), labels)
        return Group(heading, Text(" "), table, Text(" "), labels)

    def public_statistics(self):
        visible = self.revealed or frozenset()
        freeze = self.contest.rules.freeze_at
        if self.practice or self.elapsed >= self.contest.rules.duration or freeze is not None and self.elapsed >= freeze:
            visible = frozenset(self.contest.problems)
        ordered = [p for p in self.contest.problems if p in visible]
        # Keep anonymous counts together, ordered by public activity rather than
        # A..M, so their positions do not expose the hidden problem labels.
        hidden = [p for p in self.contest.problems if p not in visible]
        if self.stats:
            hidden.sort(key=lambda p: tuple(self.stats[key][p] for key in
                                            ("accepted", "attempted", "submitted")), reverse=True)
        columns = ordered + hidden
        table = Table(box=None, expand=True, padding=(0, 1), collapse_padding=True,
                      style="default", header_style="bold")
        table.add_column("public", justify="left", no_wrap=False, overflow="fold")
        for problem in columns:
            table.add_column(problem if problem in visible else "?", justify="center", ratio=1,
                             min_width=1, no_wrap=False, overflow="fold")
        for key in ("submitted", "attempted", "accepted"):
            table.add_row(key, *(str(self.stats[key][p]) if self.stats else "—" for p in columns))
        caption = f"CCPC · revealed {len(ordered)}/{len(self.contest.problems)} · {self.contest.rules.problem_reveal_count} teams incl. starred"
        if freeze is not None:
            caption += " · all labels at " + clock(freeze)
        return Group(Text(caption, style="#555555"), table)

    @staticmethod
    def _record_table(width=160, rows=None):
        rows = rows or []
        desired = [max([minimum] + [max((cell_len(line) for line in row[index].plain.splitlines()), default=0)
                                    for row in rows]) for index, minimum in enumerate((7, 10, 19, 6))]
        minimum = [7, 10, 10, 6]
        padding = 1 if width >= sum(desired) + 8 else 0
        gaps = [0, 0, 0, 0] if padding else [1, 1, 1, 0]
        # Use the left half on wide panes. A narrow pane may need more than
        # half to keep complete IDs, timestamps and verdicts readable.
        target = min(width, max(width // 2, sum(desired) + 8 * padding + sum(gaps)))
        available = max(4, target - 8 * padding - sum(gaps))
        for index in (3, 1, 0, 2):
            excess = max(0, sum(desired)-available)
            desired[index] -= min(excess, desired[index]-minimum[index])
        extra = max(0, available - sum(desired))
        total = sum(desired)
        additions = [extra * size // total for size in desired]
        additions[-1] += extra - sum(additions)
        desired = [size + addition + gap for size, addition, gap in zip(desired, additions, gaps)]
        results = Table.grid(expand=False, padding=(0, padding))
        results.width = target
        for index, size in enumerate(desired):
            results.add_column(width=size, justify='left',
                               no_wrap=False, overflow='fold')
        return results

    @staticmethod
    def record_heading(width, rows=None):
        if width < 40:
            return Text('problem · submission · time · result', style='bold black on #eeeeee', overflow='fold')
        table = Dashboard._record_table(width, rows)
        table.add_row('problem', 'submission', 'time', 'result', style='bold black on #eeeeee')
        return Padding(table, (0, max(0, width - table.width), 0, 0),
                       style='bold black on #eeeeee', expand=False)

    def records(self):
        return SubmissionRecords(self)

    @staticmethod
    def record_rows(view):
        rows = []
        for item in view.submissions:
            v = item["verdict"]
            color = "#008000" if v == "CORRECT" else "#b8860b" if v == "PRETESTS-PASSED" else "#2563eb" if v in {"PENDING", "FROZEN"} else "#808080" if v in UNRESOLVED_VERDICTS | NON_ATTEMPT_VERDICTS | {"TOO-LATE"} else "#ff0000"
            text = "RESULT UNAVAILABLE" if v == "UNAVAILABLE" else v
            if item.get('practice'):
                text += ' · practice'
            started = getattr(view, 'start_time', None)
            if started is None:
                started = getattr(getattr(view, 'contest', None), 'original_start', None)
            submitted_at = item.get('submitted_at')
            if submitted_at is None and isinstance(started, (int, float)):
                submitted_at = started + item['time']
            try:
                stamp = datetime.fromtimestamp(submitted_at, user_zone()).strftime('%Y-%m-%d %H:%M:%S') if submitted_at is not None else '—'
            except (ValueError, OverflowError, OSError, TypeError):
                stamp = '—'
            rows.append([Text(item["problem"]), Text("#" + item["id"]), Text(stamp), Text(text, style=color)])
        return rows

    def __rich_console__(self, console, options):
        yield Group(self.fixed(options.max_width), self.records(), Text(self.message, style="#555555"))


@dataclass
class SubmissionRecords:
    view: object

    def __rich_console__(self, console, options):
        rows = Dashboard.record_rows(self.view)
        if not rows:
            yield Text('No submissions yet', style='#555555')
        elif options.max_width < 40:
            for row in rows:
                for label, value in zip(('problem', 'submission', 'time', 'result'), row):
                    yield Text(label + ': ') + value
                yield Text('')
        else:
            table = Dashboard._record_table(options.max_width, rows)
            for row in rows:
                table.add_row(*row)
            yield table


def display(contest: Contest, elapsed: float, me: TeamScore, rank: str,
            prize: str, stats: dict | None, submissions: list[dict], message: str = "") -> Dashboard:
    return Dashboard(contest, elapsed, me, rank, prize, stats, submissions, message)


@dataclass
class LiveDashboard:
    contest: Contest
    elapsed: float
    snapshot: dict
    submissions: list[dict]
    message: str = ""
    handle: str = ""
    practice: bool = False
    practice_rank: str | None = None
    final_rank: str | None = None
    prize: str = ''
    participant_label: str = ""
    participant_title: str = ''
    participant_subtitle: str = ''

    _record_table = staticmethod(Dashboard._record_table)
    records = Dashboard.records
    __rich_console__ = Dashboard.__rich_console__

    def fixed(self, width: int):
        phase = self.snapshot.get("phase", self.contest.extra["phase"])
        frozen = self.snapshot.get("frozen", False) or self.contest.rules.frozen(self.elapsed)
        from .workspaces import display_name
        title = Text(display_name(self.contest) + (" · BT" if self.practice else " · LIVE"), style="bold")
        timing = ("Starts in " + clock(-self.elapsed) if self.elapsed < 0 else
                  clock(min(self.elapsed, self.contest.rules.duration)) + " / " + clock(self.contest.rules.duration))
        phase_text = {"BEFORE": "Waiting for start", "CODING": "Coding",
                      "PENDING_SYSTEM_TEST": "Waiting for system tests",
                      "SYSTEM_TEST": "System testing", "FINISHED": "Finished"}.get(phase, phase)
        heading = Text("" if self.practice else timing + " · " + phase_text + (" · Scoreboard frozen" if frozen else ""))
        scoring = self.contest.extra["scoring"]
        own = self.snapshot.get("own")
        columns = ["rank", "team"]
        columns.append("solved" if scoring == "ICPC" else "points")
        if scoring == "ICPC":
            columns.append("penalty")
        elif self.contest.platform == "cf":
            columns.append("hacks +/−")
        results = own["problemResults"] if own else []
        cells = []
        corrections = recorded_cells(self.contest, self.submissions, practice=True)
        original_cells = recorded_cells(self.contest, self.submissions, practice=False)
        original_attempts = recorded_cells(self.contest, self.submissions, practice=False, all_attempts=True) if self.practice else {}
        for index, label in enumerate(self.contest.problems):
            result = results[index] if index < len(results) else {}
            if self.practice:
                original = original_cells[label]
                points = result.get('points', 0)
                maximum = self.contest.extra.get('problem_max_scores', {}).get(label)
                accepted = result.get('accepted', original.solved or bool(points) and (
                    scoring in ('ICPC', 'CF') or maximum is not None and points >= maximum))
                attempts = result.get('attempted')
                if type(attempts) is not int:
                    attempts = result.get('rejectedAttemptCount', 0) + int(bool(accepted))
                original = replace(original, tries=max(original.tries, attempts), solved=bool(accepted))
                native_time = result.get('bestSubmissionTimeSeconds')
                original_time = (int(native_time // 60) if type(native_time) in (int, float) and native_time >= 0 else
                                 original_cells[label].solve_time)
                c, blue = practice_cell(original, corrections[label], original_attempts[label])
                color = PRACTICE_ACCEPTED if blue else 'black on #60e760' if c.solved else (
                    PENDING_STYLE if c.pending else 'black on #e87272' if c.tries else 'default')
                detail = f"\n{original_time}" if original.solved and original_time is not None else ""
                cells.append(Text(attempt_text(c, practice=True) + detail, style=color))
                continue
            if index >= len(results) and original_cells[label].solved:
                c = original_cells[label]
                cells.append(Text(f"{c.tries} {'try' if c.tries == 1 else 'tries'}\n{c.solve_time}", style='black on #60e760'))
                continue
            if corrections[label].solved and not result.get('points', 0):
                c = corrections[label]
                cells.append(Text(f"{c.tries} {'try' if c.tries == 1 else 'tries'}\n{c.solve_time}", style=PRACTICE_ACCEPTED))
                continue
            points = result.get("points", 0)
            rejected = result.get("rejectedAttemptCount", 0)
            preliminary = result.get("type") == "PRELIMINARY"
            value = (f"+{rejected}" if rejected else "+") if scoring == "ICPC" and points else f"{points:g}"
            if not points:
                value = f"−{rejected}" if rejected else "—"
            if "display" in result:
                value = result["display"]
            elif preliminary and points:
                value += "*"
            when = result.get("bestSubmissionTimeSeconds")
            if when is not None:
                value += "\n" + clock(when)
            pending = preliminary or result.get('pending') or result.get('frozen') or original_cells[label].pending
            accepted = result.get('accepted', bool(points) if scoring in ('ICPC', 'CF') else False)
            color = ("#b8860b" if points and (preliminary or not accepted) else "#008000" if points else
                     PENDING_STYLE if pending else "#ff0000" if rejected else "default")
            cells.append(Text(value, style=color))
        name = (own["party"].get("teamName") or ";".join(m["handle"] for m in own["party"]["members"])) if own else self.handle
        name = self.participant_label or name
        value = (str(int(own["points"])) if scoring == "ICPC" else f"{own['points']:g}") if own else "—"
        rank = self.practice_rank if self.practice else str(own['rank']) if own else '—'
        rank = self.final_rank if self.final_rank is not None else rank
        prize = {'Gold':'Gold Prize','Silver':'Silver Prize','Bronze':'Bronze Prize','None':'No Prize','Unknown':'Prize unavailable'}.get(self.prize, '')
        values = [(str(rank).lstrip('=') if rank is not None else '—') + ('\n'+prize if prize else ''),
                  participant_text(self.participant_title, self.participant_subtitle, name)]
        values.append(value)
        if scoring == "ICPC":
            values.append(str(own["penalty"]) if own else "—")
        elif self.contest.platform == "cf":
            values.append(f"{own.get('successfulHackCount', 0)}/{own.get('unsuccessfulHackCount', 0)}" if own else "—")
        rows = [[*values, *cells]]
        stats = self.snapshot.get("stats", {})
        if frozen and (not self.snapshot.get('frozen') or self.contest.extra.get('freeze_source') in ('manual', 'room')):
            # A manually configured freeze can be earlier than the original
            # judge's. Its later public counts would expose sealed activity.
            stats = {}
        for key in (() if self.practice else ("scored", "attempted") if self.contest.platform == "cf" else tuple(stats)):
            rows.append(["", "attempted ≥" if key == "attempted" and self.contest.platform == "cf" else key,
                         *([""] * (len(columns) - 2)), *(str(stats[key][p]) if key in stats and p in stats[key] else "—"
                                                       for p in self.contest.problems)])
        table = score_table(columns, self.contest.problems, rows, width)
        note = ("Official public standings · scored = positive points · * preliminary" if self.contest.platform == "cf"
                else f"{'Nowcoder' if self.contest.platform == 'nowcoder' else 'CWNUOJ'} scoreboard · original scores, ranks and public statistics")
        if own is None:
            note = ("Official standings are temporarily unavailable; retrying with the original contest schedule."
                    if self.snapshot.get("available") is False else
                    "Your participation is absent from the public official standings; points and rank unavailable.")
        if self.practice:
            note = ""
        labels = Dashboard.record_heading(width, Dashboard.record_rows(self))
        return Group(title, heading, table, Text(note, style="#666666"), Text(" "), labels)


class TerminalDashboard:
    """Fixed scoreboard with every submission in a keyboard/mouse scroll region."""

    def __init__(self, continue_monitor=None):
        # Curses draws the segments; Rich must not substitute legacy Windows boxes.
        self.console = Console(style="default", color_system="256", highlight=False,
                               legacy_windows=False, safe_box=False)
        self.offset = 0
        self.page_size = 1
        self.total_lines = 0
        self.horizontal_offset = 0
        self.horizontal_limit = 0
        self.horizontal_stops = [0]
        self.pairs = {}
        self.continue_monitor = continue_monitor

    def __enter__(self):
        import curses
        self.curses = curses
        self.screen = curses.initscr()
        try:
            curses.noecho()
            curses.cbreak()
            self.screen.keypad(True)
            self.screen.nodelay(True)
            curses.start_color()
            self.default_colors = False
            try:
                curses.use_default_colors()
                self.default_colors = True
            except curses.error:
                pass
            self.background = self._attributes(None)
            self.screen.bkgd(" ", self.background)
            try:
                curses.curs_set(0)
                curses.mousemask(curses.ALL_MOUSE_EVENTS)
            except curses.error:
                pass
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_):
        self.screen.keypad(False)
        self.curses.nocbreak()
        self.curses.echo()
        self.curses.endwin()

    def _color(self, color, default: tuple[int, int, int]) -> int:
        if (color is None or color.is_default) and self.default_colors:
            return -1
        rgb = tuple(color.get_truecolor()) if color else default
        colors = self.curses.COLORS
        if colors >= 256:
            levels = (0, 95, 135, 175, 215, 255)
            indices = [min(range(6), key=lambda i: abs(levels[i] - value)) for value in rgb]
            cube = tuple(levels[i] for i in indices)
            gray_index = min(range(24), key=lambda i: sum((8 + i * 10 - value) ** 2 for value in rgb))
            gray = 8 + gray_index * 10
            if sum((gray - value) ** 2 for value in rgb) < sum((a - b) ** 2 for a, b in zip(cube, rgb)):
                return 232 + gray_index
            return 16 + 36 * indices[0] + 6 * indices[1] + indices[2]
        palette = ((0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0),
                   (0, 0, 238), (205, 0, 205), (0, 205, 205), (229, 229, 229))
        return min(range(min(8, colors)), key=lambda i: sum((a - b) ** 2 for a, b in zip(palette[i], rgb)))

    def _attributes(self, style) -> int:
        if not self.curses.has_colors():
            return self.curses.A_BOLD if style and style.bold else 0
        foreground = self._color(style.color if style else None, (0, 0, 0))
        background = self._color(style.bgcolor if style else None, (255, 255, 255))
        pair = (foreground, background)
        if pair not in self.pairs and len(self.pairs) + 1 < min(self.curses.COLOR_PAIRS, 256):
            number = len(self.pairs) + 1
            self.curses.init_pair(number, *pair)
            self.pairs[pair] = number
        attributes = self.curses.color_pair(self.pairs.get(pair, 0))
        if style and style.bold:
            attributes |= self.curses.A_BOLD
        if style and style.underline:
            attributes |= self.curses.A_UNDERLINE
        return attributes

    def input(self) -> bool:
        if self.continue_monitor is not None and not self.continue_monitor():
            return False
        c = self.curses
        while True:
            key = self.screen.getch()
            if key == -1:
                return True
            if key in (ord("q"), ord("Q")):
                return False
            if key in (c.KEY_LEFT, ord("h")):
                self.horizontal_offset = next((value for value in reversed(self.horizontal_stops)
                                               if value < self.horizontal_offset), 0)
            elif key in (c.KEY_RIGHT, ord("l")):
                self.horizontal_offset = next((value for value in self.horizontal_stops
                                               if value > self.horizontal_offset), self.horizontal_limit)
            elif key == ord("["):
                self.horizontal_offset = 0
            elif key == ord("]"):
                self.horizontal_offset = self.horizontal_limit
            elif key in (c.KEY_UP, ord("k")):
                self.offset -= 1
            elif key in (c.KEY_DOWN, ord("j")):
                self.offset += 1
            elif key == c.KEY_PPAGE:
                self.offset -= self.page_size
            elif key == c.KEY_NPAGE:
                self.offset += self.page_size
            elif key in (c.KEY_HOME, ord("g")):
                self.offset = 0
            elif key in (c.KEY_END, ord("G")):
                self.offset = max(0, self.total_lines - self.page_size)
            elif key == c.KEY_MOUSE:
                try:
                    buttons = c.getmouse()[4]
                    if buttons & c.BUTTON4_PRESSED:
                        self.offset -= 3
                    elif buttons & getattr(c, "BUTTON5_PRESSED", 0):
                        self.offset += 3
                except c.error:
                    pass
            self.offset = max(0, min(self.offset, max(0, self.total_lines - self.page_size)))
            self.horizontal_offset = max(0, min(self.horizontal_offset, self.horizontal_limit))

    def _lines(self, renderable, width: int):
        options = self.console.options.update(width=width, height=None)
        return self.console.render_lines(renderable, options, pad=False)

    def _draw(self, row: int, segments, width: int, left: int = 0):
        column = 0
        for segment in segments:
            if segment.control:
                continue
            text = segment.text.replace("\n", "")
            if segment.cell_length > width - column:
                text = set_cell_size(text, width - column)
            try:
                self.screen.addstr(row, left + column, text, self._attributes(segment.style))
            except self.curses.error:
                # Writing the bottom-right cell can report ERR after succeeding.
                pass
            column += min(segment.cell_length, width - column)
            if column >= width:
                break

    def update(self, dashboard: Dashboard):
        height, width = self.screen.getmaxyx()
        self.screen.erase()
        if height < 12 or width < 45:
            self._draw(0, self._lines(Text("Enlarge the score pane to view submissions."), width)[0], width)
            self.screen.refresh()
            return
        content_width = width - 4
        left = (width - content_width) // 2
        fixed_view = dashboard.fixed(content_width)
        viewports = list(problem_viewports(fixed_view))
        self.horizontal_limit = max((item.max_offset(content_width) for item in viewports), default=0)
        self.horizontal_offset = max(0, min(self.horizontal_offset, self.horizontal_limit))
        self.horizontal_stops = list(range(self.horizontal_limit + 1))
        for item in viewports:
            item.offset = self.horizontal_offset
        fixed = self._lines(fixed_view, content_width)
        records = self._lines(dashboard.records(), content_width)
        # Wrapped names and results can make the scoreboard taller than the
        # pane. Keep every field reachable by scrolling the whole view then.
        scroll_all = len(fixed) > height - 5
        scrollable = [*fixed, *records] if scroll_all else records
        start = 0 if scroll_all else len(fixed)
        self.page_size = max(1, height - start - 2)
        self.total_lines = len(scrollable)
        self.offset = min(self.offset, max(0, self.total_lines - self.page_size))
        if not scroll_all:
            for row, segments in enumerate(fixed):
                self._draw(row, segments, content_width, left)
        for row, segments in enumerate(scrollable[self.offset:self.offset + self.page_size], start=start):
            if row < height - 2:
                self._draw(row, segments, content_width, left)
        last = min(self.offset + self.page_size, self.total_lines)
        first = self.offset + 1 if self.total_lines else 0
        footer = Table.grid(expand=True, padding=(0, 1))
        footer.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        footer.add_column(justify="right", no_wrap=True)
        footer.add_row(Text(dashboard.message, style="#666666"),
                       Text(f"{len(dashboard.submissions)} submissions · {first}–{last} / {self.total_lines} lines", style="#666666"))
        self._draw(height - 2, self._lines(footer, content_width)[0], content_width, left)
        help_text = ("q return · ←/→ h/l problems · [/] first/last · ↑/↓ logs" if self.horizontal_limit else
                     "q return · ↑/↓ scroll · PgUp/PgDn · Home/End")
        keys = Text(help_text, style="#999999", no_wrap=True, overflow="ellipsis")
        self._draw(height - 1, self._lines(keys, content_width)[0], content_width, left)
        self.screen.noutrefresh()
        self.curses.doupdate()
