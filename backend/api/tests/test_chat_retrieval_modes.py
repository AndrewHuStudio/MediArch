from backend.api.schemas.chat import ChatRequest


def test_chat_request_accepts_external_baseline_modes():
    bm25 = ChatRequest(message="test", retrieval_mode="BM25")
    vrag = ChatRequest(message="test", retrieval_mode="VRAG")

    assert bm25.retrieval_mode == "BM25"
    assert vrag.retrieval_mode == "VRAG"
