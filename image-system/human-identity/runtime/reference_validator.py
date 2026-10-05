#!/usr/bin/env python3
"""Fail-closed Phase 7 reference validation; no network and no biometric persistence."""
from __future__ import annotations
import io, os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES = 50 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_SIDE = 8192
MIN_FACE_SIDE = 80
SUPPORTED = {"JPEG", "MPO", "PNG", "WEBP"}
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

class ReferenceError(RuntimeError):
    def __init__(self, code: str, message: str): super().__init__(message); self.code = code

@dataclass(frozen=True)
class ValidatedReference:
    path: str
    bytes: int
    width: int
    height: int
    format: str
    face_count: int
    face_bbox: tuple[float, float, float, float]
    detection_score: float
    rgb: np.ndarray

def _within(path: Path, roots: Iterable[Path]) -> bool:
    return any(path == root or root in path.parents for root in roots)

def _safe_path(raw: str, roots: Iterable[str]) -> Path:
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise ReferenceError("REFERENCE_INVALID_PATH", "Reference path is invalid.")
    candidate = Path(raw)
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise ReferenceError("REFERENCE_INVALID_PATH", "Reference path must be absolute and traversal-free.")
    allowed = [Path(x).resolve(strict=True) for x in roots]
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        try:
            if current.is_symlink(): raise ReferenceError("REFERENCE_SYMLINK", "Symlink references are forbidden.")
        except OSError: raise ReferenceError("REFERENCE_INVALID_PATH", "Reference path cannot be inspected safely.")
    try: resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, RuntimeError, OSError):
        raise ReferenceError("REFERENCE_NOT_FOUND", "Reference file does not exist.")
    if not _within(resolved, allowed):
        raise ReferenceError("REFERENCE_PATH_FORBIDDEN", "Reference is outside authorized local roots.")
    st = os.lstat(resolved)
    if not os.path.isfile(resolved): raise ReferenceError("REFERENCE_NOT_REGULAR", "Reference must be a regular file.")
    if st.st_nlink != 1: raise ReferenceError("REFERENCE_HARDLINK_AMBIGUOUS", "Hard-linked references are forbidden.")
    if st.st_size <= 0 or st.st_size > MAX_BYTES: raise ReferenceError("REFERENCE_SIZE_INVALID", "Reference byte size is outside policy.")
    return resolved

def _read_stable(path: Path) -> tuple[bytes, os.stat_result]:
    before = os.stat(path, follow_symlinks=False)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino): raise ReferenceError("REFERENCE_CHANGED", "Reference changed while opening.")
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, MAX_BYTES + 1 - total))
            if not chunk: break
            chunks.append(chunk); total += len(chunk)
            if total > MAX_BYTES: raise ReferenceError("REFERENCE_SIZE_INVALID", "Reference exceeds byte limit.")
        after = os.fstat(fd)
    finally: os.close(fd)
    keys = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before,k) != getattr(after,k) for k in keys) or total != after.st_size:
        raise ReferenceError("REFERENCE_CHANGED", "Reference changed while reading.")
    return b"".join(chunks), after

def validate_reference(raw_path: str, allowed_roots: Iterable[str], detector: Callable[[np.ndarray], list]) -> ValidatedReference:
    path = _safe_path(raw_path, allowed_roots)
    payload, st = _read_stable(path)
    try:
        with Image.open(io.BytesIO(payload)) as decoded:
            fmt = str(decoded.format or "").upper()
            if fmt not in SUPPORTED: raise ReferenceError("REFERENCE_FORMAT_UNSUPPORTED", "Reference format is unsupported.")
            decoded.verify()
        with Image.open(io.BytesIO(payload)) as decoded:
            if fmt == "MPO": decoded.seek(0)
            image = ImageOps.exif_transpose(decoded).convert("RGB")
            image.load()
    except ReferenceError: raise
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, ValueError):
        raise ReferenceError("REFERENCE_DECODE_FAILED", "Reference image cannot be decoded safely.")
    width, height = image.size
    if width < MIN_FACE_SIDE or height < MIN_FACE_SIDE or width > MAX_SIDE or height > MAX_SIDE or width * height > MAX_PIXELS:
        raise ReferenceError("REFERENCE_DIMENSIONS_INVALID", "Reference dimensions are outside policy.")
    rgb = np.asarray(image, dtype=np.uint8)
    faces = list(detector(rgb))
    if not faces: raise ReferenceError("REFERENCE_NO_FACE", "Reference contains no evaluable face.")
    if len(faces) != 1: raise ReferenceError("REFERENCE_MULTIPLE_FACES", "Reference must contain exactly one face.")
    face = faces[0]; bbox = tuple(float(x) for x in np.asarray(face.bbox).reshape(4)); score = float(getattr(face, "det_score", 0.0))
    if min(bbox[2]-bbox[0], bbox[3]-bbox[1]) < MIN_FACE_SIDE or not np.isfinite(bbox).all() or not np.isfinite(score) or score < 0.5:
        raise ReferenceError("REFERENCE_UNSUITABLE", "Reference face is too small or unreliable.")
    return ValidatedReference(str(path), st.st_size, width, height, "JPEG" if fmt == "MPO" else fmt, 1, bbox, score, rgb)

def antelope_detector(model_root: str):
    from insightface.app import FaceAnalysis
    import cv2
    app = FaceAnalysis(name="antelopev2", root=model_root, providers=["CPUExecutionProvider"], allowed_modules=["detection", "recognition"])
    app.prepare(ctx_id=-1, det_size=(640,640))
    return lambda rgb: app.get(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
