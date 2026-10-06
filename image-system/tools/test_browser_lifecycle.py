"""Real browser ownership regression checks. Never attach to a user browser."""
from pathlib import Path
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import shutil

from browser_lifecycle import OwnedBrowser, profile_processes
from browser_profile_checkpoint import checkpoint_command, owned_directory, MARKER


class BrowserLifecycleTests(unittest.TestCase):
    def profile(self):
        return Path(tempfile.mkdtemp(prefix='aag-browser-test-'))

    def args(self, profile):
        return ['/usr/bin/brave-browser', '--headless=new', '--no-sandbox',
                '--disable-gpu', '--disable-dev-shm-usage', '--no-first-run',
                '--remote-allow-origins=*', f'--user-data-dir={profile}', 'about:blank']

    def clean(self, profile):
        self.assertFalse(profile_processes(profile))
        self.assertFalse(profile.exists())

    def test_success_and_unrelated_browser_preserved(self):
        unrelated, profile, downloads = self.profile(), self.profile(), self.profile()
        with OwnedBrowser(self.args(unrelated)) as other:
            other.target()
            port = other.debug_port
            with OwnedBrowser(self.args(profile), resources=[downloads]) as owned:
                owned.target()
                self.assertNotEqual(port, owned.debug_port)
            self.clean(profile)
            self.clean(downloads)
            self.assertIsNone(other.poll())
            self.assertEqual(port, other.debug_port)
            self.assertTrue(other.target())
        self.clean(unrelated)

    def test_assertion_failure(self):
        profile = self.profile()
        with self.assertRaises(AssertionError):
            with OwnedBrowser(self.args(profile)) as owned:
                owned.target()
                raise AssertionError('injected assertion failure')
        self.clean(profile)

    def test_exception_during_setup(self):
        profile = self.profile()
        with self.assertRaises(ValueError):
            with OwnedBrowser(self.args(profile)):
                raise ValueError('injected exception before CDP connects')
        self.clean(profile)

    def test_browser_launch_failure(self):
        profile = self.profile()
        with OwnedBrowser(['/nonexistent/browser', f'--user-data-dir={profile}']) as owned:
            with self.assertRaises(RuntimeError):
                owned.target()
        self.clean(profile)

    def test_hard_timeout(self):
        profile = self.profile()
        with OwnedBrowser(self.args(profile), timeout=.5) as owned:
            owned.wait(timeout=8)
        self.clean(profile)

    def test_abort_and_killed_test_owner(self):
        for sig in [signal.SIGTERM, signal.SIGKILL]:
            with self.subTest(signal=sig):
                profile = self.profile()
                code = '''import sys,time
from browser_lifecycle import OwnedBrowser
b=OwnedBrowser(%r, timeout=60)
b.target()
print('READY',flush=True)
time.sleep(60)
''' % self.args(profile)
                owner = subprocess.Popen([sys.executable, '-c', code], cwd=Path(__file__).parent,
                                         stdout=subprocess.PIPE, text=True)
                try:
                    import select
                    self.assertTrue(select.select([owner.stdout], [], [], 25)[0])
                    self.assertEqual(owner.stdout.readline().strip(), 'READY')
                    owner.send_signal(sig)
                    owner.wait(timeout=5)
                    deadline = time.monotonic() + 8
                    while (profile.exists() or profile_processes(profile)) and time.monotonic() < deadline:
                        time.sleep(.05)
                    self.clean(profile)
                finally:
                    if owner.poll() is None:
                        owner.kill()
                        owner.wait(timeout=5)
                    owner.stdout.close()

    def test_owned_child_tree_ignoring_term(self):
        profile = self.profile()
        code = "import subprocess,sys,signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)',next(s for s in sys.argv if s.startswith('--user-data-dir='))]);time.sleep(60)"
        with OwnedBrowser([sys.executable, '-c', code, f'--user-data-dir={profile}']) as owned:
            deadline = time.monotonic() + 5
            while len(profile_processes(profile)) < 2 and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertGreaterEqual(len(profile_processes(profile)), 2)
        self.clean(profile)

    def test_checkpoint_rejects_unowned_and_nonempty_destinations(self):
        source, destination = self.profile(), self.profile()
        try:
            with self.assertRaises(ValueError):
                owned_directory(Path.home())
            with self.assertRaises(ValueError):
                checkpoint_command(self.args(source), source)
            (destination / 'preserved').write_text('Do not overwrite')
            with self.assertRaises(ValueError):
                checkpoint_command(self.args(source), destination)
            self.assertEqual((destination / 'preserved').read_text(), 'Do not overwrite')
        finally:
            shutil.rmtree(source); shutil.rmtree(destination)

    def test_checkpoint_launcher_abort_remains_owned(self):
        profile, destination = self.profile(), self.profile()
        with self.assertRaises(AssertionError):
            with OwnedBrowser(checkpoint_command(self.args(profile), destination), resources=[destination]) as owned:
                owned.target()
                raise AssertionError('Abort before clean browser shutdown')
        self.clean(profile); self.clean(destination)

    def test_checkpoint_after_clean_browser_shutdown(self):
        import json
        import websocket
        profile, destination = self.profile(), self.profile()
        try:
            with OwnedBrowser(checkpoint_command(self.args(profile), destination)) as owned:
                socket = owned.attach(websocket.create_connection(owned.target()['webSocketDebuggerUrl'], origin='http://127.0.0.1'))
                socket.send(json.dumps({'id':1, 'method':'Browser.close'}))
                self.assertEqual(json.loads(socket.recv())['id'], 1)
                owned.wait(timeout=20)
                self.assertTrue((destination / MARKER).is_file())
                self.assertTrue((destination / 'Default').is_dir())
            self.clean(profile)
        finally:
            shutil.rmtree(destination, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
