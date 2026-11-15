"""Repository access for the post commands, through dulwich.

Each post is written on its own draft/<slug> branch, started from main
and pushed only to the private drafts remote, since origin is public.
"""

import io
import re
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import click
from dulwich import porcelain
from dulwich.client import get_transport_and_path
from dulwich.errors import NotGitRepository
from dulwich.object_store import tree_lookup_path
from dulwich.objects import ZERO_SHA, Blob, Commit, ObjectID
from dulwich.refs import HEADREF, Ref
from dulwich.repo import Repo

from . import metadata

BASE_BRANCH = "main"
PUBLIC_REMOTE = "origin"
DRAFTS_REMOTE = "drafts"
DRAFTS_URL = "git@github.com:crossjam/memexponent.drafts.git"
# The drafts repository on GitHub; install-draft-hooks.sh accepts the same URLs
DRAFTS_HOST = "github.com"
DRAFTS_REPO = "crossjam/memexponent.drafts"
DRAFT_PREFIX = "draft/"
POSTS_DIR = "posts"
POST_FILE = "index.qmd"


def to_ref(name: str) -> Ref:
    return Ref(name.encode())


def head_ref(branch: str) -> Ref:
    return to_ref(f"refs/heads/{branch}")


def commit_at(repo: Repo, sha: ObjectID) -> Commit:
    obj = repo[sha]
    if not isinstance(obj, Commit):
        raise click.ClickException(f"{sha.decode()} is not a commit")
    return obj


def branch_commit(repo: Repo, branch: str) -> Commit:
    """The tip of branch; for main, origin's copy when there is no local one yet."""
    ref = head_ref(branch)
    if branch == BASE_BRANCH and ref not in repo.refs:
        ref = to_ref(f"refs/remotes/{PUBLIC_REMOTE}/{BASE_BRANCH}")
    if ref not in repo.refs:
        raise click.ClickException(f"no branch {branch}")
    return commit_at(repo, repo.refs[ref])


def merge_base(repo: Repo, a: Commit, b: Commit) -> Commit | None:
    bases = porcelain.merge_base(repo, [a.id.decode(), b.id.decode()])
    return commit_at(repo, bases[0]) if bases else None


def commits_missing(repo: Repo, ref: str, other: str) -> int:
    """How many commits ref has that other does not; 0 when either ref is missing."""
    if not (ref_exists(repo, ref) and ref_exists(repo, other)):
        return 0
    walker = repo.get_walker(include=[repo.refs[to_ref(ref)]], exclude=[repo.refs[to_ref(other)]])
    return sum(1 for _ in walker)


def blob_data(repo: Repo, sha: ObjectID) -> bytes:
    obj = repo[sha]
    if not isinstance(obj, Blob):
        raise click.ClickException(f"{sha.decode()} is not a file")
    return obj.data


def post_path(slug: str) -> str:
    """Where the post for slug lives: its own directory under posts/."""
    return f"{POSTS_DIR}/{slug}/{POST_FILE}"


def is_post(path: bytes) -> bool:
    """Whether path is a post's own page, posts/<slug>/index.qmd."""
    parts = path.split(b"/")
    return len(parts) == 3 and parts[0] == POSTS_DIR.encode() and parts[2] == POST_FILE.encode()


def post_is_draft(repo: Repo, sha: ObjectID) -> bool:
    """Whether a post blob is a draft, so not yet marked draft: false."""
    return metadata.is_draft(blob_data(repo, sha).decode())


@contextmanager
def open_repo() -> Generator[Repo]:
    """The repository or worktree containing the current directory."""
    try:
        repo = Repo.discover()
    except NotGitRepository:
        raise click.ClickException("not inside a git repository") from None
    with repo:
        yield repo


def root(repo: Repo) -> Path:
    return Path(repo.path)


def current_branch(repo: Repo) -> str:
    """The checked-out branch, or an empty string on a detached HEAD."""
    head = repo.refs.read_ref(HEADREF) or b""
    prefix = b"ref: refs/heads/"
    return head.removeprefix(prefix).decode() if head.startswith(prefix) else ""


def ref_exists(repo: Repo, ref: str) -> bool:
    return to_ref(ref) in repo.refs


def require_clean(repo: Repo) -> None:
    """Refuse to continue with uncommitted changes to tracked files."""
    status = porcelain.status(repo, untracked_files="no")
    if any(status.staged.values()) or status.unstaged:
        raise click.ClickException(
            "the working tree has uncommitted changes; commit or stash them first"
        )


def in_tree(repo: Repo, tree: ObjectID, path: str) -> bool:
    try:
        tree_lookup_path(repo.__getitem__, tree, path.encode())
    except KeyError:
        return False
    return True


def on_branch(repo: Repo, branch: str, path: str) -> bool:
    """Whether path exists in the tip of branch."""
    return in_tree(repo, branch_commit(repo, branch).tree, path)


def in_head(repo: Repo, path: str) -> bool:
    """Whether path is tracked in the checked-out commit."""
    return in_tree(repo, commit_at(repo, repo.head()).tree, path)


def remote_url(repo: Repo, remote: str) -> str | None:
    try:
        return repo.get_config().get((b"remote", remote.encode()), b"url").decode()
    except KeyError:
        return None


def fetch(repo: Repo, remote: str) -> None:
    try:
        porcelain.fetch(repo, remote, outstream=io.StringIO(), errstream=io.BytesIO(), quiet=True)
    except Exception as e:
        raise click.ClickException(f"fetching {remote} failed: {e}") from e


def add_remote(repo: Repo, remote: str, url: str) -> None:
    """Add a remote with git's default fetch refspec, which dulwich's remote_add leaves out."""
    porcelain.remote_add(repo, remote, url)
    config = repo.get_config()
    config.set(
        (b"remote", remote.encode()), b"fetch", f"+refs/heads/*:refs/remotes/{remote}/*".encode()
    )
    config.write_to_path()


def track(repo: Repo, branch: str, remote: str) -> None:
    """Set branch's upstream, so a plain pull or push goes to remote."""
    config = repo.get_config()
    section = (b"branch", branch.encode())
    config.set(section, b"remote", remote.encode())
    config.set(section, b"merge", head_ref(branch))
    config.write_to_path()


def base_branch_source(repo: Repo) -> str | None:
    """origin's main when there is no local main yet, to create it from; else None."""
    if ref_exists(repo, f"refs/heads/{BASE_BRANCH}"):
        return None
    tracking = f"refs/remotes/{PUBLIC_REMOTE}/{BASE_BRANCH}"
    if not ref_exists(repo, tracking):
        raise click.ClickException(f"no {BASE_BRANCH} branch here or on {PUBLIC_REMOTE}")
    return tracking


def create_branch(repo: Repo, branch: str, start: str, remote: str) -> None:
    """Create branch at start, tracking remote, as git switch does for a remote branch."""
    porcelain.branch_create(repo, branch, start)
    track(repo, branch, remote)


def draft_branch(name: str) -> str:
    """Accept either a slug or a full draft/<slug> branch name."""
    return name if name.startswith(DRAFT_PREFIX) else DRAFT_PREFIX + name


def draft_slugs(repo: Repo, remote: str | None = None) -> set[str]:
    """Slugs of the local draft branches, or of remote's as last fetched."""
    base = to_ref(f"refs/remotes/{remote}/{DRAFT_PREFIX}") if remote else head_ref(DRAFT_PREFIX)
    return {name.decode() for name in repo.refs.keys(base=base)}


def all_draft_slugs() -> list[str]:
    """Local and drafts-remote slugs, for shell completion; none outside a repository."""
    try:
        with open_repo() as repo:
            return sorted(draft_slugs(repo) | draft_slugs(repo, DRAFTS_REMOTE))
    except click.ClickException:
        return []


def is_drafts_url(url: str) -> bool:
    """
    Whether url names the drafts repository, and nothing that merely contains its name.

    Accepts GitHub's SSH and HTTPS forms, and a path on this machine, which
    is what the tests push to.
    """
    host, name = re.escape(DRAFTS_HOST), re.escape(DRAFTS_REPO) + r"(?:\.git)?"
    forms = [
        rf"git@{host}:{name}",
        rf"ssh://git@{host}/{name}",
        rf"https://{host}/{name}",
        rf"(?:file://)?/(?:.*/)?{name}",
    ]
    return any(re.fullmatch(form, url) for form in forms)


def guard_draft_push(repo: Repo, remote: str, branch: str) -> str:
    """
    The remote's URL, refusing to send a draft branch anywhere but the drafts repository.

    dulwich does not run the pre-push hook, so its draft guard is enforced here.
    """
    url = remote_url(repo, remote)
    if url is None:
        raise click.ClickException(f"no '{remote}' remote")
    if branch.startswith(DRAFT_PREFIX) and not is_drafts_url(url):
        raise click.ClickException(f"refusing to push {branch} to {remote} ({url})")
    return url


def rejected_refs(result: object) -> dict[str, str]:
    """The refs a push result reports the server refused, with its reasons."""
    statuses = getattr(result, "ref_status", None) or {}
    return {ref.decode(errors="replace"): error for ref, error in statuses.items() if error}


def push(repo: Repo, remote: str, branch: str) -> None:
    """
    Push a branch, raising when the server refuses it.

    dulwich can return normally after a refusal, listing it in the result,
    and it moves the remote-tracking ref either way, so on failure the
    ref is put back where it was.
    """
    guard_draft_push(repo, remote, branch)
    tracking = to_ref(f"refs/remotes/{remote}/{branch}")
    try:
        before: ObjectID | None = repo.refs[tracking]
    except KeyError:
        before = None

    errors = io.BytesIO()
    try:
        result = porcelain.push(
            repo,
            remote,
            [head_ref(branch)],
            outstream=io.BytesIO(),
            errstream=errors,
            set_upstream=True,
        )
        if rejected := rejected_refs(result):
            reasons = "; ".join(f"{ref}: {error}" for ref, error in rejected.items())
            raise click.ClickException(f"pushing {branch} to {remote} was refused: {reasons}")
    except click.ClickException:
        restore_ref(repo, tracking, before)
        raise
    # dulwich's transports raise many unrelated exception types
    except Exception as e:  # noqa: BLE001
        restore_ref(repo, tracking, before)
        detail = errors.getvalue().decode(errors="replace").strip()
        raise click.ClickException(f"pushing {branch} to {remote} failed: {e}\n{detail}".rstrip())

    # Keep the remote-tracking ref in step, as git push does
    repo.refs[tracking] = repo.refs[head_ref(branch)]


def restore_ref(repo: Repo, ref: Ref, sha: ObjectID | None) -> None:
    if sha is None:
        if ref in repo.refs:
            del repo.refs[ref]
    else:
        repo.refs[ref] = sha


def delete_branch(repo: Repo, remote: str, branch: str, expect: ObjectID) -> None:
    """
    Delete branch on remote, but only while it is still at expect.

    Like git push --force-with-lease: if the branch moved on since it was
    last fetched, someone pushed work that is not here, so nothing is deleted.
    """
    url = guard_draft_push(repo, remote, branch)
    ref = head_ref(branch)
    found: dict[str, ObjectID | None] = {}

    def update_refs(refs: dict[Ref, ObjectID]) -> dict[Ref, ObjectID]:
        found["tip"] = refs.get(ref)
        if found["tip"] != expect:
            # Asking for no changes sends nothing
            return {}
        return {ref: ZERO_SHA}

    try:
        client, path = get_transport_and_path(url, config=repo.get_config_stack())
        result = client.send_pack(
            path.encode(), update_refs, generate_pack_data=lambda *a, **k: (0, iter([]))
        )
    # dulwich's transports raise many unrelated exception types
    except Exception as e:
        raise click.ClickException(f"deleting {branch} on {remote} failed: {e}") from e

    tracking = to_ref(f"refs/remotes/{remote}/{branch}")
    tip = found.get("tip")
    if tip is None:
        # Already gone; just drop the stale tracking ref
        restore_ref(repo, tracking, None)
        return
    if tip != expect:
        raise click.ClickException(
            f"{branch} on {remote} has moved on since it was fetched, to {tip.decode()[:8]}; "
            f"it has work that is not here, so it was not deleted; "
            f"get it with: post draft switch {branch.removeprefix(DRAFT_PREFIX)}"
        )
    if rejected := rejected_refs(result):
        reasons = "; ".join(f"{name}: {error}" for name, error in rejected.items())
        raise click.ClickException(f"deleting {branch} on {remote} was refused: {reasons}")
    restore_ref(repo, tracking, None)


def install_hooks(repo: Repo) -> None:
    """Refresh the draft-branch hook guards, in case another tool rewrote a hook."""
    script = root(repo) / "install-draft-hooks.sh"
    if subprocess.run([str(script)], check=False, cwd=root(repo)).returncode:
        raise click.ClickException("could not install the draft-branch hook guards")
