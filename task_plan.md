# Task Plan

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
