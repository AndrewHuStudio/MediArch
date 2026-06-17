# Task Plan

## Current Task: R2 vs VRAG Source-Aware Performance Validation
- [complete] Confirm Docker-backed services are running and port 8018 is free.
- [complete] Start the current-code API on port 8018 and verify `/api/v1/health`.
- [complete] Run the stratified16 VRAG/R2 answer benchmark with unlimited per-request timeout.
- [complete] Run judge A and judge B on the same 32 answer rows.
- [complete] Combine A/B judgments, adjudicate final scores, and generate final summary/aggregate files.
- [complete] Record that R2 is not yet stably stronger than VRAG on this sample; next fix should focus on R2 citation/claim-support binding and conservative synthesis behavior.

## Current Task: R2 Source-Aware Evidence Planning Handoff
- [complete] Write a Claude Code implementation plan for fixing the broad R2 regression through source-aware evidence planning.
- [complete] Document code-level root cause across `evidence_orchestration.py`, MongoDB retrieval planning, Synthesizer coverage audit, Chat API metadata, and benchmark runner payloads.
- [complete] Include TDD tasks for policy, academic-paper, book/report, and technical-standard routing behavior.
- [complete] Include validation protocol using fresh API port 8018 and unlimited benchmark/judge timeout via `--timeout 0`.
- [pending] Have Claude Code execute `docs/plans/2026-06-09-r2-source-aware-evidence-planning.md` with `superpowers:executing-plans`.

## Current Task Decisions
- The next fix should not keep tuning standards-first globally. The plan makes evidence planning aware of `source_type` and `task_type` so policy, paper, and book/report questions retrieve their own authority families instead of defaulting to `code_spec`.
- `question_id` is diagnostic metadata only. It must not be used for behavior branching or benchmark-question patching.
- Port 8018 is reserved for the next source-aware validation run. The answer and judge benchmark commands must use `--timeout 0` and confirm `Timeout: unlimited per request`.

## Current Task: R2 Authority Evidence Need Generalization
- [complete] Reframe the next R2 improvement away from benchmark-question patching and toward a reusable authority evidence need interface.
- [complete] Add tests proving authority retrieval terms are generated from normalized evidence needs, not internal enum leakage or question IDs.
- [complete] Implement `AuthorityEvidenceNeed` with required roles, domain terms, constraint terms, claim scopes, and search terms.
- [complete] Wire MongoDB standards-first planning to expose `authority_evidence_need` diagnostics.
- [complete] Add standards-first hit-quality diagnostics: hit count, role distribution, doc titles, code-spec count, and retained/dropped counts.
- [complete] Verify focused R2 test suite and compile checks.

## Current Task Decisions
- Treat `AuthorityEvidenceNeed` as the stable interface for future offline authority indexing. The current pass remains retrieval-compatible and does not require rebuilding the database.
- Use Chinese domain/constraint terms for retrieval and diagnostics; internal enums such as `surgery_department` and `numeric_boundary` are control-plane labels only, not user-facing search terms.
- Keep `standards_first` standards-oriented but not GB-only. It can still retrieve guides/atlases as supplemental hits, while only true code/spec chunks receive `evidence_tier=code_spec`.
- Next robust step after this pass is an offline authority index over sections/tables/figures, using this same evidence-need schema as the query contract.

## Current Task: Authority Evidence Index v1 Pure Functions
- [complete] Add tests for extracting authority evidence records from generic chunks.
- [complete] Add tests for ranking code/spec numeric evidence ahead of guide evidence when the need requires hard constraints.
- [complete] Add tests proving atlas/image evidence is retained as spatial supplemental evidence instead of being discarded.
- [complete] Implement `AuthorityEvidenceRecord`, `build_authority_records`, and `rank_authority_records`.
- [complete] Keep this pass pure-function only; no database migration or ingestion rebuild yet.
- [complete] Verify focused R2 test suite and compile checks.

## Current Task Decisions
- Authority records are the future unit of authority retrieval: role, document, anchor, content type, domain terms, constraint terms, claim scopes, text, and original citation.
- Ranking is intentionally simple and inspectable: required source role authority, claim-scope overlap, constraint overlap, domain overlap, and anchor availability.
- This pass does not replace MongoDB search yet. It creates the selector/ranker foundation that can later be fed by MongoDB chunks, an in-memory cache, or a persisted authority index.

## Current Task: MongoDB Standards-First Selector Integration
- [complete] Add a failing test proving standards-first chunks are ranked by `AuthorityEvidenceNeed` before merge.
- [complete] Convert standards-first chunks into authority records and rank them before they are merged with general MongoDB results.
- [complete] Preserve standards-first metadata on returned chunks, including `retrieval_lane`, `evidence_tier` for true code/spec evidence, and `authority_record`.
- [complete] Keep atlas/image evidence ahead of guide evidence when spatial reference is a required lane, while keeping code/spec evidence first for hard constraints.
- [complete] Verify source routing tests, focused R2 suite, and compile checks.

## Current Task Decisions
- Standards-first now uses the authority selector/ranker in the live MongoDB agent path, but still falls back to ordinary MongoDB keyword search and existing role-coverage supplement logic.
- The selector remains deterministic and non-LLM. This keeps behavior inspectable and avoids adding a costly verifier.
- Live Q004/Q005/Q012 comparison was not rerun in this pass; unit coverage verifies ordering and metadata, while live benchmark can be a separate validation step.

## Current Task: Stratified16 R2 vs VRAG Live Evaluation
- [complete] Select a 16-question stratified sample across technical standards, policy documents, academic papers, and books/reports.
- [complete] Start a fresh current-code API on port 8017.
- [complete] Run VRAG/R2 answers with unlimited timeout for 32 target rows.
- [complete] Run judge A with unlimited timeout for all 32 answer rows.
- [complete] Generate summary CSV and aggregate JSON.
- [complete] Stop temporary API and record results.

## Current Task Decisions
- The selector/ranker integration is not enough by itself. On the 16-question stratified sample, R2 underperformed VRAG overall.
- The next implementation direction should be source-aware evidence planning. `code_spec` should be required primarily for technical-standard/normative questions, while policy, academic-paper, and book/report questions need first-class authority lanes for their own source families.
- Do not keep tuning standards-first for all question types. That improves narrow technical-standard behavior but hurts policy/paper/book retrieval.

## Current Task: R2 Evidence Orchestration Control Plane Performance
- [complete] Align the 2026-06-08 control-plane plan with the current partially optimized R2 implementation.
- [complete] Add focused failing tests for profile/plan/source-role tiers/coverage audit/fallback/supplemental lane queries.
- [complete] Implement reusable `evidence_orchestration` helpers instead of keeping orchestration logic buried inside Synthesizer internals.
- [complete] Wire the helpers into Synthesizer diagnostics and prompt context with compact tier payloads.
- [complete] Replace benchmark/QA fallback with evidence-insufficient structured fallback when required lanes are missing.
- [complete] Run focused unit/regression tests and record remaining live benchmark checks.

## Current Task Decisions
- Treat "R2 performance" as both latency and answer-performance: reduce wasted synthesis/fallback paths by giving Synthesizer a compact role-aware evidence control plane.
- Keep the first pass unit-level and diagnostic-first. Supplemental retrieval terms are exposed but not wired into a full graph retry loop yet, to avoid a new latency regression.
- Preserve the prior latency optimizations already in the workspace: opt-in LLM quality evaluation, compact prompt document views, prompt-only citation compaction, and worker timing diagnostics.
- Verification passed: `python -m pytest backend/api/tests/test_r2_evidence_orchestration.py backend/api/tests/test_r2_source_routing.py script/tests/test_benchmark_judge.py script/tests/test_benchmark_modes.py -q` -> 42 passed; `python -m py_compile backend\app\agents\evidence_orchestration.py backend\app\agents\result_synthesizer_agent\agent.py` -> exit 0.
- Remaining live check: focused Q004/Q005/Q012 VRAG/R2 regression on API 8011, because it needs model/embedding runtime and can take several minutes per row.
- Quality/stability follow-up implemented: non-citable KG/unknown evidence is filtered from numbered final citations; missing required evidence lanes switch Synthesizer into conservative generation mode with smaller prompt budget; benchmark/QA Synthesizer attempts are capped by `RESULT_SYNTHESIZER_QA_MAX_ATTEMPTS` (default 2); MongoDB rewrite adds generic role-based evidence terms for required lanes without hardcoding document titles.
- Latest verification passed: `python -m pytest backend/api/tests/test_r2_evidence_orchestration.py backend/api/tests/test_r2_source_routing.py script/tests/test_benchmark_judge.py script/tests/test_benchmark_modes.py -q` -> 46 passed; `python -m py_compile backend\app\agents\result_synthesizer_agent\agent.py backend\app\agents\mongodb_agent\agent.py backend\app\agents\evidence_orchestration.py` -> exit 0.

## Current Task: R2 Sparse Retrieved Text Debug
- [complete] Trace evidence text from worker retrieval outputs into Synthesizer prompt context.
- [complete] Reproduce sparse-text behavior with focused unit tests before changing production code.
- [complete] Fix the boundary where valid retrieved text is dropped, over-truncated, or filtered.
- [complete] Verify focused backend tests and compile the changed modules.

## Current Debug Hypothesis
- Knowledge graph visualization being healthy does not prove answer evidence text reaches the Synthesizer.
- The latest 2026-06-15 evidence passing simplification likely changed the prompt payload: secondary citation channels were removed and snippet budgets were reduced. This can produce healthy KG nodes with thin answer text if worker evidence contains richer `highlight_text`/citation data that is not carried into `final_citations` or prompt document views.
- Confirmed root cause at prompt construction: `documents_view.highlights` used only `AgentItem.snippet`, so MongoDB `attrs.chunk_text` could be present but not passed to the LLM. Also, `documents_view` was hard-capped to 4 documents before the later `max_prompt_documents` policy, making that policy ineffective.
- Fix: choose the richest body text from `attrs.chunk_text` / citation `highlight_text` / citation `snippet` / `item.snippet` for document highlights, and remove the early 4-document hard cap so `synthesis_mode["max_prompt_documents"]` controls prompt breadth.
- Follow-up root cause for "KG shows many sources but answer cites one": `KnowledgeFusion` rendered Source nodes into `answer_graph_data`, but did not expose those Source names through `unified_hints`; MongoDB also short-circuited to `chunk_ids` when present and did not supplement uncovered KG source documents. Fix: add `unified_hints.source_documents`, pass it through MediArch graph metadata, and let MongoDB perform a small source-document supplement for KG sources not covered by chunk-id hits.

## Current Task: R2 Multi-Source Evidence Orchestration
- [complete] Read `docs/实验部分/R2检索问题诊断与修复交接_2026-06-07.md` and reframe the target as evidence orchestration rather than GB-only retrieval.
- [complete] Add focused tests for Q004 implicit normative query expansion and citation evidence tiering.
- [complete] Implement Q004 nursing-unit expansion terms that include `GB 51039-2014` hard-constraint phrases while retaining guide/atlas design terms.
- [complete] Add Synthesizer `evidence_tiers` and answer evidence policy in `enhanced_context`.
- [complete] Keep normative `document_views` multi-source while sorting code/spec documents first.
- [complete] Replace Q004-specific document-title injection with generic numeric constraint normalization and evidence-role coverage selection.
- [complete] Remove fixed "10+ citations" assumption; citation volume is governed by evidence coverage and correctness.
- [pending] Run live Q004/Q001 R2 answer + judge comparison when model/API runtime is available.

## Current Task: R2 Latency Optimization
- [complete] Profile quick R2 latency on representative benchmark questions and identify recommendation timeouts.
- [complete] Make Synthesizer LLM quality evaluation opt-in and use heuristic quality gating by default.
- [complete] Add deduplicated worker timing diagnostics for API responses.
- [complete] Compact Synthesizer prompt document views and prompt-only citations/evidence tiers.
- [complete] Verify Q019 latency/prompt reduction and Q002 timeout recovery on local 8011 API.
- [pending] Continue optimizing Synthesizer generation time, especially for recommendation questions.

## Current Task: 54-Question Benchmark Pipeline
- [complete] Confirm reviewer-driven benchmark requirements: larger 54-question set, operationalized metrics, model-based judging, inter-rater reliability, and bootstrap confidence intervals.
- [complete] Add tested benchmark schema/statistics module for canonical questions, run matrix, adjudication, kappa, and CI reporting.
- [complete] Convert `benchmark_questions_54.csv` to the canonical model-judge-friendly schema.
- [complete] Generate `benchmark_runs_54.csv` as the 54 x 5 long-form run table.
- [complete] Replace benchmark runner/scorer/review helper scripts with canonical 54-question pipeline defaults.
- [complete] Delete obsolete intermediate question tables and old CSV/XLSX scoring templates.
- [complete] Run final focused verification and report remaining execution prerequisites.

## Current Task: Related Sources Light Theme
- [complete] Locate related-source/PDF preview UI and page metadata data flow.
- [complete] Add focused light-theme/interface checks before production edits.
- [complete] Convert related-source preview modal to the MediArch light theme and improve layout.
- [complete] Verify "page value" and "key explanation" backend fields still render from API data.
- [complete] Run theme and TypeScript checks, then report remaining risks.

## Current Task Notes
- Preserve existing benchmark-baseline planning content below.
- Do not revert the existing light-theme edits already present in the workspace.

## Current Task: 54-Question Automatic Experiment Execution and Thesis Integration
- [complete] Align the active experiment plan with `docs/实验部分/实验思路_自动管线比较与论文呈现.md`.
- [complete] Re-check the formal 54-question inputs and run matrix scripts.
- [complete] Run offline validation and refresh the 54 x 5 run matrix if safe.
- [complete] Confirm API/model prerequisites for executing BM25, VRAG, R0, R1, and R2 answers.
- [pending] Prepare the thesis experiment section update path around setup, metrics, judge protocol, CI reporting, and result table templates after full benchmark results are available.

## Current Experiment Execution Decisions
- Use the 54-question benchmark and 270-row run matrix as the formal experiment input.
- Keep BM25 and VRAG as external baselines, and R0/R1/R2 as internal MediArch variants.
- Do not claim statistical significance unless a paired difference test is later added; report means with question-level bootstrap 95% CI.
- Refer to evaluator consistency as LLM-based judge agreement or inter-judge agreement, not human inter-rater reliability.
- Execute the formal experiment in 6 batches of 9 questions each: B1 Q001-Q009, B2 Q010-Q018, B3 Q019-Q027, B4 Q028-Q036, B5 Q037-Q045, B6 Q046-Q054.
- Maintain the batch status table in `docs/实验部分/实验执行批次计划_6批9题.md`.
- Use v2 scoring for formal judging: gold evidence is only a reference anchor, exact gold matching is not a metric, and `Evidence_Hit` means evidence support from either the anchor or substantively equivalent cited evidence.
- Continue B2-B6 with `benchmark_judgments_54_v2.csv`, `benchmark_adjudicated_54_v2.csv`, and `benchmark_stats_54_v2.json`.

## Current Experiment Execution Risks
- The current 8010 API service is stale for benchmark baselines because it rejects `BM25/VRAG`; use the current-code 8011 experiment API or restart 8010.
- Model/embedding network access is required for VRAG and MediArch modes. In this Codex environment, elevated execution was required for successful model/embedding calls.
- Q001 smoke outputs show all five systems missing the gold `GB 51039-2014` evidence, and judge A/B both scored all systems 0 on Evidence Hit, Accuracy, and Completeness. Treat this as an early retrieval-quality warning, not as the final benchmark result.
- `docs/` is ignored by git, so experiment documents and benchmark CSV/JSON outputs are local working artifacts unless explicitly force-added later.
- B1 v1 strict scoring was superseded by B1 v2 scoring because original gold evidence may be incomplete or misassigned.

## Errors Encountered
| Error | Attempt | Resolution |
| --- | --- | --- |
| `ModuleNotFoundError: No module named 'script'` | Ran `python script/benchmark_run.py ...` from the documented command | Use module invocation from repo root: `python -m script.benchmark_run ...`; updated the experiment document accordingly. |
| API 422 rejected `BM25` | Ran BM25 smoke against port 8010 | Source code accepts `BM25/VRAG`, so 8010 is stale; use current-code 8011 experiment API. |
| Embedding API connection failure | Ran VRAG smoke inside restricted sandbox | Retried with elevated network permission; VRAG Q001 completed successfully. |

## Current Task: Instant Navigation
- [complete] Locate the route transition code causing black flashes.
- [complete] Replace the full-screen black overlay and exit animation with immediate navigation.
- [complete] Keep the existing `usePageTransition` API so current callers do not need large rewrites.
- [complete] Add prefetching for primary routes and hover/focus prefetch for `/chat`.
- [complete] Verify theme checks and TypeScript compilation.

## Navigation Finding
- The black interruption was caused by `frontend/components/page-transition.tsx`: it rendered a fixed `bg-black` overlay and used `AnimatePresence mode="wait"` with exit/enter opacity and scale transitions on every pathname change.

## Goal
Add two independent experimental baselines for the MediArch QA benchmark:
BM25+LLM and Vector RAG+LLM. Keep these separate from the existing
R0/R1/R2 MediArch ablation modes so the paper can report external baselines
and internal variants clearly.

## Phases
- [in_progress] Record current system findings and baseline design decisions.
- [pending] Add failing tests for new retrieval modes, BM25 ranking, and benchmark script mode support.
- [pending] Implement a small baseline retrieval service for BM25 and vector-only RAG.
- [pending] Route `BM25` and `VRAG` through `/chat` using the existing Synthesizer, without changing the main MediArch graph.
- [pending] Update benchmark runner/scoring scripts for the new baseline answer and score columns.
- [pending] Run focused verification and summarize the final technical configuration for the paper.

## Decisions
- `R0/R1/R2` remain internal MediArch variants.
- `BM25` and `VRAG` are external baseline modes exposed through `retrieval_mode`.
- `BM25` should use lexical BM25 over MongoDB chunks, not the existing MongoDB keyword/regex search, because the paper reviewer specifically asked for a comparable retrieval baseline.
- `VRAG` should use vector retrieval only, then the same LLM answer synthesizer. It excludes Neo4j, Knowledge Fusion, and MongoDB grounding.
- Both baseline modes should use the same questions, `top_k`, and synthesis LLM as MediArch to isolate retrieval differences.

## Risks
- The existing Milvus agent includes query rewriting, optional reranking, and knowledge-point extraction. If `VRAG` reuses it unchanged, the paper must disclose those settings or disable reranking for a purer vector RAG baseline.
- True BM25 over Chinese text needs tokenization. The system already has a `jieba`-aware query expansion module, so BM25 should reuse available tokenization logic with a regex fallback.
