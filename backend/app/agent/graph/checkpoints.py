from __future__ import annotations

from typing import Any

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from ...config import get_settings

_connection: aiosqlite.Connection | None = None
_checkpointer: AsyncSqliteSaver | None = None


async def init_graph_checkpointer() -> None:
    global _connection, _checkpointer
    if _checkpointer is not None:
        return
    settings = get_settings()
    settings.langgraph_checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    _connection = await aiosqlite.connect(str(settings.langgraph_checkpoint_path))
    _checkpointer = AsyncSqliteSaver(_connection)
    await _checkpointer.setup()


async def close_graph_checkpointer() -> None:
    global _connection, _checkpointer
    _checkpointer = None
    if _connection is not None:
        await _connection.close()
        _connection = None


def get_graph_checkpointer() -> AsyncSqliteSaver | None:
    return _checkpointer


async def read_graph_checkpoint(thread_id: str) -> dict[str, Any] | None:
    if _checkpointer is None:
        return None
    config = {"configurable": {"thread_id": thread_id}}
    checkpoint = await _checkpointer.aget_tuple(config)
    if checkpoint is None:
        return None
    values = checkpoint.checkpoint.get("channel_values") or {}
    task_state = values.get("task_state")
    if hasattr(task_state, "model_dump"):
        task_state = task_state.model_dump(mode="json")
    return {
        "thread_id": thread_id,
        "checkpoint_id": checkpoint.config.get("configurable", {}).get("checkpoint_id", ""),
        "status": values.get("status", "running"),
        "task": values.get("task", ""),
        "run_id": values.get("run_id", ""),
        "task_state": task_state or {},
        "final_result": values.get("final_result") or {},
    }
