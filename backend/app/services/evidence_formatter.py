from __future__ import annotations

DEFAULT_SECTION_TITLE = "## Graph Search Result"
DEFAULT_BULLET_PREFIX = "- "


def _normalise_line(line: str) -> str:
    return line.strip()


def _ensure_markdown_block(lines: list[str]) -> str:
    cleaned = [_normalise_line(line) for line in lines if _normalise_line(line)]
    if not cleaned:
        return ""
    joined = "\n".join(cleaned)
    return joined if joined.startswith("##") else f"{DEFAULT_SECTION_TITLE}\n\n{joined}"


def enhance_graph_search_result(raw_text: str) -> str:
    """Format graph search output into a minimal Markdown snippet."""

    if not raw_text:
        return ""

    lines = raw_text.splitlines()

    if any(line.strip().startswith("##") for line in lines):
        cleaned = [_normalise_line(line) for line in lines]
        return "\n".join(cleaned).strip()

    bullets: list[str] = []
    for line in lines:
        normalised = _normalise_line(line)
        if not normalised:
            continue
        if not normalised.startswith(DEFAULT_BULLET_PREFIX):
            normalised = f"{DEFAULT_BULLET_PREFIX}{normalised}"
        bullets.append(normalised)

    return _ensure_markdown_block(bullets)


__all__ = ["enhance_graph_search_result"]
