"""Atlassian Document Format helpers.

Jira Cloud returns rich text as ADF (a JSON document tree) and accepts it on
write. Agents want plain markdown-ish text, so this module converts in both
directions. Keeping it here means no other module ever learns what ADF is.
"""

from __future__ import annotations

from typing import Any

_LIST_MARKERS = {"bulletList": "- ", "orderedList": "1. "}


def adf_to_text(node: Any, *, depth: int = 0) -> str:
    """Flatten an ADF document (or a plain string) into readable text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(child, depth=depth) for child in node)
    if not isinstance(node, dict):
        return str(node)

    node_type = node.get("type")
    content = node.get("content", [])

    if node_type == "text":
        text = node.get("text", "")
        for mark in node.get("marks", []):
            kind = mark.get("type")
            if kind == "strong":
                text = f"**{text}**"
            elif kind == "em":
                text = f"*{text}*"
            elif kind == "code":
                text = f"`{text}`"
            elif kind == "link":
                href = mark.get("attrs", {}).get("href", "")
                text = f"[{text}]({href})"
        return text
    if node_type == "hardBreak":
        return "\n"
    if node_type == "paragraph":
        return adf_to_text(content, depth=depth) + "\n\n"
    if node_type == "heading":
        level = node.get("attrs", {}).get("level", 1)
        return f"{'#' * int(level)} {adf_to_text(content, depth=depth).strip()}\n\n"
    if node_type == "codeBlock":
        language = node.get("attrs", {}).get("language", "")
        return f"```{language}\n{adf_to_text(content, depth=depth).rstrip()}\n```\n\n"
    if node_type == "blockquote":
        inner = adf_to_text(content, depth=depth).strip().splitlines()
        return "\n".join(f"> {line}" for line in inner) + "\n\n"
    if node_type in _LIST_MARKERS:
        marker = _LIST_MARKERS[node_type]
        indent = "  " * depth
        items = []
        for index, item in enumerate(content, start=1):
            bullet = marker if node_type == "bulletList" else f"{index}. "
            body = adf_to_text(item.get("content", []), depth=depth + 1).strip()
            items.append(f"{indent}{bullet}{body}")
        return "\n".join(items) + "\n\n"
    if node_type == "listItem":
        return adf_to_text(content, depth=depth)
    if node_type == "rule":
        return "\n---\n\n"
    if node_type == "mediaSingle" or node_type == "mediaGroup":
        return "(attachment)\n\n"
    if node_type == "table":
        rows = [adf_to_text(row, depth=depth).strip() for row in content]
        return "\n".join(rows) + "\n\n"
    if node_type in {"tableRow", "tableCell", "tableHeader"}:
        cells = [adf_to_text(cell, depth=depth).strip() for cell in content]
        return " | ".join(c for c in cells if c)
    if node_type == "doc" or content:
        return adf_to_text(content, depth=depth)
    return ""


def text_to_adf(text: str) -> dict[str, Any]:
    """Wrap plain text into a minimal valid ADF document."""
    paragraphs = [block for block in text.split("\n\n") if block.strip()] or [""]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": block.strip()}] if block.strip() else [],
            }
            for block in paragraphs
        ],
    }


def extract_bullets(text: str) -> list[str]:
    """Pull bullet/checkbox lines out of flattened text (acceptance criteria)."""
    bullets: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        for prefix in ("- [ ] ", "- [x] ", "- ", "* ", "• "):
            if line.startswith(prefix):
                candidate = line[len(prefix) :].strip()
                if candidate:
                    bullets.append(candidate)
                break
        else:
            if len(line) > 3 and line[0].isdigit() and line[1:3] in (". ", ") "):
                bullets.append(line[3:].strip())
    return bullets
