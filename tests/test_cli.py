"""Drive both post commands against a throwaway repository."""

import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

from memexponent import metadata
from memexponent.cli import cli

HOOKS_SCRIPT = Path(__file__).parents[1] / "install-draft-hooks.sh"

OLD_POST = "---\ntitle: Old\ndraft: false\n---\n\nOld post.\n"


def run(*args: str, cwd: Path) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


def post(blog: Path, slug: str) -> Path:
    return blog / "posts" / slug / "index.qmd"


@pytest.fixture
def blog(tmp_path, monkeypatch):
    """A clone with main, a bare origin, and a bare drafts remote."""
    # The pre-push guard only lets draft branches go to a URL like this one
    drafts = tmp_path / "crossjam" / "memexponent.drafts.git"
    origin = tmp_path / "origin.git"
    for bare in (drafts, origin):
        run("git", "init", "-q", "--bare", "-b", "main", str(bare), cwd=tmp_path)

    work = tmp_path / "work"
    run("git", "init", "-q", "-b", "main", str(work), cwd=tmp_path)
    for key, value in [
        ("user.name", "Test"),
        ("user.email", "test@example.com"),
        ("commit.gpgsign", "false"),
    ]:
        run("git", "config", key, value, cwd=work)
    shutil.copy(HOOKS_SCRIPT, work)
    post(work, "old").parent.mkdir(parents=True)
    post(work, "old").write_text(OLD_POST)
    run("git", "add", ".", cwd=work)
    run("git", "commit", "-q", "-m", "Initial", cwd=work)
    run("git", "remote", "add", "origin", str(origin), cwd=work)
    run("git", "remote", "add", "drafts", str(drafts), cwd=work)
    run("git", "push", "-q", "origin", "main", cwd=work)

    monkeypatch.chdir(work)
    return work


def test_draft_then_publish(blog):
    runner = CliRunner()
    new = runner.invoke(
        cli,
        ["draft", "new", "--title", "Hello, World", "--file", "-", "--yes"],
        input="First draft.\n",
    )
    assert new.exit_code == 0, new.output
    assert run("git", "branch", "--show-current", cwd=blog).strip() == "draft/hello_world"
    assert "draft/hello_world" in run("git", "ls-remote", "--heads", "drafts", cwd=blog)

    path = post(blog, "hello_world")
    assert metadata.get_field(path.read_text(), "draft") == "true"
    assert path.read_text().rstrip().endswith("First draft.")

    run("git", "switch", "-q", "main", cwd=blog)
    published = runner.invoke(cli, ["publish", "draft", "hello_world", "--push"])
    assert published.exit_code == 0, published.output

    assert run("git", "log", "-1", "--format=%s", cwd=blog).strip() == "Publish Hello, World"
    assert metadata.get_field(path.read_text(), "draft") == "false"
    assert run("git", "rev-parse", "main", cwd=blog) == run(
        "git", "rev-parse", "origin/main", cwd=blog
    )
    assert run("git", "ls-remote", "--heads", "drafts", cwd=blog) == ""
    assert run("git", "branch", "--list", "draft/*", cwd=blog) == ""


def test_draft_refuses_a_published_slug(blog):
    result = CliRunner().invoke(cli, ["draft", "new", "--title", "Old", "--yes"])
    assert result.exit_code == 1
    assert "already a published post" in result.output
    assert run("git", "branch", "--show-current", cwd=blog).strip() == "main"


def test_publish_switches_to_main_from_the_draft(blog):
    start_draft("From Draft")
    result = CliRunner().invoke(cli, ["publish", "draft", "from_draft"])
    assert result.exit_code == 0, result.output
    assert run("git", "branch", "--show-current", cwd=blog).strip() == "main"
    assert run("git", "log", "-1", "--format=%s", cwd=blog).strip() == "Publish From Draft"


def test_publish_refuses_uncommitted_changes(blog):
    start_draft("Dirty")
    post(blog, "dirty").write_text("unsaved\n")
    result = CliRunner().invoke(cli, ["publish", "draft", "dirty"])
    assert result.exit_code == 1
    assert "uncommitted changes" in result.output
    assert run("git", "branch", "--show-current", cwd=blog).strip() == "draft/dirty"


def start_draft(title: str) -> None:
    result = CliRunner().invoke(cli, ["draft", "new", "--title", title, "--yes"])
    assert result.exit_code == 0, result.output


def commit_on_main(blog: Path, slug: str, text: str) -> None:
    run("git", "switch", "-q", "main", cwd=blog)
    post(blog, slug).write_text(text)
    run("git", "commit", "-q", "-am", f"Change {slug}", cwd=blog)


def test_publish_merges_onto_a_main_that_moved_on(blog):
    start_draft("Later")
    commit_on_main(blog, "old", OLD_POST.replace("Old post.", "Old post, edited."))

    result = CliRunner().invoke(cli, ["publish", "draft", "later"])
    assert result.exit_code == 0, result.output
    assert "edited" in post(blog, "old").read_text()
    assert not metadata.is_draft(post(blog, "later").read_text())
    assert run("git", "status", "--porcelain", cwd=blog) == ""
    # A squash merge: one parent, so no draft history on main
    assert len(run("git", "log", "-1", "--format=%P", cwd=blog).split()) == 1


def test_publish_conflict_changes_nothing(blog):
    start_draft("Clash")
    post(blog, "old").write_text("---\ntitle: Old\n---\n\nDraft side.\n")
    run("git", "commit", "-q", "-am", "Draft edit", cwd=blog)
    commit_on_main(blog, "old", "---\ntitle: Old\n---\n\nMain side.\n")
    head = run("git", "rev-parse", "HEAD", cwd=blog)

    result = CliRunner().invoke(cli, ["publish", "draft", "clash"])
    assert result.exit_code == 1
    assert "conflicts with main in posts/old/index.qmd" in result.output
    assert run("git", "rev-parse", "HEAD", cwd=blog) == head
    assert run("git", "status", "--porcelain", cwd=blog) == ""


def test_push_refuses_draft_branches_outside_drafts(blog):
    from memexponent import repo

    run("git", "switch", "-q", "-c", "draft/leak", cwd=blog)
    with repo.open_repo() as r, pytest.raises(click.ClickException, match="refusing to push"):
        repo.push(r, "origin", "draft/leak")
    assert run("git", "ls-remote", "--heads", "origin", cwd=blog).count("draft/") == 0


def test_save_commits_and_pushes(blog):
    start_draft("Saved")
    path = post(blog, "saved")
    path.write_text(path.read_text() + "\nMore words.\n")
    (path.parent / "figure.png").write_bytes(b"png")

    result = CliRunner().invoke(cli, ["draft", "save", "-m", "Add words"])
    assert result.exit_code == 0, result.output
    assert run("git", "status", "--porcelain", cwd=blog) == ""
    assert run("git", "log", "-1", "--format=%s", cwd=blog).strip() == "Add words"
    remote_tip = run("git", "ls-remote", "drafts", "refs/heads/draft/saved", cwd=blog).split()[0]
    assert remote_tip == run("git", "rev-parse", "HEAD", cwd=blog).strip()


def test_switch_fetches_a_draft_started_elsewhere(blog):
    start_draft("Elsewhere")
    run("git", "switch", "-q", "main", cwd=blog)
    run("git", "branch", "-q", "-D", "draft/elsewhere", cwd=blog)
    run("git", "update-ref", "-d", "refs/remotes/drafts/draft/elsewhere", cwd=blog)

    listed = CliRunner().invoke(cli, ["draft", "list"])
    assert "elsewhere  (drafts)" in listed.output

    result = CliRunner().invoke(cli, ["draft", "switch", "elsewhere"])
    assert result.exit_code == 0, result.output
    assert run("git", "branch", "--show-current", cwd=blog).strip() == "draft/elsewhere"
    assert (
        run("git", "rev-parse", "--abbrev-ref", "@{upstream}", cwd=blog).strip()
        == "drafts/draft/elsewhere"
    )


def test_finish_refuses_an_unpublished_draft(blog):
    start_draft("Not Yet")
    run("git", "switch", "-q", "main", cwd=blog)

    result = CliRunner().invoke(cli, ["publish", "finish", "not_yet"])
    assert result.exit_code == 1
    assert "does not have posts/not_yet/index.qmd published" in result.output
    assert "draft/not_yet" in run("git", "branch", "--list", "draft/*", cwd=blog)


def test_publish_then_finish(blog):
    start_draft("Two Step")
    run("git", "switch", "-q", "main", cwd=blog)
    runner = CliRunner()
    published = runner.invoke(cli, ["publish", "draft", "two_step"])
    assert published.exit_code == 0, published.output
    assert "post publish finish two_step" in published.output
    assert "git " not in published.output

    finished = runner.invoke(cli, ["publish", "finish", "two_step"])
    assert finished.exit_code == 0, finished.output
    assert run("git", "ls-remote", "--heads", "drafts", cwd=blog) == ""
    assert run("git", "rev-parse", "main", cwd=blog) == run(
        "git", "rev-parse", "origin/main", cwd=blog
    )


def test_publish_refuses_to_overwrite_an_untracked_file(blog):
    start_draft("Collide")
    run("git", "switch", "-q", "main", cwd=blog)
    notes = post(blog, "collide")
    notes.parent.mkdir()
    notes.write_text("Local notes, never committed.\n")

    result = CliRunner().invoke(cli, ["publish", "draft", "collide"])
    assert result.exit_code == 1
    assert "posts/collide/index.qmd" in result.output
    assert notes.read_text() == "Local notes, never committed.\n"
    assert run("git", "log", "-1", "--format=%s", cwd=blog).strip() == "Initial"


def test_publish_commits_only_the_merged_paths(blog):
    start_draft("Tidy")
    run("git", "switch", "-q", "main", cwd=blog)
    (blog / "posts" / "half_written").mkdir()
    post(blog, "half_written").write_text("Not ready.\n")

    result = CliRunner().invoke(cli, ["publish", "draft", "tidy"])
    assert result.exit_code == 0, result.output
    committed = run("git", "show", "--name-only", "--format=", "HEAD", cwd=blog).split()
    assert committed == ["posts/tidy/index.qmd"]
    assert run("git", "status", "--porcelain", cwd=blog) == "?? posts/half_written/\n"


def test_publish_carries_the_posts_other_files(blog):
    start_draft("With Figure")
    (post(blog, "with_figure").parent / "figure.png").write_bytes(b"png")
    assert CliRunner().invoke(cli, ["draft", "save"]).exit_code == 0

    result = CliRunner().invoke(cli, ["publish", "draft", "with_figure"])
    assert result.exit_code == 0, result.output
    committed = run("git", "show", "--name-only", "--format=", "HEAD", cwd=blog).split()
    assert committed == ["posts/with_figure/figure.png", "posts/with_figure/index.qmd"]


def test_publish_warns_about_a_dangling_symlink(blog, tmp_path):
    start_draft("Linked")
    run("git", "switch", "-q", "main", cwd=blog)
    outside = tmp_path / "outside.qmd"
    (blog / "posts" / "linked").mkdir()
    post(blog, "linked").symlink_to(outside)

    result = CliRunner().invoke(cli, ["publish", "draft", "linked"])
    assert result.exit_code == 1
    assert "WARNING: posts/linked/index.qmd resolves outside the repository" in result.output
    assert not outside.exists()


def test_publish_refuses_a_symlinked_parent_directory(blog, tmp_path):
    start_draft("Nested")
    run("git", "switch", "-q", "main", cwd=blog)
    outside = tmp_path / "outside"
    outside.mkdir()
    (blog / "posts" / "nested").symlink_to(outside)

    result = CliRunner().invoke(cli, ["publish", "draft", "nested"])
    assert result.exit_code == 1
    assert "WARNING: posts/nested/index.qmd resolves outside the repository" in result.output
    assert list(outside.iterdir()) == []


def test_drop_deletes_a_pushed_draft_from_the_draft_itself(blog):
    start_draft("Abandon")
    result = CliRunner().invoke(cli, ["draft", "drop", "abandon", "--yes"])
    assert result.exit_code == 0, result.output
    assert run("git", "branch", "--show-current", cwd=blog).strip() == "main"
    assert run("git", "branch", "--list", "draft/*", cwd=blog) == ""
    assert run("git", "ls-remote", "--heads", "drafts", cwd=blog) == ""


def test_drop_refuses_unpushed_commits_without_force(blog):
    start_draft("Unsaved")
    path = post(blog, "unsaved")
    path.write_text(path.read_text() + "\nLocal only.\n")
    run("git", "commit", "-q", "-am", "Local edit", cwd=blog)

    refused = CliRunner().invoke(cli, ["draft", "drop", "unsaved", "--yes"])
    assert refused.exit_code == 1
    assert "commits that are not on drafts" in refused.output
    assert "draft/unsaved" in run("git", "branch", "--list", "draft/*", cwd=blog)

    forced = CliRunner().invoke(cli, ["draft", "drop", "unsaved", "--yes", "--force"])
    assert forced.exit_code == 0, forced.output
    assert run("git", "branch", "--list", "draft/*", cwd=blog) == ""
    assert run("git", "ls-remote", "--heads", "drafts", cwd=blog) == ""


def test_commands_recreate_a_missing_local_main(blog):
    start_draft("No Main")
    run("git", "branch", "-q", "-D", "main", cwd=blog)

    result = CliRunner().invoke(cli, ["draft", "new", "--title", "Second", "--yes"])
    assert result.exit_code == 0, result.output
    assert run("git", "rev-parse", "main", cwd=blog) == run(
        "git", "rev-parse", "origin/main", cwd=blog
    )
    assert (
        run("git", "rev-parse", "--abbrev-ref", "main@{upstream}", cwd=blog).strip()
        == "origin/main"
    )


def test_setup_adds_a_drafts_remote_git_can_use(blog, monkeypatch):
    from memexponent import repo

    url = run("git", "remote", "get-url", "drafts", cwd=blog).strip()
    run("git", "remote", "remove", "drafts", cwd=blog)
    monkeypatch.setattr(repo, "DRAFTS_URL", url)

    result = CliRunner().invoke(cli, ["setup"])
    assert result.exit_code == 0, result.output
    assert (
        run("git", "config", "remote.drafts.fetch", cwd=blog).strip()
        == "+refs/heads/*:refs/remotes/drafts/*"
    )
    start_draft("Tracked")
    assert (
        run("git", "rev-parse", "--abbrev-ref", "@{upstream}", cwd=blog).strip()
        == "drafts/draft/tracked"
    )


def snapshot(blog: Path) -> tuple[str, ...]:
    """Everything a command could change: refs, branch, working tree, both remotes."""
    return (
        run("git", "show-ref", cwd=blog),
        run("git", "branch", "--show-current", cwd=blog),
        run("git", "status", "--porcelain", "--untracked-files=all", cwd=blog),
        run("git", "ls-remote", "origin", cwd=blog),
        run("git", "ls-remote", "drafts", cwd=blog),
        run("git", "config", "--list", "--local", cwd=blog),
    )


@pytest.mark.parametrize(
    ("setup", "args", "listed"),
    [
        (
            None,
            ["draft", "new", "-t", "Dry", "-y"],
            [
                "would Switch to new branch draft/dry",
                "would Write posts/dry/index.qmd",
                'would Commit "Start draft: Dry"',
                "would Push draft/dry to drafts",
            ],
        ),
        (
            "Dry",
            ["publish", "draft", "dry", "--push"],
            [
                "would Switch to main",
                "would Write posts/dry/index.qmd, marked published",
                'would Commit "Publish Dry" on main',
                "would Push main to origin",
                "would Delete draft/dry on drafts",
                "would Delete local branch draft/dry",
            ],
        ),
        (
            "Dry",
            ["draft", "drop", "dry", "-y"],
            [
                "would fetch drafts",
                "would Switch to main",
                "would Delete draft/dry on drafts",
                "would Delete local branch draft/dry",
            ],
        ),
        ("Dry", ["draft", "save"], ["would Push draft/dry to drafts"]),
    ],
)
def test_dry_run_lists_operations_and_changes_nothing(blog, setup, args, listed):
    if setup:
        start_draft(setup)
    before = snapshot(blog)

    result = CliRunner().invoke(cli, [*args, "--dry-run"])
    assert result.exit_code == 0, result.output
    for line in listed:
        assert line in result.output
    assert "✓" not in result.output
    assert snapshot(blog) == before


def test_dry_run_still_refuses(blog):
    start_draft("Not Yet")
    result = CliRunner().invoke(cli, ["publish", "finish", "not_yet", "--dry-run"])
    assert result.exit_code == 1
    assert "does not have posts/not_yet/index.qmd published" in result.output


def test_save_ignores_tracked_symlinked_directories(blog):
    (blog / "media").mkdir()
    (blog / "media" / "image.png").write_bytes(b"png")
    (blog / "posts" / "media").symlink_to("../media")
    run("git", "add", "-A", cwd=blog)
    run("git", "commit", "-q", "-m", "Media link", cwd=blog)
    start_draft("Linked Media")
    path = post(blog, "linked_media")
    path.write_text(path.read_text() + "\nMore.\n")

    result = CliRunner().invoke(cli, ["draft", "save"])
    assert result.exit_code == 0, result.output
    assert "Stage posts/linked_media/index.qmd" in result.output
    assert "Stage posts/media" not in result.output
    committed = run("git", "show", "--name-only", "--format=", "HEAD", cwd=blog).split()
    assert committed == ["posts/linked_media/index.qmd"]


def behind_main(blog: Path, title: str) -> str:
    """Start a draft, then move main on with a new and an edited file; return the branch."""
    start_draft(title)
    branch = run("git", "branch", "--show-current", cwd=blog).strip()
    commit_on_main(blog, "old", OLD_POST.replace("Old post.", "Old post, edited."))
    (blog / "styles.css").write_text("body {}\n")
    run("git", "add", "styles.css", cwd=blog)
    run("git", "commit", "-q", "-m", "Add styles", cwd=blog)
    return branch


def test_update_merges_main_into_the_draft(blog):
    branch = behind_main(blog, "Behind")
    run("git", "switch", "-q", branch, cwd=blog)

    result = CliRunner().invoke(cli, ["draft", "update"])
    assert result.exit_code == 0, result.output
    assert "Write styles.css" in result.output
    assert run("git", "log", "-1", "--format=%s", cwd=blog).strip() == f"Merge main into {branch}"
    parents = run("git", "log", "-1", "--format=%P", cwd=blog).split()
    assert parents[1] == run("git", "rev-parse", "main", cwd=blog).strip()
    assert run("git", "status", "--porcelain", cwd=blog) == ""
    assert "edited" in post(blog, "old").read_text()
    assert post(blog, "behind").exists()
    remote_tip = run("git", "ls-remote", "drafts", f"refs/heads/{branch}", cwd=blog).split()[0]
    assert remote_tip == run("git", "rev-parse", "HEAD", cwd=blog).strip()

    again = CliRunner().invoke(cli, ["draft", "update"])
    assert again.exit_code == 0, again.output
    assert "up to date with main" in again.output


def test_switch_updates_the_draft(blog):
    branch = behind_main(blog, "Switched")

    result = CliRunner().invoke(cli, ["draft", "switch", "switched"])
    assert result.exit_code == 0, result.output
    assert run("git", "branch", "--show-current", cwd=blog).strip() == branch
    assert (blog / "styles.css").exists()
    assert run("git", "log", "-1", "--format=%s", cwd=blog).strip() == f"Merge main into {branch}"


def test_switch_without_update_leaves_the_draft(blog):
    branch = behind_main(blog, "Left")
    tip = run("git", "rev-parse", branch, cwd=blog)

    result = CliRunner().invoke(cli, ["draft", "switch", "left", "--no-update"])
    assert result.exit_code == 0, result.output
    assert run("git", "rev-parse", "HEAD", cwd=blog) == tip
    assert not (blog / "styles.css").exists()


def test_update_conflict_changes_nothing(blog):
    start_draft("Clash")
    post(blog, "old").write_text("---\ntitle: Old\n---\n\nDraft side.\n")
    run("git", "commit", "-q", "-am", "Draft edit", cwd=blog)
    commit_on_main(blog, "old", "---\ntitle: Old\n---\n\nMain side.\n")

    run("git", "switch", "-q", "draft/clash", cwd=blog)
    head = run("git", "rev-parse", "HEAD", cwd=blog)
    result = CliRunner().invoke(cli, ["draft", "update"])
    assert result.exit_code == 1
    assert "conflicts with draft/clash in posts/old/index.qmd" in result.output
    assert run("git", "rev-parse", "HEAD", cwd=blog) == head
    assert run("git", "status", "--porcelain", cwd=blog) == ""

    # Switching still switches, and says the update was skipped
    run("git", "switch", "-q", "main", cwd=blog)
    switched = CliRunner().invoke(cli, ["draft", "switch", "clash"])
    assert switched.exit_code == 0, switched.output
    assert "without updating it" in switched.output
    assert run("git", "rev-parse", "HEAD", cwd=blog) == head


def test_update_refuses_to_overwrite_an_untracked_file(blog):
    branch = behind_main(blog, "Untracked")
    run("git", "switch", "-q", branch, cwd=blog)
    (blog / "styles.css").write_text("mine\n")

    result = CliRunner().invoke(cli, ["draft", "update"])
    assert result.exit_code == 1
    assert "would write over untracked paths styles.css" in result.output
    assert (blog / "styles.css").read_text() == "mine\n"


@pytest.mark.parametrize(
    "args", [["draft", "update"], ["draft", "switch", "dry_update"]], ids=["update", "switch"]
)
def test_dry_run_update_changes_nothing(blog, args):
    branch = behind_main(blog, "Dry Update")
    if args[1] == "update":
        run("git", "switch", "-q", branch, cwd=blog)
    before = snapshot(blog)

    result = CliRunner().invoke(cli, [*args, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would Write styles.css" in result.output
    assert f'would Commit "Merge main into {branch}"' in result.output
    assert f"would Push {branch} to drafts" in result.output
    assert "✓" not in result.output
    assert snapshot(blog) == before


def test_update_warns_when_origin_main_is_ahead(blog):
    branch = behind_main(blog, "Stale Main")
    run("git", "push", "-q", "origin", "main", cwd=blog)
    run("git", "switch", "-q", branch, cwd=blog)
    run("git", "branch", "-f", "main", "main~1", cwd=blog)
    run("git", "update-ref", "refs/remotes/origin/main", "main", cwd=blog)

    result = CliRunner().invoke(cli, ["draft", "update"])
    assert result.exit_code == 0, result.output
    assert "Fetch origin" in result.output
    assert "WARNING: origin/main has 1 commit that local main does not" in result.output
    assert "git " not in result.output


def test_update_does_not_warn_when_main_is_current(blog):
    branch = behind_main(blog, "Current Main")
    run("git", "push", "-q", "origin", "main", cwd=blog)
    run("git", "switch", "-q", branch, cwd=blog)

    result = CliRunner().invoke(cli, ["draft", "update"])
    assert result.exit_code == 0, result.output
    assert "WARNING" not in result.output


def test_draft_new_wraps_the_body(blog):
    long = " ".join(["word"] * 40)
    result = CliRunner().invoke(
        cli,
        ["draft", "new", "--title", "Wrapped", "--file", "-", "--yes"],
        input=f"{long}\n\n> {long}\n",
    )
    assert result.exit_code == 0, result.output
    text = post(blog, "wrapped").read_text()
    body = text.split("---\n", 2)[2]
    paragraph, quote = body.strip().split("\n\n")
    assert all(len(line) <= 88 for line in paragraph.splitlines())
    assert len(paragraph.splitlines()) > 1
    assert quote == f"> {long}"


def test_lint_reports_then_fixes_drafts(blog):
    long = " ".join(["word"] * 40)
    (blog / "posts" / "new").mkdir()
    draft = post(blog, "new")
    draft.write_text(f"---\ntitle: New\ndraft: true\n---\n\n{long}\n")
    # Published posts are left alone unless named
    post(blog, "old").write_text(f"---\ntitle: Old\ndraft: false\n---\n\n{long}\n")
    runner = CliRunner()

    check = runner.invoke(cli, ["lint"])
    assert check.exit_code != 0
    assert "new/index.qmd needs formatting" in check.output
    assert "old/index.qmd" not in check.output

    fixed = runner.invoke(cli, ["lint", "--fix"])
    assert fixed.exit_code == 0, fixed.output
    assert all(len(line) <= 88 for line in draft.read_text().splitlines())
    assert runner.invoke(cli, ["lint"]).exit_code == 0
    assert runner.invoke(cli, ["lint", "posts/old/index.qmd"]).exit_code != 0


# Writes that would clobber or escape: refused before anything changes


def test_draft_new_refuses_an_untracked_post(blog):
    notes = post(blog, "notes")
    notes.parent.mkdir()
    notes.write_text("Local notes, never committed.\n")

    result = CliRunner().invoke(cli, ["draft", "new", "--title", "Notes", "--yes"])
    assert result.exit_code == 1
    assert "untracked paths posts/notes/index.qmd" in result.output
    assert notes.read_text() == "Local notes, never committed.\n"
    assert run("git", "branch", "--show-current", cwd=blog).strip() == "main"
    assert run("git", "branch", "--list", "draft/*", cwd=blog) == ""


def test_draft_new_refuses_a_symlinked_post_directory(blog, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (blog / "posts" / "linked").symlink_to(outside)

    result = CliRunner().invoke(cli, ["draft", "new", "--title", "Linked", "--yes"])
    assert result.exit_code == 1
    assert "symlinked directories posts/linked/index.qmd (via posts/linked)" in result.output
    assert list(outside.iterdir()) == []
    assert run("git", "branch", "--list", "draft/*", cwd=blog) == ""


def test_switch_refuses_to_overwrite_an_untracked_file(blog):
    start_draft("Has Post")
    run("git", "switch", "-q", "main", cwd=blog)
    mine = post(blog, "has_post")
    mine.parent.mkdir()
    mine.write_text("mine\n")

    result = CliRunner().invoke(cli, ["draft", "switch", "has_post"])
    assert result.exit_code == 1
    assert "switching to draft/has_post would write over untracked paths" in result.output
    assert mine.read_text() == "mine\n"
    assert run("git", "branch", "--show-current", cwd=blog).strip() == "main"


def test_publish_keeps_symlinks_as_symlinks(blog):
    start_draft("Linking")
    (post(blog, "linking").parent / "latest.qmd").symlink_to("index.qmd")
    assert CliRunner().invoke(cli, ["draft", "save"]).exit_code == 0

    result = CliRunner().invoke(cli, ["publish", "draft", "linking"])
    assert result.exit_code == 0, result.output
    link = post(blog, "linking").parent / "latest.qmd"
    assert link.is_symlink()
    assert os.readlink(link) == "index.qmd"
    staged = run("git", "ls-files", "-s", "posts/linking/latest.qmd", cwd=blog)
    assert staged.startswith("120000 ")


def test_write_replaces_a_symlink_instead_of_following_it(blog, tmp_path):
    from memexponent import repo, worktree

    outside = tmp_path / "outside.css"
    outside.write_text("theirs\n")
    (blog / "styles.css").symlink_to(outside)

    with repo.open_repo() as r:
        worktree.write(r, "styles.css", b"ours\n", 0o100644)
    assert outside.read_text() == "theirs\n"
    assert not (blog / "styles.css").is_symlink()
    assert (blog / "styles.css").read_text() == "ours\n"


# Finishing: never delete draft work that main does not have


def publish_then_edit(blog: Path, title: str, slug: str, edit) -> None:
    """Start and publish a draft without finishing, then commit an edit on the draft."""
    start_draft(title)
    published = CliRunner().invoke(cli, ["publish", "draft", slug])
    assert published.exit_code == 0, published.output
    run("git", "switch", "-q", f"draft/{slug}", cwd=blog)
    edit(post(blog, slug))
    assert CliRunner().invoke(cli, ["draft", "save", "-m", "After publishing"]).exit_code == 0
    run("git", "switch", "-q", "main", cwd=blog)


def test_finish_refuses_a_draft_edited_after_publishing(blog):
    publish_then_edit(
        blog, "Edited", "edited", lambda p: p.write_text(p.read_text() + "\nA late fix.\n")
    )

    result = CliRunner().invoke(cli, ["publish", "finish", "edited"])
    assert result.exit_code == 1
    assert "does not have posts/edited/index.qmd published as on draft/edited" in result.output
    assert "draft/edited" in run("git", "branch", "--list", "draft/*", cwd=blog)
    assert "draft/edited" in run("git", "ls-remote", "--heads", "drafts", cwd=blog)


def test_finish_refuses_a_file_added_after_publishing(blog):
    publish_then_edit(
        blog, "Late Figure", "late_figure", lambda p: (p.parent / "figure.png").write_bytes(b"png")
    )

    result = CliRunner().invoke(cli, ["publish", "finish", "late_figure"])
    assert result.exit_code == 1
    assert "posts/late_figure/figure.png" in result.output
    assert "draft/late_figure" in run("git", "branch", "--list", "draft/*", cwd=blog)


def test_finish_accepts_a_redated_post(blog):
    start_draft("Redated")
    run("git", "switch", "-q", "main", cwd=blog)
    published = CliRunner().invoke(cli, ["publish", "draft", "redated", "--redate", "--push"])
    assert published.exit_code == 0, published.output
    assert run("git", "branch", "--list", "draft/*", cwd=blog) == ""


def test_finish_refuses_when_drafts_has_newer_commits(blog, tmp_path):
    start_draft("Shared")
    run("git", "switch", "-q", "main", cwd=blog)
    assert CliRunner().invoke(cli, ["publish", "draft", "shared"]).exit_code == 0
    origin_main = run("git", "ls-remote", "origin", "refs/heads/main", cwd=blog)

    # Another clone saves more of the draft after this one last fetched
    drafts = run("git", "remote", "get-url", "drafts", cwd=blog).strip()
    other = tmp_path / "other"
    run("git", "clone", "-q", "-b", "draft/shared", drafts, str(other), cwd=tmp_path)
    (other / "posts" / "shared" / "index.qmd").write_text("Written elsewhere.\n")
    run(
        "git", "-c", "user.name=T", "-c", "user.email=t@example.com",
        "commit", "-q", "-am", "Elsewhere", cwd=other,
    )  # fmt: skip
    run("git", "push", "-q", "origin", "draft/shared", cwd=other)
    newer = run("git", "rev-parse", "HEAD", cwd=other).strip()

    result = CliRunner().invoke(cli, ["publish", "finish", "shared"])
    assert result.exit_code == 1
    assert "drafts has 1 commit on draft/shared that are not here" in result.output
    remote = run("git", "ls-remote", "drafts", "refs/heads/draft/shared", cwd=blog).split()[0]
    assert remote == newer
    assert run("git", "ls-remote", "origin", "refs/heads/main", cwd=blog) == origin_main
    assert "draft/shared" in run("git", "branch", "--list", "draft/*", cwd=blog)


def test_delete_branch_refuses_a_tip_that_moved(blog):
    from dulwich.objects import ObjectID

    from memexponent import repo

    start_draft("Leased")
    tip = run("git", "rev-parse", "draft/leased", cwd=blog).strip()
    stale = run("git", "rev-parse", "main", cwd=blog).strip()
    with repo.open_repo() as r, pytest.raises(click.ClickException, match="has moved on"):
        repo.delete_branch(r, "drafts", "draft/leased", ObjectID(stale.encode()))
    remote = run("git", "ls-remote", "drafts", "refs/heads/draft/leased", cwd=blog).split()[0]
    assert remote == tip


def test_a_refused_push_stops_finish_before_deleting_anything(blog, monkeypatch):
    from memexponent import repo

    start_draft("Refused")
    run("git", "switch", "-q", "main", cwd=blog)
    tracking = run("git", "rev-parse", "origin/main", cwd=blog)

    def refuse(r, remote, refspecs, **kwargs):
        # As dulwich does: it moves the tracking ref, then reports the refusal in the result
        r.refs[b"refs/remotes/origin/main"] = r.refs[b"refs/heads/main"]
        return SimpleNamespace(ref_status={b"refs/heads/main": "pre-receive hook declined"})

    monkeypatch.setattr(repo.porcelain, "push", refuse)
    result = CliRunner().invoke(cli, ["publish", "draft", "refused", "--push"])
    assert result.exit_code == 1
    assert "pushing main to origin was refused: refs/heads/main: pre-receive hook declined" in (
        result.output
    )
    assert run("git", "rev-parse", "origin/main", cwd=blog) == tracking
    assert "draft/refused" in run("git", "branch", "--list", "draft/*", cwd=blog)
    assert "draft/refused" in run("git", "ls-remote", "--heads", "drafts", cwd=blog)


# Hook guards


def test_hooks_install_where_core_hooks_path_points(blog):
    run("git", "config", "core.hooksPath", "githooks", cwd=blog)
    run("./install-draft-hooks.sh", cwd=blog)
    assert "memexponent draft guard" in (blog / "githooks" / "pre-push").read_text()
    assert "memexponent draft guard" in (blog / "githooks" / "post-commit").read_text()


def test_pre_push_guard_hands_the_ref_updates_on(blog):
    hook = blog / ".git" / "hooks" / "pre-push"
    seen = blog / "seen"
    hook.write_text(f'#!/bin/sh\ncat > "{seen}"\n')
    hook.chmod(0o755)
    run("./install-draft-hooks.sh", cwd=blog)

    updates = "refs/heads/main aaa refs/heads/main bbb\n"
    url = "git@github.com:crossjam/memexponent.net.git"
    ok = subprocess.run([str(hook), "origin", url], check=False, input=updates, text=True, cwd=blog)
    assert ok.returncode == 0
    assert seen.read_text() == updates

    draft = "refs/heads/draft/x aaa refs/heads/draft/x bbb\n"
    refused = subprocess.run(
        [str(hook), "origin", url],
        check=False,
        input=draft,
        text=True,
        cwd=blog,
        capture_output=True,
    )
    assert refused.returncode == 1
    assert "refusing to push refs/heads/draft/x" in refused.stderr


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("git@github.com:crossjam/memexponent.drafts.git", True),
        ("git@github.com:crossjam/memexponent.drafts", True),
        ("ssh://git@github.com/crossjam/memexponent.drafts.git", True),
        ("https://github.com/crossjam/memexponent.drafts.git", True),
        ("/home/me/crossjam/memexponent.drafts.git", True),
        ("file:///home/me/crossjam/memexponent.drafts.git", True),
        ("https://attacker.example/crossjam/memexponent.drafts.git", False),
        ("git@evil.example:crossjam/memexponent.drafts.git", False),
        ("git@github.com:crossjam/memexponent.drafts.evil.git", False),
        ("git@github.com:someone/crossjam/memexponent.drafts.git", False),
        ("git@github.com:crossjam/memexponent.net.git", False),
    ],
)
def test_drafts_url_must_be_the_drafts_repository(url, ok):
    from memexponent import repo

    assert repo.is_drafts_url(url) is ok


def test_pre_push_guard_refuses_a_look_alike_drafts_url(blog):
    run("./install-draft-hooks.sh", cwd=blog)
    hook = blog / ".git" / "hooks" / "pre-push"
    draft = "refs/heads/draft/x aaa refs/heads/draft/x bbb\n"
    for url, code in [
        ("https://attacker.example/crossjam/memexponent.drafts.git", 1),
        ("git@github.com:crossjam/memexponent.drafts.git", 0),
    ]:
        result = subprocess.run(
            [str(hook), "drafts", url], check=False, input=draft, text=True, capture_output=True
        )
        assert result.returncode == code, url


def test_lint_fix_refuses_to_rewrite_through_a_symlink(blog, tmp_path):
    long = " ".join(["word"] * 40)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "index.qmd").write_text(f"---\ntitle: Out\ndraft: true\n---\n\n{long}\n")
    (blog / "posts" / "linked").symlink_to(outside)
    (blog / "posts" / "here").mkdir()
    here = post(blog, "here")
    here.write_text(f"---\ntitle: Here\ndraft: true\n---\n\n{long}\n")

    result = CliRunner().invoke(cli, ["lint", "--fix"])
    assert result.exit_code == 1
    assert "refusing to rewrite through symlinks" in result.output
    assert (outside / "index.qmd").read_text().endswith(f"{long}\n")
    # Nothing is rewritten when any file is refused
    assert here.read_text().endswith(f"{long}\n")


def test_dry_run_publish_push_refuses_as_the_real_run_would(blog):
    # The draft and main both edit the old post, so publishing merges them and main
    # will not carry the post as the draft has it: finishing must refuse
    start_draft("Both")
    post(blog, "old").write_text(OLD_POST + "\nDraft addition.\n")
    run("git", "commit", "-q", "-am", "Draft edit", cwd=blog)
    commit_on_main(blog, "old", OLD_POST.replace("title: Old", "title: Old, Retitled"))
    before = snapshot(blog)

    result = CliRunner().invoke(cli, ["publish", "draft", "both", "--push", "--dry-run"])
    assert result.exit_code == 1
    assert "does not have posts/old/index.qmd published as on draft/both" in result.output
    assert snapshot(blog) == before


def draft_turns_assets_into_a_file(blog: Path, title: str) -> str:
    """Track an assets/ directory on main, then start a draft that replaces it with a file."""
    (blog / "assets").mkdir()
    (blog / "assets" / "a.txt").write_text("a\n")
    run("git", "add", "assets", cwd=blog)
    run("git", "commit", "-q", "-m", "Assets", cwd=blog)
    start_draft(title)
    run("git", "rm", "-q", "-r", "assets", cwd=blog)
    (blog / "assets").write_text("now a file\n")
    run("git", "add", "assets", cwd=blog)
    run("git", "commit", "-q", "-m", "Assets as a file", cwd=blog)
    return run("git", "branch", "--show-current", cwd=blog).strip()


def test_switch_replaces_a_directory_with_a_file(blog):
    draft_turns_assets_into_a_file(blog, "Dir To File")
    run("git", "switch", "-q", "main", cwd=blog)

    result = CliRunner().invoke(cli, ["draft", "switch", "dir_to_file", "--no-update"])
    assert result.exit_code == 0, result.output
    assert (blog / "assets").read_text() == "now a file\n"
    assert run("git", "status", "--porcelain", cwd=blog) == ""


def test_switch_refuses_to_replace_a_directory_holding_untracked_files(blog):
    draft_turns_assets_into_a_file(blog, "Keeps Notes")
    run("git", "switch", "-q", "main", cwd=blog)
    (blog / "assets" / "notes.txt").write_text("mine\n")

    result = CliRunner().invoke(cli, ["draft", "switch", "keeps_notes", "--no-update"])
    assert result.exit_code == 1
    assert "untracked paths assets/notes.txt" in result.output
    assert (blog / "assets" / "a.txt").read_text() == "a\n"
    assert (blog / "assets" / "notes.txt").read_text() == "mine\n"
    assert run("git", "branch", "--show-current", cwd=blog).strip() == "main"


def test_merges_refuse_a_directory_becoming_a_file(blog):
    draft_turns_assets_into_a_file(blog, "Merged")
    run("git", "switch", "-q", "main", cwd=blog)
    head = run("git", "rev-parse", "HEAD", cwd=blog)

    result = CliRunner().invoke(cli, ["publish", "draft", "merged"])
    assert result.exit_code == 1
    assert "conflicts with main in assets; nothing was changed" in result.output
    assert run("git", "rev-parse", "HEAD", cwd=blog) == head
    assert (blog / "assets" / "a.txt").exists()


def test_switch_refuses_to_replace_a_directory_holding_an_untracked_symlink(blog, tmp_path):
    draft_turns_assets_into_a_file(blog, "Keeps Link")
    run("git", "switch", "-q", "main", cwd=blog)
    (tmp_path / "elsewhere").mkdir()
    (blog / "assets" / "linked").symlink_to(tmp_path / "elsewhere")

    result = CliRunner().invoke(cli, ["draft", "switch", "keeps_link", "--no-update"])
    assert result.exit_code == 1
    assert "untracked paths assets/linked" in result.output
    assert (blog / "assets" / "a.txt").read_text() == "a\n"
