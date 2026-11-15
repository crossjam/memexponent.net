"""Reading and rewriting post front matter.

Posts carry YAML front matter between '---' fences. Only top-level
"key: value" lines are read or rewritten; everything else in the front
matter, such as a multi-line pyproject block, is kept as written.

posts/_metadata.yml defaults every post to `draft: true`, so a post is
published only when its own front matter says `draft: false`.
"""

import json
import re
from datetime import datetime

# A top-level front matter line: "key: value"
META_LINE = re.compile(r"^([A-Za-z][\w-]*):(.*)$")

DATE_FORMAT = "%Y-%m-%d %H:%M"


def header_bounds(lines: list[str]) -> tuple[int, int]:
    """
    Locate the front matter of a post.

    Returns (start, end): the front matter's lines are lines[start:end],
    empty when the post has none.
    """
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return 1, i
        raise ValueError("unterminated YAML front matter")
    return 0, 0


def get_field(text: str, key: str) -> str | None:
    """Return a front matter field's raw value, or None when it is absent."""
    lines = text.splitlines()
    start, end = header_bounds(lines)
    for line in lines[start:end]:
        m = META_LINE.match(line)
        if m and m.group(1).lower() == key:
            return m.group(2).strip()
    return None


def set_field(text: str, key: str, value: str) -> str:
    """Set a front matter field, replacing it in place or appending it."""
    lines = text.splitlines()
    start, end = header_bounds(lines)
    if end == 0:
        raise ValueError("no YAML front matter")

    for i in range(start, end):
        m = META_LINE.match(lines[i])
        if m and m.group(1).lower() == key:
            lines[i] = f"{m.group(1)}: {value}"
            break
    else:
        lines.insert(end, f"{key}: {value}")

    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def is_draft(text: str) -> bool:
    """Whether a post is a draft; without its own draft field it inherits draft: true."""
    value = get_field(text, "draft")
    return value is None or value.strip("\"'").lower() != "false"


def title_of(text: str) -> str | None:
    """A post's title with any surrounding quotes removed."""
    title = get_field(text, "title")
    return title.strip("\"'") if title else None


def yaml_string(value: str) -> str:
    """Quote a YAML scalar; JSON strings are valid YAML double-quoted scalars."""
    return json.dumps(value, ensure_ascii=False)


def new_post(title: str, author: str, content: str, date: datetime) -> str:
    """The text of a new draft post with YAML front matter."""
    return f"""---
title: {yaml_string(title)}
date: {date.strftime(DATE_FORMAT)}
author: {yaml_string(author)}
categories: []
draft: true
---

{content}
"""


def publish_text(text: str, redate: bool, now: datetime) -> str:
    """Mark a post published, dating it now if undated or if asked to."""
    text = set_field(text, "draft", "false")
    if redate or get_field(text, "date") is None:
        text = set_field(text, "date", now.strftime(DATE_FORMAT))
    return text
