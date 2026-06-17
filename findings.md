# Findings

## R2 vs VRAG Source-Aware Stratified16 Validation
- Current-code API port 8018 was used for the post-fix validation; Docker-backed MongoDB, Milvus, Neo4j, Postgres, MinIO, and etcd were running.
- Answer run: `r2_vrag_stratified16_source_aware_perf_2026-06-09.csv`, 32/32 rows completed with `--timeout 0`.
- Judge outputs: `r2_vrag_stratified16_judgments_A_source_aware_perf_2026-06-09.csv` and `r2_vrag_stratified16_judgments_B_source_aware_perf_2026-06-09.csv`; adjudicated output: `r2_vrag_stratified16_adjudicated_source_aware_perf_2026-06-09.csv`.
- Final summary files: `r2_vrag_stratified16_final_summary_source_aware_perf_2026-06-09.json` and `r2_vrag_stratified16_aggregate_adjudicated_source_aware_perf_2026-06-09.json`.
- Final adjudicated quality does not support the claim that R2 is stably stronger than VRAG: VRAG has higher evidence hit, accuracy, completeness, lower unsupported-claim score, and lower latency on this 16-question sample.
- Per-source pattern: book_report is roughly comparable, academic_paper slightly favors R2 on accuracy/completeness despite both missing evidence-hit, while policy_document and technical_standard remain clear R2 weak spots.
- Root-cause signal from diagnostics: source-aware lane planning improved coverage bookkeeping, but R2 still overproduces unsupported or weakly bound claims; claim-support audit failed for every R2 row, so the next repair should target synthesis/citation binding and conservative answer shaping before adding more retrieval breadth.

## R2 Source-Aware Evidence Planning Handoff
- Created `docs/plans/2026-06-09-r2-source-aware-evidence-planning.md` as the Claude Code handoff plan for the next R2 fix.
- The plan's root cause is code-level: `profile_question(query)` and `build_evidence_plan(profile)` infer required evidence lanes from query text only, so non-standard rows with words like `应`, `如何`, `分区`, `流线`, or numeric constraints can be misrouted toward `code_spec`.
- The target design introduces `EvidenceContext` from request/benchmark metadata and `build_evidence_plan_for_query(query, metadata)` as the shared entry point for MongoDB and Synthesizer.
- New first-class lanes should include `policy_document` and `book_report`; source role classification and evidence tiers must recognize policy documents and books/reports instead of forcing them into guide or code/spec buckets.
- Benchmark metadata must flow from `script/benchmark_run.py` into Chat API request metadata: `question_id`, `source_type`, `task_type`, and `difficulty`. `question_id` is for diagnostics only.
- The validation protocol reserves port 8018 for the source-aware run, requires `--timeout 0` for answer and judge runs, requires the runner to print `Timeout: unlimited per request`, and requires stopping the temporary API and confirming port release afterward.

## R2 Authority Evidence Need Generalization
- The previous standards-first pass improved Q005 and helped Q012 retrieve a GB/code document, but it still risked becoming query-patch driven.
- The new direction is to compile each question profile and evidence plan into an `AuthorityEvidenceNeed`: required roles, optional roles, Chinese domain terms, Chinese constraint terms, claim scopes, and standards-oriented search terms.
- `AuthorityEvidenceNeed` prevents internal enum leakage into retrieval. Examples: `surgery_department` maps to `手术部/手术室/洁净手术部`; `radiology_department` maps to `放射科/放射诊断/医学影像/磁共振/MRI`; `zoning` maps to `分区/洁污分区/防护分区/感染控制`.
- Standards-first MongoDB planning now exposes `authority_evidence_need` in diagnostics, so later live runs can show whether the evidence request was well formed independent of which benchmark question triggered it.
- MongoDB standards-first diagnostics now report hit count, code-spec count, role distribution, unique doc titles, code-spec doc titles, retained count, and dropped-by-dedup/rebalance count.
- This is still a retrieval-compatible first phase, not the full offline authority index. The next durable step is to materialize sections/tables/figures into an authority index keyed by the same evidence-need schema.

## Authority Evidence Index v1 Pure Functions
- `AuthorityEvidenceRecord` now provides the pure-function index record shape: `record_id`, `source_role`, `doc_title`, `text`, `content_type`, `anchor`, `page_number`, `chunk_id`, `domain_terms`, `constraint_terms`, `claim_scopes`, and original citation payload.
- `build_authority_records(chunks)` can extract records from generic MongoDB-like chunks without a database dependency. It filters non-citable inference context, classifies source role, infers content type (`clause`, `table`, `figure`, `text`), extracts domain/constraint terms from title/section/text, and derives claim scopes.
- `rank_authority_records(need, records)` ranks by source-role authority, required/optional role match, claim-scope overlap, constraint/domain overlap, and anchor availability. It prefers code/spec numeric evidence over guide evidence for normative numeric needs while keeping atlas/image evidence for spatial needs.
- This layer is intentionally not connected to live MongoDB yet. The next step is to feed standards-first candidates through `build_authority_records` and `rank_authority_records`, then compare diagnostics before deciding whether to persist an offline authority collection.

## MongoDB Standards-First Selector Integration
- MongoDB standards-first retrieval now feeds candidate chunks through `build_authority_records` and `rank_authority_records` before merging them with general keyword results.
- Ranked standards-first chunks carry `retrieval_lane=standards_first`, `authority_record` metadata, and `evidence_tier=code_spec` only when the authority record/source role is true code/spec.
- For normative spatial questions, the ordering policy is now `code_spec` hard evidence first, `atlas_or_image` spatial supplement next when required, and `guide` design explanation after that. This prevents guides from crowding out required spatial reference evidence.
- The integration is deterministic and does not require a database migration. The next validation step should be a focused live run that inspects `standards_first_diagnostics`, `authority_evidence_need`, and final retrieved docs for Q004/Q005/Q012 or a larger stratified sample.

## Stratified 16-Question R2 vs VRAG Evaluation After Selector Integration
- Current-code API port: 8017. It was freshly started for this evaluation and stopped after the run.
- Sample: Q004, Q005, Q012, Q016, Q019, Q020, Q021, Q022, Q024, Q029, Q033, Q037, Q041, Q045, Q050, Q054.
- Sample design: 4 technical-standard, 4 policy-document, 4 academic-paper, and 4 book/report questions; includes fact, spatial_reasoning, cross_document, and recommendation tasks.
- Result files:
  - `docs/实验部分/54题实验补充/r2_vrag_stratified16_authority_selector_2026-06-09.csv`
  - `docs/实验部分/54题实验补充/r2_vrag_stratified16_judgments_A_authority_selector_2026-06-09.csv`
  - `docs/实验部分/54题实验补充/r2_vrag_stratified16_summary_authority_selector_2026-06-09.csv`
  - `docs/实验部分/54题实验补充/r2_vrag_stratified16_aggregate_authority_selector_2026-06-09.json`
- Answer run completed 32/32 rows with no API errors. Judge A completed 32/32 rows.
- Overall judge means on this sample: R2 evidence/accuracy/completeness/unsupported = 0.2500/0.6250/0.6250/1.2500; VRAG = 0.4375/0.8750/0.8750/1.0625. Lower unsupported is better, so R2 underperformed VRAG overall.
- Latency means: R2 60.14s, VRAG 42.70s. R2 remains slower.
- Coverage pass rate: R2 0.3750, VRAG 0.2500. R2 has more coverage-passed diagnostics, but this did not translate to higher judge scores, indicating the coverage plan is misaligned for some non-standard source types.
- Claim-support pass rate from ledger: R2 0.0625, VRAG 0.1875. R2 frequently still over-extends citations or binds claims to insufficient scopes.
- Source-type breakdown shows the largest R2 regression is policy documents: R2 evidence/accuracy/completeness = 0/0/0, while VRAG = 0.5/1.0/0.75. R2 often retrieved hospital design guides/atlases instead of the policy source.
- Technical-standard subset was tied between R2 and VRAG at 0.25/0.75/0.75/1.5. Q005 remains good for R2, but Q004/Q012/Q016 remain weak.
- Book/report subset: R2 evidence/accuracy/completeness = 0.75/1.25/1.25 vs VRAG = 1.0/1.25/1.5. R2 improved Q054 relative to VRAG but regressed Q041 and Q045.
- Academic-paper subset: both systems had evidence 0 and accuracy/completeness 0.5; R2 reduced unsupported claims vs VRAG (1.0 vs 1.5).
- Main diagnosis: authority selector integration did not produce an overall R2 gain. The current `EvidencePlan` treats many spatial/recommendation questions as requiring code_spec even when the question's source type is policy, academic paper, or book/report. This causes R2 to chase the wrong authority lane and sometimes retrieve standards/guides instead of the question's expected source family.
- Next optimization should not be more standards-first tuning. It should make evidence planning source-aware and task-aware: technical-standard questions require code_spec, policy questions require policy_document, academic-paper questions require paper_or_report, and book/report questions require guide/book_report evidence unless the question explicitly asks for normative code compliance.

## R2 Evidence Orchestration Control Plane: 2026-06-08 Plan Alignment
- The active plan is `docs/plans/2026-06-08-r2-evidence-orchestration-control-plane.md`.
- The repo already contains a Synthesizer-local `_build_evidence_tiers` implementation in `backend/app/agents/result_synthesizer_agent/agent.py`, but there is no standalone `backend/app/agents/evidence_orchestration.py` module yet.
- Existing R2 latency work already reduced prompt size and Synthesizer time for Q019, but the remaining bottleneck is still generation time and fallback correctness.
- The control-plane plan should improve answer quality and reduce wasted generation by giving Synthesizer a compact `question_profile`, `evidence_plan`, `evidence_tiers`, and `coverage_audit`.
- The first implementation pass should expose supplemental lane queries as diagnostics only. A graph-level supplemental retrieval loop is likely useful later, but it can add latency if introduced before unit behavior and focused Q004/Q005/Q012 regression are stable.
- Implemented the standalone `backend/app/agents/evidence_orchestration.py` module with question profiling, role-only evidence planning, source role classification, tiered evidence cards, coverage audit, and supplemental lane query construction.
- Synthesizer now includes `question_profile`, `evidence_plan`, `coverage_audit`, and `supplemental_lane_queries` in prompt context and `synthesizer_diagnostics`.
- Benchmark/QA fallback now returns a structured evidence-insufficient answer instead of the legacy `核心要点 / 参考资料 / 延伸探索` template when R2 has no usable evidence or synthesis fails.
- Current-code R2 performance test must use port 8012. Port 8011 was stale for the latest control-plane code during the quick comparison and should not be used for current-code performance claims.
- The unlimited R2 8-question run on 8012 started with `--timeout 7200`; Q004 completed in 126.41s / API took 124338ms. Q005 then reached Synthesizer and logged LLM connection retries, indicating a long-tail generation/API issue rather than local retrieval deadlock.
- Final current-code R2 performance sample used 8 questions across technical standard, policy document, academic paper, and book/report sources: Q004, Q005, Q019, Q023, Q024, Q041, Q045, Q049.
- Result files:
  - `docs/实验部分/54题实验补强/r2_perf_current8012_8q_unlimited_2026-06-08.csv`
  - `docs/实验部分/54题实验补强/r2_perf_current8013_remaining6_unlimited_2026-06-08.csv`
  - `docs/实验部分/54题实验补强/r2_perf_current_8q_summary_2026-06-08.csv`
- R2 8-question latency: mean 197.27s, median 112.90s, min 61.42s, max 833.04s, p75 140.69s.
- Excluding Q005, R2 latency: mean 106.45s, median 99.92s, min 61.42s, max 145.45s, p75 126.41s.
- Synthesizer dominates runtime: all-question Synthesizer mean 152.38s / median 65.64s; excluding Q005 mean 61.23s / median 65.46s.
- Q005 is the extreme long-tail: 833.04s total, 790.38s in Synthesizer, `fallback_used=True`, and logs show repeated Synthesizer LLM connection failures before fallback. Treat this as model/API instability plus retry behavior, not a normal retrieval latency datapoint.
- Coverage audit surfaced quality/performance tradeoffs: Q004 missing `code_spec` and `atlas_or_image`; Q024 missing `code_spec` and `guide`; Q041/Q045 missing `code_spec`. These cases still generated answers, but the audit flags show where R2 is spending generation time despite incomplete required evidence lanes.
- Follow-up quality fixes now address those findings directly:
  - Final numbered citations exclude `inference_context` evidence such as `multiple`, `[unknown]`, and KG community-only snippets, improving citation authenticity.
  - If required lanes are missing, Synthesizer uses `conservative_missing_required_evidence` mode: fewer prompt documents, fewer citations per tier, and explicit instructions to state evidence gaps instead of asserting normative conclusions.
  - Benchmark/QA Synthesizer retry budget is reduced to default 2 attempts through `RESULT_SYNTHESIZER_QA_MAX_ATTEMPTS`, preventing Q005-style 5-attempt connection-error long tails from dominating runtime.
  - MongoDB query rewrite now adds generic role-based evidence terms (`规范/标准/条文`, `指南/手册`, `图集/详图`, etc.) from the evidence plan without injecting exact document titles. This should improve evidence hit/completeness while preserving generalization.

## Focused VRAG vs R2 Comparison After Quality Fixes
- Current-code API port: 8014. It was freshly started for the focused comparison and stopped after the run.
- Result files:
  - `docs/实验部分/54题实验补强/r2_vrag_focused_compare_q004_q005_q012_current_2026-06-08.csv`
  - `docs/实验部分/54题实验补强/r2_vrag_focused_compare_q004_q005_q012_judgments_A_current_2026-06-08.csv`
  - `docs/实验部分/54题实验补强/r2_vrag_focused_compare_q004_q005_q012_summary_current_2026-06-08.csv`
- Answer run completed 6/6 with no API errors.
- Latency mean: VRAG 59.77s, R2 92.68s. R2 remains slower because it runs multi-agent retrieval and richer synthesis, but Q005 R2 dropped from the earlier 833s long-tail to 100.06s.
- Judge A mean scores over Q004/Q005/Q012:
  - VRAG: Evidence 0.333, Accuracy 1.000, Completeness 1.000, Unsupported Claim 1.000.
  - R2: Evidence 0.333, Accuracy 1.000, Completeness 1.000, Unsupported Claim 1.333.
- Q004: both VRAG and R2 missed required GB code evidence; both scored Evidence 0 / Accuracy 1 / Completeness 1 / Unsupported 1. R2 correctly surfaced missing `code_spec;atlas_or_image` in coverage audit but did not retrieve the missing code evidence.
- Q005: both systems scored Evidence 1 / Accuracy 2 / Completeness 2. VRAG had Unsupported 0; R2 had Unsupported 1 due to citation over-extension around a hand-surgery-room dimension table used as support for quantity/configuration inference.
- Q012: both systems failed the normative cross-department standard question (Evidence 0 / Accuracy 0 / Completeness 0 / Unsupported 2). R2 retrieved guide/paper evidence, not the required GB standard evidence. Coverage audit differed: VRAG missing `code_spec`; R2 missing `atlas_or_image`, which indicates R2 classified some retrieved evidence as code-like or otherwise did not flag the actual missing standard evidence strongly enough for Q012.

## 54-Question Automatic Experiment Execution
- The active execution document is `docs/实验部分/实验思路_自动管线比较与论文呈现.md`.
- The document defines the formal experiment inputs as `docs/实验部分/54题实验补强/benchmark_questions_54.csv` and `docs/实验部分/54题实验补强/benchmark_runs_54.csv`.
- The planned system comparison is five systems: BM25, VRAG, R0, R1, and R2.
- The expected execution sequence is: validate questions, initialize run matrix, run five system modes, run judge A and judge B, adjudicate, then generate `benchmark_stats_54.json`.
- The paper-facing result should prioritize two tables: experimental setup/benchmark summary and main QA performance with bootstrap CI. A reliability audit table is optional.
- Wording should describe judge consistency as LLM-based judge agreement, not human inter-rater reliability.
- Offline validation passed for the formal 54-question table.
- The run matrix was refreshed to 270 rows, all initially `todo`.
- Port 8010 is listening, but a BM25 smoke run returned a 422 validation error saying only `R0/R1/R2` are accepted. Current source code already accepts `BM25/VRAG`, so the listening 8010 service is stale or not running the current checkout.
- Port 8011 is free and `script/start_benchmark_api_8011.ps1` is configured to start the benchmark API from the current workspace.
- Running the temporary 8011 API and benchmark command in the same shell task works in this environment; starting it in a separate short-lived tool call causes the process to be cleaned up afterward.
- Q001 BM25 succeeded on the current-code 8011 API without elevated network access, but VRAG/R0/R1/R2 required elevated model/embedding network access to complete reliably.
- Q001 answer smoke results: BM25 55.49s / 1508 chars, VRAG 64.80s / 1710 chars, R0 59.95s / 1352 chars, R1 66.33s / 1621 chars, R2 79.22s / 2630 chars.
- Q001 judge smoke produced 10 judgment rows and 5 adjudicated rows. Judge A/B agreed that all five systems missed the gold `GB 51039-2014` evidence, yielding 0 for Evidence Hit, Answer Accuracy, and Response Completeness in the single-question smoke stats.
- The Q001 all-zero result appears to reflect retrieval misses rather than a scoring-script failure: `retrieved_doc_ids` did not include the gold document for any system.
- The target experiment document and benchmark CSV/JSON outputs are under ignored `docs/` paths, so they are local experiment artifacts rather than tracked git changes.

## B1 Experiment Results: Q001-Q009
- B1 execution completed on 2026-06-06 for Q001-Q009.
- `benchmark_runs_54.csv`: B1 has 45/45 answer rows completed.
- `benchmark_judgments_54.csv`: B1 has 90/90 judge rows completed.
- `benchmark_adjudicated_54.csv`: B1 has 45/45 adjudicated rows completed.
- `benchmark_batch_status_6x9.csv` and `benchmark_stats_table_current.csv` were generated for batch/status tracking.
- Current B1-only cumulative stats: BM25 evidence/accuracy/completeness = 0.0000/0.0556/0.0556; VRAG = 0.2222/0.1667/0.1667; R0 = 0.1111/0.1667/0.1667; R1 = 0.1111/0.2222/0.2222; R2 = 0.0000/0.2222/0.2778.
- Current B1 judge agreement: Evidence Hit kappa 0.8454, Accuracy weighted kappa 0.6625, Completeness weighted kappa 0.6144.
- Treat B1 as an interim diagnostic batch only. Do not use these partial results as final thesis claims.

## v2 Scoring Standard
- The formal scoring standard no longer includes a Gold Match metric.
- Gold evidence is treated as a reference anchor, not as an exclusive answer key.
- `Evidence_Hit` is now interpreted as evidence support: support can come from the gold anchor or substantively equivalent cited evidence.
- Accuracy and Completeness should not be forced to 0 only because the cited document differs from the original gold reference.
- B1 was re-judged under v2 into `benchmark_judgments_54_v2.csv`, `benchmark_adjudicated_54_v2.csv`, and `benchmark_stats_54_v2.json`.
- B1 v2 stats: BM25 evidence/accuracy/completeness = 0.0000/0.4444/0.4444; VRAG = 0.2222/0.5556/0.5556; R0 = 0.2222/0.5000/0.5000; R1 = 0.2222/0.5556/0.5556; R2 = 0.0000/0.5000/0.5000.
- B1 v2 judge agreement: Evidence Hit kappa 0.8966, Accuracy weighted kappa 0.7273, Completeness weighted kappa 0.7273.

## 54-Question Benchmark Pipeline
- The canonical question table is `docs/实验部分/54题实验补强/benchmark_questions_54.csv`.
- The old `benchmark_questions_54_final.csv` was not the completed table; it contained blank rows and has been deleted.
- The canonical table now has exactly 54 rows, 13 fields, and all rows use `status=ready`.
- Required model-judge fields are present for every row: `gold_evidence`, `gold_answer`, and `judge_rubric`.
- Source coverage remains reviewer-facing and stratified: technical standards 16, policy documents 6, academic papers 16, books/reports 16.
- Task coverage remains balanced: fact 15, spatial reasoning 13, cross-document synthesis 12, design recommendation 14.
- The run matrix `benchmark_runs_54.csv` contains 270 rows: 54 questions x 5 systems (`BM25`, `VRAG`, `R0`, `R1`, `R2`).
- Automatic evaluation is structured as two model judge channels (`A` and `B`), deterministic adjudication, kappa-style reliability reporting, and bootstrap confidence intervals.

## Current Thesis Experiment Section Review
- `docs/实验部分/experiment_part_thesis` contains five screenshot images of the current experiment section.
- Current Section 3 evaluates MediArch from two perspectives: knowledge graph construction and end-to-end QA.
- The knowledge graph subsection reports approximate graph scale: roughly 7.2k nodes and 8.7k relationships, with chunk-level provenance and schema coverage claims.
- The QA subsection currently uses 18 manually curated questions across 9 documents and reports only R0/R1/R2.
- Current source distribution table is 4 technical-standard, 2 policy-document, 6 academic-paper, and 6 book/report questions.
- Current reported QA result figure/table gives R0/R1/R2 values: R0 evidence/accuracy/completeness 0.67/0.33/0.33, R1 0.94/0.75/0.69, R2 0.94/0.86/0.78.
- Missing details that directly match the reviewer criticism: no full execution protocol, no baseline definitions beyond R0/R1/R2, no scoring rubric, no judge/rater protocol, no reliability statistic, no confidence intervals, and no statistical uncertainty around the reported bars.

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

## R2 Pipeline Optimization Notes
- `result_synthesizer_agent` now prioritizes `GB / 规范 / 标准` documents for normative queries and exposes an explicit answer skeleton helper.
- `result_synthesizer_agent` now suppresses online supplements by default for normative queries.
- `mongodb_agent` now only auto-includes diagram supplements when the query has explicit figure/image intent, not merely because it is a spatial or normative question.
- The new routing tests currently pass after the pipeline narrowing changes.
- Remaining validation goal: run a small stratified batch over fact, recommendation, spatial_reasoning, and cross_document questions to see whether completeness improves and unsupported claims drop on R2.
- Design inference should not be deleted wholesale. For recommendation and spatial reasoning answers, the better R2 target is a layered answer: `证据依据 -> 空间约束 -> 设计回应 -> 推论边界`. This keeps useful architectural reasoning while making clear which parts are directly evidenced and which parts are evidence-informed design judgment.
- The v2 judge should not treat a clearly labeled `推论边界` / design inference as unsupported solely because it is not a verbatim code clause. It should penalize inference only when it contradicts evidence, invents numeric thresholds, misstates sources, or presents speculation as mandatory code.
- Two-question validation after answer/judge calibration: Q004 generated the intended four-layer answer, but the score remained `0/1/1/2` because retrieval still used `医院建筑设计指南` and did not route strongly enough to `GB 51039-2014`. This shows the next bottleneck is implicit normative routing, not judge strictness alone.
- Implicit normative routing should treat signals such as `30米`, `30m`, `服务半径`, `护士站`, `护理单元`, `通视`, and `最远病房` as standard-like constraints even when the question does not explicitly mention `GB` or `规范`.
- R2 evidence orchestration now separates citations into `code_spec`, `guide`, `atlas_or_image`, `paper_or_report`, and `inference_context` tiers for `enhanced_context`, while preserving the flat `final_citations` list for citation numbering and frontend compatibility.
- Q004-style nursing-unit query expansion now explicitly adds `GB 51039-2014`, `综合医院建筑设计规范`, `最远病房门口`, `不宜超过30m`, `病房门口`, and `患者隐私`, plus guide/atlas design terms such as `医院建筑设计指南` and `护理单元平面布置`.
- Normative Synthesizer document views now keep guide/atlas/paper materials available instead of filtering to only GB/spec documents; code/spec sources are sorted first for hard constraints.
- The Q004-specific expansion above has been superseded by a generic retrieval strategy: rewrite now only performs numeric/unit normalization such as `30米` -> `30m` / `30 m`, and MongoDB role coverage searches broad candidates to fill missing `code_spec`, `guide`, `atlas_or_image`, and `paper_or_report` roles without hardcoding document titles.
- Synthesizer citation volume is no longer treated as a fixed target such as 10+ citations. The current policy is comprehensive and correct evidence coverage, with `max_citations` only acting as an upper bound.
- R2 latency profiling on Q019 showed retrieval workers were not the dominant cost: Milvus ~9.6s, Neo4j ~14.0s, MongoDB ~9.1s, while Synthesizer took ~49.3s with a ~52k-character prompt before prompt compaction.
- Compacting prompt document views and prompt-only citations/evidence tiers reduced Q019 prompt size to ~25k characters and Synthesizer time to ~26.7s; total R2 latency improved to ~53.1s. Q002 recommendation changed from repeated timeout (180s/360s client limits) to a completed 68.8s response.
- Remaining R2 latency bottleneck is still Synthesizer generation, not retrieval: optimized Q002 still spent ~47.6s in `synthesize` despite worker timings around 9.6s / 12.1s / 6.7s.

## 2026-06-15 R2 Sparse Retrieved Text Debug
- User symptom: knowledge graph appears healthy, but the generated answer says available evidence is limited and only cites sparse text.
- Initial trace target: distinguish retrieval miss from evidence-passing loss between worker outputs, `AgentItem` merge, `evidence_passing.py`, and `result_synthesizer_agent` prompt context.
- Relevant recent change from `progress.md`: R2 evidence passing was simplified on 2026-06-15; secondary citation channels were removed from LLM prompt and prompt snippet budgets were reduced.
- Confirmed failing behavior with a focused test: a MongoDB `AgentItem` can carry full `attrs.chunk_text`, but `documents_view.highlights[0].snippet` was only `item.snippet` (`短摘要。`), so the wide document channel did not give the LLM the retrieved body text.
- Confirmed a second prompt-breadth bug: `documents_payload` was created from only six documents and then immediately truncated to four via `top_documents = documents_payload[:4]`; this made `synthesis_mode["max_prompt_documents"]` (10/12) ineffective.
- Implemented fix in `result_synthesizer_agent`: `_best_body_text_for_item()` now selects the richest body text from `attrs.chunk_text`, generic body fields, `item.snippet`, and citation `highlight_text/snippet`; document payloads are no longer pre-truncated to four before the synthesis-mode limit.
- Follow-up investigation for the screenshot-specific symptom found a separate bridge gap: `answer_graph_data` contained Source nodes from Neo4j/Milvus, but `UnifiedHints` did not carry Source document names. Therefore the frontend could draw many source nodes while MongoDB/Synthesizer only used chunk-level hits from one source.
- Added `UnifiedHints.source_documents`, populated it from Neo4j `MENTIONED_IN` Source edges plus Milvus/MongoDB citation sources, passed it through `mediarch_graph`, and added MongoDB KG-source supplement retrieval for hinted source documents not already covered by chunk-id results.
