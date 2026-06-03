# Standard QA Extraction Plan

## Goal
Complete the technical-standard question and answer extraction for the 54-question MediArch benchmark, starting with `GB 51039-2014 综合医院建筑设计规范.pdf`.

## Scope
- Primary benchmark file: `docs/实验部分/54题实验补强/benchmark_questions_54.csv`
- Primary source OCR: `data_process/documents_ocr/标准规范/GB 51039-2014 综合医院建筑设计规范/full/full.md`
- Target rows for this pass: Q005-Q016

## Phases
- [in_progress] Inspect the benchmark schema and GB 51039 OCR structure.
- [pending] Select stable clauses for fact, spatial reasoning, cross-document-style, and recommendation questions.
- [pending] Draft Q005-Q016 with evidence points and expected answer summaries.
- [pending] Write completed rows into `benchmark_questions_54.csv`.
- [pending] Validate CSV parseability and row coverage.

## Decisions
- Keep existing root `task_plan.md`, `findings.md`, and `progress.md` intact because they currently track a separate baseline experiment task.
- Use dedicated `standard_qa_*` files for this extraction work.
- Treat Q011-Q014 as "cross_document" task-type rows inside the standard block, but do not introduce unrelated sources unless the benchmark already expects secondary references.

## Risks
- OCR may contain recognition noise, so clause numbers and values must be checked against nearby context.
- The existing Q001-Q004 rows are migrated and still need review, but this pass focuses on the blank Q005-Q016 rows.
