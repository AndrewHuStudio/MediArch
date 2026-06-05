from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Mapping, Sequence

from script import benchmark_pipeline


SYSTEM_PROMPT = """You are a strict academic evaluator for a healthcare-architecture QA benchmark.
Score only against the provided gold reference, gold evidence, gold answer, rubric, system answer, and citations.
Do not reward plausible domain knowledge that is not grounded in the supplied evidence.
Return JSON only."""


def _clean(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _extract_json_object(text: str) -> dict[str, object]:
    cleaned = _clean(text)
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.S | re.I)
    if fence_match:
        cleaned = fence_match.group(1)
    elif not cleaned.startswith("{"):
        object_match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not object_match:
            raise ValueError("Judge response did not contain a JSON object")
        cleaned = object_match.group(0)
    return json.loads(cleaned)


def _score_value(payload: Mapping[str, object], key: str, valid: set[int]) -> str:
    if key not in payload:
        raise ValueError(f"Judge response missing {key}")
    value = int(payload[key])
    if value not in valid:
        raise ValueError(f"Judge response {key} out of range: {value}")
    return str(value)


def parse_judge_response(text: str) -> dict[str, str]:
    payload = _extract_json_object(text)
    return {
        "evidence_hit": _score_value(payload, "evidence_hit", {0, 1}),
        "accuracy": _score_value(payload, "accuracy", {0, 1, 2}),
        "completeness": _score_value(payload, "completeness", {0, 1, 2}),
        "unsupported_claim": _score_value(payload, "unsupported_claim", {0, 1, 2}),
        "rationale": _clean(payload.get("rationale")),
        "raw_json": json.dumps(payload, ensure_ascii=False),
    }


def build_judge_messages(
    question: Mapping[str, object],
    run: Mapping[str, object],
    *,
    judge_id: str,
) -> list[dict[str, str]]:
    user_content = f"""
Judge ID: {judge_id}

Question ID: {_clean(question.get("question_id"))}
System ID: {_clean(run.get("system_id"))}

Question:
{_clean(question.get("question"))}

Gold reference documents:
{_clean(question.get("gold_reference_docs"))}

Gold reference sections:
{_clean(question.get("gold_reference_sections"))}

Gold evidence:
{_clean(question.get("gold_evidence"))}

Gold answer:
{_clean(question.get("gold_answer"))}

Rubric:
{_clean(question.get("judge_rubric"))}

System answer:
{_clean(run.get("answer"))}

System citations:
{_clean(run.get("citations"))}

Return JSON with exactly these keys:
{{
  "evidence_hit": 0 or 1,
  "accuracy": 0, 1, or 2,
  "completeness": 0, 1, or 2,
  "unsupported_claim": 0, 1, or 2,
  "rationale": "brief reason in Chinese or English"
}}
"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content.strip()},
    ]


def call_judge_model(
    messages: Sequence[Mapping[str, str]],
    *,
    model: str | None = None,
    timeout: int = 90,
) -> str:
    from openai import OpenAI

    from backend.env_loader import load_dotenv
    from backend.llm_env import get_api_key, get_llm_base_url, get_llm_model

    load_dotenv()
    api_key = get_api_key()
    if not api_key:
        raise ValueError("Missing MEDIARCH_API_KEY for benchmark judge")

    judge_model = model or os.getenv("BENCHMARK_JUDGE_MODEL") or os.getenv("EVALUATOR_MODEL") or get_llm_model("gpt-4o-mini")
    client = OpenAI(api_key=api_key, base_url=get_llm_base_url() or None, timeout=timeout)
    response = client.chat.completions.create(
        model=judge_model,
        messages=list(messages),
        temperature=0,
        max_tokens=600,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or ""


def _load_questions(path: Path) -> dict[str, dict[str, str]]:
    questions = benchmark_pipeline.read_csv(path)
    benchmark_pipeline.validate_questions(questions)
    return {row["question_id"]: row for row in questions}


def _selected(value: str, allowed: set[str] | None) -> bool:
    return not allowed or value.upper() in allowed


def judge_runs(
    *,
    questions_path: Path,
    runs_path: Path,
    output_path: Path,
    judge_id: str,
    judge_model: str | None = None,
    ids: set[str] | None = None,
    systems: set[str] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> list[dict[str, str]]:
    questions = _load_questions(questions_path)
    runs = benchmark_pipeline.read_csv(runs_path)

    existing = benchmark_pipeline.read_csv(output_path) if output_path.exists() else []
    seen = {(row["question_id"], row["system_id"], row["judge_id"]) for row in existing}
    output = list(existing)

    completed = 0
    for run in runs:
        qid = _clean(run.get("question_id"))
        system_id = _clean(run.get("system_id")).upper()
        if not _selected(qid, ids) or not _selected(system_id, systems):
            continue
        if (qid, system_id, judge_id) in seen:
            continue
        if not _clean(run.get("answer")):
            continue
        if qid not in questions:
            raise ValueError(f"Run row references unknown question_id: {qid}")

        messages = build_judge_messages(questions[qid], run, judge_id=judge_id)
        if dry_run:
            parsed = {
                "evidence_hit": "",
                "accuracy": "",
                "completeness": "",
                "unsupported_claim": "",
                "rationale": "dry_run",
                "raw_json": json.dumps({"messages": messages}, ensure_ascii=False),
            }
        else:
            raw = call_judge_model(messages, model=judge_model)
            parsed = parse_judge_response(raw)

        row = {
            "question_id": qid,
            "system_id": system_id,
            "judge_id": judge_id,
            "judge_model": judge_model or os.getenv("BENCHMARK_JUDGE_MODEL") or os.getenv("EVALUATOR_MODEL") or "",
            **parsed,
        }
        output.append(row)
        benchmark_pipeline.write_csv(output_path, benchmark_pipeline.JUDGMENT_FIELDS, output)
        completed += 1
        if limit is not None and completed >= limit:
            break
        if not dry_run:
            time.sleep(0.2)

    if completed == 0 and not output_path.exists():
        benchmark_pipeline.write_csv(output_path, benchmark_pipeline.JUDGMENT_FIELDS, output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Run model-based automatic judging for MediArch benchmark outputs")
    parser.add_argument("--questions", default=str(benchmark_pipeline.QUESTIONS_PATH))
    parser.add_argument("--runs", default=str(benchmark_pipeline.RUNS_PATH))
    parser.add_argument("--output", default=str(benchmark_pipeline.JUDGMENTS_PATH))
    parser.add_argument("--judge-id", required=True, help="Judge channel, usually A or B")
    parser.add_argument("--judge-model", default=None)
    parser.add_argument("--ids", nargs="+", default=None)
    parser.add_argument("--systems", nargs="+", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = judge_runs(
        questions_path=Path(args.questions),
        runs_path=Path(args.runs),
        output_path=Path(args.output),
        judge_id=args.judge_id,
        judge_model=args.judge_model,
        ids={item.upper() for item in args.ids} if args.ids else None,
        systems={item.upper() for item in args.systems} if args.systems else None,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    print(f"Wrote/updated {len(rows)} judgment rows: {args.output}")


if __name__ == "__main__":
    main()
