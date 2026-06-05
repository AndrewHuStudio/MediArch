# Findings

## 54-Question Benchmark Pipeline
- The canonical question table is `docs/实验部分/54题实验补强/benchmark_questions_54.csv`.
- The old `benchmark_questions_54_final.csv` was not the completed table; it contained blank rows and has been deleted.
- The canonical table now has exactly 54 rows, 13 fields, and all rows use `status=ready`.
- Required model-judge fields are present for every row: `gold_evidence`, `gold_answer`, and `judge_rubric`.
- Source coverage remains reviewer-facing and stratified: technical standards 16, policy documents 6, academic papers 16, books/reports 16.
- Task coverage remains balanced: fact 15, spatial reasoning 13, cross-document synthesis 12, design recommendation 14.
- The run matrix `benchmark_runs_54.csv` contains 270 rows: 54 questions x 5 systems (`BM25`, `VRAG`, `R0`, `R1`, `R2`).
- Automatic evaluation is structured as two model judge channels (`A` and `B`), deterministic adjudication, kappa-style reliability reporting, and bootstrap confidence intervals.

## Related Sources Light Theme Task
- Screenshot corresponds to `frontend/components/chat/pdf-viewer-modal.tsx`.
- The modal still uses dark tokens: black overlay, `bg-gray-950`, `border-white/10`, `text-white`, `text-gray-*`, and dark sidebar/viewer surfaces.
- `本页价值` is rendered from `buildPageValueSummary(source)`.
- `重点说明` is rendered from sanitized `source.highlightText || source.snippet`.
- The source modal uses `PDFSource` from `pdf-source-card.tsx`; the citation badge exports a compatible `PDFSource` shape.
- Backend citations are converted in `frontend/components/chat/chat-interface.tsx` via local `citationsToPDFSources`.
- Current conversion maps `snippet`, `highlight_text`, `pdf_url`, `image_url`, `positions`, and `metadata`, but not explicit `page_value` / `key_explanation` fields.
- To make the backend interface stable, the citation-to-source mapping should preserve explicit backend fields and the viewer should prefer them over derived heuristics.

- Existing API schema only accepts `retrieval_mode` values `R0`, `R1`, and `R2`.
- Current comments define `R0=Milvus-only`, `R1=Neo4j+Milvus`, and `R2=Full pipeline`.
- `R0/R1/R2` are internal MediArch ablations, not independent external systems. This matches the reviewer's criticism.
- `backend/api/routers/chat.py` forwards `retrieval_mode` into `AgentRequest.metadata` before invoking `mediarch_graph`.
- `mediarch_graph` filters workers for `R0` and `R1`, and skips MongoDB for `R0/R1`. This confirms the current modes are implemented inside the MediArch graph.
- `script/benchmark_run.py`, `script/benchmark_score.py`, and `script/benchmark_review_helper.py` currently assume only `R0/R1/R2`.
- MongoDB has text-index and regex keyword retrieval, but that is not necessarily BM25. A paper baseline named `BM25+LLM` should use BM25 scoring directly or be renamed as keyword retrieval.
- The system already uses `text-embedding-3-large` with 3072-dimensional vectors in Milvus (`COSINE + IVF_FLAT`) and a `qwen3-reranker-8b` reranker in the Milvus agent.
- Reusing the existing result synthesizer is preferable for fair comparison because it keeps the generation model and answer formatting constant across BM25, vector RAG, and MediArch.
