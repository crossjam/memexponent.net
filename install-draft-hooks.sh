#!/usr/bin/env bash

# install-draft-hooks.sh
# Adds draft-branch guards to this clone's git hooks:
#
#   pre-push     refuses to push draft/* branches anywhere except the
#                private drafts remote, since origin is public
#   post-commit  skips automatic review of commits on draft/* branches
#
# The guards are inserted as marked blocks right after the shebang, so
# hooks installed by other tools stay in place and keep running, except
# that on draft/* branches the post-commit guard ends the hook before
# anything else in it runs. The pre-push guard hands its stdin back, so
# the rest of that hook still sees every ref update. Hooks go wherever
# git runs them from, core.hooksPath included. Re-running is safe, and
# `post draft new` runs this on every invocation in case another tool
# has rewritten a hook since.

set -euo pipefail

# The private drafts repository on GitHub; src/memexponent/repo.py accepts
# the same URLs: SSH, HTTPS, or a path on this machine
DRAFTS_REPO="crossjam/memexponent.drafts"

MARK_BEGIN="# >>> memexponent draft guard >>>"
MARK_END="# <<< memexponent draft guard <<<"

# Git's active hooks directory, which honors core.hooksPath
HOOKS_DIR="$(git rev-parse --git-path hooks)"
mkdir -p "$HOOKS_DIR"

pre_push_block() {
    cat << EOF
$MARK_BEGIN
# Refuse to send draft/* branches to any remote but the private drafts repo.
# The ref updates on stdin are saved first and handed back afterwards, so the
# rest of this hook still reads every one of them.
draft_guard_input="\$(mktemp)"
cat > "\$draft_guard_input"
case "\$2" in
    git@github.com:$DRAFTS_REPO|git@github.com:$DRAFTS_REPO.git|\\
    ssh://git@github.com/$DRAFTS_REPO|ssh://git@github.com/$DRAFTS_REPO.git|\\
    https://github.com/$DRAFTS_REPO|https://github.com/$DRAFTS_REPO.git|\\
    /$DRAFTS_REPO|/$DRAFTS_REPO.git|/*/$DRAFTS_REPO|/*/$DRAFTS_REPO.git|\\
    file:///*/$DRAFTS_REPO|file:///*/$DRAFTS_REPO.git) ;;
    *)
        while read -r local_ref _ remote_ref _; do
            case "\$local_ref \$remote_ref" in
                refs/heads/draft/*|*" refs/heads/draft/"*)
                    echo "pre-push: refusing to push \$local_ref to \$1 (\$2)." >&2
                    echo "pre-push: draft branches belong on the drafts remote: git push drafts <branch>" >&2
                    rm -f "\$draft_guard_input"
                    exit 1
                    ;;
            esac
        done < "\$draft_guard_input"
        ;;
esac
# The open descriptor keeps the saved input readable once the file is removed
exec < "\$draft_guard_input"
rm -f "\$draft_guard_input"
unset draft_guard_input
$MARK_END
EOF
}

post_commit_block() {
    cat << EOF
$MARK_BEGIN
# Draft prose stays private: no automatic review on draft/* branches
case "\$(git symbolic-ref --short -q HEAD)" in
    draft/*) exit 0 ;;
esac
$MARK_END
EOF
}

# Insert or refresh the guard block in one hook file
install_block() {
    local hook="$1" block="$2"
    local path="$HOOKS_DIR/$hook"
    local stripped tmp
    stripped="$(mktemp)"
    tmp="$(mktemp)"

    if [ ! -f "$path" ]; then
        printf '#!/bin/sh\n' > "$path"
    fi

    # Drop any previous copy of the block, then re-add it after the shebang
    awk -v b="$MARK_BEGIN" -v e="$MARK_END" '
        $0 == b {skip=1; next}
        $0 == e {skip=0; next}
        !skip
    ' "$path" > "$stripped"

    if head -n 1 "$stripped" | grep -q '^#!'; then
        { head -n 1 "$stripped"; printf '%s\n' "$block"; tail -n +2 "$stripped"; } > "$tmp"
    else
        { printf '#!/bin/sh\n%s\n' "$block"; cat "$stripped"; } > "$tmp"
    fi
    rm -f "$stripped"

    if ! cmp -s "$tmp" "$path"; then
        cat "$tmp" > "$path"
        echo "Updated draft guard in $path"
    fi
    rm -f "$tmp"
    chmod +x "$path"
}

install_block pre-push "$(pre_push_block)"
install_block post-commit "$(post_commit_block)"
