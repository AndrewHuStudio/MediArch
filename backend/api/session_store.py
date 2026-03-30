from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.app.agents.persistence import (
    POSTGRES_STORE_AVAILABLE,
    SQLITE_BACKEND_AVAILABLE,
    create_store_from_runtime,
)
from backend.app.agents.postgres_deployment_policy import get_shared_postgres_uri
from backend.app.agents.runtime_policy import resolve_store_runtime_status


SESSION_METADATA_NAMESPACE = ("api_sessions", "metadata")


def _default_session_store_backend() -> str:
    return (
        os.getenv("SESSION_STORE_BACKEND")
        or os.getenv("STORE_BACKEND")
        or os.getenv("CHECKPOINT_BACKEND")
        or "sqlite"
    )


def _default_session_store_path() -> str:
    return os.getenv("SQLITE_SESSION_STORE_PATH") or os.getenv("SQLITE_STORE_PATH") or ".langgraph_api/store.db"


def _default_session_store_uri() -> str:
    return (
        os.getenv("POSTGRES_SESSION_STORE_URI")
        or os.getenv("POSTGRES_STORE_URI")
        or os.getenv("POSTGRES_CHECKPOINT_URI")
        or get_shared_postgres_uri()
    )


def _message_namespace(session_id: str) -> tuple[str, ...]:
    return ("api_sessions", "messages", session_id)


class PersistentSessionStore:
    def __init__(self, store: Any, *, max_history: int = 20):
        self.store = store
        self.max_history = max(1, int(max_history))

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        item = self.store.get(SESSION_METADATA_NAMESPACE, session_id)
        return dict(item.value) if item else None

    def get_or_create_session(self, session_id: str | None = None) -> str:
        existing = self.get_session(session_id) if session_id else None
        if existing is not None:
            existing["last_active"] = time.time()
            self._save_session(existing["session_id"], existing)
            return existing["session_id"]

        new_session_id = session_id or f"session-{uuid.uuid4().hex[:16]}"
        now = time.time()
        session = {
            "session_id": new_session_id,
            "created_at": now,
            "last_active": now,
            "title": "New Chat",
            "is_pinned": False,
            "message_count": 0,
            "last_sequence": 0,
        }
        self._save_session(new_session_id, session)
        return new_session_id

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        citations: Optional[List[Dict[str, Any]]] = None,
        images: Optional[List[str]] = None,
    ) -> None:
        session = self.get_session(session_id)
        if session is None:
            return

        timestamp = time.time()
        next_sequence = int(session.get("last_sequence", 0)) + 1
        message = {
            "role": role,
            "content": content,
            "timestamp": timestamp,
            "sequence": next_sequence,
            "citations": citations or [],
            "images": images or [],
        }
        message_key = f"{timestamp:.6f}-{uuid.uuid4().hex}"
        self.store.put(_message_namespace(session_id), message_key, message)

        messages = self._list_message_items(session_id)
        if len(messages) > self.max_history:
            for stale in messages[: len(messages) - self.max_history]:
                self.store.delete(_message_namespace(session_id), stale["key"])
            messages = messages[len(messages) - self.max_history :]

        if role == "user" and session.get("title") == "New Chat":
            session["title"] = content[:50] + ("..." if len(content) > 50 else "")

        session["last_active"] = timestamp
        session["message_count"] = len(messages)
        session["last_sequence"] = next_sequence
        self._save_session(session_id, session)

    def list_sessions(self) -> List[Dict[str, Any]]:
        items = self.store.search(SESSION_METADATA_NAMESPACE, limit=10000)
        sessions = [dict(item.value) for item in items]
        sessions.sort(key=lambda item: (-int(item.get("is_pinned", False)), -float(item.get("last_active", 0))))
        return sessions

    def get_session_history(self, session_id: str) -> Optional[List[Dict[str, Any]]]:
        session = self.get_session(session_id)
        if session is None:
            return None
        return [dict(item["value"]) for item in self._list_message_items(session_id)]

    def update_session(
        self,
        session_id: str,
        *,
        title: Optional[str] = None,
        is_pinned: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        session = self.get_session(session_id)
        if session is None:
            return None

        if title is not None:
            session["title"] = title
        if is_pinned is not None:
            session["is_pinned"] = is_pinned
        session["last_active"] = time.time()
        self._save_session(session_id, session)

        return {
            "session_id": session_id,
            "title": session["title"],
            "is_pinned": session["is_pinned"],
        }

    def delete_session(self, session_id: str) -> bool:
        session = self.get_session(session_id)
        if session is None:
            return False

        for item in self._list_message_items(session_id):
            self.store.delete(_message_namespace(session_id), item["key"])
        self.store.delete(SESSION_METADATA_NAMESPACE, session_id)
        return True

    def _save_session(self, session_id: str, session: Dict[str, Any]) -> None:
        self.store.put(SESSION_METADATA_NAMESPACE, session_id, session)

    def _list_message_items(self, session_id: str) -> List[Dict[str, Any]]:
        session = self.get_session(session_id)
        if session is None:
            return []

        limit = max(int(session.get("message_count", 0)) + 5, self.max_history, 1)
        items = self.store.search(_message_namespace(session_id), limit=limit)
        messages = [
            {
                "key": item.key,
                "value": dict(item.value),
            }
            for item in items
        ]
        messages.sort(
            key=lambda item: (
                float(item["value"].get("timestamp", 0)),
                int(item["value"].get("sequence", 0)),
                item["key"],
            )
        )
        return messages


def build_session_store_repository(
    *,
    max_history: int,
    backend: Optional[str] = None,
    sqlite_path: str | Path | None = None,
    postgres_uri: Optional[str] = None,
) -> PersistentSessionStore:
    runtime_status = resolve_session_store_runtime_status(backend=backend)
    store = create_store_from_runtime(
        runtime_status,
        sqlite_path=sqlite_path or _default_session_store_path(),
        postgres_uri=postgres_uri or _default_session_store_uri(),
    )
    repository = PersistentSessionStore(store, max_history=max_history)
    repository.runtime_status = runtime_status
    return repository


def resolve_session_store_runtime_status(*, backend: Optional[str] = None) -> Dict[str, Any]:
    return resolve_store_runtime_status(
        backend or _default_session_store_backend(),
        is_langgraph_api=False,
        sqlite_available=SQLITE_BACKEND_AVAILABLE,
        postgres_available=POSTGRES_STORE_AVAILABLE,
    )
