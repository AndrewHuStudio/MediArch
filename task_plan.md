# Task Plan

## Goal
Stabilize answer formatting by removing prompt leakage from final answers, preserving list hierarchy between streaming/final render, and fixing Markdown table rendering, with priority on the actual HDMS QA symptom where completed output loses numbering hierarchy.

## Phases
- [completed] Trace answer-generation prompts and post-processing that inject process-only instructions into final content.
- [completed] Reproduce frontend Markdown rendering issues for nested lists and tables with failing tests.
- [completed] Implement minimal backend/frontend fixes for prompt leakage, list stability, and table rendering.
- [completed] Verify with focused tests and type/build checks, including fully collapsed single-line table regressions after numbered items and citation-only newline preservation before the next ordered sibling item.
- [completed] Identify and fix backend final-answer post-processing that collapsed Markdown indentation and caused HDMS QA completed output to flatten nested numbering levels.

## Notes
- User wants final answers to be comprehensive but not rigidly forced into a fixed "intro/compliance/optimization" scaffold.
- Formatting should be stabilized by modules/renderers, not by verbose prompt constraints.
- Distinguish clearly between:
  - generic frontend Markdown fixes already made earlier
  - the HDMS QA-specific completed-output jump now traced to backend whitespace normalization in finalized answers
