"""RPC entry point for the stitch-notebooklm service plugin.

Spawned by ``ServicePluginHost`` as ``python -m stitch_notebooklm``.
Implements the JSON-RPC 2.0 line protocol via ``RpcPluginServer``
(imported from ``autoreg.plugin.rpc`` when available, otherwise from
the vendored ``_vendor/rpc_server.py`` copy).

Protocol methods handled by ``RpcPluginServer``:
  - ``plugin.init``   → stores handshake params (db_path, data_dir, ...).
  - ``plugin.call``   → dispatches to command handlers.
  - ``plugin.ping``   → returns ``"pong"``.
  - ``plugin.shutdown``→ returns ``None`` and exits.

Commands:
  - ``list_notebooks``   → local SQLite read (empty when no account).
  - ``create_notebook``  → local SQLite insert + return record.
  - ``ask``              → graceful degradation (returns empty answer
                           when no cookies; real call when cookies
                           provided and notebooklm-py installed).
  - ``generate_audio``   → same graceful degradation as ``ask``.
  - ``_migrate_db``      → creates SQLite tables.
"""

# _generated_by: stitch_plugin_tools scaffold v3

from __future__ import annotations

import asyncio
import os
from typing import Any

from . import storage
from .service import NotebookLMService, NotebookLMUnavailableError, write_storage_state_file

try:
    from autoreg.plugin.rpc import RpcPluginServer
except ImportError:
    from ._vendor.rpc_server import RpcPluginServer


# ── State received in plugin.init handshake ───────────────────────────────


class _Ctx:
    """Mutable container for plugin.init handshake state."""

    db_path: str = ""
    data_dir: str = ""


ctx = _Ctx()


def _uid(params: dict[str, Any]) -> int | None:
    """Caller user id forwarded by the namespaced route or SPI proxy.

    Reads ``caller_user_id`` (namespaced route) first, then ``owner_id``
    (SPI proxy path).  None = guest (shared/instance-wide notebooks).
    """
    uid = params.get("caller_user_id")
    if uid is None:
        uid = params.get("owner_id")
    return int(uid) if uid is not None else None


def _handle_init(params: dict[str, Any]) -> dict[str, Any]:
    """Store handshake params and return them as the init result."""
    ctx.db_path = str(params.get("db_path", ""))
    ctx.data_dir = str(params.get("data_dir", ""))
    return {
        "plugin_id": params.get("plugin_id", ""),
        "db_path": ctx.db_path,
        "data_dir": ctx.data_dir,
        # Capability negotiation: no reverse-RPC used.  Declared
        # explicitly for contract uniformity.
        "capabilities": [],
    }


def _handle_migrate_db(params: dict[str, Any]) -> dict[str, Any]:
    """Create SQLite tables (raw_sql migration, from_version→to_version)."""
    if ctx.db_path:
        storage.migrate(ctx.db_path)
    return {"from_version": params.get("from_version", 0), "to_version": params.get("to_version", 1)}


def _handle_list_notebooks(params: dict[str, Any]) -> list[dict[str, Any]]:
    """Return notebooks from local storage (empty when no data).

    Scoped by owner_id: caller sees own + shared (NULL owner) notebooks.
    """
    if not ctx.db_path:
        return []
    return storage.list_notebooks(ctx.db_path, _uid(params))


def _handle_create_notebook(params: dict[str, Any]) -> dict[str, Any]:
    """Create a notebook in local storage and return it.

    Stamps owner_id = caller (None for guest → shared/instance-wide).
    """
    title = str(params.get("title", "Untitled"))
    if not ctx.db_path:
        return {"id": "", "title": title}
    return storage.create_notebook(ctx.db_path, title, _uid(params))


def _handle_ask(params: dict[str, Any]) -> dict[str, Any]:
    """Ask a question to a notebook.

    When ``cookies`` param is provided and notebooklm-py is installed,
    calls the real Google API.  Otherwise returns a graceful empty answer.
    """
    cookies = str(params.get("cookies", ""))
    notebook_id = str(params.get("notebookId", params.get("notebook_id", "")))
    question = str(params.get("question", ""))
    if not notebook_id or not question:
        return {"answer": ""}
    if not cookies:
        return {"answer": ""}
    try:
        path = write_storage_state_file(cookies)
        try:
            service = NotebookLMService()
            text = asyncio.run(service.ask(path, notebook_id, question))
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        return {"answer": text}
    except NotebookLMUnavailableError:
        return {"answer": ""}


def _handle_generate_audio(params: dict[str, Any]) -> dict[str, Any]:
    """Generate audio for a notebook.

    When ``cookies`` param is provided and notebooklm-py is installed,
    calls the real Google API.  Otherwise returns a graceful empty result.
    """
    cookies = str(params.get("cookies", ""))
    notebook_id = str(params.get("notebookId", params.get("notebook_id", "")))
    instructions = str(params.get("instructions", ""))
    if not notebook_id:
        return {"task_id": ""}
    if not cookies:
        return {"task_id": ""}
    try:
        path = write_storage_state_file(cookies)
        try:
            service = NotebookLMService()
            result = asyncio.run(
                service.generate_audio(path, notebook_id, instructions)
            )
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        return result
    except NotebookLMUnavailableError:
        return {"task_id": ""}


# ── Server entry point ────────────────────────────────────────────────────


def main() -> None:
    """Register handlers and serve the JSON-RPC loop."""
    server = RpcPluginServer()
    server.set_init_handler(_handle_init)
    server.register("_migrate_db", _handle_migrate_db)
    server.register("list_notebooks", _handle_list_notebooks)
    server.register("create_notebook", _handle_create_notebook)
    server.register("ask", _handle_ask)
    server.register("generate_audio", _handle_generate_audio)
    server.serve()


if __name__ == "__main__":
    main()
