"""Checkpoint an owned test profile only after Chromium has closed cleanly.
The tiny launcher stays in OwnedBrowser's process group while it copies the
profile, so cleanup cannot race the copy. No user profile is accepted.
"""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

MARKER = 'AAG_TEST_PROFILE_CHECKPOINT_READY'

def owned_directory(value):
    directory = Path(value).resolve()
    if directory.parent != Path(tempfile.gettempdir()).resolve() or not directory.name.startswith('aag-') or not directory.is_dir():
        raise ValueError('Checkpoint requires an existing owned aag-* temporary directory')
    return directory

def checkpoint_command(browser_args, destination):
    target = owned_directory(destination)
    profiles = [v.split('=', 1)[1] for v in browser_args if v.startswith('--user-data-dir=')]
    if len(profiles) != 1 or owned_directory(profiles[0]) == target or any(target.iterdir()):
        raise ValueError('Checkpoint requires a distinct empty owned destination')
    return [sys.executable, str(Path(__file__).resolve()), '--launch', str(target), *browser_args]

def finish_checkpoint(browser, cdp, destination):
    cdp.call('Browser.close')
    browser.wait(timeout=20)
    if not (owned_directory(destination) / MARKER).is_file():
        raise RuntimeError('Browser did not produce a clean persisted profile checkpoint')

def main():
    if len(sys.argv) < 5 or sys.argv[1] != '--launch':
        raise SystemExit('Internal test-browser checkpoint launcher')
    destination = owned_directory(sys.argv[2]); args = sys.argv[3:]
    source = owned_directory(next(v.split('=', 1)[1] for v in args if v.startswith('--user-data-dir=')))
    if source == destination or any(destination.iterdir()):
        raise ValueError('Refusing to overwrite an existing checkpoint')
    result = subprocess.run(args, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    shutil.copytree(source, destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('Singleton*', 'DevToolsActivePort'))
    (destination / MARKER).write_text('Chromium closed cleanly before this profile was copied.\n')

if __name__ == '__main__':
    main()
