# How `pkg` works

Depth for `bin/pkg`. Numbers below were measured on this box; re-measure when the
repo sets or the containers change noticeably.

## Routing contract

Per package name, first match wins:

1. installed via rpm → report and skip (never reinstall)
2. present in the Fedora name list → Fedora batch (`sudo dnf install -y …`)
3. installed in the Arch container → report and skip
4. resolvable by `yay -Si` → yay batch (`yay -S --noconfirm --needed …`), which
   covers Arch repo *and* AUR
5. installed in the Ubuntu container → report and skip
6. present in the Ubuntu name list → apt batch (`apt-get install -y …`)
7. otherwise → "not found in Fedora, Arch/AUR or Ubuntu", exit 1

`yay` decides repo vs AUR itself, so the scriptable path needs one Arch bucket; the
`arch/` vs `aur/` split exists for the TUI display and `pkg info`. Ubuntu needs an
explicit list check because there is no apt equivalent of `yay -Si` that also
answers "does this exist at all".

After the batches run, re-check every requested name (`rpm -q` / `pacman -Q` /
`dpkg-query`) and exit 1 if any is missing — installers can report success without
having registered a package. Guard that check behind the dry-run flag, or every dry
run reports "did not install".

## Data sources

| List | Command | Size | Cost |
|---|---|---|---|
| Fedora available names | `dnf repoquery --available --qf '%{name}\n'` | ~70k | 0.6 s |
| Arch repo names | `pacman -Slq` (exported shim) | ~15k | 0.3 s |
| AUR names | `yay -Slqa` | ~119k | 3.5 s |
| Ubuntu names | `in_ubuntu apt-cache pkgnames` | 78,921 | 1.0 s incl. `distrobox enter` |
| AUR ∩ repo | `comm -23` AUR against repo | ~11 | — |

`yay -Slq` is repo **plus** AUR (~134k) — not repo-only, do not use it as the repo
set. `comm`/`sort` pairs must share a collation: the script sets `LC_ALL=C` at the
top, because `comm` refuses C-sorted input when the calling shell runs another
locale.

`apt-cache pkgnames` lists real package names only — purely virtual names
(`debconf-2.0`, `mail-transport-agent`) are absent, so a name that appears in the
list is installable. The list is only as good as the container's apt index, which is
why `pkg refresh` and `install.sh` run `apt-get update`, and why an install into the
container refreshes the index first when it is older than `PKG_APT_INDEX_DAYS`
(default 7).

Installed sets are read live, not cached:

| Container | all installed | explicitly installed (the remove list) |
|---|---|---|
| Fedora | `rpm -qa --qf '%{NAME}\n'` | — |
| Arch | `pacman -Qq` | `pacman -Qqe` |
| Ubuntu | `dpkg-query -W -f '${Package}\n'` | `apt-mark showmanual` |

`pacman -Qq` would offer dependencies in the remove TUI; `apt-mark showmanual` is
the apt equivalent of `-Qqe`. Both include the base system, which is honest — a
container's `bash` can be removed if you insist.

Use `dnf -q info` / `dnf -q search`: the non-quiet forms print "Updating and loading
repositories: Repositories loaded" into the preview pane.

## Containers

Two distrobox containers hold the non-Fedora worlds, named in exactly two places:

```bash
CONTAINER="${PKG_CONTAINER:-Arch}"
UBUNTU_CONTAINER="${PKG_UBUNTU_CONTAINER:-Ubuntu}"
in_arch()   { distrobox enter "$CONTAINER" -- "$@"; }
in_ubuntu() { distrobox enter "$UBUNTU_CONTAINER" -- "$@"; }
in_cont()   { distrobox enter "$1" -- "${@:2}"; }
```

`pacman`, `yay` and `apt` are also distrobox-exported shims in `~/.local/bin`, so
host-side calls to them work without `distrobox enter`. Both containers have
passwordless sudo (distrobox writes `/etc/sudoers.d/<user>` with `NOPASSWD:ALL` at
creation), so container installs/removals are fully automatable — the Fedora host is
the only world that needs a password.

Cold start is slow: a stopped container makes the first `yay`/`pacman`/`apt` call
take seconds. The cached name lists mean only the preview and the install itself pay
that cost. When a container is unreachable, degrade with a warning instead of
failing the whole command.

## Preview pane contract

Preview is a re-invocation of the script's own hidden verb
(`pkg __preview <mode> {1}`), dispatched on the entry prefix:

| prefix | info | remove mode | alt-b |
|---|---|---|---|
| `dnf/` | `dnf -q info` | same | — |
| `arch/` | `pacman -Sii` | `yay -Qi` | — |
| `aur/` | `yay -Siia` | `yay -Qi` | `yay -Gpa` (PKGBUILD) |
| `apt/` | `apt-cache show` | `dpkg-query -s` | — (AUR-only) |

Entry line: `<src>/<name>` + two spaces + optional `[installed]`, with `--nth=1` so
typing matches only the name token while `dnf/`, `arch/`, `aur/`, `apt/` filter by
source. Keep the lines ANSI-free — with `--ansi` the escapes leak into placeholders
and have to be stripped before parsing. Per-package preview info is cached on disk
(`dnf info` is ~0.75 s warm and fzf re-runs the preview on every cursor move); keep
the installed-status line *outside* the cache because install state changes under it.

## Export layer

Classification comes from the package's own file list — `pacman -Ql` for Arch,
`dpkg-query -L` for Ubuntu (`dpkg -L` prints bare paths, `pacman -Ql` prints
`pkg<TAB>path`, so `cont_owned_files` normalises both to one path per line) — never
from guessing:

- `*/share/applications/*.desktop` with no `NoDisplay=true` → `distrobox-export --app`
- executables under `/{usr,usr/local}/{sbin,bin,games}` → `distrobox-export --bin`

Guards, in order: never export a name the Fedora host already provides
(`host_provides_cmd`, ignoring `~/.local/bin` itself), never overwrite a
`~/.local/bin` file distrobox did not create (`bin_dir_taken`), and never auto-export
a package owning more than `PKG_EXPORT_MAX_BINS` (8) new commands — that needs an
explicit `pkg export --bin <pkg>`.

Target lines carry their container (`container<TAB>kind<TAB>path`), so teardown
addresses the right container, and un-export deletes `<container>-<basename>` for
apps (distrobox's own naming: `Ubuntu-com.anthropic.Claude.desktop`). Teardown must
run *before* `yay -Rns` / `apt-get purge`, because `distrobox-export --delete`
requires the file to still exist in the container; if it is already gone, the shim is
removed directly.

## apt specifics

- `apt-get purge -y <pkgs>` followed by `apt-get autoremove --purge -y` is the apt
  equivalent of `yay -Rns`: the second call is what removes the pulled-in
  dependencies. It also removes them when the *whole* set is gone, so expect the
  container's package count to shrink back to where it started.
- Everything runs with `DEBIAN_FRONTEND=noninteractive` via `sudo env …`, and
  `--no-install-recommends` is deliberately **not** passed: plain `apt install` is
  what packagers test against, and recommends are usually load-bearing.
- `dpkg-query -W -f '${Status}' <pkg>` is the installed test: it prints
  `install ok installed` and exits 1 for an unknown name. Quote it with single quotes
  so the *container's* dpkg expands `${Status}`, not the host shell.
- A package installed from a local `.deb` (e.g. `claude-desktop`) is known to dpkg but
  not to the archives: `pkg info` falls back to `dpkg-query -s` for those instead of
  claiming the package does not exist.

## Pitfalls

- **`--pointer=green` aborts fzf** with "pointer display width should be up to 2":
  `--pointer` sets the marker *string*, not a colour. Colours go through
  `--color pointer:…,marker:…`.
- **Marking installed with awk:** `awk -v inf=<file> -v s=<src>
  'FILENAME==inf{i[$0]=1;next}{…}' <inf> <names>`. Passing the same path as both
  arguments makes the first rule match every line and emits nothing; `NR==FNR`
  silently swallows the first line of the names file when the installed file is empty.
- **JSON emitters need a newline per record.** Concatenating awk passes whose format
  strings lack `\n` produces one giant line of glued objects, i.e. invalid output.
  Emit one object per line (JSON Lines) and let the consumer split.
- **`yay -Rns` removes pulled-in dependencies**, so removing one package can also drop
  a dep another app merely optionally needs (seen with libnotify). Same for
  `apt-get autoremove`.
- **Host sudo needs a real terminal.** `sudo` timestamps are per-TTY, so an
  agent-driven shell cannot borrow the user's: run host-side installs in Konsole and
  let the user type the password. Container sudo is passwordless and needs nothing.
- **`rm -f ""` is safe** (exit 0, no message), which is what lets the temp-file
  cleanup in the TUIs skip existence checks.
- **`in_cont_each` payloads are single-quoted on purpose** so the *container's* bash
  expands `"$@"` (shellcheck flags these as SC2016; that is intended, there are no
  real findings).

## Verification recipe

Container-side roundtrips need no human. Pick a name that is genuinely
Ubuntu-only (compute it, do not guess — the AUR is large):

```bash
comm -23 <(pkg list apt | sed 's|^apt/||' | sort) \
         <(sort ~/.cache/pkg/dnf.names ~/.cache/pkg/arch.names ~/.cache/pkg/aur.names | sort -u)
```

Then:

```bash
pkg add aiohttp-wsgi-serve && ls -l ~/.local/bin/aiohttp-wsgi-serve   # apt install + export
pkg drop aiohttp-wsgi-serve && ls ~/.local/bin/aiohttp-wsgi-serve     # unexport + purge + autoremove
```

Routing without side effects — one call covers every branch:

```bash
pkg add -n cowsay cbonsai pipes.sh neovim zzz-nope
# → dnf batch (cowsay, cbonsai), yay batch (pipes.sh), "already installed" (neovim),
#   "not found" (zzz-nope), exit 1
```

TUI without side effects, driven in tmux:

```bash
tmux new-session -d -s t1 -x 200 -y 50 'PKG_DRY_RUN=1 pkg install; echo EXIT=$?; sleep 60'
sleep 7; tmux capture-pane -p -t t1 | sed -n '1,2p;14,26p'     # header, count, preview
tmux send-keys -t t1 'apt/0install'; sleep 3                    # apt preview renders
tmux send-keys -t t1 Tab; tmux send-keys -t t1 C-u 'dnf/cowsay'; sleep 2
tmux send-keys -t t1 Tab; tmux send-keys -t t1 Enter; sleep 6
tmux capture-pane -p -t t1 -S -25 | sed '/^$/d' | tail -6       # one command per world
tmux kill-session -t t1
```

Expect exactly one `sudo dnf install -y …` and one `apt-get install -y …` line, not
one call per package.

Static analysis without installing anything on the host — shellcheck in the
container, comparing against the previous revision so only new findings matter:

```bash
distrobox enter Ubuntu -- sudo apt-get install -y shellcheck
distrobox enter Ubuntu -- shellcheck -S warning -f gcc ~/.local/bin/pkg
distrobox enter Ubuntu -- sudo apt-get purge -y shellcheck   # clean up after
```

## Launchers

`desktop/pkg-{install,remove}.desktop` are KRunner entries (Alt+Space) that open
Konsole running the TUI. `install.sh` substitutes `$HOME` into the `Exec=` line;
desktop files cannot expand `$HOME` themselves. Re-run `kbuildsycoca6` after editing.
