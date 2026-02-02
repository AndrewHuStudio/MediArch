"""
Utility for exporting the MediArch Graph LangGraph workflow as a flowchart image.

The script loads the compiled MediArch Graph and writes either the Graphviz PNG
(matches ``images/mediarch_graph.png``) or a Mermaid definition so that
designers can refresh the docs snapshot whenever the workflow changes.
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Literal

import sys

logger = logging.getLogger(__name__)


PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.agents import mediarch_graph
DEFAULT_OUTPUT = PROJECT_ROOT / "images" / "mediarch_graph.png"
SupportedFormat = Literal["png", "mermaid", "mermaid-png"]


def _get_compiled_mediarch_graph(rebuild: bool):
    """Return the compiled LangGraph instance, optionally forcing a rebuild."""
    if rebuild:
        logger.info("Rebuilding MediArch Graph before export.")
        return mediarch_graph.build_mediarch_graph()
    return mediarch_graph.graph


def _safe_output_path(path: Path, allow_overwrite: bool) -> Path:
    """Return a writable output path, adding a timestamp if needed."""
    if allow_overwrite or not path.exists():
        return path
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    new_name = f"{path.stem}_{timestamp}{path.suffix}"
    new_path = path.with_name(new_name)
    logger.warning(
        "Target %s already exists. Writing new export to %s instead. "
        "Use --allow-overwrite to reuse the same filename.",
        path,
        new_path,
    )
    return new_path


def generate_mediarch_flowchart(
    output_path: Path | str = DEFAULT_OUTPUT,
    *,
    fmt: SupportedFormat = "png",
    xray_depth: int | bool = False,
    rebuild: bool = False,
    allow_overwrite: bool = False,
) -> Path:
    """
    Render the MediArch Graph diagram.

    Args:
        output_path: Where to write the artifact (suffix controls type).
        fmt: Export format. ``png`` uses Graphviz, ``mermaid`` dumps text,
            and ``mermaid-png`` renders via Mermaid.
        xray_depth: Set to True/int to expand nested LangGraph subgraphs.
        rebuild: Force a fresh compilation of the MediArch Graph before drawing.

    Returns:
        Path to the generated artifact.
    """
    compiled_graph = _get_compiled_mediarch_graph(rebuild=rebuild)
    graph = compiled_graph.get_graph(xray=xray_depth)

    output = _safe_output_path(Path(output_path), allow_overwrite)
    output.parent.mkdir(parents=True, exist_ok=True)

    fmt = fmt.lower()
    if fmt == "png":
        try:
            graph.draw_png(str(output))
        except ImportError as exc:
            # Graceful fallback when pygraphviz is not installed.
            logger.warning(
                "pygraphviz not available (%s). Falling back to Mermaid PNG.", exc
            )
            graph.draw_mermaid_png(output_file_path=str(output))
    elif fmt == "mermaid":
        output.write_text(graph.draw_mermaid(), encoding="utf-8")
    elif fmt == "mermaid-png":
        graph.draw_mermaid_png(output_file_path=str(output))
    else:
        raise ValueError(f"Unsupported format '{fmt}'.")

    logger.info("MediArch graph exported to %s [%s]", output, fmt)
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export the MediArch Graph LangGraph workflow diagram."
    )
    parser.add_argument(
        "-o",
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Output path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=["png", "mermaid", "mermaid-png"],
        default="png",
        help="Export format.",
    )
    parser.add_argument(
        "--xray",
        type=int,
        default=0,
        help="Include nested LangGraph subgraphs (0 disables).",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Force recompiling the MediArch Graph before rendering.",
    )
    parser.add_argument(
        "--allow-overwrite",
        action="store_true",
        help="Permit overwriting the target file. Otherwise a timestamp suffix is used.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    xray = args.xray
    if xray <= 0:
        xray = False

    generate_mediarch_flowchart(
        output_path=args.output,
        fmt=args.format,
        xray_depth=xray,
        rebuild=args.rebuild,
        allow_overwrite=args.allow_overwrite,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    main()
