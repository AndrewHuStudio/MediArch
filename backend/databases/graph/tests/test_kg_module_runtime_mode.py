import sys
from pathlib import Path
import builtins


PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from data_process.kg import kg_module
from backend.databases.graph.builders.kg_builder import MedicalKGBuilder


class _FakeResult:
    def single(self):
        return {"c": 0}


class _FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def run(self, *_args, **_kwargs):
        return _FakeResult()


class _FakeDriver:
    def session(self, database=None):
        return _FakeSession()


def test_builder_explicit_build_mode_skips_prompt(monkeypatch):
    builder = MedicalKGBuilder.__new__(MedicalKGBuilder)
    builder.build_mode = "incremental"
    builder.neo4j_driver = _FakeDriver()
    builder.neo4j_database = "neo4j"

    monkeypatch.setattr(
        builtins,
        "input",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("prompt should not be used")),
    )

    assert MedicalKGBuilder._determine_build_strategy(builder) == "incremental"


def test_kg_module_uses_incremental_builder_mode_for_service_runtime(monkeypatch):
    captured = {}

    class _FakeBuilder:
        def __init__(self, build_mode=None):
            captured["build_mode"] = build_mode
            self.schema = {"Labels": [], "Relations": []}
            self.alias_map = {}

        def apply_runtime_profile(self, **_kwargs):
            return None

    class _FakeLLMClient:
        pass

    monkeypatch.setattr(kg_module, "MedicalKGBuilder", _FakeBuilder)
    monkeypatch.setattr(kg_module, "LLMClient", _FakeLLMClient)

    kg_module.KgModule(strategy="B0")

    assert captured["build_mode"] == "incremental"
