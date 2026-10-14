# one package manager over three worlds

`pkg` is a single bash script that treats three package worlds as one namespace:

| world | tooling | TUI / list prefix | size today |
|---|---|---|---|
| Fedora host | `dnf` / `rpm` | `dnf/` | ~70k names |
| Arch container (distrobox) | `pacman` / `yay`, incl. the AUR | `arch/`, `aur/` | ~15k repo + ~119k AUR |
| Ubuntu container (distrobox) | `apt` / `dpkg` | `apt/` | ~79k names |

One `pkg install` gives you a single fzf list of **286,256** packages across all
three; one `pkg add cowsay cbonsai 3d-ascii-viewer` routes each name to the right
world and installs them in one batch per world.

This is a personal tool: it is written for *this* machine (Fedora host, KDE,
containers named `Arch` and `Ubuntu`). It is not a portable cross-distro
abstraction and makes no attempt to be one. See
[Reproducing on a fresh install](#reproducing-on-a-fresh-install).

## The three worlds, in resolution order

For every name, first match wins:

1. **already installed via rpm** → reported and skipped, never reinstalled
2. **exists in the Fedora repos** → `sudo dnf install -y …` (batched)
3. **already installed in Arch** → reported and skipped
4. **resolvable by `yay -Si`** → `yay -S --noconfirm --needed …` (Arch repo *or* AUR)
5. **already installed in Ubuntu** → reported and skipped
6. **exists in the Ubuntu archives** → `apt-get install -y …` (batched)
7. otherwise → "not found in Fedora, Arch/AUR or Ubuntu", exit 1

Why that order: the Fedora host is the only world whose packages get host updates,
so it always wins. Arch is next because the AUR covers ~119k extra names
(including some Debian-only ones). Ubuntu is the last resort — of its 78,921 names,
only **67,387** are unavailable from Fedora/Arch/AUR, which is exactly the gap it
fills. Everything is installable from one command; only the *routing* changes.

After the batches run, `pkg` re-checks each requested name (`rpm -q` / `pacman -Q` /
`dpkg-query`) and exits 1 if any is missing — installers can succeed without
registering a package.

## Verbs

Interactive (needs `fzf`):

```
pkg install                  pick from all four sources and install (dnf/ arch/ aur/ apt/ filters)
pkg remove                   pick from what is installed (Fedora + explicit Arch + explicit Ubuntu)
```

Scriptable:

```
pkg add [-n] <pkgs...>       install; already-installed names are skipped
pkg drop [-n] <pkgs...>      remove what is installed, ignore the rest
pkg search <term>...         dnf search + yay -Ss + apt-cache search
pkg info [-a] <pkg>...       dnf info, else pacman -Sii, else yay -Siia, else apt-cache show
pkg update                   dnf upgrade --refresh + yay -Syu + apt update/upgrade + flatpak update
pkg autoremove               dnf autoremove + yay -Yc + apt autoremove --purge
pkg clean                    dnf clean all + yay -Scc + apt clean
pkg present <pkgs...>        exit 0 if all are installed   (predicate)
pkg missing <pkgs...>        exit 0 if any is missing      (predicate)
pkg list [scope] [--json]    scope: installed | available | dnf | arch | aur | apt
pkg export [--app|--bin|--both] [-c <container>] <pkgs...>
pkg unexport <pkgs...>       tear those exports down again
pkg refresh                  drop the caches, refresh dnf + apt metadata, rebuild
pkg help
```

`-n` / `PKG_DRY_RUN=1` prints the commands instead of running them, which is how
routing gets tested without side effects.

## Exports: the containers reach the host

A distrobox container's packages exist only inside it, so `pkg` forwards them:

- a package owning a visible `.desktop` → `distrobox-export --app` (`~/.local/share/applications`)
- a package owning executables → `distrobox-export --bin` (`~/.local/bin`)

Three guards apply: a binary that Fedora already provides is never shadowed, a file
in `~/.local/bin` that distrobox did not create is never touched, and a package
owning more than `PKG_EXPORT_MAX_BINS` (8) executables is left for a deliberate
`pkg export --bin <pkg>`. Teardown runs *before* the package is removed
(`distrobox-export --delete` needs the file to still exist), and the container in
question is derived per package, so Arch and Ubuntu exports coexist.
`--no-export` / `PKG_NO_EXPORT=1` skips the whole step.

```bash
pkg export --bin ripgrep          # export everything ripgrep owns
pkg export -c Ubuntu --app claude-desktop
pkg unexport claude-desktop
```

## Try it

```bash
pkg search cow                    # all three worlds side by side
pkg list apt   | grep -i nyancat   # Ubuntu names only
pkg info neovim                    # which world has it, and how it is installed
pkg add -n cowsay neovim zzz-nope  # dry-run: shows the routing, exit 1 (zzz-nope)
pkg present cowsay neovim && echo all here
pkg list installed --json | jq -c 'select(.source=="apt")' | head
```

## Knobs

| env var | default | meaning |
|---|---|---|
| `PKG_DRY_RUN` | `0` | print commands instead of running them |
| `PKG_CONTAINER` | `Arch` | Arch container name |
| `PKG_UBUNTU_CONTAINER` | `Ubuntu` | Ubuntu container name |
| `PKG_CACHE_TTL` | `86400` | seconds before the cached name lists are rebuilt |
| `PKG_INFO_TTL` | `86400` | seconds before cached preview info expires |
| `PKG_NO_INFO_CACHE` | — | set to skip the preview-info cache |
| `PKG_APT_INDEX_DAYS` | `7` | refresh the container's apt index before installing when older |
| `PKG_EXPORT_BIN_DIR` | `~/.local/bin` | where container binaries are exported |
| `PKG_EXPORT_APP_DIR` | `~/.local/share/applications` | where container apps are exported |
| `PKG_EXPORT_MAX_BINS` | `8` | auto-export gives up above this many new commands |
| `PKG_NO_EXPORT` | — | set to skip exporting entirely |

Lists live in `~/.cache/pkg` (`PKG_CACHE_TTL`, default 24 h); the first build takes
~4 s, afterwards they load instantly. `pkg refresh` when metadata moved.

## Install

Assumed to exist already: a fresh Fedora with `dnf` and `distrobox`, and the two
containers. Create those first if they are not there:

```bash
distrobox create --name Arch   --image archlinux:latest
distrobox create --name Ubuntu --image ubuntu:latest
```

Then install — this is the whole thing (a public repo, so no GitHub login needed
on the new machine):

```bash
git clone https://github.com/get-monochromed/pkg-meta-package-manager.git
cd pkg-meta-package-manager
./install.sh
```

`install.sh` is idempotent and does everything else: `fzf` + `konsole` on the host,
the `base-devel`/`git`/`yay` toolchain in `Arch` (bootstrapping `yay-bin` from the AUR
when it is missing), an `apt-get update` in `Ubuntu`, the `pacman`/`yay`/`apt` host
shims via `distrobox-export`, `~/.local/bin/pkg`, the two KRunner launchers (with your
`$HOME` substituted into `Exec=`), then a cache warm-up and a smoke test. It also
checks passwordless sudo inside both containers, which distrobox normally writes
itself and which unattended container installs depend on. It asks for your sudo
password once, for the host `dnf install`.

### What it writes

| path | what |
|---|---|
| `~/.local/bin/pkg` | the tool |
| `~/.local/bin/pacman`, `~/.local/bin/yay` | shims exported from the `Arch` container |
| `~/.local/bin/apt` | shim exported from the `Ubuntu` container |
| `~/.local/share/applications/pkg-install.desktop`, `pkg-remove.desktop` | KRunner launchers |
| `~/.cache/pkg/` | cached name lists and preview info (deleteable, rebuilt on demand) |
| `~/.local/bin/<name>`, `~/.local/share/applications/<container>-<name>.desktop` | added later, per container package `pkg` installs and exports |

### Upgrade

Nothing to version-bump: re-running the installer *is* the upgrade. It overwrites the
script and the launchers (plain `install`, no "already exists" refusal), skips shims
that are already exported, and the container steps are no-ops when everything is
current.

```bash
cd ~/Documents/Git/pkg-meta-package-manager
git pull
./install.sh
```

### Uninstall

```bash
# 1. see which container exports exist, then tear down the ones you want gone,
#    while their packages are still installed in their containers
grep -l distrobox_binary ~/.local/bin/* 2>/dev/null | xargs -n1 basename
pkg unexport <package>...

# 2. remove what the installer put on the host
rm -f  ~/.local/bin/pkg ~/.local/bin/pacman ~/.local/bin/yay ~/.local/bin/apt
rm -f  ~/.local/share/applications/pkg-install.desktop \
       ~/.local/share/applications/pkg-remove.desktop
rm -rf ~/.cache/pkg
kbuildsycoca6 --noincremental

# 3. the containers themselves, their packages and their distrobox configs are untouched
```

Two things worth knowing before you run that: the `pacman`/`yay`/`apt` shims are how
those commands reach the host *outside* `pkg` too, so if you only want `pkg` gone,
drop those three lines from step 2 and re-run `./install.sh` to restore them; and
packages already installed inside the containers stay installed.

### Not done by the installer

- it does not create the containers (assumed present)
- it does not install Flatpak apps — those come from Discover, and `pkg` only calls
  `flatpak update` when upgrading
- it does not add `~/.local/bin` to your `PATH` — it warns if it is missing
- it does not touch any other file in `~/.local/bin`

## Layout

```
bin/pkg                      the whole tool — one bash file, no helper files
desktop/pkg-install.desktop  KRunner launcher: pkg install
desktop/pkg-remove.desktop   KRunner launcher: pkg remove
install.sh                   rebuild the setup on a fresh host
docs/how-it-works.md         routing contract, data sources, measured costs, pitfalls
```

## Extending it

`bin/pkg` is self-contained: a `cmd_*`/`tui_*` function per verb, a `case` dispatcher
at the bottom, and a hidden `pkg __preview <mode> <entry>` verb that fzf calls for
its preview pane. Adding a world means adding one `in_<world>` primitive, one name
list to `ensure_lists`, one bucket in the routing block, and the export layer
already takes a container as an argument. Keep the entry format (`src/name`), the
bucket routing and the dry-run flag intact — they are what makes it testable.
`docs/how-it-works.md` has the traps worth knowing before you edit (awk list
building, fzf flags, apt index staleness, export teardown order).
