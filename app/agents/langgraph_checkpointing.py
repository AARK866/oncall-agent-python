from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path
from typing import Any

from app.config import settings

_MEMORY_CHECKPOINTER: Any | None = None
_SQLITE_CHECKPOINTERS: dict[str, Any] = {}
_SQLITE_CONTEXTS: dict[str, Any] = {}


def create_langgraph_checkpointer(
    mode: str,
    sqlite_path: str | None = None,
) -> tuple[Any | None, str]:
    normalized_mode = mode.strip().lower()
    if normalized_mode in {"", "none", "disabled", "off"}:
        return None, "disabled"
    if normalized_mode == "auto":
        if not is_memory_checkpointer_available():
            return None, "disabled"
        return _memory_checkpointer(), "memory"
    if normalized_mode in {"memory", "in_memory"}:
        if not is_memory_checkpointer_available():
            raise RuntimeError(
                "OPS_GRAPH_CHECKPOINTER=memory requires langgraph.checkpoint.memory."
            )
        return _memory_checkpointer(), "memory"
    if normalized_mode in {"sqlite", "sqlite_persistent", "persistent_sqlite"}:
        return _sqlite_checkpointer(sqlite_path or settings.ops_graph_checkpoint_db_path), "sqlite"

    raise ValueError(f"Unsupported OPS_GRAPH_CHECKPOINTER: {mode}")


def is_memory_checkpointer_available() -> bool:
    return find_spec("langgraph.checkpoint.memory") is not None


def is_sqlite_checkpointer_available() -> bool:
    return find_spec("langgraph.checkpoint.sqlite") is not None


def _memory_checkpointer() -> Any:
    global _MEMORY_CHECKPOINTER
    if _MEMORY_CHECKPOINTER is None:
        from langgraph.checkpoint.memory import MemorySaver

        _MEMORY_CHECKPOINTER = MemorySaver()
    return _MEMORY_CHECKPOINTER


def _sqlite_checkpointer(db_path: str) -> Any:
    if not is_sqlite_checkpointer_available():
        raise RuntimeError(
            "OPS_GRAPH_CHECKPOINTER=sqlite requires langgraph-checkpoint-sqlite. "
            "Install it with: pip install langgraph-checkpoint-sqlite"
        )

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.resolve())
    if key in _SQLITE_CHECKPOINTERS:
        return _SQLITE_CHECKPOINTERS[key]

    from langgraph.checkpoint.sqlite import SqliteSaver

    saver = SqliteSaver.from_conn_string(str(path))
    if hasattr(saver, "__enter__"):
        _SQLITE_CONTEXTS[key] = saver
        saver = saver.__enter__()

    setup = getattr(saver, "setup", None)
    if callable(setup):
        setup()

    _SQLITE_CHECKPOINTERS[key] = saver
    return saver
