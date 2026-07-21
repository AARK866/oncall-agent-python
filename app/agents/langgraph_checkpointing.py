from __future__ import annotations

from importlib.util import find_spec
from typing import Any


_MEMORY_CHECKPOINTER: Any | None = None


def create_langgraph_checkpointer(mode: str) -> tuple[Any | None, str]:
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

    raise ValueError(f"Unsupported OPS_GRAPH_CHECKPOINTER: {mode}")


def is_memory_checkpointer_available() -> bool:
    return find_spec("langgraph.checkpoint.memory") is not None


def _memory_checkpointer() -> Any:
    global _MEMORY_CHECKPOINTER
    if _MEMORY_CHECKPOINTER is None:
        from langgraph.checkpoint.memory import MemorySaver

        _MEMORY_CHECKPOINTER = MemorySaver()
    return _MEMORY_CHECKPOINTER
