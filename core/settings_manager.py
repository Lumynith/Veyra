"""Settings = defaults.json overlaid with the user's settings.json. Only overrides are saved."""
from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


class SettingsManager:
    def __init__(self, defaults_path: Path, user_path: Path) -> None:
        self._defaults = json.loads(Path(defaults_path).read_text("utf-8"))
        self._user_path = Path(user_path)
        self._user = self._load_user()
        self._data = _merge(self._defaults, self._user)

    def _load_user(self) -> dict:
        if not self._user_path.exists():
            return {}
        try:
            data = json.loads(self._user_path.read_text("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("top level is not an object")
            return data
        except (OSError, ValueError) as e:
            # Keep the broken file for the user instead of silently overwriting it later.
            backup = self._user_path.with_suffix(".corrupt.json")
            os.replace(self._user_path, backup)
            log.warning("Settings file unreadable (%s); moved to %s and using defaults", e, backup)
            return {}

    def get(self, key: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, key: str, value: Any) -> None:
        *parents, leaf = key.split(".")
        for target in (self._data, self._user):
            for part in parents:
                target = target.setdefault(part, {})
            target[leaf] = value

    def save(self) -> None:
        self._user_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._user_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._user, indent=2), "utf-8")
        os.replace(tmp, self._user_path)  # atomic: a crash mid-write never leaves a half file
        log.info("Settings saved to %s", self._user_path)
