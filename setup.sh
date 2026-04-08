#!/usr/bin/env bash
#
# Marianne AI Compose — Install / Reinstall
#
# Installs mzt so it works globally, permanently.
#
# Usage:
#   ./setup.sh              # Install (or reinstall)
#   ./setup.sh --dev        # Include dev tools (pytest, mypy, ruff)
#   ./setup.sh --docs       # Include docs tools (mkdocs)
#   ./setup.sh --help       # Show this message
#
set -euo pipefail

PYTHON_MIN="3.11"
VENV=".venv"
BIN_DIR="${HOME}/.local/bin"

# --- colors (off if not a terminal) ---
if [[ -t 1 ]]; then
    G='\033[0;32m' Y='\033[0;33m' R='\033[0;31m' B='\033[0;34m' N='\033[0m'
else
    G='' Y='' R='' B='' N=''
fi
info()  { echo -e "${B}[·]${N} $1"; }
ok()    { echo -e "${G}[✓]${N} $1"; }
warn()  { echo -e "${Y}[!]${N} $1"; }
die()   { echo -e "${R}[✗]${N} $1" >&2; exit 1; }

# --- args ---
EXTRAS=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --dev)  EXTRAS="${EXTRAS:+$EXTRAS,}dev"; shift ;;
        --docs) EXTRAS="${EXTRAS:+$EXTRAS,}docs"; shift ;;
        --help|-h)
            sed -n '3,12s/^# \?//p' "$0"
            exit 0 ;;
        *) die "Unknown option: $1" ;;
    esac
done

# --- find python 3.11+ ---
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}')" 2>/dev/null) || continue
        maj=${ver%%.*} min=${ver#*.}
        req_maj=${PYTHON_MIN%%.*} req_min=${PYTHON_MIN#*.}
        if (( maj > req_maj || (maj == req_maj && min >= req_min) )); then
            PYTHON="$cmd"
            break
        fi
    fi
done
[[ -n "$PYTHON" ]] || die "Python ${PYTHON_MIN}+ required but not found"
ok "Python $($PYTHON --version 2>&1)"

# --- venv ---
if [[ -d "$VENV" ]]; then
    info "Removing old venv..."
    rm -rf "$VENV"
fi
info "Creating venv..."
"$PYTHON" -m venv "$VENV"
ok "Venv at $VENV"

# --- install ---
if command -v uv &>/dev/null; then
    INSTALLER="uv pip"
    ok "Using uv"
else
    INSTALLER="$VENV/bin/pip"
    info "Upgrading pip..."
    "$VENV/bin/pip" install --quiet --upgrade pip
fi

SPEC="."
[[ -n "$EXTRAS" ]] && SPEC=".[$EXTRAS]"
info "Installing mzt${EXTRAS:+ [$EXTRAS]}..."
$INSTALLER install --quiet -e "$SPEC"
ok "Installed"

# --- verify mzt in venv ---
[[ -x "$VENV/bin/mzt" ]] || die "mzt not found in venv after install"
MZT_VER=$("$VENV/bin/mzt" --version 2>&1 || echo "unknown")
ok "$MZT_VER"

# --- symlink to ~/.local/bin ---
mkdir -p "$BIN_DIR"
ln -sf "$(realpath "$VENV/bin/mzt")" "$BIN_DIR/mzt"
ok "Linked mzt → $BIN_DIR/mzt"

if ! echo "$PATH" | tr ':' '\n' | grep -qx "$BIN_DIR"; then
    warn "$BIN_DIR is not on your PATH"
    echo "    Add to your shell profile:"
    echo "    export PATH=\"$BIN_DIR:\$PATH\""
fi

# --- done ---
echo ""
ok "Done. Run: mzt --version"
