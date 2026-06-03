# Findings

- Existing API schema only accepts `retrieval_mode` values `R0`, `R1`, and `R2`.
- Current comments define `R0=Milvus-only`, `R1=Neo4j+Milvus`, and `R2=Full pipeline`.
- `R0/R1/R2` are internal MediArch ablations, not independent external systems. This matches the reviewer's criticism.
- `backend/api/routers/chat.py` forwards `retrieval_mode` into `AgentRequest.metadata` before invoking `mediarch_graph`.
- `mediarch_graph` filters workers for `R0` and `R1`, and skips MongoDB for `R0/R1`. This confirms the current modes are implemented inside the MediArch graph.
- `script/benchmark_run.py`, `script/benchmark_score.py`, and `script/benchmark_review_helper.py` currently assume only `R0/R1/R2`.
- MongoDB has text-index and regex keyword retrieval, but that is not necessarily BM25. A paper baseline named `BM25+LLM` should use BM25 scoring directly or be renamed as keyword retrieval.
- The system already uses `text-embedding-3-large` with 3072-dimensional vectors in Milvus (`COSINE + IVF_FLAT`) and a `qwen3-reranker-8b` reranker in the Milvus agent.
- Reusing the existing result synthesizer is preferable for fair comparison because it keeps the generation model and answer formatting constant across BM25, vector RAG, and MediArch.
