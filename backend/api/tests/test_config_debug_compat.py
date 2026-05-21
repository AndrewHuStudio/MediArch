import importlib
import sys


def _reload_config_module():
    sys.modules.pop("backend.api.core.config", None)
    return importlib.import_module("backend.api.core.config")


def test_settings_accepts_release_debug_value(monkeypatch):
    monkeypatch.setenv("DEBUG", "release")
    monkeypatch.setenv("ENVIRONMENT", "production")

    config_module = _reload_config_module()

    assert config_module.settings.DEBUG is False
    assert config_module.settings.ENVIRONMENT == "production"


def test_settings_accepts_debug_build_label(monkeypatch):
    monkeypatch.setenv("DEBUG", "debug")
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    config_module = _reload_config_module()

    assert config_module.settings.DEBUG is True
