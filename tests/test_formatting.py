"""Wrapping post paragraphs while leaving every other block as written."""

from memexponent.formatting import WIDTH, format_body, format_post

LONG = " ".join(["word"] * 40)


def test_wraps_paragraphs_to_the_width():
    out = format_body(LONG + "\n")
    assert len(out.splitlines()) > 1
    assert all(len(line) <= WIDTH for line in out.splitlines())
    assert out.split() == LONG.split()


def test_joins_short_lines_in_a_paragraph():
    assert format_body("one\ntwo\nthree\n") == "one two three\n"


def test_keeps_blockquotes_and_code_as_written():
    body = "\n".join(
        [
            "> " + LONG,
            "> short",
            "",
            "```python",
            "x = '" + LONG + "'",
            "",
            "```",
            "",
            "    indented " + LONG,
            "",
        ]
    )
    assert format_body(body) == body


def test_keeps_lists_and_tables():
    body = "\n".join(
        [
            "* item " + LONG,
            "    * nested with four spaces",
            "",
            "1. first",
            "",
            "| a | b |",
            "|---|---|",
            "",
            "[^1]: " + LONG,
            "",
        ]
    )
    assert format_body(body) == body


def test_keeps_html_comments():
    body = f"{LONG}\n\n<!-- a note -->\n\nAfter.\n"
    out = format_body(body)
    assert "\n\n<!-- a note -->\n\nAfter.\n" in out


def test_keeps_quarto_divs_shortcodes_cells_and_math():
    body = "\n".join(
        [
            "::: {.callout-note}",
            LONG,
            ":::",
            "",
            "{{< video https://example.com/" + LONG.replace(" ", "-") + " >}}",
            "",
            "```{python}",
            "#| echo: false",
            "x = '" + LONG + "'",
            "```",
            "",
            "$$",
            "a + b",
            "= c",
            "$$",
            "",
        ]
    )
    assert format_body(body) == body


def test_keeps_hard_line_breaks():
    body = "line one  \nline two\n"
    assert format_body(body) == body


def test_keeps_yaml_front_matter_exactly():
    header = '---\ntitle: "A: Title"\ndate: 2026-11-12 20:02\ndraft: true\n---\n'
    out = format_post(header + "\n" + LONG + "\n")
    assert out.startswith(header + "\n")
    assert len(out.splitlines()) > 7


def test_is_idempotent():
    once = format_post("---\ntitle: x\n---\n\n" + LONG + "\n\n> " + LONG + "\n")
    assert format_post(once) == once


def test_moves_only_line_breaks():
    body = "_The Book_ by [_An Author_](https://example.com/x) was __great__, `a b c`. " + LONG
    out = format_body(body + "\n")
    assert out.split() == body.split()
    assert "`a b c`" in out


def test_keeps_raw_html_across_blank_lines():
    body = f"<pre>\n\nalpha\nbeta\n\n</pre>\n\n<!--\n\n{LONG}\n\n-->\n"
    assert format_body(body) == body


def test_keeps_everything_inside_fenced_divs():
    body = f"::: {{.callout-note}}\n::: inner\n\n{LONG}\n\n:::\n\n{LONG}\n\n:::\n"
    assert format_body(body) == body


def test_wraps_again_after_a_div_or_raw_block_ends():
    out = format_body(f"::: note\n:::\n\n<pre>x</pre>\n\n{LONG}\n")
    assert len(out.splitlines()) > 5


def test_keeps_backslash_hard_breaks():
    body = "line one\\\nline two\n"
    assert format_body(body) == body
