from __future__ import annotations

import csv
from pathlib import Path
import textwrap


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "docs" / "智能体检索实验" / "benchmark_scoring.csv"
OUT_PATH = ROOT / "docs" / "智能体检索实验" / "benchmark_review.md"


def clip(text: str, limit: int = 1800) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


def main() -> None:
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    parts: list[str] = []
    parts.append("# Benchmark Review Sheet")
    parts.append("")
    parts.append(f"- Rows: {len(rows)}")
    parts.append("- Purpose: question-by-question review before manual scoring")
    parts.append("")

    for row in rows:
        parts.append(f"## {row['ID']} {row['Question']}")
        parts.append("")
        parts.append(f"- Source Type: {row['Source_Type']}")
        parts.append("- Key Evidence:")
        parts.append("")
        parts.append("```text")
        parts.append((row.get("Key_Evidence") or "").strip())
        parts.append("```")
        parts.append("")

        for mode in ("R0", "R1", "R2"):
            answer = (row.get(f"{mode}_Answer") or "").strip()
            parts.append(f"### {mode}")
            parts.append("")
            parts.append(
                f"- Answer Length: {len(answer)} chars"
            )
            parts.append("")
            parts.append("```text")
            parts.append(clip(answer))
            parts.append("```")
            parts.append("")

        parts.append("---")
        parts.append("")

    OUT_PATH.write_text("\n".join(parts), encoding="utf-8")
    print(f"Wrote review sheet: {OUT_PATH}")


if __name__ == "__main__":
    main()
