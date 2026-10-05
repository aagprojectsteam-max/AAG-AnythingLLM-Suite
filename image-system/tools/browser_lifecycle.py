"""Own one test browser, its process group, debug port and temporary profile.

The supervisor owns the browser even if the test is killed or fails during
setup. EOF on the private pipe or the hard deadline always triggers cleanup.
No executable-name or user-wide process killing is used.
"""
from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time


def profile_processes(profile):
    marker = "--user-data-dir=" + str(Path(profile).resolve())
    found = []
    for entry in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            if marker.encode() in entry.read_bytes().split(b'\0'):
                found.append(int(entry.parent.name))
        except (OSError, ValueError):
            pass
    return found


def _supervise(config_path, owner_fd):
    config = json.loads(Path(config_path).read_text())
    profile = Path(config['profile'])
    proc = None
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, stop)
    try:
        # Separate session gives this browser and all Chromium children an
        # exclusive process group, distinct from the test and user's browser.
        proc = subprocess.Popen(config['args'], start_new_session=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + config['timeout']
        while not stopped and proc.poll() is None and time.monotonic() < deadline:
            readable, _, _ = select.select([owner_fd], [], [], .1)
            if readable and not os.read(owner_fd, 1):
                break
    finally:
        if proc is not None:
            for sig, grace in ((signal.SIGTERM, 2), (signal.SIGKILL, 2)):
                try:
                    os.killpg(proc.pid, sig)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    pass
            # Chromium helpers that escaped the group must still carry the
            # exact unique profile token. Never match partial names or paths.
            for pid in profile_processes(profile):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            deadline = time.monotonic() + 3
            while profile_processes(profile) and time.monotonic() < deadline:
                time.sleep(.05)
        shutil.rmtree(profile, ignore_errors=True)
        for resource in config['resources']:
            shutil.rmtree(resource, ignore_errors=True)
        Path(config_path).unlink(missing_ok=True)
        os.close(owner_fd)


class OwnedBrowser:
    """Popen-compatible bounded owner, also usable as a context manager.

    A profile must be a fresh aag-* directory in the system temp directory.
    Pass resources=[downloads] for additional owned temporary directories.
    Always use debug_port/target(), never a fixed global debugging endpoint.
    """
    def __init__(self, args, *, timeout=180, resources=(), **_popen_options):
        self._closed = False
        self._write_fd = None
        self._supervisor = None
        self.connections = []
        args = list(args)
        profiles = [s.split('=', 1)[1] for s in args if s.startswith('--user-data-dir=')]
        if len(profiles) != 1:
            raise ValueError('Exactly one test-owned temporary profile is required')
        self.profile = Path(profiles[0]).resolve()
        self.resources = [Path(p).resolve() for p in resources]
        for directory in [self.profile, *self.resources]:
            if directory.parent != Path(tempfile.gettempdir()).resolve() or not directory.name.startswith('aag-'):
                raise ValueError('Refusing to own a non-test temporary directory')
        if profile_processes(self.profile):
            raise ValueError('Profile is already owned by another running browser')
        args = [s for s in args if not s.startswith('--remote-debugging-port=')]
        args += ['--remote-debugging-port=0']
        fd, name = tempfile.mkstemp(prefix='aag-browser-owner-', suffix='.json')
        self.config_path = Path(name)
        os.close(fd)
        read_fd, self._write_fd = os.pipe()
        try:
            self.config_path.write_text(json.dumps({'args': args, 'profile': str(self.profile),
                'resources': [str(p) for p in self.resources], 'timeout': timeout}))
            self._supervisor = subprocess.Popen([sys.executable, __file__, '--supervise', name, str(read_fd)],
                pass_fds=(read_fd,), start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except BaseException:
            self.close()
            raise
        finally:
            os.close(read_fd)
        atexit.register(self.close)

    @property
    def debug_port(self):
        return int((self.profile/'DevToolsActivePort').read_text().splitlines()[0])

    @property
    def endpoint(self):
        return f'http://127.0.0.1:{self.debug_port}/json/list'

    def target(self, timeout=20):
        import urllib.request
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.poll() is not None:
                raise RuntimeError('Owned test browser exited during setup')
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{self.debug_port}/json/list', timeout=1) as r:
                    return next(p for p in json.load(r) if p['type'] == 'page')
            except (OSError, ValueError, StopIteration):
                time.sleep(.1)
        raise TimeoutError('Owned browser debugging target did not start')

    def attach(self, connection):
        self.connections.append(connection)
        return connection

    def poll(self):
        return self._supervisor.poll() if self._supervisor else None

    def terminate(self):
        for connection in self.connections:
            try:
                (connection.socket if hasattr(connection, 'socket') else connection).close()
            except Exception:
                pass
        self.connections.clear()
        if self._write_fd is not None:
            os.close(self._write_fd)
            self._write_fd = None

    def kill(self):
        # The supervisor still performs exact process-tree cleanup.
        self.terminate()

    def wait(self, timeout=10):
        result = self._supervisor.wait(timeout=timeout) if self._supervisor else 0
        if profile_processes(self.profile):
            raise RuntimeError('Test browser left orphan processes for its profile')
        return result

    def close(self):
        if self._closed:
            return
        self.terminate()
        self.wait(timeout=10)
        self.config_path.unlink(missing_ok=True)
        shutil.rmtree(self.profile, ignore_errors=True)
        for directory in self.resources:
            shutil.rmtree(directory, ignore_errors=True)
        remaining = profile_processes(self.profile)
        if remaining:
            raise RuntimeError(f'Owned browser processes survived cleanup: {remaining}')
        self._closed = True
        atexit.unregister(self.close)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


if __name__ == '__main__' and sys.argv[1:2] == ['--supervise']:
    _supervise(sys.argv[2], int(sys.argv[3]))
