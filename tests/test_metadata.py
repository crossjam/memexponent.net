from datetime import datetime

import pytest

from memexponent import metadata

NOW = datetime(2026, 11, 10, 9, 30)

POST = """---
title: "Hello"
draft: true
---

Body text: with a colon.
"""


def test_publish_marks_draft_false_and_adds_date_inside_front_matter():
    text = metadata.publish_text(POST, redate=False, now=NOW)
    assert (
        text
        == """---
title: "Hello"
draft: false
date: 2026-11-10 09:30
---

Body text: with a colon.
"""
    )


def test_publish_keeps_existing_date_unless_redated():
    dated = metadata.set_field(POST, "date", "2020-01-01 00:00")
    assert (
        metadata.get_field(metadata.publish_text(dated, False, NOW), "date") == "2020-01-01 00:00"
    )
    assert metadata.get_field(metadata.publish_text(dated, True, NOW), "date") == "2026-11-10 09:30"


def test_publish_leaves_nested_front_matter_alone():
    text = '---\ntitle: "M"\npyproject: |-\n  draft: true\n  date: x\n---\n\nBody.\n'
    published = metadata.publish_text(text, False, NOW)
    assert "  draft: true\n  date: x\n" in published
    assert metadata.get_field(published, "draft") == "false"


@pytest.mark.parametrize(
    ("front", "draft"),
    [("draft: true", True), ("draft: false", False), ('draft: "false"', False), ("", True)],
)
def test_is_draft_defaults_to_draft_like_posts_metadata(front, draft):
    assert metadata.is_draft(f"---\ntitle: T\n{front}\n---\n\nBody.\n") is draft


def test_new_post_quotes_awkward_titles():
    text = metadata.new_post('Say "hi": a post', "Brian M. Dennis", "Body", NOW)
    assert metadata.get_field(text, "title") == '"Say \\"hi\\": a post"'
    assert metadata.get_field(text, "draft") == "true"
    assert metadata.is_draft(text)
