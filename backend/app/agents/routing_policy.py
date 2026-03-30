from __future__ import annotations

from typing import Iterable, List, Optional


STRICT_SCOPE_ALLOWED_WORKERS = {"milvus_agent", "mongodb_agent"}
DEFAULT_WORKER_PRIORITY = [
    "neo4j_agent",
    "milvus_agent",
    "mongodb_agent",
    "online_search_agent",
]


def _dedupe_preserve_order(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for value in values:
        worker = str(value or "").strip()
        if not worker or worker in seen:
            continue
        seen.add(worker)
        ordered.append(worker)
    return ordered


def select_workers_for_execution(
    available_workers: Iterable[str],
    agents_to_call: Optional[Iterable[str]] = None,
    priority: Optional[Iterable[str]] = None,
    strict_cross_doc_request: bool = False,
) -> List[str]:
    """Resolve the final worker execution order for the main graph.

    Selection policy:
    - If the orchestrator produced `agents_to_call`, treat it as the requested set.
    - Otherwise, fall back to all available workers.
    - Intersect with `available_workers`.
    - Reorder according to the global priority list while preserving any remaining order.
    - Under strict doc-scope mode, keep only workers that are allowed to stay within the
      requested document scope.
    """
    available = _dedupe_preserve_order(available_workers)
    available_set = set(available)

    requested_source = available if agents_to_call is None else _dedupe_preserve_order(agents_to_call)
    requested = [worker for worker in requested_source if worker in available_set]

    priority_list = _dedupe_preserve_order(priority or DEFAULT_WORKER_PRIORITY)
    prioritized = [worker for worker in priority_list if worker in requested]
    remaining = [worker for worker in requested if worker not in set(prioritized)]
    selected = prioritized + remaining

    if strict_cross_doc_request:
        selected = [worker for worker in selected if worker in STRICT_SCOPE_ALLOWED_WORKERS]

    return selected
