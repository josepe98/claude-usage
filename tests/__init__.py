# Keep every test away from the user's real status-line snapshot files.
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ratelimits  # noqa: E402

_tmp = Path(tempfile.mkdtemp(prefix="claude-usage-tests-"))
ratelimits.SNAPSHOT_PATH = _tmp / "rate_limits.jsonl"
ratelimits.STATE_PATH = _tmp / "rate_limits.last.json"
ratelimits.ERROR_LOG_PATH = _tmp / "statusline-errors.log"
