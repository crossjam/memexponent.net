# Writing and Publishing Posts: the `post` CLI

Each unpublished post lives on its own `draft/<slug>` branch. Draft
branches are pushed only to the `drafts` remote, the private
`crossjam/memexponent.drafts` repository. `origin` is public, so `main`
holds published posts only.

`post` is installed into the project environment by `uv sync`; run it
with `uv run post`. Every command has `--help`. It works on the
repository through [dulwich](https://github.com/jelmer/dulwich), so it
does not shell out to `git`.

## The Common Path

```bash
uv run post draft new --edit              # start draft/<slug> from main
uv run poe render_preview                 # preview while writing
uv run post draft save -m "First pass"    # commit and push to drafts
uv run post draft switch <slug>           # resume a draft, merging main into it
uv run post lint --fix                    # wrap draft paragraphs to 88 characters
uv run post publish draft <slug> --push   # squash onto main, push, clean up
uv run poe render_production              # build the site without drafts
uv run poe publish                        # deploy
```

## Dry Runs

Every command that changes anything takes `-n, --dry-run`, which lists
the operations it would perform without performing any:

```console
$ uv run post publish draft my_post --push --dry-run
would Switch to main
would Write posts/my_post/index.qmd, marked published
would Commit "Publish My Post" on main
would Push main to origin
would Delete draft/my_post on drafts
would Delete local branch draft/my_post
```

The checks still run, so a dry run also shows whether the real command
would refuse. Fetches are listed rather than run, so what follows one
reflects the remote as of the last fetch. A real run prints the same
operations, each with a ✓ as it completes.

## One-Time Setup per Clone

```bash
uv sync
uv run post setup
```

`post setup` adds the `drafts` remote if it is missing, fetches it, and
runs `install-draft-hooks.sh`, which adds two guards to the clone's git
hooks:

- **pre-push**: refuses to push any `draft/*` branch to a remote other
  than the drafts repository
- **post-commit**: ends the hook before anything else in it runs for
  commits on `draft/*` branches, so the automatic review (or anything
  else hooked there) never sees unpublished prose

The guards are inserted as marked blocks after the shebang, so hooks
installed by other tools keep running everywhere except that
post-commit case. The pre-push guard saves the ref updates it reads and
hands them back, so the rest of that hook still sees every one. The
hooks go wherever git runs them from, including a `core.hooksPath`
directory. `post draft new` re-runs the installer every time, in
case another tool has rewritten a hook since.

dulwich runs the commit hooks but not pre-push, so `post` enforces the
same rule itself: it never pushes a `draft/*` branch to a URL other than
the drafts repository's.

## Drafts: `post draft`

### `post draft new`

From a clean working tree:

```bash
uv run post draft new --edit                     # prompts for the title
uv run post draft new -t "My Post" --clipboard   # body from the macOS clipboard
uv run post draft new -t "My Post" -f notes.md   # body from a file
pbpaste | uv run post draft new -t "My Post" -f - --quote --yes
```

It refuses to start if the working tree has uncommitted changes, the
branch already exists locally or on `drafts`, or `posts/<slug>/` is
already a published post on `main`. Then it:

1. Creates and switches to `draft/<slug>`, starting from `main`
2. Writes `posts/<slug>/index.qmd` with YAML front matter marked
   `draft: true`, its paragraphs wrapped to 88 characters as
   `post lint --fix` wraps them
3. Opens it in `$EDITOR`, with `--edit`
4. Commits it as `Start draft: <title>`
5. Pushes the branch to `drafts` and sets it as the upstream

The slug is the title slugified with underscores, like the post
directories already published (`My Post` becomes `my_post`). If the
push fails (offline, say), the commit stays local; `post draft save`
pushes it later.

Options:

- `-t, --title`: the post title; prompted for when omitted
- `-a, --author`: defaults to Brian M. Dennis
- `-s, --slug`: defaults to the slugified title
- `-f, --file FILE`: read the body from a file, or `-` for stdin
- `-c, --clipboard`: take the body from the macOS clipboard; RTF is
  converted to Markdown with pandoc (`brew install pandoc`) when available
- `-q, --quote`: format the body as a Markdown blockquote
- `-e, --edit`: open the post in `$EDITOR` before the first commit
- `-y, --yes`: skip the confirmation

With no `--file` or `--clipboard`, the body is Lorem Ipsum placeholder
text. When the body comes from stdin, pass `--title` and `--yes`, so no
prompt tries to read from it.

### `post draft save`

On a draft branch, commits changes to tracked files plus any new files
under `posts/`, such as images beside the post (default message
`Update draft`, or `-m`), then pushes the branch to `drafts`. It pushes
even when there is nothing new to commit, so it also retries a push that
failed earlier.

### `post draft list` and `post draft switch`

`post draft list` fetches `drafts` (skip with `--no-fetch`) and lists
every draft, marking whether it is here, on `drafts`, or both, with `*`
on the current one.

`post draft switch <slug>` checks a draft out. A draft started on
another machine is fetched from `drafts` and set to track it. Then it
updates the draft as `post draft update` does (skip with `--no-update`);
if that would conflict, it warns and leaves the draft as it was.

### `post draft update`

A draft branch keeps the `main` it started from, so as `main` moves on
(styles, extensions, dependencies) an old draft can stop rendering, or
`uv run post` itself can vanish when `uv sync` follows the draft's older
`pyproject.toml`. On a draft branch, `post draft update` merges the
local `main` into it, commits as `Merge main into draft/<slug>`, and
pushes the branch to `drafts`. The merge is worked out in memory first,
so a conflict, or a file it would write over that git does not track,
changes nothing. A draft already up to date is left alone.

First it fetches `origin` and warns when `origin/main` has commits the
local `main` lacks, since the draft would not get them; pull them into
`main` and update again. If the fetch fails (offline, say), it warns and
checks against `origin` as last fetched.

### `post draft drop`

`post draft drop <slug>` abandons a draft: it deletes the branch on
`drafts` and locally, after asking to confirm (skip with `--yes`). It
refuses when the local branch has commits that were never pushed,
unless `--force` is given. Run on the draft itself, it switches to
`main` first. The branch on `drafts` is deleted only if it is still
where the fetch found it, so a save from another machine in the
meantime is never lost.

Preview while writing with `uv run poe render_preview`. Drafts render
with Quarto's draft styling and are listed on the preview-only
`drafts.qmd` page, not on the front page.

## Publishing: `post publish`

### `post publish draft`

From a clean working tree, on the draft branch or anywhere else (it
switches to `main` first):

```bash
uv run post publish draft <slug>            # or: draft/<slug>
uv run post publish draft <slug> --push     # then finish, as below
uv run post publish draft <slug> --redate   # date the post now, replacing its date
```

It squash-merges `draft/<slug>` into `main`, so the draft's revision
history never reaches the public repository. Everything the draft adds
under `posts/<slug>/`, such as images, comes along. The merge is worked
out in memory first, so a conflict with `main` changes nothing. In each
post the merge adds or changes that is still a draft, it sets
`draft: false` and adds a date if the post has none (or always, with
`--redate`). Then it commits as `Publish <title>`.

`posts/_metadata.yml` defaults every post to `draft: true`, so a post
counts as published only when its own front matter says `draft: false`.

Without `--push` it stops after the commit, so you can look before
anything leaves the machine.

### `post publish finish`

```bash
uv run post publish finish <slug>
```

Pushes `main` to `origin`, then deletes `draft/<slug>` on `drafts` and
locally. Before changing anything it checks two things, so unpublished
work is never deleted:

- `main` carries everything the draft changed: each post marked
  `draft: false` and otherwise as on the draft (only its `draft` and
  `date` fields may differ), and every other file exactly. A draft
  edited after `post publish draft` counts as unpublished; publish it
  again.
- `drafts` has nothing newer than the local draft. It fetches first,
  and deletes the remote branch only if it is still at the commit it
  found, like `git push --force-with-lease`.

A push the server refuses stops it there, before anything is deleted.

## Formatting: `post lint`

```bash
uv run post lint                              # check every post that is still a draft
uv run post lint posts/my_post/index.qmd      # check particular files, drafts or not
uv run post lint --fix                        # rewrite the ones that need it
```

`post lint` checks that paragraphs are wrapped to 88 characters, listing
each file that is not and exiting non-zero if any are. `--fix` rewraps
them in place instead. Wrapping uses
[flowmark](https://github.com/jlevy/flowmark)'s Markdown-aware filler,
so only line breaks move: links, code spans, and HTML tags are never
split, and the Markdown itself is left as written.

Only plain paragraphs are rewrapped. The front matter, blockquotes,
code cells, lists, tables, footnotes, HTML, shortcodes (`{{< >}}`),
display math (`$$`), and paragraphs with hard line breaks (two
trailing spaces or a trailing backslash) keep their lines exactly.
So does everything inside a fenced div (`:::`), `<pre>`, `<script>`,
`<style>`, `<textarea>`, or HTML comment, across blank lines too.

## How `post` Changes the Working Tree

Every file `post` writes follows git's own rules, and all of them are
checked before anything is touched, so a refusal changes nothing:

- an untracked file is never written over; move it aside first
- nothing is written through a symlinked directory, or outside the
  repository
- a symlink stored in git is written as a symlink, and an existing
  symlink is replaced rather than followed

That covers switching branches (dulwich's own checkout would write over
untracked files), writing a new post, and every file a merge into a
draft or a publish writes.

## Shell Completion

click generates completion scripts, and the commands that take a slug
complete draft names. Completion only applies when you type `post`
itself, not `uv run post`, so it needs `.venv/bin` on your `PATH`. For
bash:

```bash
eval "$(_POST_COMPLETE=bash_source .venv/bin/post)"
```

## Deploying

`uv run poe render_production` renders the site with the `production`
profile, where `draft-mode: gone` leaves drafts out, and
`uv run poe publish` rsyncs `_site/` to the server with `--delete`.
`render_preview` renders drafts into the same `_site/`, so always run
`render_production` before `publish`.

## Configuration

The branch and remote names are constants in
`src/memexponent/repo.py`: `BASE_BRANCH` (`main`), `PUBLIC_REMOTE`
(`origin`), `DRAFTS_REMOTE` (`drafts`), and the drafts repository's URL.

## Code Checks

`uv run poe qa` runs every check on `src/` and `tests/`: ruff format
and lint, ty, pip-audit, bandit, and pytest. Each also has its own task:
`format`, `lint`, `typecheck`, `audit`, `bandit`, and `test`.
