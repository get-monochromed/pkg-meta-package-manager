"""CLI contract tests with deterministic package-manager executables, no installs."""
import json
import os
import time
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
STUB = r'''#!/usr/bin/python3
import os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['PKG_TEST_LOG'], 'a') as f:
    f.write(name + ' ' + ' '.join(args) + '\n')
if name == 'distrobox':
    if args == ['list']:
        print('ID | NAME | STATUS\n1 | Arch | Up\n2 | Ubuntu | Up')
        sys.exit(0)
    os.execvp(args[3], args[3:])
if name == 'sudo':
    # -v: the interactive prompt (succeeds unless AUTH_OK=0)
    # -n: cached-credential probe (succeeds only when SUDO_CACHED, as inside
    #     the containers, which have passwordless sudo)
    cached = bool(os.environ.get('SUDO_CACHED'))
    if args == ['-v'] or args == ['-n', '-v']:
        ok = os.environ.get('AUTH_OK', '1') == '1' and (args == ['-v'] or cached)
        print('[sudo] authenticated' if ok else 'sudo: a password is required',
              file=sys.stderr)
        sys.exit(0 if ok else 1)
    rest = [a for a in args if a not in ('-n', '-S', '-v')]
    if '-n' in args and not cached:
        print('sudo: a password is required', file=sys.stderr)
        sys.exit(1)
    if os.environ.get('FAIL_SUDO'):
        print('sudo: a password is required', file=sys.stderr)
        sys.exit(1)
    os.execvp(rest[0], rest)
if name == 'dnf':
    if os.environ.get('FAIL_DNF'):
        print('repository unavailable', file=sys.stderr)
        sys.exit(1)
    if os.environ.get('FAIL_DNF_REFRESH') and '--refresh' in args:
        # dies mid-output: the truncated line must never be counted
        sys.stdout.write('alpha.x86_64 2-1\npar')
        print('\r  cache expired\terror  \t', file=sys.stderr)
        sys.exit(1)
    if os.environ.get('FAIL_DNF_BOTH') and 'repoquery' in args:
        sys.stdout.write('alpha.x86_64 2-1\npar')
        sys.exit(1)
    if 'repoquery' in args:
        print('alpha.x86_64 2-1\nalpha.x86_64 2-1\nbeta.noarch 3-1')
    elif 'upgrade' in args:
        print('dnf: transaction complete')
    else:
        print('MUTATING DNF CALL', file=sys.stderr)
        sys.exit(99)
if name == 'fakeroot':
    # fakeroot runs its command; the isolated pacman sync is the only user
    os.execvp(args[0], args)
if name == 'pacman' and '-Sy' in args and os.environ.get('FAIL_ARCH'):
    print('error: failed retrieving file from mirror', file=sys.stderr)
    sys.exit(1)
if name == 'pacman' and '-Qu' in args:
    print('arch-one 1 -> 2\narch-two 1 -> 2')
if name == 'yay':
    if '-Qua' in args:
        if os.environ.get('EMPTY_AUR'):
            sys.exit(1)  # yay's normal no-updates status
        print('aur-one 1 -> 2')
    elif '-Syu' in args:
        print(':: nothing to do')
    else:
        sys.exit(99)
if name == 'apt-get':
    if os.environ.get('FAIL_APT_UPDATE') and 'update' in args:
        print('E: Failed to fetch ... 404  Not Found', file=sys.stderr)
        sys.exit(100)
    print('apt-get ' + ' '.join(args))
if name == 'apt':
    if os.environ.get('FAIL_APT'):
        print('E: Unable to lock directory /var/lib/apt/lists/', file=sys.stderr)
        sys.exit(100)
    print('Listing...\nubuntu-one/release 2 amd64 [upgradable from: 1]')
if name == 'flatpak':
    if os.environ.get('FAIL_FLATPAK'):
        print('error: no remote refs found', file=sys.stderr)
        sys.exit(1)
    if args[0] == 'remote-ls':
        if os.environ.get('EXTENSION_ONLY_FLATPAK'):
            if '--all' in args:
                print('runtime/org.example.App.Locale/x86_64/stable\nruntime/org.example.App.Debug/x86_64/stable')
                if '--arch=*' in args:
                    print('runtime/org.example.App.Locale/aarch64/stable')
        else:
            print('app/org.example.App/x86_64/stable\nruntime/org.example.Runtime/x86_64/stable')
    elif args[0] == 'update':
        print('Nothing to do.')
'''


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        stub = self.dir / 'stub'
        stub.write_text(STUB)
        stub.chmod(0o755)
        for name in ('dnf', 'rpm', 'distrobox', 'pacman', 'yay', 'sudo',
                     'fakeroot', 'apt-get', 'apt', 'dpkg', 'flatpak'):
            (self.dir / name).symlink_to(stub)
        self.env = dict(os.environ, PATH=f'{self.dir}:/usr/bin:/bin',
                        XDG_CACHE_HOME=str(self.dir / 'cache'),
                        PKG_TEST_LOG=str(self.dir / 'calls'), CONTAINER_ID='',
                        SUDO_CACHED='1')  # inside the containers sudo is passwordless

    def apt_lists(self, hours_old=48):
        """Point the container's index probe at a fixture (the real one is read-only)."""
        d = self.dir / 'apt-lists'
        d.mkdir(exist_ok=True)
        f = d / 'resolute-updates_Packages'
        f.write_text('x')
        stamp = time.time() - hours_old * 3600
        os.utime(f, (stamp, stamp))
        return d

    def run_pkg(self, *args, **env):
        return subprocess.run([str(ROOT / 'bin/pkg'), *args],
                              env=self.env | env, text=True, capture_output=True,
                              timeout=30)

    def test_counts_are_only_four_numbers_and_never_upgrade(self):
        p = self.run_pkg('update', '--counts')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout, '2 3 1 2\n')
        calls = (self.dir / 'calls').read_text()
        self.assertNotIn('dnf upgrade', calls)
        self.assertNotIn('yay -Syu', calls)
        self.assertNotIn('flatpak update', calls)
        self.assertIn('fakeroot pacman', calls)
        self.assertIn('sudo -n env DEBIAN_FRONTEND=noninteractive apt-get', calls)

    def test_flatpak_extension_only_updates_include_all_arches(self):
        p = self.run_pkg('update', '--json', EXTENSION_ONLY_FLATPAK='1')
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertTrue(data['ok'])
        self.assertEqual(data['counts']['flatpak'], 3)
        calls = (self.dir / 'calls').read_text().splitlines()
        flatpak_call, = [line for line in calls if line.startswith('flatpak remote-ls ')]
        for flag in ('--updates', '--columns=ref', '--all', '--arch=*'):
            self.assertIn(flag, flatpak_call.split())

    def test_total_and_json(self):
        p = self.run_pkg('update', '--count')
        self.assertEqual((p.returncode, p.stdout), (0, '8\n'), p.stderr)
        p = self.run_pkg('update', '--json')
        data = json.loads(p.stdout)
        self.assertTrue(data['ok'])
        self.assertEqual(data['counts'], dict(dnf=2, arch=3, ubuntu=1, flatpak=2))
        self.assertEqual(data['total'], 8)

    def test_failed_check_is_not_zero(self):
        """A failed world is reported as unknown, never as zero."""
        for mode in ('--count', '--counts', '--json'):
            p = self.run_pkg('update', mode, FAIL_DNF='1')
            self.assertEqual(p.returncode, 1)
            self.assertIn('repository unavailable', p.stderr)
            if mode == '--json':
                data = json.loads(p.stdout)
                self.assertFalse(data['ok'])
                self.assertIsNone(data['counts']['dnf'])
                self.assertEqual(data['status']['dnf'], 'error')
            elif mode == '--counts':
                self.assertEqual(p.stdout, '? 3 1 2\n')
            else:
                self.assertEqual(p.stdout, '6\n')  # known subtotal, exit 1

    def test_no_aur_updates_is_success(self):
        p = self.run_pkg('update', '--counts', EMPTY_AUR='1')
        self.assertEqual((p.returncode, p.stdout), (0, '2 2 1 2\n'), p.stderr)

    def test_inside_ubuntu_skips_host_and_arch(self):
        p = self.run_pkg('update', '--json', CONTAINER_ID='Ubuntu')
        data = json.loads(p.stdout)
        self.assertEqual(p.returncode, 0, p.stderr)
        # unreachable sources are unknown, not zero
        self.assertEqual(data['counts'], dict(dnf=None, arch=None, ubuntu=1, flatpak=None))
        self.assertEqual(data['status']['flatpak'], 'unavailable')

    def test_human_output_and_option_errors(self):
        p = self.run_pkg('update')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('alpha.x86_64 2-1', p.stdout)
        self.assertIn('Total: 8', p.stdout)
        for args in [('--wat',), ('--counts', '--json')]:
            p = self.run_pkg('update', *args)
            self.assertNotEqual(p.returncode, 0)
            self.assertEqual(p.stdout, '')

    def test_one_failing_world_is_degraded_not_a_total_failure(self):
        """A broken mirror must not hide the counts the other worlds did produce."""
        p = self.run_pkg('update', '--json', FAIL_FLATPAK='1')
        data = json.loads(p.stdout)
        self.assertEqual(p.returncode, 1)  # still not "all checked"
        self.assertFalse(data['ok'])
        self.assertTrue(data['degraded'])
        self.assertIsNone(data['counts']['flatpak'])
        self.assertEqual(data['status']['flatpak'], 'error')
        self.assertEqual(data['counts']['dnf'], 2)
        self.assertIn('flatpak', data['problems'])
        self.assertIn('no remote refs', data['problems']['flatpak'])

    def test_total_failure_is_not_degraded(self):
        p = self.run_pkg('update', '--json', FAIL_DNF='1', FAIL_ARCH='1',
                         FAIL_APT='1', FAIL_FLATPAK='1')
        data = json.loads(p.stdout)
        self.assertEqual(p.returncode, 1)
        self.assertFalse(data['degraded'])
        self.assertFalse(data['ok'])
        self.assertEqual(set(data['counts'].values()), {None})
        self.assertEqual(data['status'], dict(dnf='error', arch='error',
                                              ubuntu='error', flatpak='error'))

    def test_apt_refresh_failure_falls_back_to_the_cached_index(self):
        lists = self.apt_lists(hours_old=48)
        p = self.run_pkg('update', '--json', FAIL_APT_UPDATE='1',
                         PKG_APT_LISTS_DIR=str(lists))
        data = json.loads(p.stdout)
        self.assertEqual(p.returncode, 1)
        self.assertEqual(data['status']['ubuntu'], 'stale')
        self.assertEqual(data['counts']['ubuntu'], 1)  # counted from the last index
        self.assertIn('ubuntu', data['problems'])

    def test_apt_with_no_index_at_all_is_an_error_not_a_stale_zero(self):
        empty = self.dir / 'no-apt-lists'
        empty.mkdir()
        p = self.run_pkg('update', '--json', FAIL_APT_UPDATE='1',
                         PKG_APT_LISTS_DIR=str(empty))
        data = json.loads(p.stdout)
        self.assertEqual(p.returncode, 1)
        self.assertEqual(data['status']['ubuntu'], 'error')   # not 'stale'
        self.assertIsNone(data['counts']['ubuntu'])           # `?`, never 0
        counts = self.run_pkg('update', '--counts', FAIL_APT_UPDATE='1',
                              PKG_APT_LISTS_DIR=str(empty))
        self.assertEqual(counts.stdout.split()[2], '?')

    def test_dnf_stale_fallback_counts_only_real_cache_output(self):
        """A refresh that dies mid-line must not contribute a phantom update."""
        p = self.run_pkg('update', '--json', FAIL_DNF_REFRESH='1')
        data = json.loads(p.stdout)
        self.assertEqual(data['status']['dnf'], 'stale')
        self.assertEqual(data['counts']['dnf'], 2)  # alpha + beta, not the 'par' fragment

    def test_dnf_with_no_usable_cache_is_an_error(self):
        p = self.run_pkg('update', '--json', FAIL_DNF_BOTH='1')
        data = json.loads(p.stdout)
        self.assertEqual(p.returncode, 1)
        self.assertEqual(data['status']['dnf'], 'error')
        self.assertIsNone(data['counts']['dnf'])

    def test_control_characters_in_error_text_stay_valid_json(self):
        p = self.run_pkg('update', '--json', FAIL_DNF_REFRESH='1')
        data = json.loads(p.stdout)  # would raise on an unescaped control char
        self.assertIn('dnf', data['problems'])

    def test_no_reachable_world_is_a_failure_not_zero_updates(self):
        minimal = self.dir / 'minbin'
        minimal.mkdir()
        for exe in ('bash', 'readlink', 'mktemp', 'date', 'awk', 'grep', 'rm', 'sed', 'tr', 'head'):
            (minimal / exe).symlink_to(f'/usr/bin/{exe}')
        p = self.run_pkg('update', '--json', PATH=str(minimal))
        data = json.loads(p.stdout)
        self.assertEqual(p.returncode, 1)
        self.assertFalse(data['ok'])
        self.assertFalse(data['degraded'])
        self.assertEqual(set(data['counts'].values()), {None})
        count = self.run_pkg('update', '--count', PATH=str(minimal))
        self.assertEqual(count.returncode, 1)

    def test_numeric_modes_report_unknown_worlds_without_lying(self):
        p = self.run_pkg('update', '--counts', FAIL_FLATPAK='1')
        self.assertEqual(p.returncode, 1)
        self.assertEqual(p.stdout, '2 3 1 ?\n')
        p = self.run_pkg('update', '--count', FAIL_FLATPAK='1')
        self.assertEqual((p.returncode, p.stdout), (1, '6\n'))

    def test_upgrade_authenticates_first_and_reports_each_world(self):
        p = self.run_pkg('upgrade', SUDO_CACHED='')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('AUTHENTICATE NOW', p.stdout)
        calls = (self.dir / 'calls').read_text()
        self.assertIn('sudo -v', calls)
        for world in ('dnf', 'arch', 'ubuntu', 'flatpak'):
            self.assertIn(world, p.stdout.lower())
        self.assertIn('upgraded', p.stdout.lower())

    def test_upgrade_stops_before_touching_anything_when_auth_fails(self):
        p = self.run_pkg('upgrade', AUTH_OK='0', SUDO_CACHED='')
        self.assertEqual(p.returncode, 1)
        self.assertIn('nothing was upgraded', p.stderr.lower())
        self.assertIn('AUTHENTICATE NOW', p.stdout)
        self.assertNotIn('upgraded:', p.stdout)
        self.assertNotIn('dnf upgrade', (self.dir / 'calls').read_text())

    def test_upgrade_reports_a_failed_world_without_hiding_the_others(self):
        p = self.run_pkg('upgrade', FAIL_SUDO='1', SUDO_CACHED='')
        self.assertEqual(p.returncode, 1)
        self.assertIn('sudo: a password is required', p.stderr)  # real cause shown
        self.assertIn('FAILED:', p.stdout)
        self.assertIn('dnf=FAILED', p.stdout)
        self.assertNotIn('upgraded:', p.stdout)

    def test_upgrade_dry_run_is_still_all_four_backends(self):
        p = self.run_pkg('upgrade', '-n')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('dnf upgrade --refresh', p.stderr)
        self.assertIn('yay -Syu', p.stderr)
        self.assertIn('apt-get upgrade', p.stderr)
        self.assertIn('flatpak update', p.stderr)


if __name__ == '__main__':
    unittest.main()
