from __future__ import annotations

import atexit
import asyncio
import json
import logging
import random
import sqlite3
import threading
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.store.base import (
    BaseStore,
    GetOp,
    Item,
    ListNamespacesOp,
    Op,
    PutOp,
    Result,
    SearchItem,
    SearchOp,
)


try:
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    POSTGRES_CHECKPOINTER_AVAILABLE = True
except Exception:
    PostgresSaver = None
    AsyncPostgresSaver = None
    POSTGRES_CHECKPOINTER_AVAILABLE = False

try:
    from langgraph.store.postgres import PostgresStore

    POSTGRES_STORE_AVAILABLE = True
except Exception:
    PostgresStore = None
    POSTGRES_STORE_AVAILABLE = False


SQLITE_BACKEND_AVAILABLE = True
_OPEN_CONTEXT_MANAGERS: list[Any] = []
logger = logging.getLogger("mediarch_graph.persistence")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_path(path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _encode_namespace(namespace: tuple[str, ...]) -> str:
    return json.dumps(list(namespace), ensure_ascii=False, separators=(",", ":"))


def _decode_namespace(raw: str) -> tuple[str, ...]:
    return tuple(json.loads(raw))


def _encode_version(version: str | int | float) -> str:
    return json.dumps(version, ensure_ascii=False, separators=(",", ":"))


def _parse_timestamp(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


def _json_matches_filter(value: dict[str, Any], filter_dict: dict[str, Any] | None) -> bool:
    if not filter_dict:
        return True
    return all(value.get(key) == expected for key, expected in filter_dict.items())


def _activate_managed_resource(resource: Any) -> Any:
    if hasattr(resource, "__enter__") and hasattr(resource, "__exit__"):
        entered = resource.__enter__()
        _OPEN_CONTEXT_MANAGERS.append(resource)
        return entered
    return resource


@atexit.register
def _close_managed_resources() -> None:
    while _OPEN_CONTEXT_MANAGERS:
        manager = _OPEN_CONTEXT_MANAGERS.pop()
        try:
            manager.__exit__(None, None, None)
        except Exception:
            continue


class SQLiteStore(BaseStore, AbstractContextManager["SQLiteStore"]):
    """Minimal durable LangGraph store backed by sqlite3."""

    def __init__(self, path: str | Path):
        self.path = _normalize_path(path)
        self._lock = threading.RLock()
        self._init_db()

    def __enter__(self) -> "SQLiteStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS store_items (
                    namespace TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (namespace, key)
                )
                """
            )
            conn.commit()

    def batch(self, ops: Iterable[Op]) -> list[Result]:
        operations = list(ops)
        results: list[Result] = []

        with self._lock, self._connect() as conn:
            for op in operations:
                if isinstance(op, GetOp):
                    results.append(self._handle_get(conn, op))
                elif isinstance(op, SearchOp):
                    results.append(self._handle_search(conn, op))
                elif isinstance(op, ListNamespacesOp):
                    results.append(self._handle_list_namespaces(conn, op))
                elif isinstance(op, PutOp):
                    self._handle_put(conn, op)
                    results.append(None)
                else:
                    raise ValueError(f"Unknown operation type: {type(op)}")
            conn.commit()

        return results

    async def abatch(self, ops: Iterable[Op]) -> list[Result]:
        operations = list(ops)
        return await asyncio.to_thread(self.batch, operations)

    def _handle_get(self, conn: sqlite3.Connection, op: GetOp) -> Item | None:
        row = conn.execute(
            """
            SELECT namespace, key, value_json, created_at, updated_at
            FROM store_items
            WHERE namespace = ? AND key = ?
            """,
            (_encode_namespace(op.namespace), op.key),
        ).fetchone()
        if row is None:
            return None
        return Item(
            namespace=_decode_namespace(row["namespace"]),
            key=row["key"],
            value=json.loads(row["value_json"]),
            created_at=_parse_timestamp(row["created_at"]),
            updated_at=_parse_timestamp(row["updated_at"]),
        )

    def _handle_search(self, conn: sqlite3.Connection, op: SearchOp) -> list[SearchItem]:
        rows = conn.execute(
            """
            SELECT namespace, key, value_json, created_at, updated_at
            FROM store_items
            """
        ).fetchall()
        matched: list[SearchItem] = []
        for row in rows:
            namespace = _decode_namespace(row["namespace"])
            if namespace[: len(op.namespace_prefix)] != op.namespace_prefix:
                continue
            value = json.loads(row["value_json"])
            if not _json_matches_filter(value, op.filter):
                continue
            if op.query:
                haystack = json.dumps(value, ensure_ascii=False)
                if str(op.query) not in haystack:
                    continue
            matched.append(
                SearchItem(
                    namespace=namespace,
                    key=row["key"],
                    value=value,
                    created_at=_parse_timestamp(row["created_at"]),
                    updated_at=_parse_timestamp(row["updated_at"]),
                    score=None,
                )
            )
        return matched[op.offset : op.offset + op.limit]

    def _handle_list_namespaces(
        self, conn: sqlite3.Connection, op: ListNamespacesOp
    ) -> list[tuple[str, ...]]:
        rows = conn.execute("SELECT DISTINCT namespace FROM store_items").fetchall()
        namespaces = [_decode_namespace(row["namespace"]) for row in rows]

        def _matches(namespace: tuple[str, ...]) -> bool:
            for condition in op.match_conditions:
                path = tuple(condition.path)
                if condition.match_type == "prefix" and namespace[: len(path)] != path:
                    return False
                if condition.match_type == "suffix" and namespace[-len(path) :] != path:
                    return False
            return True

        filtered = [ns for ns in namespaces if _matches(ns)]
        if op.max_depth is not None:
            filtered = sorted({ns[: op.max_depth] for ns in filtered})
        else:
            filtered = sorted(filtered)
        return filtered[op.offset : op.offset + op.limit]

    def _handle_put(self, conn: sqlite3.Connection, op: PutOp) -> None:
        namespace = _encode_namespace(op.namespace)
        if op.value is None:
            conn.execute(
                "DELETE FROM store_items WHERE namespace = ? AND key = ?",
                (namespace, op.key),
            )
            return

        existing = conn.execute(
            "SELECT created_at FROM store_items WHERE namespace = ? AND key = ?",
            (namespace, op.key),
        ).fetchone()
        now = _utc_now().isoformat()
        created_at = existing["created_at"] if existing else now
        conn.execute(
            """
            INSERT OR REPLACE INTO store_items(namespace, key, value_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                namespace,
                op.key,
                json.dumps(op.value, ensure_ascii=False, separators=(",", ":")),
                created_at,
                now,
            ),
        )


class SQLiteCheckpointSaver(BaseCheckpointSaver[str], AbstractContextManager["SQLiteCheckpointSaver"]):
    """Minimal durable checkpoint saver backed by sqlite3."""

    def __init__(self, path: str | Path, *, serde=None):
        super().__init__(serde=serde)
        self.path = _normalize_path(path)
        self._lock = threading.RLock()
        self._init_db()

    def __enter__(self) -> "SQLiteCheckpointSaver":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    thread_id TEXT NOT NULL,
                    checkpoint_ns TEXT NOT NULL,
                    checkpoint_id TEXT NOT NULL,
                    checkpoint_type TEXT NOT NULL,
                    checkpoint_blob BLOB NOT NULL,
                    metadata_type TEXT NOT NULL,
                    metadata_blob BLOB NOT NULL,
                    parent_checkpoint_id TEXT,
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoint_blobs (
                    thread_id TEXT NOT NULL,
                    checkpoint_ns TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    version TEXT NOT NULL,
                    value_type TEXT NOT NULL,
                    value_blob BLOB NOT NULL,
                    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoint_writes (
                    thread_id TEXT NOT NULL,
                    checkpoint_ns TEXT NOT NULL,
                    checkpoint_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    write_idx INTEGER NOT NULL,
                    channel TEXT NOT NULL,
                    value_type TEXT NOT NULL,
                    value_blob BLOB NOT NULL,
                    task_path TEXT NOT NULL,
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, write_idx)
                )
                """
            )
            conn.commit()

    def _load_blobs(
        self,
        conn: sqlite3.Connection,
        thread_id: str,
        checkpoint_ns: str,
        versions: ChannelVersions,
    ) -> dict[str, Any]:
        channel_values: dict[str, Any] = {}
        for channel, version in versions.items():
            row = conn.execute(
                """
                SELECT value_type, value_blob
                FROM checkpoint_blobs
                WHERE thread_id = ? AND checkpoint_ns = ? AND channel = ? AND version = ?
                """,
                (thread_id, checkpoint_ns, channel, _encode_version(version)),
            ).fetchone()
            if row is None:
                continue
            typed_value = (row["value_type"], row["value_blob"])
            if typed_value[0] != "empty":
                channel_values[channel] = self.serde.loads_typed(typed_value)
        return channel_values

    def _build_tuple(
        self,
        conn: sqlite3.Connection,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
        checkpoint_typed: tuple[str, bytes],
        metadata_typed: tuple[str, bytes],
        parent_checkpoint_id: str | None,
    ) -> CheckpointTuple:
        checkpoint = self.serde.loads_typed(checkpoint_typed)
        metadata = self.serde.loads_typed(metadata_typed)
        writes_rows = conn.execute(
            """
            SELECT task_id, channel, value_type, value_blob
            FROM checkpoint_writes
            WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
            ORDER BY write_idx ASC, task_id ASC
            """,
            (thread_id, checkpoint_ns, checkpoint_id),
        ).fetchall()
        pending_writes = [
            (
                row["task_id"],
                row["channel"],
                self.serde.loads_typed((row["value_type"], row["value_blob"])),
            )
            for row in writes_rows
        ]
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                }
            },
            checkpoint={
                **checkpoint,
                "channel_values": self._load_blobs(
                    conn, thread_id, checkpoint_ns, checkpoint["channel_versions"]
                ),
            },
            metadata=metadata,
            parent_config=(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": parent_checkpoint_id,
                    }
                }
                if parent_checkpoint_id
                else None
            ),
            pending_writes=pending_writes,
        )

    def get_tuple(self, config) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = get_checkpoint_id(config)

        with self._lock, self._connect() as conn:
            if checkpoint_id:
                row = conn.execute(
                    """
                    SELECT checkpoint_id, checkpoint_type, checkpoint_blob, metadata_type, metadata_blob, parent_checkpoint_id
                    FROM checkpoints
                    WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                    """,
                    (thread_id, checkpoint_ns, checkpoint_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT checkpoint_id, checkpoint_type, checkpoint_blob, metadata_type, metadata_blob, parent_checkpoint_id
                    FROM checkpoints
                    WHERE thread_id = ? AND checkpoint_ns = ?
                    ORDER BY checkpoint_id DESC
                    LIMIT 1
                    """,
                    (thread_id, checkpoint_ns),
                ).fetchone()

            if row is None:
                return None

            return self._build_tuple(
                conn,
                thread_id,
                checkpoint_ns,
                row["checkpoint_id"],
                (row["checkpoint_type"], row["checkpoint_blob"]),
                (row["metadata_type"], row["metadata_blob"]),
                row["parent_checkpoint_id"],
            )

    def list(
        self,
        config,
        *,
        filter: dict[str, Any] | None = None,
        before=None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        with self._lock, self._connect() as conn:
            if config:
                thread_id = config["configurable"]["thread_id"]
                checkpoint_ns = config["configurable"].get("checkpoint_ns")
                rows = conn.execute(
                    """
                    SELECT thread_id, checkpoint_ns, checkpoint_id, checkpoint_type, checkpoint_blob, metadata_type, metadata_blob, parent_checkpoint_id
                    FROM checkpoints
                    WHERE thread_id = ?
                    ORDER BY checkpoint_id DESC
                    """,
                    (thread_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT thread_id, checkpoint_ns, checkpoint_id, checkpoint_type, checkpoint_blob, metadata_type, metadata_blob, parent_checkpoint_id
                    FROM checkpoints
                    ORDER BY checkpoint_id DESC
                    """
                ).fetchall()

            config_checkpoint_id = get_checkpoint_id(config) if config else None
            before_checkpoint_id = get_checkpoint_id(before) if before else None
            remaining = limit

            for row in rows:
                if config and "checkpoint_ns" in config["configurable"]:
                    if row["checkpoint_ns"] != config["configurable"].get("checkpoint_ns", ""):
                        continue
                if config_checkpoint_id and row["checkpoint_id"] != config_checkpoint_id:
                    continue
                if before_checkpoint_id and row["checkpoint_id"] >= before_checkpoint_id:
                    continue

                metadata = self.serde.loads_typed(
                    (row["metadata_type"], row["metadata_blob"])
                )
                if filter and not all(metadata.get(key) == value for key, value in filter.items()):
                    continue

                if remaining is not None and remaining <= 0:
                    break
                if remaining is not None:
                    remaining -= 1

                yield self._build_tuple(
                    conn,
                    row["thread_id"],
                    row["checkpoint_ns"],
                    row["checkpoint_id"],
                    (row["checkpoint_type"], row["checkpoint_blob"]),
                    (row["metadata_type"], row["metadata_blob"]),
                    row["parent_checkpoint_id"],
                )

    def put(
        self,
        config,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ):
        c = checkpoint.copy()
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        values: dict[str, Any] = c.pop("channel_values")

        with self._lock, self._connect() as conn:
            for channel, version in new_versions.items():
                if channel in values:
                    value_type, value_blob = self.serde.dumps_typed(values[channel])
                else:
                    value_type, value_blob = ("empty", b"")
                conn.execute(
                    """
                    INSERT OR REPLACE INTO checkpoint_blobs(thread_id, checkpoint_ns, channel, version, value_type, value_blob)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        thread_id,
                        checkpoint_ns,
                        channel,
                        _encode_version(version),
                        value_type,
                        value_blob,
                    ),
                )

            checkpoint_type, checkpoint_blob = self.serde.dumps_typed(c)
            metadata_type, metadata_blob = self.serde.dumps_typed(
                get_checkpoint_metadata(config, metadata)
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO checkpoints(
                    thread_id, checkpoint_ns, checkpoint_id, checkpoint_type, checkpoint_blob,
                    metadata_type, metadata_blob, parent_checkpoint_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    checkpoint_ns,
                    checkpoint["id"],
                    checkpoint_type,
                    checkpoint_blob,
                    metadata_type,
                    metadata_blob,
                    config["configurable"].get("checkpoint_id"),
                ),
            )
            conn.commit()

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]

        with self._lock, self._connect() as conn:
            for idx, (channel, value) in enumerate(writes):
                write_idx = WRITES_IDX_MAP.get(channel, idx)
                value_type, value_blob = self.serde.dumps_typed(value)
                query = (
                    """
                    INSERT OR REPLACE INTO checkpoint_writes(
                        thread_id, checkpoint_ns, checkpoint_id, task_id, write_idx,
                        channel, value_type, value_blob, task_path
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """
                    if write_idx < 0
                    else """
                    INSERT OR IGNORE INTO checkpoint_writes(
                        thread_id, checkpoint_ns, checkpoint_id, task_id, write_idx,
                        channel, value_type, value_blob, task_path
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """
                )
                conn.execute(
                    query,
                    (
                        thread_id,
                        checkpoint_ns,
                        checkpoint_id,
                        task_id,
                        write_idx,
                        channel,
                        value_type,
                        value_blob,
                        task_path,
                    ),
                )
            conn.commit()

    def delete_thread(self, thread_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
            conn.execute("DELETE FROM checkpoint_blobs WHERE thread_id = ?", (thread_id,))
            conn.execute("DELETE FROM checkpoint_writes WHERE thread_id = ?", (thread_id,))
            conn.commit()

    async def aget_tuple(self, config) -> CheckpointTuple | None:
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(
        self,
        config,
        *,
        filter: dict[str, Any] | None = None,
        before=None,
        limit: int | None = None,
    ):
        items = await asyncio.to_thread(
            lambda: list(self.list(config, filter=filter, before=before, limit=limit))
        )
        for item in items:
            yield item

    async def aput(
        self,
        config,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ):
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        await asyncio.to_thread(self.delete_thread, thread_id)

    def get_next_version(self, current: str | None, channel: None) -> str:
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            current_v = int(str(current).split(".")[0])
        next_v = current_v + 1
        next_h = random.random()
        return f"{next_v:032}.{next_h:016}"


def create_store_from_runtime(
    status: dict[str, Any],
    *,
    sqlite_path: str | Path,
    postgres_uri: str | None = None,
):
    effective = status["effective_backend"]
    if effective == "postgres" and POSTGRES_STORE_AVAILABLE and PostgresStore is not None:
        store = _activate_managed_resource(PostgresStore.from_conn_string(str(postgres_uri)))
        if hasattr(store, "setup"):
            store.setup()
        return store
    if effective == "sqlite":
        return SQLiteStore(sqlite_path)
    from langgraph.store.memory import InMemoryStore

    return InMemoryStore()


def create_checkpointer_from_runtime(
    status: dict[str, Any],
    *,
    sqlite_path: str | Path,
    postgres_uri: str | None = None,
):
    effective = status["effective_backend"]
    if effective == "platform":
        return None
    if effective == "postgres" and POSTGRES_CHECKPOINTER_AVAILABLE and AsyncPostgresSaver is not None:
        try:
            from psycopg_pool import AsyncConnectionPool
        except ModuleNotFoundError as exc:
            logger.warning(
                "[Persistence] psycopg_pool 不可用，回退到本地 checkpointer: %s",
                exc,
            )
            if SQLITE_BACKEND_AVAILABLE:
                return SQLiteCheckpointSaver(sqlite_path)
            from langgraph.checkpoint.memory import MemorySaver

            return MemorySaver()
        pool = AsyncConnectionPool(conninfo=str(postgres_uri), open=False)
        try:
            saver = AsyncPostgresSaver(conn=pool)
        except RuntimeError as exc:
            if "running event loop" not in str(exc):
                raise
            logger.warning(
                "[Persistence] AsyncPostgresSaver 初始化失败，回退到本地 checkpointer: %s",
                exc,
            )
            awaitable_close = getattr(pool, "close", None)
            if callable(awaitable_close):
                maybe_coro = awaitable_close()
                if asyncio.iscoroutine(maybe_coro):
                    try:
                        maybe_coro.close()
                    except Exception:
                        pass
            if SQLITE_BACKEND_AVAILABLE:
                return SQLiteCheckpointSaver(sqlite_path)
            from langgraph.checkpoint.memory import MemorySaver

            return MemorySaver()
        saver._pool = pool  # lifespan 中需要 open + setup
        return saver
    if effective == "sqlite":
        return SQLiteCheckpointSaver(sqlite_path)
    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()
