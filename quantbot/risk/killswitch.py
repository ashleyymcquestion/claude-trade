"""Kill switch: when engaged, NO new orders of any kind (entries or exits) may be submitted.

State lives in the store (flag ``kill_switch``) so it survives restarts. Operators can also
engage it without Python by creating the file named by ``$QUANTBOT_KILL_FILE`` (default
``state/KILL``, relative to the working directory): while that file exists the switch is
engaged, and ``release()`` cannot override it — delete the file.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..store import Store

FLAG = "kill_switch"
KILL_FILE_ENV = "QUANTBOT_KILL_FILE"
DEFAULT_KILL_FILE = "state/KILL"


class KillSwitch:
    def __init__(self, store: "Store", kill_file: str | Path | None = None) -> None:
        self.store = store
        self._kill_file = Path(kill_file) if kill_file is not None else None

    @property
    def kill_file(self) -> Path:
        """Resolved on every call so a changed environment variable takes effect."""
        if self._kill_file is not None:
            return self._kill_file
        return Path(os.environ.get(KILL_FILE_ENV) or DEFAULT_KILL_FILE)

    def _file_engaged(self) -> bool:
        try:
            return self.kill_file.exists()
        except OSError:   # unreadable path: fail safe
            return True

    def _stored(self) -> dict | None:
        raw = self.store.get_flag(FLAG)
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {"reason": str(data)}
        except ValueError:     # a hand-written flag value still means engaged
            return {"reason": raw}

    def engage(self, reason: str, by: str) -> None:
        prev = self._stored()
        since = (prev or {}).get("since") or datetime.now(UTC).isoformat()
        self.store.set_flag(FLAG, json.dumps({"reason": reason, "by": by, "since": since}), reason)
        self.store.record_event("critical", "kill_switch_engaged", f"kill switch engaged by {by}: {reason}",
                                reason=reason, by=by, already_engaged=prev is not None)

    def release(self, by: str) -> None:
        was = self._stored()
        self.store.set_flag(FLAG, None, f"released by {by}")
        file_present = self._file_engaged()
        msg = f"kill switch released by {by}"
        if file_present:
            msg += f" (still engaged: kill file {self.kill_file} exists)"
        self.store.record_event("warning", "kill_switch_released", msg, by=by,
                                was_engaged=was is not None, kill_file_present=file_present)

    def is_engaged(self) -> bool:
        return self._file_engaged() or self._stored() is not None

    def status(self) -> dict:
        stored = self._stored()
        if stored is not None:
            since = stored.get("since")
            return {"engaged": True, "reason": stored.get("reason", ""), "by": stored.get("by", ""),
                    "since": datetime.fromisoformat(since) if since else None,
                    "kill_file": self._file_engaged()}
        if self._file_engaged():
            since = None
            try:
                since = datetime.fromtimestamp(self.kill_file.stat().st_mtime, UTC)
            except OSError:
                pass
            return {"engaged": True, "reason": f"kill file present: {self.kill_file}", "by": "file",
                    "since": since, "kill_file": True}
        return {"engaged": False, "reason": "", "by": "", "since": None, "kill_file": False}
