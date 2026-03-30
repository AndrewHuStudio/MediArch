from pathlib import Path
import importlib
import runpy
import sys
import types


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_module_launcher_sets_windows_selector_policy_before_uvicorn(monkeypatch):
    original_main = sys.modules.get("__main__")
    original_platform = sys.platform
    policy_calls: list[str] = []
    run_calls: list[dict[str, object]] = []

    class FakeAsyncio:
        @staticmethod
        def WindowsSelectorEventLoopPolicy():
            return "selector-policy"

        @staticmethod
        def set_event_loop_policy(policy):
            policy_calls.append(policy)

    fake_uvicorn = types.SimpleNamespace(
        run=lambda app, **kwargs: run_calls.append(
            {"app": app, "kwargs": kwargs, "policy_calls_seen": list(policy_calls)}
        )
    )

    monkeypatch.setitem(sys.modules, "asyncio", FakeAsyncio())
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["python"])
    sys.modules.pop("backend.api.__main__", None)

    try:
        runpy.run_module("backend.api", run_name="__main__")
    finally:
        monkeypatch.setattr(sys, "platform", original_platform)
        if original_main is not None:
            sys.modules["__main__"] = original_main
        else:
            sys.modules.pop("__main__", None)

    assert policy_calls == ["selector-policy"]
    assert run_calls == [
        {
            "app": "backend.api.main:app",
            "kwargs": {"host": "0.0.0.0", "port": 8010, "reload": True, "log_level": "info"},
            "policy_calls_seen": ["selector-policy"],
        }
    ]
