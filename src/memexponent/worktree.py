"""Every change the post commands make to the working tree, made safely.

The rules are git's own, and all of them are checked before anything is
touched, so a refusal changes nothing:

- an untracked file is never written over or removed
- nothing is written through a symlinked directory, or outside the tree
- a symlink stored in git is written as a symlink, and an existing
  symlink is replaced, never followed
- removals come before writes, so a file can become a directory
- a directory that becomes a file must hold nothing but the tracked files
  going away; merges treat that case as a conflict anyway, but dulwich's
  checkout would delete the tracked files, then stop partway
"""

import os
from pathlib import Path

import click
from dulwich import porcelain
from dulwich.diff_tree import CHANGE_DELETE, tree_changes
from dulwich.objects import ObjectID
from dulwich.repo import Repo

from . import repo
from .content import warn

SYMLINK_MODE = 0o120000


def is_symlink_mode(mode: int) -> bool:
    return mode & 0o170000 == SYMLINK_MODE


def linked_parent(top: Path, name: str) -> str | None:
    """The first directory on the way to name that is a symlink, if any."""
    parts = Path(name).parts[:-1]
    for i in range(1, len(parts) + 1):
        if (top / Path(*parts[:i])).is_symlink():
            return str(Path(*parts[:i]))
    return None


def file_parent(top: Path, name: str) -> str | None:
    """The first path on the way to name that exists but is not a directory, if any."""
    parts = Path(name).parts[:-1]
    for i in range(1, len(parts) + 1):
        path = top / Path(*parts[:i])
        if path.exists() and not path.is_dir():
            return str(Path(*parts[:i]))
    return None


def refuse_blocked(
    r: Repo,
    names: list[str],
    doing: str,
    tracked: list[ObjectID],
    removes: list[str] | None = None,
) -> None:
    """
    Refuse before writing names, or removing removes, when that would be unsafe.

    A path in any of the tracked trees is not untracked, so it is fair to write over.
    """
    top = repo.root(r)
    inside = top.resolve()

    # The clean check ignores untracked files, so refuse to write over one, as git does;
    # lexists so that a dangling symlink counts too
    def is_tracked(path: str) -> bool:
        return any(repo.in_tree(r, tree, path) for tree in tracked)

    untracked = {name for name in names if not is_tracked(name) and os.path.lexists(top / name)}
    # An untracked file where the write needs a directory blocks it as well
    for name in names:
        if (parent := file_parent(top, name)) and not is_tracked(parent):
            untracked.add(parent)
    # And a file replacing a directory needs everything in it to be going away
    going = set(removes or [])
    for name in names:
        path = top / name
        if path.is_dir() and not path.is_symlink():
            # os.walk lists a symlinked directory among dirs without entering it; count it
            # as an entry of its own
            untracked.update(
                str(Path(root, f).relative_to(top))
                for root, dirs, files in os.walk(path)
                for f in [*files, *(d for d in dirs if Path(root, d).is_symlink())]
                if str(Path(root, f).relative_to(top)) not in going
            )
    for name in names:
        path = top / name
        if path.is_symlink() and not path.resolve().is_relative_to(inside):
            warn(f"WARNING: {name} resolves outside the repository, to {path.resolve()}")
    through_links = {}
    for name in [*names, *(removes or [])]:
        if link := linked_parent(top, name):
            through_links[name] = link
            target = (top / link).resolve()
            if not target.is_relative_to(inside):
                outside = target / Path(name).relative_to(link)
                warn(f"WARNING: {name} resolves outside the repository, to {outside}")
    problems = []
    if untracked:
        problems.append(f"untracked paths {', '.join(sorted(untracked))}")
    if through_links:
        links = ", ".join(f"{name} (via {link})" for name, link in sorted(through_links.items()))
        problems.append(f"paths through symlinked directories {links}")
    if problems:
        raise click.ClickException(
            f"{doing} would write over {' and '.join(problems)}; "
            "move or remove them first; nothing was changed"
        )


def write(r: Repo, name: str, data: bytes, mode: int) -> None:
    """Write one file as git stores it, then stage it."""
    dest = repo.root(r) / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Replace a symlink rather than writing through it to its target
    if dest.is_symlink():
        dest.unlink()
    if is_symlink_mode(mode):
        if dest.exists():
            dest.unlink()
        os.symlink(data.decode(), dest)
    else:
        dest.write_bytes(data)
        dest.chmod(0o755 if mode & 0o100 else 0o644)
    porcelain.add(r, [str(dest)])


def remove(r: Repo, name: str) -> None:
    porcelain.remove(r, [str(repo.root(r) / name)])


def symlink_on_path(path: Path, top: Path | None) -> Path | None:
    """The symlink a write to path would go through, at path or a directory below top."""
    path = path.absolute()
    if path.is_symlink():
        return path
    if top is not None and path.is_relative_to(top.absolute()):
        rel = path.relative_to(top.absolute())
        if link := linked_parent(top.absolute(), str(rel)):
            return top.absolute() / link
    return None


def check_switch(r: Repo, target: ObjectID, doing: str) -> None:
    """
    Refuse, before checking out the tree target, what git checkout would refuse.

    dulwich's checkout writes over untracked files, and stops partway at
    a symlinked directory, so the files it would write are checked first.
    """
    current = repo.commit_at(r, r.head()).tree
    names, removes = [], []
    for change in tree_changes(r.object_store, current, target):
        if change.type == CHANGE_DELETE or change.new is None:
            if change.old is not None:
                removes.append(change.old.path.decode())
        else:
            names.append(change.new.path.decode())
    refuse_blocked(r, names, doing, [current], removes)
