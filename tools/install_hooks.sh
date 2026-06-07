#!/usr/bin/env bash
# Install the repo's tracked git hooks into .git/hooks. This is a plain file
# copy — it needs NO git-config change (core.hooksPath is left untouched) and no
# third-party tooling, so it cannot reroute git or affect any other repo.
#
# Run once after cloning (or after editing a hook):  make install-hooks
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
hooks_src="$repo_root/hooks"
hooks_dst="$repo_root/.git/hooks"

if [ ! -d "$repo_root/.git" ]; then
	echo "install_hooks: not a git working tree ($repo_root)" >&2
	exit 1
fi
if [ ! -d "$hooks_src" ]; then
	echo "install_hooks: no hooks/ directory to install from" >&2
	exit 1
fi

for h in "$hooks_src"/*; do
	[ -e "$h" ] || continue
	name="$(basename "$h")"
	cp "$h" "$hooks_dst/$name"
	chmod +x "$hooks_dst/$name"
	echo "installed $name -> .git/hooks/$name"
done
