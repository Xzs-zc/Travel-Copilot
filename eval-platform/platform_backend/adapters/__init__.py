from __future__ import annotations

import sys

from ..config import TRAVEL_ROOT


if str(TRAVEL_ROOT) not in sys.path:
    sys.path.insert(0, str(TRAVEL_ROOT))
