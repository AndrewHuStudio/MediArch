from __future__ import annotations

from typing import Any, Dict, Optional


SUPPORTED_PHASE1_RETRIEVAL_MODES = {"parallel", "neo4j_first"}
EXPERIMENT_PHASE1_RETRIEVAL_MODE = "parallel"
SUPPORTED_CHECKPOINT_BACKENDS = {"memory", "sqlite", "postgres"}
SUPPORTED_STORE_BACKENDS = {"memory", "sqlite", "postgres"}


def _normalize_phase1_runtime_mode(configured_mode: Optional[str]) -> str:
    normalized = str(configured_mode or EXPERIMENT_PHASE1_RETRIEVAL_MODE).strip().lower()
    if normalized not in SUPPORTED_PHASE1_RETRIEVAL_MODES:
        return EXPERIMENT_PHASE1_RETRIEVAL_MODE
    return normalized


def resolve_phase1_runtime_mode(configured_mode: Optional[str]) -> Dict[str, Any]:
    configured = _normalize_phase1_runtime_mode(configured_mode)
    effective = EXPERIMENT_PHASE1_RETRIEVAL_MODE
    is_forced = configured != effective

    return {
        "configured_mode": configured,
        "effective_mode": effective,
        "is_forced": is_forced,
    }


def build_phase1_runtime_diagnostics(configured_mode: Optional[str]) -> Dict[str, Any]:
    mode_info = resolve_phase1_runtime_mode(configured_mode)
    return {
        "phase1_retrieval_mode": {
            "configured": mode_info["configured_mode"],
            "effective": mode_info["effective_mode"],
            "is_forced": mode_info["is_forced"],
        }
    }


def resolve_checkpointer_runtime_status(
    configured_backend: Optional[str],
    *,
    is_langgraph_api: bool,
    sqlite_available: bool,
    postgres_available: bool,
) -> Dict[str, Any]:
    normalized = str(configured_backend or "memory").strip().lower() or "memory"
    if normalized not in SUPPORTED_CHECKPOINT_BACKENDS:
        normalized = "memory"

    if is_langgraph_api:
        return {
            "configured_backend": normalized,
            "effective_backend": "platform",
            "fallback_reason": None,
            "is_fallback": False,
        }

    if normalized == "postgres":
        if postgres_available:
            return {
                "configured_backend": normalized,
                "effective_backend": "postgres",
                "fallback_reason": None,
                "is_fallback": False,
            }
        if sqlite_available:
            return {
                "configured_backend": normalized,
                "effective_backend": "sqlite",
                "fallback_reason": "missing_postgres_backend",
                "is_fallback": True,
            }
        return {
            "configured_backend": normalized,
            "effective_backend": "memory",
            "fallback_reason": "missing_postgres_backend",
            "is_fallback": True,
        }

    if normalized == "sqlite":
        if sqlite_available:
            return {
                "configured_backend": normalized,
                "effective_backend": "sqlite",
                "fallback_reason": None,
                "is_fallback": False,
            }
        return {
            "configured_backend": normalized,
            "effective_backend": "memory",
            "fallback_reason": "missing_sqlite_backend",
            "is_fallback": True,
        }

    return {
        "configured_backend": normalized,
        "effective_backend": "memory",
        "fallback_reason": None,
        "is_fallback": False,
    }


def build_checkpointer_runtime_diagnostics(status: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "checkpointer_runtime": {
            "configured": status["configured_backend"],
            "effective": status["effective_backend"],
            "fallback_reason": status["fallback_reason"],
            "is_fallback": status["is_fallback"],
        }
    }


def resolve_store_runtime_status(
    configured_backend: Optional[str],
    *,
    is_langgraph_api: bool,
    sqlite_available: bool,
    postgres_available: bool,
) -> Dict[str, Any]:
    normalized = str(configured_backend or "sqlite").strip().lower() or "sqlite"
    if normalized not in SUPPORTED_STORE_BACKENDS:
        normalized = "sqlite"

    if normalized == "postgres":
        if postgres_available:
            return {
                "configured_backend": normalized,
                "effective_backend": "postgres",
                "fallback_reason": None,
                "is_fallback": False,
            }
        if sqlite_available:
            return {
                "configured_backend": normalized,
                "effective_backend": "sqlite",
                "fallback_reason": "missing_postgres_backend",
                "is_fallback": True,
            }
        return {
            "configured_backend": normalized,
            "effective_backend": "memory",
            "fallback_reason": "missing_postgres_backend",
            "is_fallback": True,
        }

    if normalized == "sqlite":
        if sqlite_available:
            return {
                "configured_backend": normalized,
                "effective_backend": "sqlite",
                "fallback_reason": None,
                "is_fallback": False,
            }
        return {
            "configured_backend": normalized,
            "effective_backend": "memory",
            "fallback_reason": "missing_sqlite_backend",
            "is_fallback": True,
        }

    return {
        "configured_backend": normalized,
        "effective_backend": "memory",
        "fallback_reason": None,
        "is_fallback": False,
    }


def build_store_runtime_diagnostics(status: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "store_runtime": {
            "configured": status["configured_backend"],
            "effective": status["effective_backend"],
            "fallback_reason": status["fallback_reason"],
            "is_fallback": status["is_fallback"],
        }
    }
