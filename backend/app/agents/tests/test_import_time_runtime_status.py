from pathlib import Path
import importlib
import logging
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _reload_module(module_name: str):
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_query_expansion_import_without_jieba_does_not_warn(caplog):
    caplog.set_level(logging.WARNING)

    module = _reload_module("backend.app.services.query_expansion")

    assert module.JIEBA_AVAILABLE is False
    assert not any("jieba未安装" in record.getMessage() for record in caplog.records)
    assert module.get_query_expansion_runtime_status()["tokenizer_backend"] == "regex"


def test_mediarch_graph_import_reports_checkpointer_fallback_without_warning(caplog):
    caplog.set_level(logging.WARNING)

    module = _reload_module("backend.app.agents.mediarch_graph")

    assert module.CHECKPOINTER_RUNTIME_STATUS["configured_backend"] == "postgres"
    assert module.CHECKPOINTER_RUNTIME_STATUS["effective_backend"] == "sqlite"
    assert module.CHECKPOINTER_RUNTIME_STATUS["fallback_reason"] == "missing_postgres_backend"
    assert module.STORE_RUNTIME_STATUS["effective_backend"] == "sqlite"
    assert not any("MemorySaver会产生阻塞调用警告" in record.getMessage() for record in caplog.records)
