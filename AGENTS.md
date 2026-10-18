# AGENTS.md — pkg (pkg-meta-package-manager)

`pkg` is one bash script that treats three package worlds as a single namespace:

| world | tooling | entry prefix |
|---|---|---|
| Fedora host | `dnf` / `rpm` | `dnf/` |
| Arch container (distrobox) | `pacman` / `yay`, incl. the AUR | `arch/`, `aur/` |
| Ubuntu container (distrobox) | `apt` / `dpkg` | `apt/` |

Interactive fzf TUIs (`pkg install`, `pkg remove`) plus scriptable subcommands
(`add`, `drop`, `search`, `info`, `update`, `autoremove`, `clean`, `present`, `missing`,
`list`, `export`, `unexport`, `refresh`). This is a personal tool for *this* machine
(Fedora host + containers named `Arch` and `Ubuntu`) — not a portable cross-distro
abstraction. `docs/how-it-works.md` is the design document; read it before touching
routing or detection.

## Layout

| path | what |
|---|---|
| `bin/pkg` | the entire program (~1280 lines of bash) |
| `docs/how-it-works.md` | routing contract, data sources, detection, pitfalls, verification recipes |
| `install.sh` | rebuilds the whole setup on a fresh Fedora host (host packages, yay in the Arch container, shims, `~/.local/bin/pkg`, KRunner launchers, then a smoke test) |
| `desktop/pkg-{install,remove}.desktop` | KRunner launchers; `install.sh` substitutes `$HOME` into `Exec=` (desktop files can't expand it) |
| `README.md` | user-facing docs |

## Invariants — do not break these

- **Resolution order is the contract.** For every name: installed via rpm → skip ·
  in the Fedora repos → `dnf` · installed in Arch → skip · resolvable by `yay -Si` →
  `yay` · installed in Ubuntu → skip · in the Ubuntu archives → `apt` · else
  "not found", exit 1. Fedora wins because it is the only world whose packages get
  host updates; Arch is second for the AUR's ~119k extra names; Ubuntu fills the gap.
- **Only ever act in worlds reachable from where `pkg` runs.** Detection happens once
  at startup into `FEDORA_HERE` / `ARCH_HERE` / `APT_HERE`, and `pkg` walks only those.
  Inside a container that means that container's manager alone — `pkg add fastfetch`
  in Ubuntu must produce `apt-get install`, never `dnf`.
- **Never trust `command -v` inside a container.** `~/.local/bin` comes first in `PATH`
  and holds distrobox shims that hop *out* to the host and into *another*
  container (`pacman -Q fastfetch` inside Ubuntu answers for Arch). Use `native_cmd`,
  which skips the export dir, for every availability decision.
- **Worlds are batched.** One install command per world per invocation, never one call
  per package. After the batches, every requested name is re-verified with
  `rpm -q` / `pacman -Q` / `dpkg-query` and `pkg add` exits 1 if any is missing —
  installers can succeed without registering a package.
- **`present` / `missing` are predicates**: exit status only, no output beyond it.
  Scripts depend on that.
- **Name lists are cached** in `~/.cache/pkg` (TTL `PKG_CACHE_TTL`, default 24 h) and
  refreshed in parallel, one job per world. Never stamp a list written by a failed job
  — a killed fetch leaves a non-empty `.tmp` and would cache a truncated list as fresh.
  The AUR list is diffed against a freshly fetched repo list so repo packages are not
  double-listed.
- **Style:** single file, bash, `set -uo pipefail` (no `-e` — most calls are expected to
  fail), `LC_ALL=C`, no new dependencies outside dnf/pacman/yay/apt/fzf. Helpers that
  answer a yes/no question must end with an explicit `return 0`/`return 1`: a trailing
  `[[ … ]] && return 1` returns 1 on the good path too.
- **No secrets, no writes outside `~/.cache/pkg`, `~/.local/bin`, `~/.local/share/applications`
  and the three package managers.** Container sudo is passwordless; **host** sudo needs a
  real TTY — an agent-driven shell cannot borrow the user's timestamp. Run host-side
  installs in Konsole and let the user type the password.

## Testing

Nothing needs a human for the dry-run and container paths.

```bash
# routing, every branch, no side effects (2 installs, 1 skip, 1 not-found, exit 1)
pkg add -n cowsay cbonsai pipes.sh neovim zzz-nope
# → one dnf batch (cowsay, cbonsai), one yay batch (pipes.sh),
#   "already installed" (neovim), "not found" (zzz-nope)

pkg help | grep Currently           # what this shell can reach
pkg list installed | head           # cached lists + installed marks
pkg list dnf --json | head -3       # JSON Lines, one record per line

# regression test for world detection — must say apt, never dnf
distrobox enter Ubuntu -- pkg add -n fastfetch
distrobox enter Arch   -- pkg list apt      # → "not available here", exit 1
```

`PKG_DRY_RUN=1` prints the commands instead of running them — use it for anything that
would change the system. For a real container round-trip pick an Ubuntu-only name
computed with the `comm` recipe in `docs/how-it-works.md` (do not guess one — the AUR is
large). TUI checks are driven in tmux; shellcheck lives in the Ubuntu container so
nothing is installed on the host. Both recipes are in `docs/how-it-works.md`
("Verification recipe").

## Pitfalls already paid for

- `--pointer=green` aborts fzf ("pointer display width should be up to 2") — colours go
  through `--color pointer:…,marker:…`; `--pointer` sets a marker *string*.
- Marking installed with awk: pass the installed file as `-v inf=` and both files as
  arguments (`FILENAME==inf`); the same path twice emits nothing, and `NR==FNR`
  swallows the first names line when the installed file is empty.
- JSON emitters must end every format string with `\n` — JSON Lines, one object per line.
- `yay -Rns` and `apt-get autoremove` remove pulled-in dependencies; removing one package
  can drop a dep another app merely optionally needs.
- `in_cont_each` payloads are single-quoted on purpose so the *container's* bash expands
  `"$@"`. shellcheck flags them (SC2016); that is intended.

## Conventions

- Commits: `pkg: <imperative summary>`, e.g. `pkg: use only the worlds reachable from where it runs`.
- Docs and behaviour move together: routing or detection changes go into
  `docs/how-it-works.md` and `pkg help` in the same commit.
- After editing the `.desktop` files, re-run `install.sh` (or copy them yourself) and
  `kbuildsycoca6`.
