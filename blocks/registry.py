"""Blocks register themselves with @register; modules in blocks/categories load automatically."""
from __future__ import annotations

import importlib
import importlib.util
import logging
import pkgutil
from pathlib import Path

from blocks.base_block import Block

log = logging.getLogger(__name__)
_blocks: dict[str, type[Block]] = {}


plugin_status: dict[str, str] = {}  # plugin file -> "loaded: …" or the error, shown on the Custom Blocks page


def register(cls: type[Block], replace: bool = False) -> type[Block]:
    """replace=True lets custom/plugin blocks be updated; built-in ids can never be replaced by them."""
    if not cls.type_id or (cls.type_id in _blocks and not (replace and _replaceable(cls.type_id))):
        raise ValueError(f"Block type id missing or duplicate: {cls.type_id!r}")
    _blocks[cls.type_id] = cls
    return cls


def _replaceable(type_id: str) -> bool:
    return getattr(_blocks[type_id], "user_defined", False) or getattr(_blocks[type_id], "BLOCK_ID", "")


def unregister(type_id: str) -> None:
    if type_id in _blocks and _replaceable(type_id):
        del _blocks[type_id]


def get(type_id: str) -> type[Block]:
    try:
        return _blocks[type_id]
    except KeyError:
        raise KeyError(f"Unknown block type {type_id!r}") from None


def all_blocks() -> list[type[Block]]:
    return list(_blocks.values())


def load_builtin() -> None:
    import blocks.categories as pkg
    for mod in pkgutil.iter_modules(pkg.__path__):
        try:
            importlib.import_module(f"{pkg.__name__}.{mod.name}")
        except Exception:
            log.exception("Failed to load block module %s", mod.name)


def load_plugins(folder: Path) -> None:
    """Import every plugins/*.py. BaseBlock subclasses register themselves. Errors are logged, never fatal."""
    for path in sorted(Path(folder).glob("*.py")):
        if path.name.startswith("_"):
            continue
        before = set(_blocks)
        try:
            spec = importlib.util.spec_from_file_location(f"veyra_plugin_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as e:
            plugin_status[path.name] = f"failed: {type(e).__name__}: {e}"
            log.exception("Plugin %s failed to load", path.name)
            continue
        added = sorted(set(_blocks) - before)
        plugin_status[path.name] = f"loaded: {', '.join(added) or 'no blocks'}"
        log.info("Plugin %s loaded (%s)", path.name, ", ".join(added) or "no blocks")
