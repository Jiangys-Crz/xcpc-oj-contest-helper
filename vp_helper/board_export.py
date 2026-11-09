"""Public room scoreboard projection and standalone archive export for the web service."""
import json
import sys
from dataclasses import asdict
from urllib.parse import urlsplit

from .models import VPError


def snapshot(reference, elapsed, revealed):
    data = {
        'elapsed': int(elapsed),
        'released': reference.released,
        'revealed': sorted(revealed),
        'rows': [
            {'id': row.id, 'name': row.name, 'kind': 'official' if row.eligible else 'starred',
             'solved': row.solved, 'penalty': row.penalty,
             'points': row.points if row.scoring != 'ICPC' else None,
             'cells': {
                 label: {'solved': cell.solved, 'tries': cell.tries, 'pending': cell.pending,
                         'solveTime': cell.solve_time, 'points': cell.score}
                 for label, cell in row.cells.items()
                 if label in revealed and (cell.solved or cell.tries or cell.pending or cell.score)
             }}
            for row in reference.rows
        ],
    }
    return data if len(json.dumps(data, ensure_ascii=False).encode()) <= 512 * 1024 else None


def external_archive(page):
    from .external_rank import source_url
    from . import rank_ccpc, rank_xcpcio
    page = source_url(page)
    provider = rank_xcpcio if urlsplit(page).netloc == 'board.xcpcio.com' else rank_ccpc
    metadata, events, _ = provider.load(page, None)
    return {'metadata': metadata, 'events': [asdict(event) for event in events]}


if __name__ == '__main__':
    try:
        print(json.dumps(external_archive(sys.argv[1]), ensure_ascii=False, allow_nan=False))
    except VPError as error:
        sys.exit(str(error))
