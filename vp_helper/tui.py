from __future__ import annotations

from dataclasses import dataclass

from rich import box
from rich.cells import set_cell_size
from rich.console import Console, Group
from rich.table import Table
from rich.text import Text

from .models import Contest, TeamScore


PRACTICE_WRONG = "#0000ff"
PRACTICE_ACCEPTED = "#0000ff"
SCORE_BOX = box.Box("    \n    \n ── \n    \n ── \n ── \n    \n    \n")


def clock(value: float) -> str:
    value = max(0, int(value))
    return f"{value // 3600:02d}:{value // 60 % 60:02d}:{value % 60:02d}"


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

    def fixed(self, width: int):
        # Equal side columns keep the contest title at the screen's center.
        sides = min(30, max(19, (width - 8) // 3))
        heading = Table.grid(expand=True, padding=0)
        heading.style = "default"
        heading.add_column(width=sides, no_wrap=True)
        heading.add_column(ratio=1, justify="center", no_wrap=True, overflow="ellipsis")
        heading.add_column(width=sides, justify="right", no_wrap=True)
        prize = {"Gold": "Gold Prize", "Silver": "Silver Prize", "Bronze": "Bronze Prize",
                 "None": "No Prize", "Unknown": "Unknown"}.get(self.prize, self.prize)
        result = self.rank + (f"\n{prize}" if prize else "")
        timer = "Starts in " + clock(-self.elapsed) if self.elapsed < 0 else f"{clock(min(self.elapsed, self.contest.rules.duration))} / {clock(self.contest.rules.duration)}"
        if self.practice:
            timer = ""
        label = self.contest.name + (f" · VP #{self.contest.extra['attempt']}" if self.contest.extra.get("attempt") else "")
        if self.practice:
            label += " · BT"
        if self.contest.extra.get("local_attempt") and not self.practice:
            label += " · local timer"
        heading.add_row(Text(" " + timer, style="bold default"), Text(label, style="bold"), Text(""))
        table = Table(box=SCORE_BOX, padding=(0, 1), style="default",
                      header_style="bold black on #eeeeee", border_style="#bbbbbb",
                      show_edge=False, expand=True)
        for label in ("rank", "team", "solved", "penalty", *self.contest.problems):
            table.add_column(label, justify="left" if label == "team" else "center",
                             no_wrap=label == "rank", min_width=12 if label == "rank" else None)
        cells = []
        for label in self.contest.problems:
            c = self.me.cells[label]
            tries = (f"{c.tries} + {c.pending} tries" if c.pending else
                     f"{c.tries} {'try' if c.tries == 1 else 'tries'}" if c.tries else "—")
            if width < 155:
                tries = f"{c.tries}+{c.pending}" if c.pending else str(c.tries) if c.tries else "—"
            if c.unresolved:
                tries = f"{c.tries}+?" if c.tries else "?"
            if c.solved:
                cells.append(Text(f"{tries}\n{c.solve_time}", style="black on #60e760"))
            elif c.pending:
                cells.append(Text(tries, style="white on #6666ff"))
            elif c.tries:
                cells.append(Text(tries, style="black on #e87272"))
            else:
                cells.append(Text(tries))
        table.add_row(Text(result, style="bold default"), self.me.name, str(self.me.solved),
                      str(self.me.penalty) if self.me.results_complete else "?",
                      *cells, end_section=True)
        if not self.practice and not self.contest.rules.problem_reveal_count:
            for key in ("submitted", "attempted", "accepted"):
                table.add_row("", key, "", "", *(str(self.stats[key][p]) if self.stats else "—"
                                                 for p in self.contest.problems))
        labels = self._record_table()
        labels.add_row("problem", "submission", "time", "result", style="bold black on #eeeeee")
        if not self.practice and self.contest.rules.problem_reveal_count:
            return Group(heading, Text(" "), table, self.public_statistics(), Text(" "), labels)
        return Group(heading, Text(" "), table, Text(" "), labels)

    def public_statistics(self):
        visible = self.revealed or frozenset()
        freeze = self.contest.rules.freeze_at
        if self.elapsed >= self.contest.rules.duration or freeze is not None and self.elapsed >= freeze:
            visible = frozenset(self.contest.problems)
        ordered = [p for p in self.contest.problems if p in visible]
        # Keep anonymous counts together, ordered by public activity rather than
        # A..M, so their positions do not expose the hidden problem labels.
        hidden = [p for p in self.contest.problems if p not in visible]
        if self.stats:
            hidden.sort(key=lambda p: tuple(self.stats[key][p] for key in
                                            ("accepted", "attempted", "submitted")), reverse=True)
        columns = ordered + hidden
        table = Table(box=None, expand=True, padding=(0, 1), style="default", header_style="bold")
        table.add_column("public", justify="left")
        for problem in columns:
            table.add_column(problem if problem in visible else "?", justify="center")
        for key in ("submitted", "attempted", "accepted"):
            table.add_row(key, *(str(self.stats[key][p]) if self.stats else "—" for p in columns))
        caption = f"CCPC · revealed {len(ordered)}/{len(self.contest.problems)} · {self.contest.rules.problem_reveal_count} teams incl. starred"
        if freeze is not None:
            caption += " · all labels at " + clock(freeze)
        return Group(Text(caption, style="#555555"), table)

    @staticmethod
    def _record_table():
        results = Table.grid(expand=True, padding=(0, 1))
        results.add_column(width=9, justify="center", no_wrap=True)
        results.add_column(width=14, justify="center", no_wrap=True)
        results.add_column(width=12, justify="center", no_wrap=True)
        results.add_column(ratio=1, no_wrap=True)
        return results

    def records(self):
        results = self._record_table()
        for item in self.submissions:
            v = item["verdict"]
            practice = item.get("practice", False)
            if practice:
                color = PRACTICE_ACCEPTED if v == "CORRECT" else PRACTICE_WRONG
                values = [item["problem"], "#" + item["id"], clock(item["time"]), v + " · practice"]
                results.add_row(*values, style=color)
            else:
                color = "#008000" if v == "CORRECT" else "#b8860b" if v == "PRETESTS-PASSED" else "#808080" if v in ("PENDING", "TOO-LATE", "UNAVAILABLE", "HIDDEN") else "#ff0000"
                results.add_row(Text(item["problem"]), Text("#" + item["id"]),
                                Text(clock(item["time"])), Text("RESULT UNAVAILABLE" if v == "UNAVAILABLE" else v, style=color))
        if not self.submissions:
            results.add_row(Text("No submissions yet", style="#555555"))
        return results

    def __rich_console__(self, console, options):
        yield Group(self.fixed(options.max_width), self.records(), Text(self.message, style="#555555"))


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

    _record_table = staticmethod(Dashboard._record_table)
    records = Dashboard.records
    __rich_console__ = Dashboard.__rich_console__

    def fixed(self, width: int):
        phase = self.snapshot.get("phase", self.contest.extra["phase"])
        frozen = self.snapshot.get("frozen", False)
        title = Text(self.contest.name + " · LIVE", style="bold")
        timing = ("Starts in " + clock(-self.elapsed) if self.elapsed < 0 else
                  clock(min(self.elapsed, self.contest.rules.duration)) + " / " + clock(self.contest.rules.duration))
        phase_text = {"BEFORE": "Waiting for start", "CODING": "Coding",
                      "PENDING_SYSTEM_TEST": "Waiting for system tests",
                      "SYSTEM_TEST": "System testing", "FINISHED": "Finished"}.get(phase, phase)
        heading = Text(timing + " · " + phase_text + (" · Scoreboard frozen" if frozen else ""))
        scoring = self.contest.extra["scoring"]
        own = self.snapshot.get("own")
        table = Table(box=SCORE_BOX, padding=(0, 1), expand=True, show_edge=False)
        columns = ["rank", "team", "solved" if scoring == "ICPC" else "points"]
        if scoring == "ICPC":
            columns.append("penalty")
        elif self.contest.platform == "cf":
            columns.append("hacks +/−")
        for label in [*columns, *self.contest.problems]:
            table.add_column(label, justify="left" if label == "team" else "center")
        results = own["problemResults"] if own else []
        cells = []
        for result in results:
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
            color = "#b8860b" if preliminary and points else "#008000" if points else "#ff0000" if rejected else "default"
            cells.append(Text(value, style=color))
        cells.extend(Text("—") for _ in range(max(0, len(self.contest.problems) - len(cells))))
        name = (own["party"].get("teamName") or ";".join(m["handle"] for m in own["party"]["members"])) if own else self.handle
        value = (str(int(own["points"])) if scoring == "ICPC" else f"{own['points']:g}") if own else "—"
        values = [str(own["rank"]) if own else "—", Text(name), value]
        if scoring == "ICPC":
            values.append(str(own["penalty"]) if own else "—")
        elif self.contest.platform == "cf":
            values.append(f"{own.get('successfulHackCount', 0)}/{own.get('unsuccessfulHackCount', 0)}" if own else "—")
        table.add_row(*values, *cells)
        stats = self.snapshot.get("stats", {})
        for key in (("scored", "attempted") if self.contest.platform == "cf" else tuple(stats)):
            table.add_row("", "attempted ≥" if key == "attempted" and self.contest.platform == "cf" else key,
                          *([""] * (len(columns) - 2)), *(str(stats[key][p]) if key in stats and p in stats[key] else "—"
                                              for p in self.contest.problems))
        note = ("Official public standings · scored = positive points · * preliminary" if self.contest.platform == "cf"
                else f"{'Nowcoder' if self.contest.platform == 'nowcoder' else 'CWNUOJ'} scoreboard · original scores, ranks and public statistics")
        if own is None:
            note = "Your participation is absent from the public official standings; points and rank unavailable."
        labels = self._record_table()
        labels.add_row("problem", "submission", "time", "result", style="bold black on #eeeeee")
        return Group(title, heading, table, Text(note, style="#666666"), Text(" "), labels)


class TerminalDashboard:
    """Fixed scoreboard with every submission in a keyboard/mouse scroll region."""

    def __init__(self):
        self.console = Console(style="default", color_system="256", highlight=False)
        self.offset = 0
        self.page_size = 1
        self.total_lines = 0
        self.pairs = {}

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
        c = self.curses
        while True:
            key = self.screen.getch()
            if key == -1:
                return True
            if key in (ord("q"), ord("Q")):
                return False
            if key in (c.KEY_UP, ord("k")):
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
        content_width = min(160, width - 4)
        left = (width - content_width) // 2
        fixed = self._lines(dashboard.fixed(content_width), content_width)
        records = self._lines(dashboard.records(), content_width)
        self.page_size = max(1, height - len(fixed) - 2)
        self.total_lines = len(records)
        self.offset = min(self.offset, max(0, len(records) - self.page_size))
        for row, segments in enumerate(fixed[:height - 2]):
            self._draw(row, segments, content_width, left)
        for row, segments in enumerate(records[self.offset:self.offset + self.page_size], start=len(fixed)):
            if row < height - 2:
                self._draw(row, segments, content_width, left)
        last = min(self.offset + self.page_size, len(dashboard.submissions))
        first = self.offset + 1 if dashboard.submissions else 0
        footer = Table.grid(expand=True, padding=(0, 1))
        footer.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        footer.add_column(justify="right", no_wrap=True)
        footer.add_row(Text(dashboard.message, style="#666666"), Text(f"{first}–{last} / {len(dashboard.submissions)}", style="#666666"))
        self._draw(height - 2, self._lines(footer, content_width)[0], content_width, left)
        keys = Text("↑/↓ scroll · PgUp/PgDn · Home/End", style="#999999", no_wrap=True, overflow="ellipsis")
        self._draw(height - 1, self._lines(keys, content_width)[0], content_width, left)
        self.screen.noutrefresh()
        self.curses.doupdate()
