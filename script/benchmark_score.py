from __future__ import annotations

import argparse
import json
from pathlib import Path

from script import benchmark_pipeline


CSV_PATH = benchmark_pipeline.ADJUDICATED_PATH
SUMMARY_PATH = benchmark_pipeline.SUMMARY_PATH
MODES = tuple(system_id for system_id, _ in benchmark_pipeline.SYSTEMS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize adjudicated MediArch benchmark scores")
    parser.add_argument("--judgments", default=str(CSV_PATH), help="Adjudicated judgment CSV")
    parser.add_argument("--output", default=str(SUMMARY_PATH), help="Summary JSON output")
    parser.add_argument("--iterations", type=int, default=2000, help="Bootstrap iterations")
    args = parser.parse_args()

    summary = benchmark_pipeline.write_summary_file(
        Path(args.judgments),
        Path(args.output),
        iterations=args.iterations,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
