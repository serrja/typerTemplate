#!/usr/bin/env bash
# Source bytes are independent of HEAD. Never follow a source symlink.
set -euo pipefail
export LC_ALL=C
output=${1:-coverage/source.json}
mkdir -p -- "$(dirname -- "$output")"
root=$(pwd -P)
commit=null
dirty=true
if [[ $(git rev-parse --show-toplevel 2>/dev/null || true) == "$root" ]]; then
    if head=$(git rev-parse --verify HEAD 2>/dev/null); then commit="\"$head\""; fi
    if [[ -z $(git status --porcelain=v1 --untracked-files=normal) ]]; then dirty=false; fi
    source_paths() { git ls-files --cached --others --exclude-standard -z | sort -zu; }
else
    # A fresh Copier render need not have a Git repository. Reports and caches
    # remain excluded; null commit and dirty=true explicitly record that boundary.
    source_paths() {
        find . -type d \( -name .git -o -name .venv -o -name coverage -o -name dist \
            -o -name node_modules -o -name .local -o -name .aws -o -name .agents \
            -o -name .codex -o -name .serena -o -name backups -o -name __pycache__ \
            -o -name .mypy_cache -o -name .ruff_cache -o -name .pytest_cache \) -prune \
            -o \( -type f -o -type l \) \( ! -name '.env' ! -name '.env.*' -o -name '.env.example' \) ! -name '.coverage*' ! -name 'core.*' -print0 | sort -z
    }
fi
list=$(mktemp "${output%.json}-files.XXXXXX")
temporary=$(mktemp "${output}.XXXXXX")
trap 'rm -f -- "$temporary" "$list"' EXIT
source_paths > "$list"
identity=$(
    while IFS= read -r -d '' path; do
        path=${path#./}
        printf '%s\0' "$path"
        if [[ -L "$path" ]]; then
            printf 'symlink\0'
            readlink -- "$path"
        elif [[ -f "$path" ]]; then
            if [[ -x "$path" ]]; then printf 'executable\0'; else printf 'file\0'; fi
            sha256sum < "$path" | cut -d ' ' -f 1
        elif [[ -d "$path" ]]; then
            echo "Unsupported nested repository: $path" >&2
            exit 2
        else
            printf 'deleted\0'
        fi
        printf '\0'
    done < "$list" | sha256sum | cut -d ' ' -f 1
)
printf '{"commit":%s,"dirty":%s,"contentSha256":"%s"}\n' "$commit" "$dirty" "$identity" > "$temporary"
mv -- "$list" "${output%.json}-files.nul"
mv -- "$temporary" "$output"
