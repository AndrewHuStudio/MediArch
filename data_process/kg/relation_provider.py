from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from typing import Any, List, Sequence, Tuple


class RelationCandidateProvider(ABC):
    """关系候选器接口。"""

    @abstractmethod
    def predict(
        self,
        head: str,
        tail: str,
        existing_triplets: Sequence[Any],
    ) -> List[Tuple[str, float]]:
        """为给定实体对返回候选关系及置信度。"""


class FrequencyBasedProvider(RelationCandidateProvider):
    """基于历史关系频率的轻量候选器（R-GCN 可用前的兜底实现）。"""

    def __init__(self, top_k: int = 5):
        self.top_k = max(1, int(top_k))

    def predict(
        self,
        head: str,
        tail: str,
        existing_triplets: Sequence[Any],
    ) -> List[Tuple[str, float]]:
        rel_counter: Counter[str] = Counter()

        for triplet in existing_triplets:
            rel = str(getattr(triplet, "relation", "") or "").strip()
            subj = str(getattr(triplet, "subject", "") or "").strip()
            obj = str(getattr(triplet, "object", "") or "").strip()
            if not rel:
                continue
            if subj in {head, tail} or obj in {head, tail}:
                rel_counter[rel] += 1

        total = sum(rel_counter.values())
        if total <= 0:
            return []

        ranked: List[Tuple[str, float]] = []
        for rel, count in rel_counter.most_common(self.top_k):
            ranked.append((rel, round(float(count) / float(total), 4)))
        return ranked
