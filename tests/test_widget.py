"""Exercise the widget's real headless state and terminal launch interface."""
import fcntl
import json
import os
from pathlib import Path
import select
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'bin/pkg-updates'


class WidgetTests(unittest.TestCase):
    def test_check_persists_state_and_launch_uses_konsole(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            pkg = d / 'pkg'
            pkg.write_text('#!/bin/sh\nprintf \'%s\\n\' \'{"ok":true,"checked_at":123,"total":14,"counts":{"dnf":5,"arch":3,"ubuntu":2,"flatpak":4},"status":{"dnf":"ok","arch":"ok","ubuntu":"ok","flatpak":"ok"}}\'\n')
            pkg.chmod(0o755)
            env = dict(os.environ, PKG_UPDATES_PKG=str(pkg))
            args = [str(HELPER), '--state-file', str(d / 'state.json')]
            p = subprocess.run([*args, 'check', '--no-notify'], env=env,
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            p = subprocess.run([*args, 'state'], env=env, capture_output=True, text=True)
            data = json.loads(p.stdout)
            self.assertEqual(data['counts'], dict(dnf=5, arch=3, ubuntu=2, flatpak=4))
            p = subprocess.run([*args, 'launch', '--dry-run'], env=env,
                               capture_output=True, text=True)
            command = json.loads(p.stdout)
            self.assertEqual(command[:3], ['konsole', '--hold', '-e'])
            self.assertEqual(command[-1], 'upgrade')

    def test_invalid_backend_output_is_an_error_not_zero(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            pkg = d / 'pkg'
            pkg.write_text('#!/bin/sh\nprintf "not json\\n"\n')
            pkg.chmod(0o755)
            env = dict(os.environ, PKG_UPDATES_PKG=str(pkg))
            p = subprocess.run([str(HELPER), '--state-file', str(d / 'state.json'),
                                'check', '--no-notify'], env=env, capture_output=True, text=True)
            self.assertEqual(p.returncode, 1)
            data = json.loads((d / 'state.json').read_text())
            self.assertFalse(data['ok'])
            self.assertIsNone(data['counts']['dnf'])
            self.assertIn('error', data)

    def test_upgrade_refreshes_counts_afterwards(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            pkg = d / 'pkg'
            pkg.write_text('''#!/usr/bin/python3
import json, pathlib, sys
with pathlib.Path(__file__).with_name('calls').open('a') as f:
    f.write(' '.join(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'update':
    print(json.dumps(dict(ok=True, checked_at=123, total=0,
        counts=dict(dnf=0, arch=0, ubuntu=0, flatpak=0),
        status=dict(dnf='ok', arch='ok', ubuntu='ok', flatpak='ok'))))
else:
    sys.exit(7)
''')
            pkg.chmod(0o755)
            p = subprocess.run([str(HELPER), '--state-file', str(d / 'state.json'),
                                'upgrade'], env=dict(os.environ, PKG_UPDATES_PKG=str(pkg)),
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 7)
            self.assertEqual((d / 'calls').read_text(), 'upgrade\nupdate --json\n')
            self.assertEqual(json.loads((d / 'state.json').read_text())['total'], 0)

    def test_upgrade_waits_for_check_lock_before_refreshing(self):
        for upgrade_rc in (0, 7):
            with self.subTest(upgrade_rc=upgrade_rc), tempfile.TemporaryDirectory() as d:
                d = Path(d)
                state = d / 'state.json'
                pkg = d / 'pkg'
                pkg.write_text('''#!/usr/bin/python3
import json, os, pathlib, sys
with pathlib.Path(__file__).with_name('calls').open('a') as f:
    f.write(' '.join(sys.argv[1:]) + '\\n')
if sys.argv[1:] == ['upgrade']:
    print('upgrade-finished', flush=True)
    sys.exit(int(os.environ['UPGRADE_RC']))
assert sys.argv[1:] == ['update', '--json'], sys.argv
print(json.dumps(dict(ok=True, checked_at=456, total=0,
    counts=dict(dnf=0, arch=0, ubuntu=0, flatpak=0),
    status=dict(dnf='ok', arch='ok', ubuntu='ok', flatpak='ok'))))
''')
                pkg.chmod(0o755)
                env = dict(os.environ, PKG_UPDATES_PKG=str(pkg),
                           UPGRADE_RC=str(upgrade_rc))
                with state.with_suffix('.lock').open('w') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    with subprocess.Popen([str(HELPER), '--state-file', str(state),
                                           'upgrade'], env=env, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, text=True) as p:
                        try:
                            assert p.stdout is not None
                            self.assertTrue(select.select([p.stdout], [], [], 5)[0],
                                            'stub upgrade did not run')
                            self.assertEqual(p.stdout.readline(), 'upgrade-finished\n')
                            with self.assertRaises(subprocess.TimeoutExpired):
                                p.wait(timeout=0.3)
                            self.assertEqual((d / 'calls').read_text(), 'upgrade\n')
                            # An older scheduled check finishes while holding the lock.
                            state.write_text(json.dumps(dict(ok=True, checked_at=123,
                                total=14, counts=dict(dnf=5, arch=3, ubuntu=2, flatpak=4))))
                        finally:
                            fcntl.flock(lock, fcntl.LOCK_UN)
                        _, stderr = p.communicate(timeout=5)
                self.assertEqual(p.returncode, upgrade_rc, stderr)
                self.assertEqual((d / 'calls').read_text(), 'upgrade\nupdate --json\n')
                data = json.loads(state.read_text())
                self.assertEqual(data['checked_at'], 456)
                self.assertEqual(data['total'], 0)
                self.assertEqual(data['counts'], dict(dnf=0, arch=0, ubuntu=0, flatpak=0))

    def test_notification_lists_all_four_counts(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            pkg = d / 'pkg'
            pkg.write_text('''#!/usr/bin/python3
import json
print(json.dumps(dict(ok=True, checked_at=123, total=14,
    counts=dict(dnf=5, arch=3, ubuntu=2, flatpak=4),
    status=dict(dnf='ok', arch='ok', ubuntu='ok', flatpak='ok'))))
''')
            notify = d / 'notify-send'
            notify.write_text('''#!/usr/bin/python3
import json, pathlib, sys
pathlib.Path(__file__).with_name('notification.json').write_text(json.dumps(sys.argv[1:]))
''')
            pkg.chmod(0o755)
            notify.chmod(0o755)
            env = dict(os.environ, PKG_UPDATES_PKG=str(pkg), PATH=f'{d}:/usr/bin:/bin')
            p = subprocess.run([str(HELPER), '--state-file', str(d / 'state.json'),
                                'check'], env=env, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            args = json.loads((d / 'notification.json').read_text())
            self.assertIn('14 package updates available', args)
            for text in ('DNF: 5', 'Arch/AUR: 3', 'Ubuntu: 2', 'Flatpak: 4'):
                self.assertIn(text, args[-1])

    def test_schedule_keeps_startup_check_after_interval_reset(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            systemctl = d / 'systemctl'
            systemctl.write_text('#!/bin/sh\nexit 0\n')
            systemctl.chmod(0o755)
            env = dict(os.environ, XDG_CONFIG_HOME=str(d), PATH=f'{d}:/usr/bin:/bin')
            p = subprocess.run([str(HELPER), 'schedule', '--hours', '48'], env=env,
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            config = (d / 'systemd/user/pkg-updates.timer.d/interval.conf').read_text()
            self.assertIn('OnStartupSec=10min', config)
            self.assertIn('OnUnitActiveSec=48h', config)


if __name__ == '__main__':
    unittest.main()
