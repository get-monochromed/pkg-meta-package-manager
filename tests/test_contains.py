"""Read-only integration tests; run on the configured Fedora/distrobox machine."""
import pathlib
import subprocess
import unittest

PKG = str(pathlib.Path(__file__).resolve().parents[1] / 'bin/pkg')


class ContainsTests(unittest.TestCase):
    def run_pkg(self, *args, container=None):
        cmd = [PKG, 'contains', *args]
        if container:
            cmd = ['distrobox', 'enter', container, '--', *cmd]
        return subprocess.run(cmd, text=True, capture_output=True, timeout=120)

    def test_fedora_command_names(self):
        result = self.run_pkg('bash')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('bash', result.stdout.splitlines())
        self.assertNotIn('/', result.stdout)
        self.assertEqual(len(result.stdout.splitlines()), len(set(result.stdout.splitlines())))


    def test_full_fedora_output(self):
        expected = subprocess.run(['dnf', '-q', 'repoquery', '-l', 'bash'],
                                  text=True, capture_output=True, check=True).stdout
        for args in [('bash', '--all'), ('--all', 'bash')]:
            result = self.run_pkg(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, expected)

    def test_container_commands_and_full_lists(self):
        for container, backend in [('Arch', ['pacman', '-Qlq', 'bash']),
                                   ('Ubuntu', ['dpkg-query', '-L', 'bash'])]:
            with self.subTest(container=container):
                names = self.run_pkg('bash', container=container)
                self.assertEqual(names.returncode, 0, names.stderr)
                self.assertIn('bash', names.stdout.splitlines())
                self.assertNotIn('/', names.stdout)
                full = self.run_pkg('--all', 'bash', container=container)
                expected = subprocess.run(['distrobox', 'enter', container, '--', *backend],
                                          text=True, capture_output=True, check=True).stdout
                self.assertEqual(full.returncode, 0, full.stderr)
                self.assertEqual(full.stdout, expected)

    def test_uninstalled_arch_repo_package(self):
        installed = subprocess.run(['pacman', '-Q', 'cbatticon'], capture_output=True)
        self.assertNotEqual(installed.returncode, 0, 'test requires cbatticon not installed')
        result = self.run_pkg('cbatticon', container='Arch')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'cbatticon\n')
        full = self.run_pkg('--all', 'cbatticon', container='Arch')
        self.assertEqual(full.returncode, 0, full.stderr)
        self.assertIn('/usr/bin/cbatticon', full.stdout.splitlines())
        self.assertIn('/usr/share/', full.stdout)

    def test_invalid_requests_fail(self):
        for args in [(), ('--oops', 'bash'), ('hermes-nonexistent-package-xyz',)]:
            with self.subTest(args=args):
                result = self.run_pkg(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
