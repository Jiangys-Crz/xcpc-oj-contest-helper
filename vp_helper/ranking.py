"""Keep original-contest ranks distinct from native virtual/room positions."""
from __future__ import annotations

import re

from .models import Reference, TeamScore
from .scoring import rank_label


def original_rank(score: TeamScore, reference: Reference, url: str) -> dict | None:
    rows = [row for row in reference.rows if row.eligible and row.id != score.id]
    if (not reference.complete or not score.results_complete or not rows
            or len({row.id for row in rows}) != len(rows)
            or any(not row.results_complete or row.scoring != score.scoring for row in rows)):
        return None
    return {'source': 'original', 'contestUrl': url, 'rank': rank_label(score, rows),
            'officialParticipants': len(rows), 'scoring': score.scoring,
            'scoreKey': list(score.rank_key)}


def verified_rank(state: dict, score: TeamScore) -> str:
    """A previous rank cannot describe a changed score or another contest."""
    proof = state.get('reference_rank')
    if not isinstance(proof, dict) or not score.results_complete:
        return '—'
    rank = proof.get('rank')
    count = proof.get('officialParticipants')
    if (proof.get('source') != 'original' or proof.get('contestUrl') != state.get('url')
            or proof.get('scoring') != score.scoring or proof.get('scoreKey') != list(score.rank_key)
            or type(count) is not int or count < 1 or not isinstance(rank, str)
            or not re.fullmatch(r'=?[1-9][0-9]*', rank) or int(rank.lstrip('=')) > count + 1):
        return '—'
    return rank
