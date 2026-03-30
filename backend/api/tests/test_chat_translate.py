import asyncio


def test_translate_text_uses_llm_manager_async_factory(monkeypatch):
    monkeypatch.setenv("DEBUG", "true")
    import backend.api.routers.chat as chat_router

    captured = {}

    class _FakeLLM:
        async def ainvoke(self, prompt):
            captured["prompt"] = prompt
            return type("Result", (), {"content": "General Hospital"})()

    class _FakeManager:
        async def aget_or_create(self, name, init_func):
            captured["name"] = name
            captured["init_func_called"] = True
            init_func()
            return _FakeLLM()

    monkeypatch.setattr(chat_router, "get_llm_manager", lambda: _FakeManager())
    monkeypatch.setattr(chat_router, "init_chat_model", lambda **kwargs: object())
    monkeypatch.setattr(chat_router, "get_api_key", lambda: "test-key")
    monkeypatch.setattr(chat_router, "get_llm_base_url", lambda: "https://example.com/v1")
    monkeypatch.setattr(chat_router, "get_model_provider", lambda: "openai")
    monkeypatch.setattr(chat_router, "get_llm_model", lambda default: "fake-model")

    response = asyncio.run(
        chat_router.translate_text(chat_router.TranslateRequest(text="综合医院", target_lang="en"))
    )

    assert response.translated == "General Hospital"
    assert captured["name"] == "chat_translate"
    assert "Translate the following text to English" in captured["prompt"]
