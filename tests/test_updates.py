"""CLI contract tests with deterministic package-manager executables, no installs."""
import json
import os
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
    args = [a for a in args if a != '-n']
    os.execvp(args[0], args)
if name == 'dnf':
    if os.environ.get('FAIL_DNF'):
        print('repository unavailable', file=sys.stderr)
        sys.exit(1)
    if 'repoquery' in args:
        print('alpha.x86_64 2-1\nalpha.x86_64 2-1\nbeta.noarch 3-1')
    else:
        print('MUTATING DNF CALL', file=sys.stderr)
        sys.exit(99)
if name == 'fakeroot':
    sys.exit(0)  # isolated metadata refresh only
if name == 'pacman' and '-Qu' in args:
    print('arch-one 1 -> 2\narch-two 1 -> 2')
if name == 'yay':
    if '-Qua' in args:
        if os.environ.get('EMPTY_AUR'):
            sys.exit(1)  # yay's normal no-updates status
        print('aur-one 1 -> 2')
    else:
        sys.exit(99)
if name == 'apt-get':
    assert 'update' in args and 'upgrade' not in args, args
    print('metadata refreshed')
if name == 'apt':
    print('Listing...\nubuntu-one/release 2 amd64 [upgradable from: 1]')
if name == 'flatpak':
    if args[0] == 'remote-ls':
        if os.environ.get('EXTENSION_ONLY_FLATPAK'):
            if '--all' in args:
                print('runtime/org.example.App.Locale/x86_64/stable\nruntime/org.example.App.Debug/x86_64/stable')
                if '--arch=*' in args:
                    print('runtime/org.example.App.Locale/aarch64/stable')
        else:
            print('app/org.example.App/x86_64/stable\nruntime/org.example.Runtime/x86_64/stable')
    else:
        sys.exit(99)
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
                        PKG_TEST_LOG=str(self.dir / 'calls'), CONTAINER_ID='')

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
        self.assertIn('sudo -n apt-get', calls)

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
        for mode in ('--count', '--counts', '--json'):
            p = self.run_pkg('update', mode, FAIL_DNF='1')
            self.assertEqual(p.returncode, 1)
            self.assertIn('repository unavailable', p.stderr)
            if mode == '--json':
                data = json.loads(p.stdout)
                self.assertFalse(data['ok'])
                self.assertIsNone(data['counts']['dnf'])
                self.assertEqual(data['status']['dnf'], 'error')
            else:
                self.assertEqual(p.stdout, '')

    def test_no_aur_updates_is_success(self):
        p = self.run_pkg('update', '--counts', EMPTY_AUR='1')
        self.assertEqual((p.returncode, p.stdout), (0, '2 2 1 2\n'), p.stderr)

    def test_inside_ubuntu_skips_host_and_arch(self):
        p = self.run_pkg('update', '--json', CONTAINER_ID='Ubuntu')
        data = json.loads(p.stdout)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(data['counts'], dict(dnf=0, arch=0, ubuntu=1, flatpak=0))
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

    def test_upgrade_dry_run_is_still_all_four_backends(self):
        p = self.run_pkg('upgrade', '-n')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('dnf upgrade --refresh', p.stderr)
        self.assertIn('yay -Syu', p.stderr)
        self.assertIn('apt-get upgrade', p.stderr)
        self.assertIn('flatpak update', p.stderr)


if __name__ == '__main__':
    unittest.main()
