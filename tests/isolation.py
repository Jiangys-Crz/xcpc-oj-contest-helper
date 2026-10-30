"""Local lifecycle fixtures must never use a developer's cloud account."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from vp_helper import cloud


def isolate_local_state(stack, root: Path) -> None:
    """Isolate both account and state paths, and prohibit background cloud I/O.

    The fixture's account store is empty by default. Explicit cloud tests use
    their own mocked transport rather than this local-only fixture helper.
    """
    stack.enter_context(patch.dict(os.environ, {
        'XDG_CONFIG_HOME': str(root / 'config'),
        'XDG_STATE_HOME': str(root / 'state'),
    }))
    stack.enter_context(patch.object(cloud, '_start_worker'))
    stack.enter_context(patch.object(cloud, 'kick_sync_process'))
    stack.enter_context(patch.object(cloud, '_request', side_effect=AssertionError(
        'Local test attempted cloud I/O; use an explicit mocked cloud fixture.')))
