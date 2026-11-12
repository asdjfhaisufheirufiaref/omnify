#!/bin/sh
# Omnify uninstaller (no root required).
#
# Default: remove the Omnify installation, user data and project notes.
# Backups are NEVER touched. The source checkout is kept unless
# --remove-project is passed (the uninstaller itself lives there).
set -eu

ASSUME_YES=0
DRY_RUN=0
KEEP_DATA=0
REMOVE_PROJECT=0

usage() {
    cat <<'EOF'
Usage: ./uninstall.sh [options]

Options:
  -y, --yes            do not ask for confirmation
  -n, --dry-run        show what would be removed, change nothing
      --keep-data      keep notes, settings, cache and browser data
      --remove-project also remove this source checkout
  -h, --help           show this help

Backups are always preserved:
  * the default backup folder (~/.local/share/omnify/backups)
  * OMNIFY_BACKUP_DIR, if set
  * the custom folder saved in backup.json

Omnify only removes its own launchers, desktop entries, autostart file,
icon, package symlink and its own application data. Unrelated files in the
same folders are left alone. To remove backups, do it yourself in the file
manager.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        -y|--yes) ASSUME_YES=1 ;;
        -n|--dry-run) DRY_RUN=1 ;;
        --keep-data) KEEP_DATA=1 ;;
        --remove-project) REMOVE_PROJECT=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

HOME_DIR=${HOME:-}
if [ -z "$HOME_DIR" ]; then
    echo "HOME is not set; refusing to guess where user data lives." >&2
    exit 1
fi
case "$HOME_DIR" in
    /) echo "HOME is /; refusing to uninstall." >&2; exit 1 ;;
esac

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DATA_DIR=${OMNIFY_DATA_DIR:-${XDG_DATA_HOME:-$HOME_DIR/.local/share}/omnify}
CONFIG_DIR=${XDG_CONFIG_HOME:-$HOME_DIR/.config}/omnify
CACHE_DIR=${XDG_CACHE_HOME:-$HOME_DIR/.cache}/omnify
NOTES_DIR=${OMNIFY_NOTES_DIR:-$ROOT/notes}
BIN_DIR="$HOME_DIR/.local/bin"
APPS_DIR="$HOME_DIR/.local/share/applications"
AUTOSTART_DIR="$HOME_DIR/.config/autostart"
ICON_FILE="$HOME_DIR/.local/share/icons/hicolor/256x256/apps/omnify.png"

# Resolve every backup path before any deletion. The Python helper is also
# what later removes data while protecting these paths.
BACKUP_PATHS=$(python3 - "$DATA_DIR" "${OMNIFY_BACKUP_DIR:-}" <<'PY'
import json
import sys
from pathlib import Path

data = Path(sys.argv[1]).expanduser()
paths = [data / "backups"]
env_path = sys.argv[2].strip()
if env_path:
    paths.append(Path(env_path).expanduser())
config_path = data / "backup.json"
try:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    destination = str(raw.get("destination", "") or "").strip()
    if destination:
        paths.append(Path(destination).expanduser())
except (OSError, ValueError, TypeError):
    pass

seen = set()
for path in paths:
    resolved = path.resolve()
    if resolved in seen:
        continue
    seen.add(resolved)
    print(resolved)
PY
)

if [ "$KEEP_DATA" = 1 ]; then
    DATA_MODE="keep notes, settings, cache and browser data"
else
    DATA_MODE="remove notes and application data (backups preserved)"
fi
if [ "$REMOVE_PROJECT" = 1 ]; then
    PROJECT_MODE="remove the source checkout as well"
else
    PROJECT_MODE="keep the source checkout"
fi

echo "Omnify uninstall plan"
echo "  home:            $HOME_DIR"
echo "  data:            $DATA_DIR"
echo "  notes:           $NOTES_DIR"
echo "  backups kept in:"
printf '%s\n' "$BACKUP_PATHS" | sed 's/^/    /'
echo "  data policy:     $DATA_MODE"
echo "  source policy:   $PROJECT_MODE"
echo
echo "Backups are never deleted by this script."
echo

if [ "$DRY_RUN" = 1 ]; then
    echo "Dry run: no files will be removed."
else
    if [ "$ASSUME_YES" != 1 ]; then
        printf 'Continue? [y/N] '
        read -r answer || answer=""
        case "$answer" in
            y|Y|yes|YES) ;;
            *) echo "Cancelled."; exit 0 ;;
        esac
    fi
fi

remove_file() {
    path=$1
    if [ "$DRY_RUN" = 1 ]; then
        echo "Would remove: $path"
    elif [ -e "$path" ] || [ -L "$path" ]; then
        rm -f -- "$path"
        echo "Removed: $path"
    fi
}

# Desktop integration and launchers.
remove_file "$BIN_DIR/omnify"
remove_file "$BIN_DIR/omnify-browser"
remove_file "$AUTOSTART_DIR/omnify.desktop"
remove_file "$ICON_FILE"
for path in "$APPS_DIR"/omnify*.desktop; do
    [ -e "$path" ] || continue
    remove_file "$path"
done

# Only remove the user-site package if it is the symlink created by
# install.sh. A real package directory is left in place and reported.
USER_SITE=$(python3 -c 'import site; print(site.getusersitepackages())' 2>/dev/null || true)
if [ -n "$USER_SITE" ] && [ -L "$USER_SITE/omnify" ]; then
    remove_file "$USER_SITE/omnify"
elif [ -n "$USER_SITE" ] && [ -e "$USER_SITE/omnify" ]; then
    echo "Keeping non-symlink package: $USER_SITE/omnify" >&2
fi

if command -v update-desktop-database >/dev/null 2>&1 && [ "$DRY_RUN" != 1 ]; then
    update-desktop-database "$APPS_DIR" 2>/dev/null || true
fi
if command -v kbuildsycoca6 >/dev/null 2>&1 && [ "$DRY_RUN" != 1 ]; then
    kbuildsycoca6 2>/dev/null || true
fi

# Remove application data and notes while preserving every backup path.
python3 - "$DATA_DIR" "$CONFIG_DIR" "$CACHE_DIR" "$NOTES_DIR" "$ROOT" \
    "$KEEP_DATA" "$DRY_RUN" "$REMOVE_PROJECT" "$BACKUP_PATHS" <<'PY'
import shutil
import sys
from pathlib import Path

(
    data_arg,
    config_arg,
    cache_arg,
    notes_arg,
    root_arg,
    keep_data_arg,
    dry_run_arg,
    remove_project_arg,
    backups_arg,
) = sys.argv[1:10]

data = Path(data_arg).expanduser().resolve()
config = Path(config_arg).expanduser().resolve()
cache = Path(cache_arg).expanduser().resolve()
notes = Path(notes_arg).expanduser().resolve()
root = Path(root_arg).expanduser().resolve()
keep_data = keep_data_arg == "1"
dry_run = dry_run_arg == "1"
remove_project = remove_project_arg == "1"
backups = [
    Path(line).expanduser().resolve()
    for line in backups_arg.splitlines()
    if line.strip()
]
# A path that does not exist cannot contain files worth protecting. Keeping
# its parent only to preserve an empty idea would leave a confusing skeleton.
protected_backups = [backup for backup in backups if backup.exists()]

home = Path.home().resolve()
if root == Path("/") or root == home:
    raise SystemExit("Refusing to remove the home directory or filesystem root")
if not (root / "pyproject.toml").is_file():
    raise SystemExit("Refusing to operate: project marker pyproject.toml is missing")


def guard(target: Path, label: str) -> None:
    """Reject paths whose deletion would take the home tree or source with it."""
    if target == Path("/") or target == home or home.is_relative_to(target):
        raise SystemExit(f"Refusing to remove unsafe {label} path: {target}")
    if target == root and not remove_project:
        raise SystemExit(
            f"Refusing to remove {label} path: it is the source checkout "
            "(pass --remove-project to allow this)"
        )


if not keep_data:
    for target, label in (
        (data, "data"),
        (config, "config"),
        (cache, "cache"),
        (notes, "notes"),
    ):
        guard(target, label)


def remove(path: Path) -> None:
    path = path.resolve()
    if not path.exists() and not path.is_symlink():
        return
    if any(
        path == backup or path.is_relative_to(backup)
        for backup in protected_backups
    ):
        print(f"Keeping backup: {path}")
        return
    if path.is_symlink() or path.is_file():
        if dry_run:
            print(f"Would remove: {path}")
        else:
            path.unlink()
            print(f"Removed: {path}")
        return
    if not path.is_dir():
        return

    # If this directory contains a backup, recurse into it instead of
    # deleting the whole tree. This keeps any configured custom backup
    # folder that happens to live under the data directory.
    contains_backup = any(
        backup != path and backup.is_relative_to(path)
        for backup in protected_backups
    )
    if contains_backup:
        for child in sorted(path.iterdir()):
            remove(child)
        if dry_run:
            print(f"Would keep container (contains backups): {path}")
        else:
            print(f"Kept container (contains backups): {path}")
        return

    if dry_run:
        print(f"Would remove: {path}")
        return
    try:
        shutil.rmtree(path)
        print(f"Removed: {path}")
    except OSError as exc:
        print(f"Could not remove {path}: {exc}", file=sys.stderr)

if not keep_data:
    for target in (notes, config, cache, data):
        remove(target)
else:
    print("Keeping user data by request.")

if remove_project:
    # The checkout is only removed on explicit request. If it contains a
    # backup folder, the recursive remover preserves that folder and its
    # contents, leaving a small safe container behind.
    remove(root)
PY

echo
echo "Omnify uninstalled."
echo "Backups remain untouched. Delete them yourself only if you want to:"
printf '%s\n' "$BACKUP_PATHS" | sed 's/^/  /'
if [ "$REMOVE_PROJECT" != 1 ]; then
    echo "Source checkout kept at: $ROOT"
    echo "Use --remove-project to delete it too (backups are still protected)."
fi
