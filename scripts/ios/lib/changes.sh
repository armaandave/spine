# Shared helpers for scripts/ios/*.sh: classify what a feature branch changed
# relative to origin/myapp-main. Source this file; do not run it directly.

# Production runs what was pushed to myapp-main, so that is the comparison.
spine_base_ref() { printf '%s\n' "${SPINE_BASE_REF:-origin/myapp-main}"; }

# Committed changes on HEAD since it branched from the base, one per line as
# "<A|M|D><TAB><path>". Renames become a delete plus an add.
spine_committed_changes() {
  git diff --name-status -M "$(spine_base_ref)...HEAD" | awk -F'\t' '
    $1 ~ /^R/ { print "D\t" $2; print "A\t" $3; next }
    $1 ~ /^C/ { print "A\t" $3; next }
    { print substr($1, 1, 1) "\t" $2 }'
}

# Uncommitted (staged, unstaged, or untracked) paths in the working tree.
spine_uncommitted_paths() {
  git status --porcelain --untracked-files=all | cut -c4- | sed 's/.* -> //'
}

# Which part of the project a path belongs to:
#   ios        the iPhone app (never part of a backend deploy)
#   migration  Django migrations (production applies them when a deploy starts)
#   tests      backend tests (travel with backend code)
#   tooling    CI, scripts and agent skills
#   docs       documentation
#   backend    the Django app and everything its image is built from
spine_area() {
  case "$1" in
    ios/*) echo ios ;;
    src/*/migrations/*) echo migration ;;
    src/*/tests/*|src/*/tests.py) echo tests ;;
    scripts/*|.github/*|.agents/*|.claude/*|.cursor/*|.codex/*|.gitignore) echo tooling ;;
    docs/*|*.md) echo docs ;;
    *) echo backend ;;
  esac
}

# True when the path is API code the iPhone app calls.
spine_is_api() {
  case "$1" in
    src/api/tests/*) return 1 ;;
    src/api/*) return 0 ;;
    *) return 1 ;;
  esac
}
