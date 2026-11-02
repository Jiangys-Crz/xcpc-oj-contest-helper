from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math


class VPError(RuntimeError):
    """An actionable platform, configuration or data error."""


class StartRejected(VPError):
    """The native site refused this new VP; never start a local fallback."""


@dataclass
class Rules:
    duration: int
    penalty_minutes: int = 20
    compile_penalty: bool = False
    freeze_at: int | None = None
    unfreeze_at: int | None = None
    problem_reveal_count: int | None = None
    penalty_rounding: str = "per_problem"

    def __post_init__(self):
        if self.duration <= 0 or self.penalty_minutes < 0:
            raise VPError("Invalid contest duration or penalty configuration.")
        if self.penalty_rounding not in ("per_problem", "total_seconds"):
            raise VPError("Unsupported contest penalty rounding rule.")
        if self.freeze_at is not None and not 0 <= self.freeze_at <= self.duration:
            raise VPError("The scoreboard freeze time is outside the contest window.")
        if self.unfreeze_at is not None and self.unfreeze_at < self.duration:
            raise VPError("The original scoreboard unfreeze time precedes contest end.")
        if self.problem_reveal_count is not None and (type(self.problem_reveal_count) is not int or self.problem_reveal_count <= 0):
            raise VPError("The problem reveal threshold must be a positive original-team count.")

    def frozen(self, elapsed: float, released: bool = False) -> bool:
        return (
            self.freeze_at is not None and elapsed >= self.freeze_at
            and not released
            and (self.unfreeze_at is None or elapsed < self.unfreeze_at)
        )


@dataclass
class Medals:
    gold: int
    silver: int
    bronze: int
    source: str
    additional_bronze: int = 0


@dataclass
class Contest:
    platform: str
    contest_id: str
    url: str
    name: str
    directory_name: str
    problems: dict[str, str]
    rules: Rules
    pdfs: dict[str, str] = field(default_factory=dict)
    medals: Medals | None = None
    original_start: float | None = None
    reference_url: str | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> Contest:
        data = dict(value)
        data["rules"] = Rules(**data["rules"])
        if data.get("medals"):
            data["medals"] = Medals(**data["medals"])
        return cls(**data)


@dataclass
class Submission:
    id: str
    team: str
    problem: str
    time: float
    verdict: str
    practice: bool = False
    # Native awarded points, when the judge exposes them. Never infer a CF
    # points formula from the submission time or from a binary verdict.
    score: float | None = None
    language: str | None = None


@dataclass
class Cell:
    tries: int = 0
    pending: int = 0
    solved: bool = False
    solve_time: int | None = None
    unresolved: int = 0
    score: float | None = None
    max_score: float | None = None
    score_time: int | None = None


@dataclass
class TeamScore:
    id: str
    name: str
    cells: dict[str, Cell]
    eligible: bool = True
    penalty_minutes: int = 20
    known_penalty: int | None = None
    scoring: str = "ICPC"
    known_points: float | None = None
    oi_time_tiebreak: bool = False
    known_score_tiebreak: int | None = None

    @property
    def results_complete(self) -> bool:
        return not any(c.unresolved for c in self.cells.values()) and self.score_complete

    @property
    def score_complete(self) -> bool:
        return (self.scoring in ("ICPC", "PRACTICE") or self.known_points is not None
                or all(not c.unresolved and (not c.tries or c.score is not None) for c in self.cells.values()))

    @property
    def points(self) -> float | None:
        if self.scoring == "ICPC":
            return float(self.solved)
        if self.scoring == "PRACTICE":
            return None
        if self.known_points is not None:
            return self.known_points
        if not self.score_complete:
            return None
        return sum(c.score or 0 for c in self.cells.values())

    @property
    def solved(self) -> int:
        return sum(c.solved for c in self.cells.values())

    @property
    def penalty(self) -> int:
        if self.scoring != "ICPC":
            return 0
        if self.known_penalty is not None:
            return self.known_penalty
        return sum(
            (c.solve_time or 0) + self.penalty_minutes * max(0, c.tries - 1)
            for c in self.cells.values() if c.solved
        )

    @property
    def last_correct(self) -> int:
        return max((c.solve_time or 0 for c in self.cells.values() if c.solved), default=0)

    @property
    def sort_key(self) -> tuple[float, int, int]:
        if self.scoring != "ICPC":
            return self.rank_key[0], 0, 0
        return -self.solved, self.penalty, self.last_correct

    @property
    def rank_key(self) -> tuple[float, int]:
        # Last acceptance orders equal scores without splitting their shared rank.
        if self.scoring != "ICPC":
            # Callers suppress ranks for incomplete rows. A missing native
            # score must not accidentally outrank a row with a known score.
            tiebreak = (self.known_score_tiebreak if self.known_score_tiebreak is not None else
                        sum(c.score_time or 0 for c in self.cells.values() if c.score is not None and c.score > 0)) if self.oi_time_tiebreak else 0
            return -(self.points if self.points is not None else -math.inf), tiebreak
        return -self.solved, self.penalty


@dataclass
class Reference:
    rows: list[TeamScore] = field(default_factory=list)
    complete: bool = False
    statistics_complete: bool = False
    released: bool = False
    message: str = ""
    replay: object | None = None
