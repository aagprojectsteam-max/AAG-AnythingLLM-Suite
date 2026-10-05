"""Read-only persisted artifact resolver and bounded Ubuntu desktop reveal.

Only the private lifecycle socket calls this module. The native API first checks
workspace authorization; this resolver independently binds the saved chat output
to a regular file beneath the configured host storage root. No client path or
command is accepted. Details and reveal both resolve afresh through this code.
"""
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import struct
import subprocess

NAME = re.compile(r"(?:img-[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\.png|(?:AAG-Image-)?[1-9][0-9]*-(?:0|[1-9][0-9]*)-[0-9]{8}-[0-9]{6}(?:-[1-9][0-9]*)?\.(?:png|jpe?g|webp))\Z")
UUID = r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"
SCHEMA = "aag.current-artifact-file.v1"

def image_facts(fd, header, filename):
    if header[:8] == b"\x89PNG\r\n\x1a\n" and header[8:16] == b"\0\0\0\rIHDR":
        width, height = struct.unpack('>II', header[16:24]); image_format = 'PNG'
    else:
        # The engine has already decoded the output. Pillow reads current JPEG /
        # WebP header facts from the same safe descriptor, never another path.
        from PIL import Image
        with os.fdopen(os.dup(fd), 'rb') as stream:
            stream.seek(0)
            with Image.open(stream) as image:
                image_format = image.format; width, height = image.size
                if getattr(image, 'n_frames', 1) != 1: raise ValueError('animated output')
    ext = Path(filename).suffix
    if ({'.png':'PNG','.jpg':'JPEG','.jpeg':'JPEG','.webp':'WEBP'}.get(ext) != image_format
            or not 0 < width <= 16384 or not 0 < height <= 16384 or width * height > 200000000):
        raise ValueError('format mismatch')
    return image_format, 'image/' + image_format.lower(), width, height


class ArtifactError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status


class Desktop:
    """Fixed desktop commands, argument vectors, no shell or browser URI launch."""
    def call(self, destination, object_path, method, *args):
        return subprocess.run(
            ["/usr/bin/gdbus", "call", "--session", "--timeout", "5", "--dest", destination,
             "--object-path", object_path, "--method", method, *args],
            check=True, capture_output=True, text=True, timeout=7).stdout

    def available(self):
        if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")) or not os.access("/usr/bin/nautilus", os.X_OK):
            return False
        try:
            # Listing does not activate the file manager or open a window.
            for method in ("ListNames", "ListActivatableNames"):
                names = self.call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                  "org.freedesktop.DBus." + method)
                if "'org.freedesktop.FileManager1'" in names:
                    return True
        except (OSError, subprocess.SubprocessError):
            pass
        return False

    def reveal(self, path):
        try:
            # Let the session bus own the persistent GUI process. With Files
            # closed, launching the CLI directly would make it the primary
            # process and our bounded wait would kill the new window on timeout.
            self.call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                      "org.freedesktop.DBus.StartServiceByName", "org.gnome.Nautilus", "0")
            # ShowItems can select in an existing background window without
            # presenting it to the user. Request a new Files window instead.
            # Nautilus opens a regular file's parent and selects the file;
            # --select would take its reuse-existing path and ignore --new-window.
            # Only the resolver's verified canonical path reaches this boundary.
            subprocess.run(["/usr/bin/nautilus", "--new-window", "--", str(path)],
                           check=True, capture_output=True, text=True, timeout=7)
        except (OSError, subprocess.SubprocessError) as error:
            raise ArtifactError("REVEAL_FAILED", 503) from error


class ArtifactFiles:
    def __init__(self, storage, desktop=None):
        # storage is supplied by the host service configuration, never the API.
        self.storage = Path(storage).resolve(strict=True)
        self.root = self.storage / "generated-images"
        self.database = self.storage / "anythingllm.db"
        self.desktop = desktop or Desktop()

    def persisted(self, ref):
        if (not isinstance(ref, dict) or set(ref) != {"chatId", "workspaceId", "artifact", "jobId", "artifactId"}
                or any(type(ref[k]) is not int or ref[k] <= 0 for k in ("chatId", "workspaceId"))
                or not isinstance(ref["artifact"], str) or not NAME.fullmatch(ref["artifact"])):
            raise ArtifactError("REFERENCE_INVALID")
        for key, prefix in (("jobId", "aag-"), ("artifactId", "artifact-")):
            if ref[key] is not None and (not isinstance(ref[key], str) or not re.fullmatch(prefix + UUID, ref[key])):
                raise ArtifactError("REFERENCE_INVALID")
        if bool(ref["jobId"]) != bool(ref["artifactId"]):
            raise ArtifactError("REFERENCE_INVALID")
        try:
            with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
                row = db.execute('SELECT response FROM workspace_chats WHERE id=? AND workspaceId=? AND "include"=1',
                                 (ref["chatId"], ref["workspaceId"])).fetchone()
            outputs = json.loads(row[0]).get("outputs", []) if row else []
            for output in outputs:
                payload = output.get("payload", {})
                if (output.get("type") == "imageGenerationCard" and payload.get("storageFilename") == ref["artifact"]
                        and all(payload.get(k) == ref[k] for k in ("jobId", "artifactId"))):
                    return payload
        except (sqlite3.Error, ValueError, TypeError, AttributeError):
            pass
        raise ArtifactError("ARTIFACT_NOT_FOUND", 404)

    def resolve(self, ref, reveal=False):
        payload = self.persisted(ref)
        filename = payload["storageFilename"]
        base = dict(schema=SCHEMA, jobId=payload.get("jobId"), artifactId=payload.get("artifactId"),
                    filename=filename, applicationPath="generated-images/" + filename, localAccessible=False)
        directory = fd = None
        try:
            # Reject a redirected generated-images root as well as file symlinks.
            if self.root.resolve(strict=True) != self.root:
                raise OSError("root redirected")
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            before = os.fstat(fd)
            target = self.root / filename
            canonical = Path(os.readlink(f"/proc/self/fd/{fd}"))
            if (canonical != target or not stat.S_ISREG(before.st_mode)
                    or not 128 <= before.st_size <= 200 * 1024 * 1024):
                raise OSError("source unavailable")
            digest = hashlib.sha256()
            header = os.read(fd, 33)
            digest.update(header)
            length = len(header)
            while chunk := os.read(fd, 1024 * 1024):
                length += len(chunk)
                if length > 200 * 1024 * 1024:
                    raise ArtifactError("ARTIFACT_CHANGED", 409)
                digest.update(chunk)
            try:
                image_format, mime, width, height = image_facts(fd, header, filename)
            except Exception as error:
                raise ArtifactError("ARTIFACT_CHANGED", 409) from error
            after = os.fstat(fd)
            if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ArtifactError("ARTIFACT_CHANGED", 409)
            sha = digest.hexdigest()
            recorded = payload.get("artifactSha256") or (payload.get("artifactExport") or {}).get("sourceSha256")
            if (not width or not height or (recorded and recorded.lower() != sha)
                    or (payload.get("artifactWidth") and int(payload["artifactWidth"]) != width)
                    or (payload.get("artifactHeight") and int(payload["artifactHeight"]) != height)):
                raise ArtifactError("ARTIFACT_CHANGED", 409)
            capable = self.desktop.available()
            current = os.stat(filename, dir_fd=directory, follow_symlinks=False)
            if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or target.resolve(strict=True) != canonical:
                raise ArtifactError("ARTIFACT_CHANGED", 409)
            result = dict(base, state="AVAILABLE", exists=True, sizeBytes=after.st_size, sha256=sha,
                          format=image_format, mime=mime, width=width, height=height,
                          location={"scope": "HOST_LOCAL", "value": str(canonical)}, localAccessible=capable)
            if reveal:
                if not capable:
                    raise ArtifactError("DESKTOP_UNAVAILABLE", 503)
                self.desktop.reveal(canonical)
                return {"revealed": True, "file": result}
            return result
        except OSError as error:
            if reveal:
                raise ArtifactError("ARTIFACT_NOT_FOUND", 404) from error
            return dict(base, state="NOT_FOUND", exists=False)
        except ArtifactError as error:
            if error.code != "ARTIFACT_CHANGED" or reveal:
                raise
            return dict(base, state="CHANGED", exists=False)
        finally:
            if fd is not None:
                os.close(fd)
            if directory is not None:
                os.close(directory)
