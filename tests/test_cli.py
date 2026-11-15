"""Drive both post commands against a throwaway repository."""

import shutil
import subprocess
from pathlib import Path

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
    assert "would write over untracked or outside paths styles.css" in result.output
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
