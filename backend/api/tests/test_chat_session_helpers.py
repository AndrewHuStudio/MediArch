import asyncio
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class _FakeSessionRepository:
    def __init__(self):
        self.sessions = {}

    def get_or_create_session(self, session_id=None):
        session_id = session_id or "session-1"
        self.sessions.setdefault(
            session_id,
            {
                "session_id": session_id,
                "created_at": 1.0,
                "last_active": 1.0,
                "title": "New Chat",
                "is_pinned": False,
                "message_count": 0,
                "messages": [],
            },
        )
        self.sessions[session_id]["last_active"] += 1.0
        return session_id

    def add_message(self, session_id, role, content, citations=None, images=None):
        session = self.sessions[session_id]
        session["messages"].append(
            {
                "role": role,
                "content": content,
                "timestamp": float(len(session["messages"]) + 1),
                "citations": citations or [],
                "images": images or [],
            }
        )
        session["message_count"] = len(session["messages"])
        if role == "user" and session["title"] == "New Chat":
            session["title"] = content[:50] + ("..." if len(content) > 50 else "")

    def list_sessions(self):
        sessions = [
            {
                "session_id": value["session_id"],
                "created_at": value["created_at"],
                "last_active": value["last_active"],
                "message_count": value["message_count"],
                "title": value["title"],
                "is_pinned": value["is_pinned"],
            }
            for value in self.sessions.values()
        ]
        sessions.sort(key=lambda item: (-int(item["is_pinned"]), -item["last_active"]))
        return sessions

    def get_session_history(self, session_id):
        session = self.sessions.get(session_id)
        return list(session["messages"]) if session else None

    def update_session(self, session_id, *, title=None, is_pinned=None):
        session = self.sessions.get(session_id)
        if session is None:
            return None
        if title is not None:
            session["title"] = title
        if is_pinned is not None:
            session["is_pinned"] = is_pinned
        session["last_active"] += 1.0
        return {
            "session_id": session_id,
            "title": session["title"],
            "is_pinned": session["is_pinned"],
        }

    def delete_session(self, session_id):
        return self.sessions.pop(session_id, None) is not None


def test_chat_session_helpers_and_routes_use_persistent_store(monkeypatch):
    monkeypatch.setenv("DEBUG", "true")
    import backend.api.routers.chat as chat_router

    fake_repo = _FakeSessionRepository()
    monkeypatch.setattr(chat_router, "SESSION_REPOSITORY", fake_repo)

    session_id = chat_router._get_or_create_session("session-a")
    chat_router._add_message_to_session(session_id, "user", "hello")
    chat_router._add_message_to_session(session_id, "assistant", "hi")

    sessions_response = asyncio.run(chat_router.list_sessions())
    history_response = asyncio.run(chat_router.get_session_history(session_id))
    update_response = asyncio.run(
        chat_router.update_session(
            session_id,
            chat_router.SessionUpdateRequest(title="Pinned Session", is_pinned=True),
        )
    )
    delete_response = asyncio.run(chat_router.delete_session(session_id))

    assert session_id == "session-a"
    assert sessions_response.total == 1
    assert sessions_response.sessions[0].session_id == session_id
    assert history_response.total == 2
    assert history_response.messages[0].content == "hello"
    assert update_response["title"] == "Pinned Session"
    assert update_response["is_pinned"] is True
    assert delete_response["session_id"] == session_id
