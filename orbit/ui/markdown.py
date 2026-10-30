"""Render the subset of Markdown used in chat replies."""

import html
import re


def markdown_to_html(source):
    """Turn common Markdown into Qt rich text so reply markers are rendered."""
    text = (source or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return ""
    holders = []

    def hold(piece):
        holders.append(piece)
        return f"\u0000{len(holders) - 1}\u0000"

    def code_block(body):
        escaped = html.escape(body.strip("\n")).replace("\n", "<br>")
        return (
            "<div style=\"background:#f3f1f8;border-radius:8px;padding:8px 10px;"
            "margin:4px 0;font-family:monospace;\">" + escaped + "</div>"
        )

    text = re.sub(r"```[^\n`]*\n(.*?)```", lambda match: hold(code_block(match.group(1))), text, flags=re.S)
    text = re.sub(
        r"`([^`\n]+)`",
        lambda match: hold(
            "<span style=\"background:#eceaf3;border-radius:4px;padding:0 4px;font-family:monospace;\">"
            + html.escape(match.group(1)) + "</span>"
        ),
        text,
    )
    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
        lambda match: hold(f"<a href=\"{html.escape(match.group(2), quote=True)}\">{html.escape(match.group(1))}</a>"),
        text,
    )
    text = html.escape(text)

    def inline(value):
        value = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", value)
        value = re.sub(r"__(.+?)__", r"<b>\1</b>", value)
        value = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", value)
        return value

    parts = []
    list_tag = None

    def close_list():
        nonlocal list_tag
        if list_tag:
            parts.append(f"</{list_tag}>")
            list_tag = None

    for line in text.split("\n"):
        stripped = line.strip()
        heading = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        bullet = re.match(r"^[-*+]\s+(.*)$", stripped)
        number = re.match(r"^\d+\.\s+(.*)$", stripped)
        quote = re.match(r"^&gt;\s?(.*)$", stripped)
        if stripped in ("---", "***", "___"):
            close_list()
            parts.append("<hr>")
            continue
        if heading:
            close_list()
            level = len(heading.group(1))
            size = {1: "16px", 2: "14px", 3: "13px"}[level]
            parts.append(f"<div style=\"font-size:{size};font-weight:700;margin:8px 0 2px;\">{inline(heading.group(2))}</div>")
            continue
        if bullet or number:
            tag = "ul" if bullet else "ol"
            item = (bullet or number).group(1)
            if list_tag != tag:
                close_list()
                parts.append(f"<{tag} style=\"margin:4px 0 4px 16px;\">")
                list_tag = tag
            parts.append("<li>" + inline(item) + "</li>")
            continue
        close_list()
        if quote:
            parts.append(
                "<div style=\"border-left:3px solid #d9d4e8;margin:4px 0;padding-left:8px;color:#665f80;\">"
                + inline(quote.group(1)) + "</div>"
            )
            continue
        if stripped == "":
            parts.append("<div style=\"height:8px;\"></div>")
            continue
        parts.append("<div>" + inline(line) + "</div>")
    close_list()
    rendered = "".join(parts)
    return re.sub(r"\u0000(\d+)\u0000", lambda match: holders[int(match.group(1))], rendered)
