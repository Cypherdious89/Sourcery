"""Markdown export for a notebook — sources list + full chat transcript.

A pure string-building function (no DB access), so it's cheap to unit test:
callers fetch the notebook/sources/messages themselves and hand them in.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.models import Notebook, Source
from app.schemas import MessageOut

_ROLE_LABEL = {"user": "You", "assistant": "Assistant"}


def _escape(text: str) -> str:
    # Only markdown-significant at line starts; a stray "# " or "- " from a
    # pasted question shouldn't be rendered as a heading/list in the export.
    return "\n".join(
        f"\\{line}" if line[:1] in ("#", "-", ">", "*", "+") else line
        for line in text.splitlines()
    )


def build_markdown(
    notebook: Notebook, sources: list[Source], messages: list[MessageOut]
) -> str:
    source_names = {s.id: s.original_name_or_url for s in sources}
    exported_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [f"# {notebook.title}", "", f"_Exported {exported_at}_", ""]

    lines.append(f"## Sources ({len(sources)})")
    lines.append("")
    if sources:
        for s in sources:
            lines.append(f"- **[{s.type.value.upper()}]** {s.original_name_or_url} — {s.status.value}")
    else:
        lines.append("_No sources._")
    lines.append("")

    lines.append("## Chat transcript")
    lines.append("")
    if not messages:
        lines.append("_No messages yet._")
    for m in messages:
        lines.append(f"### {_ROLE_LABEL.get(m.role.value, m.role.value)}")
        lines.append("")
        # Only the user's raw question needs escaping (a pasted "# heading"
        # shouldn't render as one) — the assistant's content is markdown it
        # generated on purpose (lists, emphasis) and escaping it would break
        # its own formatting.
        content = m.content if m.role.value == "assistant" else _escape(m.content)
        lines.append(content)
        lines.append("")
        if m.citations:
            lines.append("**Citations:**")
            lines.append("")
            for c in m.citations:
                source_name = source_names.get(c.source_id, "unknown source")
                snippet = c.snippet.replace("\n", " ")
                lines.append(f"{c.marker}. _{snippet}_ — from {source_name}")
            lines.append("")
        if m.role.value == "assistant" and m.provider:
            meta = f"provider: {m.provider} · model: {m.model} · status: {m.status}"
            if m.cache_hit:
                meta += " · cache hit"
            lines.append(f"<sub>{meta}</sub>")
            lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
