import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from backend.databases.graph.builders import relation_mapping


@pytest.mark.parametrize(
    ("raw_relation", "expected"),
    [
        ("HAS_FEATURE", "CONTAINS"),
        ("HAS_PART", "CONTAINS"),
        ("LOCATED_IN", "PERFORMED_IN"),
        ("DEPENDS_ON", "REQUIRES"),
        ("USED_FOR", "SUPPORTS"),
        ("USED_IN", "PERFORMED_IN"),
        ("HAS_ATTRIBUTE", "SKIP"),
        ("HAS_SPECIFICATION", "SKIP"),
        ("HAS_QUANTITY", "SKIP"),
        ("HAS_VALUE", "SKIP"),
        ("HAS_SIZE", "SKIP"),
        ("HAS_AREA", "SKIP"),
        ("HAS_CODE", "SKIP"),
        ("HAS_ROOM_CODE", "SKIP"),
    ],
)
def test_normalize_relation_maps_high_frequency_aliases_without_llm(
    monkeypatch, raw_relation, expected
):
    monkeypatch.setattr(
        relation_mapping,
        "_llm_classify_relation",
        lambda _name: (_ for _ in ()).throw(AssertionError("LLM fallback should not be called")),
    )

    assert relation_mapping.normalize_relation(raw_relation) == expected


def test_llm_classify_relation_passes_explicit_timeout(monkeypatch):
    captured = {}

    class _FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return type(
                "Resp",
                (),
                {
                    "choices": [
                        type(
                            "Choice",
                            (),
                            {"message": type("Msg", (), {"content": "CONTAINS"})()},
                        )()
                    ]
                },
            )()

    fake_client = type(
        "FakeWrapper",
        (),
        {
            "model": "fake-model",
            "request_timeout": 17.0,
            "client": type(
                "FakeClient",
                (),
                {"chat": type("FakeChat", (), {"completions": _FakeCompletions()})()},
            )(),
        },
    )()

    monkeypatch.setattr(relation_mapping, "LLM_FALLBACK_ENABLED", True)
    monkeypatch.setattr(relation_mapping, "_llm_client", fake_client)

    assert relation_mapping._llm_classify_relation("unknown_rel") == "CONTAINS"
    assert captured["timeout"] == 17.0


def test_normalize_relation_caches_repeated_unknown_relation(monkeypatch):
    calls = {"count": 0}

    monkeypatch.setattr(
        relation_mapping,
        "_llm_classify_relation",
        lambda _name: calls.__setitem__("count", calls["count"] + 1) or "RELATED_TO",
    )
    relation_mapping._normalize_relation_cached.cache_clear()

    assert relation_mapping.normalize_relation("totally_new_relation") == "RELATED_TO"
    assert relation_mapping.normalize_relation("totally_new_relation") == "RELATED_TO"
    assert calls["count"] == 1


def test_relation_fallback_timeout_is_capped_by_relation_timeout(monkeypatch):
    captured = {}

    class _FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return type(
                "Resp",
                (),
                {
                    "choices": [
                        type(
                            "Choice",
                            (),
                            {"message": type("Msg", (), {"content": "CONTAINS"})()},
                        )()
                    ]
                },
            )()

    fake_client = type(
        "FakeWrapper",
        (),
        {
            "model": "fake-model",
            "request_timeout": 120.0,
            "client": type(
                "FakeClient",
                (),
                {"chat": type("FakeChat", (), {"completions": _FakeCompletions()})()},
            )(),
        },
    )()

    monkeypatch.setattr(relation_mapping, "LLM_FALLBACK_ENABLED", True)
    monkeypatch.setattr(relation_mapping, "LLM_FALLBACK_TIMEOUT", 20.0)
    monkeypatch.setattr(relation_mapping, "_llm_client", fake_client)

    assert relation_mapping._llm_classify_relation("another_unknown_rel") == "CONTAINS"
    assert captured["timeout"] == 20.0
