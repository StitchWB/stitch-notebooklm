"""NotebookLM service logic — adapted from stitch_backend.domains.notebooklm.service.

The plugin owns a copy of the cookie-conversion + client-factory logic so
it can call the real Google NotebookLM API when cookies are provided.
When no cookies are available, commands fall back to local SQLite storage
(graceful degradation — the plugin still responds, just without live data).

The original domain files stay untouched as the dual-format fallback
(plan plugin-platform-v2 todo 11/24).
"""

# _generated_by: stitch_plugin_tools scaffold v3

from __future__ import annotations

import json
import tempfile
from typing import Any


class NotebookLMUnavailableError(RuntimeError):
    """Raised when the optional ``notebooklm`` dependency is not installed."""


def cookies_to_storage_state(cookies: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert CDP/harvester cookie dicts to a Playwright storage-state dict."""
    out: list[dict[str, Any]] = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name", ""))
        if not name:
            continue
        same_site = str(cookie.get("sameSite", "Lax") or "Lax")
        if same_site not in ("Strict", "Lax", "None"):
            same_site = "Lax"
        expires = cookie.get("expires", -1)
        try:
            expires = float(expires) if expires else -1
        except (TypeError, ValueError):
            expires = -1
        out.append(
            {
                "name": name,
                "value": str(cookie.get("value", "")),
                "domain": str(cookie.get("domain", ".google.com")),
                "path": str(cookie.get("path", "/")),
                "expires": expires,
                "httpOnly": bool(cookie.get("httpOnly", False)),
                "secure": bool(cookie.get("secure", True)),
                "sameSite": same_site,
            }
        )
    return {"cookies": out, "origins": []}


def parse_account_cookies(raw: str) -> list[dict[str, Any]]:
    """Accept either a JSON cookie list or a jar string."""
    raw = (raw or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            data = json.loads(raw)
            return [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []
    cookies = []
    for part in raw.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        cookies.append(
            {"name": name.strip(), "value": value.strip(), "domain": ".google.com"}
        )
    return cookies


def write_storage_state_file(cookies_raw: str) -> str:
    """Materialise cookies as a temp storage-state file. Caller cleans up."""
    cookies = parse_account_cookies(cookies_raw)
    state = cookies_to_storage_state(cookies)
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    with handle:
        json.dump(state, handle)
    return handle.name


def _default_client_factory(storage_path: str) -> Any:
    """Return NotebookLMClient.from_storage(path) (async ctx manager)."""
    try:
        from notebooklm import NotebookLMClient
    except Exception as exc:
        raise NotebookLMUnavailableError(
            "notebooklm-py is not installed. Run: pip install notebooklm-py"
        ) from exc
    return NotebookLMClient.from_storage(storage_path)


class NotebookLMService:
    """Wraps NotebookLM operations behind a DI client factory."""

    def __init__(self, client_factory: Any | None = None) -> None:
        self._client_factory = client_factory or _default_client_factory

    async def _with_client(self, operation: Any, storage_path: str) -> Any:
        ctx = self._client_factory(storage_path)
        async with ctx as client:
            return await operation(client)

    async def list_notebooks(self, storage_path: str) -> list[dict[str, Any]]:
        async def op(client: Any) -> list[dict[str, Any]]:
            notebooks = await client.notebooks.list()
            return [
                {"id": getattr(nb, "id", ""), "title": getattr(nb, "title", "")}
                for nb in notebooks
            ]
        return await self._with_client(op, storage_path)

    async def create_notebook(self, storage_path: str, title: str) -> dict[str, Any]:
        async def op(client: Any) -> dict[str, Any]:
            nb = await client.notebooks.create(title)
            return {"id": getattr(nb, "id", ""), "title": getattr(nb, "title", title)}
        return await self._with_client(op, storage_path)

    async def ask(self, storage_path: str, notebook_id: str, question: str) -> str:
        async def op(client: Any) -> str:
            result = await client.chat.ask(notebook_id, question)
            return getattr(result, "text", str(result))
        return await self._with_client(op, storage_path)

    async def generate_audio(self, storage_path: str, notebook_id: str, instructions: str = "") -> dict[str, Any]:
        async def op(client: Any) -> dict[str, Any]:
            status = await client.artifacts.generate_audio(
                notebook_id, instructions=instructions or None
            )
            return {"task_id": getattr(status, "task_id", "")}
        return await self._with_client(op, storage_path)
