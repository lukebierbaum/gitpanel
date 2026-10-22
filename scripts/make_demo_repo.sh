#!/usr/bin/env bash
# Build a demo repository for trying gitpanel by hand.
#   pixi run demo /tmp/gp-demo && pixi run start /tmp/gp-demo
set -euo pipefail

dest="${1:?usage: make_demo_repo.sh DEST}"
if [ -e "$dest" ]; then
  echo "$dest already exists" >&2
  exit 1
fi

export GIT_AUTHOR_NAME="Demo Author" GIT_AUTHOR_EMAIL="demo@example.com"
export GIT_COMMITTER_NAME="Demo Author" GIT_COMMITTER_EMAIL="demo@example.com"

git init -q -b main "$dest"
cd "$dest"
g() { git -c commit.gpgsign=false "$@"; }

mkdir -p src docs
for i in $(seq 1 60); do echo "def f$i():"; echo "    return $i"; echo; done > src/app.py
printf '# Demo\n\nA repository for trying gitpanel.\n' > README.md
printf 'notes\n' > docs/notes.md
printf 'old name\n' > docs/old_name.md
printf '\x00\x01\x02binary' > logo.bin
printf 'to delete\n' > obsolete.txt
g add -A && g commit -qm "Initial commit"

printf '\n## Usage\n\nRun it.\n' >> README.md
g commit -qam "Document usage"

g switch -q -c feature/search
printf 'def search(q):\n    return [q]\n' > src/search.py
g add -A && g commit -qm "Add search"
printf 'def search(q):\n    return [q, q.upper()]\n' > src/search.py
g commit -qam "Search: include upper-case variant"
g switch -q main

g worktree add -q "${dest%/}-wt-hotfix" -b hotfix/typo

# working tree changes: three separate hunks, a staged file, a rename,
# a deletion, a binary change and untracked files
sed -i.bak -e 's/return 3$/return 3  # changed/' -e 's/return 30$/return "thirty"/' src/app.py
rm -f src/app.py.bak
printf 'def extra():\n    return "added at the end"\n' >> src/app.py
printf 'notes\nmore notes, staged\n' > docs/notes.md
g add docs/notes.md
g mv docs/old_name.md docs/new_name.md
rm obsolete.txt
printf '\x00\x09\x09binary' > logo.bin
printf 'scratch\n' > scratch.txt
mkdir -p build && printf 'artifact\n' > build/output.log

echo "Demo repository ready: $dest  (linked worktree: ${dest%/}-wt-hotfix)"
