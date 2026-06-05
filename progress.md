# Progress

- 2026-06-05: Reworked the 54-question benchmark into a canonical model-judge-friendly schema.
- 2026-06-05: Added tested benchmark pipeline helpers for schema validation, run-matrix generation, adjudication, kappa reliability, and bootstrap CI reporting.
- 2026-06-05: Replaced benchmark runner/scorer/review helper defaults so they use the 54-question canonical table and long-form run outputs.
- 2026-06-05: Generated `docs/实验部分/54题实验补强/benchmark_runs_54.csv` with 270 run rows.
- 2026-06-05: Deleted obsolete intermediate question/scoring tables so only `benchmark_questions_54.csv` remains as the formal question table.
- 2026-06-05: Verified benchmark pipeline tests pass: `15 passed`; script compilation passed; question validation passed; run matrix distribution is BM25/R0/R1/R2/VRAG = 54 each.
- 2026-05-30: Started configuring external baselines for the MediArch benchmark: `BM25+LLM` and `Vector RAG+LLM`.
- 2026-05-30: Confirmed the current system exposes only internal ablations `R0/R1/R2` through `ChatRequest`, `/chat`, and the benchmark scripts.
- 2026-05-30: Confirmed the current Milvus path uses `text-embedding-3-large`, `3072`-dimensional vectors, `COSINE + IVF_FLAT`, and optional reranking.
- 2026-05-30: Decided to keep `R0/R1/R2` as internal MediArch variants and add `BM25` / `VRAG` as separate retrieval modes.
- 2026-05-30: Updated `task_plan.md`, `findings.md`, and `progress.md` with the baseline configuration plan.
- 2026-06-04: Converted the related-source PDF preview modal to the MediArch light theme.
- 2026-06-04: Added explicit citation mapping for backend `page_value` / `key_explanation` fields into `PDFSource.pageValue` / `PDFSource.keyExplanation`.
- 2026-06-04: Verified `frontend/.\\node_modules\\.bin\\jiti.CMD lib/chat/pdf-source-mapping.test.ts`, `frontend/.\\node_modules\\.bin\\tsc.CMD --noEmit`, and `node frontend/scripts/check-light-theme.mjs` pass.
