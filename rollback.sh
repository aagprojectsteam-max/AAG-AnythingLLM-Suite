#!/usr/bin/env bash
set -euo pipefail
backup=${1:?Usage: rollback.sh BACKUP_DIRECTORY}
manifest=$backup/manifest.tsv
[[ -f "$manifest" ]] || { echo 'Invalid backup manifest' >&2; exit 1; }
while IFS=$'\t' read -r action target; do
  case "$action" in
    RESTORE) source_file=$backup/files/${target#/}; [[ -e "$source_file" ]] || { echo "Missing backup for $target" >&2; exit 1; }; rm -rf -- "$target"; mkdir -p "$(dirname "$target")"; cp -a "$source_file" "$target";;
    REMOVE) rm -rf -- "$target";;
    *) echo "Invalid manifest action: $action" >&2; exit 1;;
  esac
done < "$manifest"
echo "ROLLBACK=PASS backup=$backup"

