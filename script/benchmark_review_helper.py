from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from script import benchmark_pipeline


QUESTIONS_PATH = benchmark_pipeline.QUESTIONS_PATH
RUNS_PATH = benchmark_pipeline.RUNS_PATH
OUT_PATH = benchmark_pipeline.BENCHMARK_DIR / "benchmark_review_54.md"


def clip(text: str, limit: int = 1800) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


def build_review_sheet(questions: list[dict[str, str]], runs: list[dict[str, str]]) -> str:
    runs_by_question: dict[str, list[dict[str, str]]] = defaultdict(list)
    for run in runs:
        runs_by_question[run["question_id"]].append(run)

    parts: list[str] = [
        "# Benchmark Review Sheet",
        "",
        f"- Questions: {len(questions)}",
        "- Systems: BM25, VRAG, R0, R1, R2",
        "- Purpose: audit model answers and model-judge scores before reporting final statistics",
        "",
    ]

    for question in questions:
        qid = question["question_id"]
        parts.append(f"## {qid} {question['question']}")
        parts.append("")
        parts.append(f"- Source Type: {question['source_type']}")
        parts.append(f"- Task Type: {question['task_type']}")
        parts.append(f"- Difficulty: {question['difficulty']}")
        parts.append(f"- Gold References: {question['gold_reference_docs']}")
        parts.append(f"- Gold Sections: {question['gold_reference_sections']}")
        parts.append("")
        parts.append("### Gold Evidence")
        parts.append("")
        parts.append("```text")
        parts.append(question["gold_evidence"].strip())
        parts.append("```")
        parts.append("")
        parts.append("### Gold Answer")
        parts.append("")
        parts.append("```text")
        parts.append(question["gold_answer"].strip())
        parts.append("```")
        parts.append("")

        by_system = {run["system_id"]: run for run in runs_by_question.get(qid, [])}
        for system_id, _ in benchmark_pipeline.SYSTEMS:
            run = by_system.get(system_id, {})
            answer = (run.get("answer") or "").strip()
            parts.append(f"### {system_id}")
            parts.append("")
            parts.append(f"- Run Status: {run.get('run_status', 'missing')}")
            parts.append(f"- Answer Length: {len(answer)} chars")
            parts.append(f"- Retrieved Docs: {run.get('retrieved_doc_ids', '')}")
            parts.append("")
            parts.append("```text")
            parts.append(clip(answer))
            parts.append("```")
            parts.append("")

        parts.append("---")
        parts.append("")

    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a Markdown audit sheet for the 54-question benchmark")
    parser.add_argument("--questions", default=str(QUESTIONS_PATH))
    parser.add_argument("--runs", default=str(RUNS_PATH))
    parser.add_argument("--output", default=str(OUT_PATH))
    args = parser.parse_args()

    questions = benchmark_pipeline.read_csv(Path(args.questions))
    benchmark_pipeline.validate_questions(questions)
    runs = benchmark_pipeline.read_csv(Path(args.runs)) if Path(args.runs).exists() else []
    content = build_review_sheet(questions, runs)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(f"Wrote review sheet: {output}")


if __name__ == "__main__":
    main()
