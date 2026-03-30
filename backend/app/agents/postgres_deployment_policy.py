from __future__ import annotations

import os
from typing import Any, Mapping


DEFAULT_SHARED_POSTGRES_URI = (
    "postgresql://postgres:mediarch_password_2024@localhost:5432/postgres?sslmode=disable"
)


def _is_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def get_shared_postgres_uri() -> str:
    return (
        os.getenv("PERSISTENCE_POSTGRES_URI")
        or os.getenv("POSTGRES_SHARED_URI")
        or os.getenv("POSTGRES_STORE_URI")
        or os.getenv("POSTGRES_CHECKPOINT_URI")
        or os.getenv("POSTGRES_SESSION_STORE_URI")
        or DEFAULT_SHARED_POSTGRES_URI
    )


def validate_required_postgres_persistence(
    *,
    require_postgres: bool | str,
    component_statuses: Mapping[str, Mapping[str, Any] | None],
) -> None:
    if not _is_truthy(require_postgres):
        return

    failures: list[str] = []
    for component_name, status in component_statuses.items():
        if not status:
            failures.append(f"{component_name}(missing_status)")
            continue

        effective = str(status.get("effective_backend") or "").strip().lower()
        configured = str(status.get("configured_backend") or "").strip().lower()
        fallback_reason = status.get("fallback_reason")
        if effective != "postgres":
            failures.append(
                f"{component_name}(configured={configured or 'unknown'}, "
                f"effective={effective or 'unknown'}, "
                f"fallback_reason={fallback_reason or 'none'})"
            )

    if failures:
        raise RuntimeError(
            "Shared Postgres persistence is required but not active: "
            + ", ".join(failures)
        )
