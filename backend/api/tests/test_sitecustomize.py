import importlib
import sys
import types


def test_sitecustomize_sets_windows_selector_policy(monkeypatch):
    policy_calls: list[str] = []

    class FakeAsyncio:
        @staticmethod
        def WindowsSelectorEventLoopPolicy():
            return "selector-policy"

        @staticmethod
        def set_event_loop_policy(policy):
            policy_calls.append(policy)

    monkeypatch.setitem(sys.modules, "asyncio", FakeAsyncio())
    monkeypatch.setattr(sys, "platform", "win32")
    sys.modules.pop("sitecustomize", None)

    importlib.import_module("sitecustomize")

    assert policy_calls == ["selector-policy"]


def test_sitecustomize_is_noop_outside_windows(monkeypatch):
    fake_asyncio = types.SimpleNamespace(
        set_event_loop_policy=lambda policy: (_ for _ in ()).throw(
            AssertionError("should not set event loop policy")
        )
    )

    monkeypatch.setitem(sys.modules, "asyncio", fake_asyncio)
    monkeypatch.setattr(sys, "platform", "linux")
    sys.modules.pop("sitecustomize", None)

    importlib.import_module("sitecustomize")
