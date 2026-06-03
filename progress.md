# Progress

- 2026-05-30: Started configuring external baselines for the MediArch benchmark: `BM25+LLM` and `Vector RAG+LLM`.
- 2026-05-30: Confirmed the current system exposes only internal ablations `R0/R1/R2` through `ChatRequest`, `/chat`, and the benchmark scripts.
- 2026-05-30: Confirmed the current Milvus path uses `text-embedding-3-large`, `3072`-dimensional vectors, `COSINE + IVF_FLAT`, and optional reranking.
- 2026-05-30: Decided to keep `R0/R1/R2` as internal MediArch variants and add `BM25` / `VRAG` as separate retrieval modes.
- 2026-05-30: Updated `task_plan.md`, `findings.md`, and `progress.md` with the baseline configuration plan.
