# Progress

- 2026-04-26: Started root-cause investigation for prompt leakage, list hierarchy mismatch, and table rendering failure.
- 2026-04-26: Located main chat Markdown renderer in `frontend/components/chat/message-with-sources.tsx`.
- 2026-04-26: Located likely backend source of prompt leakage in `backend/app/agents/result_synthesizer_agent/agent.py`.
- 2026-04-26: Reduced synthesizer prompt constraints to avoid leaking process-only structure into final answers.
- 2026-04-26: Moved frontend markdown display transforms into `frontend/lib/chat/markdown-display.ts` and removed citation-cluster redistribution before markdown parsing.
- 2026-04-26: Added focused tests for prompt contract, markdown stability, inline references, and collapsed table normalization.
- 2026-04-26 22:08: Added a failing regression test for fully collapsed single-line Markdown tables after numbered items, extended `normalizeMarkdownTables()` to expand collapsed separator/body rows, and verified with focused Node tests plus `tsc`.
- 2026-04-26 22:xx: Added a parsing-level regression test using `react-markdown + remark-gfm + rehype-raw`, identified that tables immediately after ordered-list items still failed without a blank separator line, and updated `normalizeMarkdownTables()` to inject that blank line so final render stays parseable.
- 2026-04-27: Added a failing regression test for citation-only lines before the next ordered sibling item, confirmed continuous citation replacement was swallowing the newline via `\s*`, narrowed that match to `[ \t]*`, and re-verified `frontend/tests/markdown-display.test.mjs` and `frontend/tsconfig.json`.
- 2026-04-27: Redirected debugging focus back to the actual HDMS QA symptom and traced a backend final-answer formatting bug: global whitespace collapsing in the synthesizer/API post-process was destroying Markdown indentation for nested ordered/unordered lists.
- 2026-04-27: Added backend regressions in `backend/app/agents/tests/test_result_synthesizer_markdown_stability.py` and `backend/api/tests/test_chat_markdown_postprocess.py`, verified the failure, replaced the global whitespace collapse with `_collapse_inline_whitespace_preserving_indentation()`, and re-ran the focused tests successfully.
