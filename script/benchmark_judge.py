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
Evaluate the question, system answer, citations, and provided reference anchors.
The gold evidence is a reference anchor, not an exclusive answer key.
Do not reward claims that are unsupported by the answer's cited evidence, the provided anchors, or clearly relevant healthcare-architecture design logic.
For design and spatial-reasoning answers, distinguish cited evidence, evidence-derived spatial constraints, actionable design responses, and explicitly labeled design inference.
Use only the allowed scoring ranges. Never output -1 or null. If evidence is missing or you cannot verify support, use 0 for that metric.
Return JSON only."""


V2_SCORING_GUIDANCE = """Scoring standard v2:
- Gold evidence is a reference anchor, not an exclusive answer key. It may be incomplete, too narrow, or partially misassigned.
- Evidence_Hit means evidence support, not exact-match scoring. Score 1 if the answer is supported by the gold anchor OR by substantively equivalent cited evidence from another source. Score 0 if the answer is unsupported, cites irrelevant sources, or provides no usable evidence.
- Do not set Accuracy or Completeness to zero solely because the cited document differs from the gold reference.
- Accuracy should judge whether the answer correctly responds to the question. Use 0 for wrong/irrelevant/contradictory answers, 1 for partially correct or overly generic answers, and 2 for substantively correct answers.
- Completeness should judge whether the answer covers the major design dimensions required by the question. Use 0 for missing most required logic, 1 for partial coverage, and 2 for broad, well-structured coverage.
- Unsupported_Claim should penalize important claims that are not backed by citations, the reference anchors, or defensible design logic. Do not treat every non-gold citation as unsupported.
- For recommendation and spatial reasoning tasks, an answer may include four layers: evidence basis (证据依据), spatial constraints (空间约束), design response (设计回应), and inference boundary (推论边界). A clearly labeled design inference should not be penalized as Unsupported_Claim solely because it is not a verbatim standard clause, if it is a reasonable inference from cited evidence or the provided anchors.
- Penalize design inference only when it contradicts evidence, invents numeric thresholds, misstates a cited source, or presents speculation as a binding code requirement.
- Completeness should reward well-organized answers that connect evidence -> spatial constraint -> design response -> inference boundary for design questions, even when the answer includes evidence-informed professional judgment."""


def normalize_timeout(timeout: int | float | None) -> int | float | None:
    if timeout is None:
        return None
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        return timeout
    if value <= 0:
        return None
    if value.is_integer():
        return int(value)
    return value


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

Additional v2 scoring calibration:
{V2_SCORING_GUIDANCE}

System answer:
{_clean(run.get("answer"))}

System citations:
{_clean(run.get("citations"))}

Return JSON with exactly these keys:
{{
  "evidence_hit": 0 or 1 (never -1),
  "accuracy": 0, 1, or 2 (never -1),
  "completeness": 0, 1, or 2 (never -1),
  "unsupported_claim": 0, 1, or 2 (never -1),
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
    client = OpenAI(api_key=api_key, base_url=get_llm_base_url() or None, timeout=normalize_timeout(timeout))
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
    timeout: int = 90,
    dry_run: bool = False,
    max_parse_attempts: int = 3,
    continue_on_parse_error: bool = True,
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
            last_error: Exception | None = None
            parsed = None
            raw = ""
            attempts = max(1, int(max_parse_attempts or 1))
            for attempt in range(1, attempts + 1):
                raw = call_judge_model(messages, model=judge_model, timeout=timeout)
                try:
                    parsed = parse_judge_response(raw)
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt >= attempts:
                        if not continue_on_parse_error:
                            raise
                        parsed = {
                            "evidence_hit": "",
                            "accuracy": "",
                            "completeness": "",
                            "unsupported_claim": "",
                            "rationale": f"parse_error: {exc}",
                            "raw_json": json.dumps({"raw": raw, "error": str(exc)}, ensure_ascii=False),
                        }
                        break
                    messages = list(messages) + [
                        {
                            "role": "user",
                            "content": (
                                "Your previous JSON used an invalid score or invalid schema. "
                                "Return valid JSON only. evidence_hit must be 0 or 1; "
                                "accuracy/completeness/unsupported_claim must be 0, 1, or 2."
                            ),
                        }
                    ]
                    time.sleep(0.2)
            if parsed is None:
                raise last_error or ValueError("Judge parsing failed")

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
    parser.add_argument("--timeout", type=int, default=90, help="Judge model timeout per request in seconds")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-parse-attempts", type=int, default=3)
    parser.add_argument("--fail-on-parse-error", action="store_true")
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
        timeout=args.timeout,
        dry_run=args.dry_run,
        max_parse_attempts=args.max_parse_attempts,
        continue_on_parse_error=not args.fail_on_parse_error,
    )
    print(f"Wrote/updated {len(rows)} judgment rows: {args.output}")


if __name__ == "__main__":
    main()
