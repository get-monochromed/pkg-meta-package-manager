#!/usr/bin/env bash
#
# install.sh — rebuild this whole setup on a fresh Fedora host.
#
# Assumed to exist already:
#   - Fedora with dnf and distrobox installed
#   - two distrobox containers named "Arch" and "Ubuntu" (distrobox create)
#
# Everything else is installed from here: the host packages, the AUR toolchain
# and yay inside the Arch container, the host shims, ~/.local/bin/pkg itself and
# the KRunner launchers. Safe to re-run; every step checks before it acts.
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARCH_CONTAINER="${PKG_CONTAINER:-Arch}"
UBUNTU_CONTAINER="${PKG_UBUNTU_CONTAINER:-Ubuntu}"
BIN_DIR="${PKG_EXPORT_BIN_DIR:-$HOME/.local/bin}"
APP_DIR="${PKG_EXPORT_APP_DIR:-$HOME/.local/share/applications}"

info() { printf '\033[32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

containers() { distrobox list 2>/dev/null | awk -F'|' 'NR>1 {gsub(/ /, "", $2); print $2}'; }
in_arch()    { distrobox enter "$ARCH_CONTAINER" -- "$@"; }
in_ubuntu()  { distrobox enter "$UBUNTU_CONTAINER" -- "$@"; }

# ── 0. preconditions ─────────────────────────────────────────────────────────
command -v distrobox >/dev/null || die "distrobox is not installed"
command -v dnf       >/dev/null || die "dnf is not installed — this expects Fedora"
for c in "$ARCH_CONTAINER" "$UBUNTU_CONTAINER"; do
  containers | grep -qxF "$c" || die "no distrobox container named '$c' — create it first:
    distrobox create --name $c --image $([ "$c" = "$ARCH_CONTAINER" ] && echo archlinux:latest || echo ubuntu:latest)"
done
info "found containers: $ARCH_CONTAINER, $UBUNTU_CONTAINER"

# ── 1. host packages ─────────────────────────────────────────────────────────
# fzf drives both TUIs; konsole is what the KRunner launchers open.
info "installing host packages (fzf, konsole) — this asks for your sudo password"
sudo dnf install -y fzf konsole

# ── 2. container hygiene: passwordless sudo ──────────────────────────────────
# distrobox normally writes /etc/sudoers.d/<user> with NOPASSWD:ALL at container
# creation. pkg installs into the containers unattended, so verify it is there.
for c in "$ARCH_CONTAINER" "$UBUNTU_CONTAINER"; do
  if distrobox enter "$c" -- sudo -n true 2>/dev/null; then
    info "$c: passwordless sudo ok"
  else
    warn "$c: 'sudo -n' failed, so pkg cannot install there unattended.
    Fix it once by hand:  distrobox enter $c -- sudo visudo -f /etc/sudoers.d/$USER
    with the line:        \"$USER\" ALL = (root) NOPASSWD:ALL"
  fi
done

# ── 3. Arch container: AUR toolchain + yay ───────────────────────────────────
info "$ARCH_CONTAINER: ensuring base-devel, git, fzf and yay"
in_arch sudo pacman -S --needed --noconfirm base-devel git fzf
if in_arch bash -c '[ -x /usr/bin/yay ]'; then
  info "$ARCH_CONTAINER: yay already installed"
else
  info "$ARCH_CONTAINER: building yay-bin from the AUR (takes a minute)"
  in_arch bash -c 'set -e; d="$(mktemp -d)"; git clone -q https://aur.archlinux.org/yay-bin.git "$d"; cd "$d"; makepkg -si --noconfirm'
fi

# ── 4. Ubuntu container: apt index + fzf ─────────────────────────────────────
# fzf is needed *inside* the containers too: pkg's TUIs run there now.
info "$UBUNTU_CONTAINER: apt-get update, then fzf"
in_ubuntu sudo apt-get update
in_ubuntu sudo apt-get install -y fzf

# ── 5. host shims ────────────────────────────────────────────────────────────
# pkg calls pacman/yay/apt from the host, so they must be exported.
mkdir -p "$BIN_DIR"
export_bin() {  # <container> <container-path>
  local c="$1" p="$2" name
  name="$(basename "$p")"
  if [[ -f $BIN_DIR/$name ]] && grep -q distrobox_binary "$BIN_DIR/$name" 2>/dev/null; then
    info "shim $name already exported"
  elif [[ -e $BIN_DIR/$name ]]; then
    warn "$BIN_DIR/$name exists and is not a distrobox shim — leaving it alone"
  else
    info "exporting $name from $c"
    distrobox enter "$c" -- distrobox-export --bin "$p" --export-path "$BIN_DIR"
  fi
}
export_bin "$ARCH_CONTAINER"   /usr/bin/pacman
export_bin "$ARCH_CONTAINER"   /usr/bin/yay
export_bin "$UBUNTU_CONTAINER" /usr/bin/apt

# ── 6. the script itself + KRunner launchers ─────────────────────────────────
info "installing $BIN_DIR/pkg"
install -Dm755 "$REPO_DIR/bin/pkg" "$BIN_DIR/pkg"
mkdir -p "$APP_DIR"
home_esc="$(printf '%s' "$HOME" | sed 's/[&|\\]/\\&/g')"
for f in pkg-install.desktop pkg-remove.desktop; do
  sed "s|__HOME__|$home_esc|g" "$REPO_DIR/desktop/$f" > "$APP_DIR/$f"
  info "installed $APP_DIR/$f"
done
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 --noincremental >/dev/null 2>&1 || true

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not on your PATH — add it to ~/.bashrc" ;;
esac

# ── 7. warm the caches and smoke-test ────────────────────────────────────────
info "building the package lists (Fedora + Arch + AUR + Ubuntu, a few seconds)"
"$BIN_DIR/pkg" list dnf >/dev/null
info "smoke test"
if "$BIN_DIR/pkg" present fzf >/dev/null; then info "ok: host predicate works"; fi
info "Ubuntu name list: $(wc -l < "$HOME/.cache/pkg/apt.names") names"
info "dry-run routing for 'cowsay':"
"$BIN_DIR/pkg" add -n cowsay 2>&1 | sed 's/^/    /' || true

# pkg is usable from inside the containers too, and must see only their own world
for c in "$ARCH_CONTAINER" "$UBUNTU_CONTAINER"; do
  info "$c sees: $(distrobox enter "$c" -- "$BIN_DIR/pkg" help 2>/dev/null | sed -n 's/^  Currently: //p')"
done

echo
info "done — run 'pkg help' for the full command map"
