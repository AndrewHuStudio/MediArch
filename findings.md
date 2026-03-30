# Findings & Decisions

## Requirements
- User wants real experimental values for paper section `3.2 End-to-End Retrieval and QA`.
- Fabricated chart values must be discarded.
- Final results must be based on actual saved outputs, not guessed comparisons.

## Research Findings
- `logs/benchmark_resume_8014_stdout.log` ends with `Done! success=41, failed=0, skipped=13`, confirming the resumed benchmark finished.
- `docs/智能体检索实验/benchmark_scoring.csv` contains `18` rows and all three answer columns are complete:
  - `R0_Answer=18`
  - `R1_Answer=18`
  - `R2_Answer=18`
- `docs/智能体检索实验/README.md` defines the scoring rubric:
  - `Evidence Hit`: `0/1`
  - `Accuracy`: `0/1/2`
  - `Completeness`: `0/1/2`
- The scoring columns in the CSV are still blank and need to be filled from the real outputs.
- The scoring columns have now been populated from the manual rubric record in `docs/智能体检索实验/benchmark_scores.json`.
- Benchmark questions span `Q01` to `Q18` and cover standards, policy, academic papers, and books/reports.
- The answers are often long and discursive; scoring must focus on whether each answer actually addresses the key evidence points rather than on answer length alone.
- Final normalized metrics computed from the scored CSV are:
  - `R0`: evidence `0.6667`, accuracy `0.3333`, completeness `0.3333`
  - `R1`: evidence `0.9444`, accuracy `0.7500`, completeness `0.6944`
  - `R2`: evidence `0.9444`, accuracy `0.8611`, completeness `0.7778`

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Create a local review helper script before scoring | It is safer than scoring directly from raw CSV blobs |
| Keep score entry explicit by question and mode | Easier to audit and revise |
| Compute normalized metrics from the scored CSV after score entry | Prevents spreadsheet drift and lets results be reproduced |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| Python one-liners using Chinese file paths broke due to encoding | Use PowerShell-native file access or saved UTF-8 scripts |

## Resources
- `logs/benchmark_resume_8014_stdout.log`
- `docs/智能体检索实验/benchmark_scoring.csv`
- `docs/智能体检索实验/README.md`
- `backend/app/agents/orchestrator_agent/agent.py`
- `backend/app/agents/tests/test_orchestrator_domain_fallback.py`
- `script/benchmark_review_helper.py`
- `script/benchmark_score.py`
- `docs/智能体检索实验/benchmark_scores.json`
- `docs/智能体检索实验/benchmark_summary.json`
