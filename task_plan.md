# Task Plan

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
