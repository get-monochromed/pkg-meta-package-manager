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

## Contents queries

`pkg contains [--all] <package>...` walks reachable worlds in the usual priority
order; in each world an installed package or a repository match selects that
world. It does not silently switch to a different world when a file query fails.
`contains_files` uses `dnf -q repoquery -l`, `pacman -Qlq` (installed Arch),
`pacman -Flq` (uninstalled Arch repo), or `dpkg-query -L` (installed Ubuntu).
Arch repo paths are normalized to absolute paths. Arch file metadata must first
be downloaded with `distrobox enter Arch -- sudo pacman -Fy`; query failures
print that refresh hint. Uninstalled AUR and Ubuntu packages produce a clear
unsupported-query error rather than invented results or an implicit install.

`--all` returns all paths; the default filters bin/sbin paths to deduplicated
basenames. Backend stderr is not filtered, pipefail preserves backend errors,
and source labels go to stderr to keep stdout usable in scripts. Empty command
output for a package without commands is successful. Multiple-package failures
are accumulated into a nonzero final status. Integration checks:
`python3 -m unittest discover -s tests -v` (configured host/containers required).

## Update/upgrade separation

`cmd_upgrade` authenticates **once, up front**: a visible banner, then `sudo -v`,
then a keepalive loop refreshing the timestamp during long transactions, and a
per-source summary (`upgraded: dnf=ok …` / `FAILED: dnf=FAILED …`). Without this a
widget-launched terminal sat on a fingerprint prompt that timed out; the run then
continued through the other sources printing "Nothing to do", which reads exactly
like "there is nothing to upgrade". The bridge tees the attempt to
`~/.cache/pkg/upgrade.log`. It retains the original sequential mutating operations. `cmd_update`
only refreshes metadata and queries candidates, one background job per reachable
world, each with separate stdout/stderr files and a checked exit status:

- DNF: unprivileged `dnf -q --refresh --setopt=skip_if_unavailable=False repoquery
  --upgrades --latest-limit=1`, name.arch + EVR output, unique name.arch count.
- Arch: container-local Bash creates a temporary database, symlinks its `local`
  entry to `/var/lib/pacman/local`, refreshes **only the temporary sync database**
  with `fakeroot pacman -Sy --dbpath ... --logfile /dev/null`, queries `pacman -Qu`
  there, then `yay -Qua` for AUR. Empty output/exit 1 from these queries is normal
  only without error diagnostics. The temporary database is removed on exit;
  the live sync database never advances, avoiding partial-upgrade hazards.
- Ubuntu: `sudo -n apt-get -o APT::Update::Error-Mode=any update` inside the container,
  then `apt list --upgradable`, stripping the header/repository suffix. No upgrades.
- Flatpak: host-only `flatpak remote-ls --updates --all --arch='*' --columns=ref`; count unique refs,
  including runtimes and branches, not just apps.

Modes: default grouped list, `--count` total, `--counts` four space-separated
numbers in DNF/Arch/Ubuntu/Flatpak order (`?` where a source could not be
queried), or `--json` with `checked_at`, `ok`, `degraded`, `total`, `counts`,
`status`, `problems`. Semantics: `ok` = every attempted source was checked;
`degraded` = usable numbers exist but at least one source failed; `alarm` (added
by the bridge) = neither, i.e. nothing could be checked at all. Unavailable
sources are 0/`unavailable`, failed ones `null`/`error`, and a failed metadata
refresh falls back to the cache as `stale`. Every mode exits 1 unless `ok`, so
scripts stay strict while the panel stays calm. Candidate counts are not final
solver transaction sizes.

Each source runs under `timeout` (`PKG_CHECK_TIMEOUT_{DNF,ARCH,APT,FLATPAK}` =
180/300/180/90 s) and the container's `apt-get update` runs at most every
`PKG_APT_CHECK_HOURS` (6) hours. Refreshing apt on every check once held
`/var/lib/apt/lists/lock` for minutes while a mirror was mid-sync (404s and a
size mismatch), which turned every check into an error and every upgrade into a
lock wait.

The optional `bin/pkg-updates` Python bridge persists atomic JSON under
`~/.cache/pkg/updates.json`, serializes concurrent checks/upgrades with flock,
posts notifications on successful positive checks and launches the identical
upgrade action in Konsole. Even failed upgrades trigger a new check. Ordinary
overlapping checks skip; post-upgrade checks wait for the check lock and then
refresh, so an older background check cannot leave pre-upgrade counts behind. The user
systemd timer checks every 24/48 hours and at startup; the plasmoid polls only
cached state. Resetting `OnUnitActiveSec=` clears **all** monotonic timer triggers,
so the scheduling drop-in must also reinstate `OnStartupSec=10min`.

`install-updates.sh` is an optional separate installer: it deploys pkg, its bridge,
the plasmoid and user units without package installs, enables the timer and starts
a fresh asynchronous check. No running applet reloads its QML just because files
changed; recreate its panel instance or restart the owning plasmashell unit.

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

## Containers and environment detection

pkg runs from three places — the Fedora host, inside the Arch container, inside the
Ubuntu container — and it is the same file in all of them, because the containers
share `$HOME`. Which worlds it may touch is decided once, at startup:

```bash
CONTAINER="${PKG_CONTAINER:-Arch}"           # container names live here only
UBUNTU_CONTAINER="${PKG_UBUNTU_CONTAINER:-Ubuntu}"
HERE="${CONTAINER_ID:-}"                     # distrobox sets CONTAINER_ID
```

| where | Fedora | Arch | Ubuntu |
|---|---|---|---|
| host (`HERE` empty) | `dnf` + `rpm` present | `pacman` + `yay` shims present **and** the container is listed by `distrobox list` | the Ubuntu container is listed |
| inside Arch | — | native `pacman` + `yay` | — |
| inside Ubuntu | — | — | native `apt-get` + `dpkg` |
| inside another container | native `dnf`, if any | native `pacman`/`yay`, if any | native `apt-get`/`dpkg`, if any |

Downstream code never asks those questions again: it asks `has_fedora` /
`has_arch` / `has_apt`, `w_installed <world> <pkg>` and `w_has <world> <pkg>` (both
false for a world that is not here), and iterates `"${WORLDS[@]}"` in priority
order. `require_worlds` fails early and clearly when nothing is available at all.

**Why “native” has to be proved.** Inside a container, `command -v pacman` finds the
exported shim in `~/.local/bin`, which hops out to the host and then into the Arch
container — from inside Ubuntu, `pacman -Q fastfetch` answers about *Arch's* copy,
which is how `pkg add fastfetch` used to print `installed: fastfetch` while
installing nothing. So availability is probed with `native_cmd`, which walks `PATH`
**skipping** `$EXPORT_BIN_DIR`. That also means the probe cannot use `command -v`:
`$HOME/.local/bin` precedes `/usr/bin` in a container's PATH, so `command -v pacman`
inside Arch answers with the shim and the first version of this probe concluded "no
package manager available here" while standing in the Arch container.

`in_cont` runs a command **locally** when `$HERE` is that container, because
distrobox cannot nest — `distrobox enter Ubuntu` from inside Ubuntu fails with
"failed to execute process". That is what lets `pkg add <pkg>` inside a container
still export the binary or `.desktop` to the host, and `pkg drop` un-export it before
removing it.

Not every container call needs `in_cont`, though. The Arch world speaks through the
exported `pacman`/`yay` shims, whose own logic is: hop out only when `$CONTAINER_ID`
is unset or names a *different* container, otherwise `exec` the container's real
binary. So inside Arch those calls are already local (verified: `pacman -Q` inside
Arch is a native exec, while the same command inside Ubuntu takes the host hop), and
`in_cont` is used where a *specific* container must be addressed — previews, info,
`export`/`unexport`, teardown and the Ubuntu world, which has no apt shim.

`pacman`, `yay` and `apt` are also exported as shims in `~/.local/bin` so host-side
calls work without `distrobox enter`. Both containers have passwordless sudo
(distrobox writes `/etc/sudoers.d/<user>` with `NOPASSWD:ALL` at creation), so
container installs/removals are fully automatable — the Fedora host is the only world
that needs a password.

Cold start is slow: a stopped container makes the first `yay`/`pacman`/`apt` call
take seconds. The cached name lists mean only the preview and the install itself pay
that cost, and the parallel refresh overlaps the cold starts rather than adding them
up. When a container is unreachable, degrade with a warning instead of failing the
whole command.

One step is deliberately host-only: the `flatpak update` in `pkg upgrade`. Both
containers ship their own flatpak with their own system installation, so running it
there would update something other than the user's desktop apps; inside a container
`pkg upgrade` prints `skipping Flatpaks — host-side only`.

## Refreshing the lists in parallel

`ensure_lists` decides per world whether its list is stale, starts one background job
per world and `wait`s for them, so a refresh costs roughly the slowest world instead
of the sum:

| job | work | cost |
|---|---|---|
| Fedora | `dnf repoquery --available` on the host | ~0.6 s |
| Arch | `pacman -Slq` then `yay -Slqa` in the same container | ~0.3 s + ~3.5 s |
| Ubuntu | `in_ubuntu apt-cache pkgnames` | ~1.0 s incl. the entry |

Measured here forcing a full rebuild (`PKG_CACHE_TTL=0`): **6.71 s serial → 4.76 s
parallel**, and all four lists byte-identical to the serial build (`cmp`). The AUR
listing dominates, so that is the floor. The two Arch fetches stay in one job because
the AUR list is only meaningful diffed against a freshly fetched repo list
(`comm -23`), and because two concurrent `distrobox enter` calls into the same
container would race its start. Each job writes its own `.tmp` file and the parent
validates the size afterwards, so a job that dies still produces exactly the per-world
warning it did before (`dnf: could not refresh the Fedora list…`,
`Arch container/yay unreachable…`, `Ubuntu container unreachable…`).

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
- **A helper whose last statement is `[[ … ]] && return 1` returns 1 on the good
  path too.** `native_cmd` started that way and reported every world as unavailable
  inside a container: the `[[ … ]]` was false (correctly, the path was native), so
  the function's status came from that false test. End boolean-shaped helpers with an
  explicit `return 0`/`return 1`.
- **`command -v` is not an availability test inside a container**: `~/.local/bin`
  precedes `/usr/bin`, so it answers with a distrobox shim that acts on *another*
  container (see the detection section).
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

Container-side checks (no password needed, and each is also a regression test for
the world detection):

```bash
for c in Ubuntu Arch; do distrobox enter "$c" -- pkg help | grep Currently; done
distrobox enter Ubuntu -- pkg add -n fastfetch        # → apt-get install, never dnf
distrobox enter Ubuntu -- pkg add -n cowsay-off       # apt-only: installs here
distrobox enter Ubuntu -- pkg add -n fedora-release   # → not found + hint
distrobox enter Ubuntu -- pkg present cowsay          # apt world predicate
distrobox enter Arch   -- pkg add -n cowsay-off       # apt-only name → not found
distrobox enter Arch   -- pkg list apt                # → "not available here", exit 1
distrobox enter Ubuntu -- pkg add cbatticon           # install + export from inside
ls -l ~/.local/bin/cbatticon                          # shim created on the host
distrobox enter Ubuntu -- pkg drop cbatticon          # unexport + purge from inside
```

TUI inside a container: install tmux there first (`sudo apt-get install -y tmux`,
`sudo pacman -S --needed tmux`), drive it exactly as on the host, then purge tmux.
Expect the header to advertise only that container's prefixes, and Enter to produce a
single command line for the local manager.

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
