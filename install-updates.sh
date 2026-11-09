#!/usr/bin/env bash
# Optional Plasma notifier. Does not install or upgrade any packages.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
command -v python3 >/dev/null
command -v konsole >/dev/null
command -v systemctl >/dev/null
install -Dm755 "$repo/bin/pkg" "$HOME/.local/bin/pkg"
install -Dm755 "$repo/bin/pkg-updates" "$HOME/.local/bin/pkg-updates"
widget="$HOME/.local/share/plasma/plasmoids/com.monochrome.pkgupdates"
mkdir -p "$widget"
cp -r "$repo/plasmoid/"* "$widget/"
for unit in pkg-updates.service pkg-updates.timer; do
    install -Dm644 "$repo/systemd/$unit" "$HOME/.config/systemd/user/$unit"
done
systemctl --user daemon-reload
systemctl --user enable --now pkg-updates.timer
systemctl --user start --no-block pkg-updates.service
printf '%s\n' 'Installed pkg Updates; add the widget to your panel (daily checks enabled).'
