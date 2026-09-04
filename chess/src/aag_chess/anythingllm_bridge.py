"""Narrow Unix-socket bridge from AnythingLLM to the public AAG Chess CLI.

This module contains no chess logic. It validates one skill request, invokes
the installed CLI with an explicit argv list, requires deep output integrity,
and returns only public manifest-derived metadata.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import socketserver
import stat
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Sequence

from . import __version__
from .anythingllm_solution import PrivateSolutionStore, SolutionLookupError, validate_scope


PROJECT_ROOT = Path("/mnt/data/AI/Agents/AAG-Chess-Puzzle-Agent")
CHESS_EXECUTABLE = PROJECT_ROOT / ".venv/bin/aag-chess"
DEFAULT_OUTPUT_ROOT = Path(
    "/mnt/data/AI/Apps/AnythingLLM/storage/aag-chess-puzzle/outputs"
)
DEFAULT_SOCKET = Path(
    "/mnt/data/AI/Apps/AnythingLLM/storage/aag-chess-puzzle/bridge.sock"
)
DEFAULT_PRIVATE_ROOT = Path(
    "/mnt/data/AI/Apps/AnythingLLM/storage/aag-chess-puzzle/private-solutions"
)
DEFAULT_DIVERSITY_ROOT = Path(
    "/mnt/data/AI/Apps/AnythingLLM/storage/aag-chess-puzzle/recent-diversity"
)
BRIDGE_VERSION = "aag-anythingllm-chess-bridge-v5"
MAX_BODY_BYTES = 16_384
MAX_COUNT = 10
MAX_ATTEMPTS = 2_000
MAX_STOCKFISH_NODES = 200_000
DEFAULT_TIMEOUT_SECONDS = 240
_REQUEST_DIRECTORY_PREFIX = "request-"


class SkillRequestError(ValueError):
    """A structured skill request violates the narrow public contract."""


class BridgeExecutionError(RuntimeError):
    """The trusted local CLI did not complete successfully."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class SkillRequest:
    mate: int
    side: str = "white"
    difficulty: str = "medium"
    count: int = 1
    engine: str = "auto"
    formats: tuple[str, ...] = ("png",)
    seed: int = 0
    max_attempts: int = 1_000
    stockfish_nodes: int = 20_000
    density: str = "auto"
    difficulty_defaulted: bool = False
    seed_defaulted: bool = False

    @property
    def effective_difficulty(self) -> str:
        """Map the medium UX default to the existing feasible scorer class.

        ``aag-difficulty-v1`` mechanically classifies all generated Mate-in-1
        puzzles as easy and all Mate-in-3 puzzles as hard. The requested value
        remains visible in the bridge response; only this CLI filter changes.
        """

        if self.difficulty_defaulted and self.difficulty == "medium" and self.mate == 1:
            return "easy"
        if self.difficulty_defaulted and self.difficulty == "medium" and self.mate == 3:
            return "hard"
        return self.difficulty


def _bounded_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SkillRequestError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise SkillRequestError(f"{name} must be between {minimum} and {maximum}")
    return value


def _formats(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ("png",)
    if isinstance(value, str):
        normalized = value.strip().lower().replace(" ", "")
        aliases = {
            "png": ("png",),
            "svg": ("svg",),
            "svg+png": ("png", "svg"),
            "png+svg": ("png", "svg"),
            "svg,png": ("png", "svg"),
            "png,svg": ("png", "svg"),
            "both": ("png", "svg"),
        }
        if normalized not in aliases:
            raise SkillRequestError("formats must be png, svg, or svg+png")
        return aliases[normalized]
    if isinstance(value, list):
        if not value or any(item not in {"png", "svg"} for item in value):
            raise SkillRequestError("formats may contain only png and svg")
        if len(value) != len(set(value)):
            raise SkillRequestError("formats must not contain duplicates")
        return tuple(sorted(value))
    raise SkillRequestError("formats must be png, svg, or svg+png")


def parse_skill_request(payload: Any) -> SkillRequest:
    if not isinstance(payload, dict):
        raise SkillRequestError("request body must be a JSON object")
    allowed = {
        "mate",
        "side",
        "difficulty",
        "count",
        "engine",
        "formats",
        "seed",
        "max_attempts",
        "stockfish_nodes",
        "_difficulty_defaulted",
        "_seed_defaulted",
        "_scope",
        "density",
    }
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise SkillRequestError(f"unexpected request fields: {unexpected}")
    if "mate" not in payload:
        raise SkillRequestError("mate is required")
    mate = _bounded_int(payload["mate"], "mate", 1, 3)
    side = payload.get("side", "white")
    if side not in {"white", "black"}:
        raise SkillRequestError("side must be white or black")
    difficulty = payload.get("difficulty", "medium")
    if difficulty not in {"easy", "medium", "hard"}:
        raise SkillRequestError("difficulty must be easy, medium, or hard")
    engine = payload.get("engine", "auto")
    if engine not in {"auto", "stockfish", "builtin"}:
        raise SkillRequestError("engine must be auto, stockfish, or builtin")
    density = payload.get("density", "auto")
    if density not in {"auto", "sparse", "normal", "rich"}:
        raise SkillRequestError("density must be auto, sparse, normal, or rich")
    difficulty_defaulted = payload.get(
        "_difficulty_defaulted", "difficulty" not in payload
    )
    if not isinstance(difficulty_defaulted, bool):
        raise SkillRequestError("_difficulty_defaulted must be a boolean")
    seed_defaulted = payload.get("_seed_defaulted", "seed" not in payload)
    if not isinstance(seed_defaulted, bool):
        raise SkillRequestError("_seed_defaulted must be a boolean")
    return SkillRequest(
        mate=mate,
        side=side,
        difficulty=difficulty,
        count=_bounded_int(payload.get("count", 1), "count", 1, MAX_COUNT),
        engine=engine,
        formats=_formats(payload.get("formats")),
        seed=_bounded_int(payload.get("seed", 0), "seed", 0, 2**63 - 1),
        max_attempts=_bounded_int(
            payload.get("max_attempts", 1_000),
            "max_attempts",
            1,
            MAX_ATTEMPTS,
        ),
        stockfish_nodes=_bounded_int(
            payload.get("stockfish_nodes", 20_000),
            "stockfish_nodes",
            100,
            MAX_STOCKFISH_NODES,
        ),
        density=density,
        difficulty_defaulted=difficulty_defaulted,
        seed_defaulted=seed_defaulted,
    )


def build_generate_argv(request: SkillRequest, output: Path) -> tuple[str, ...]:
    return (
        str(CHESS_EXECUTABLE),
        "generate",
        "--engine",
        request.engine,
        "--mate",
        str(request.mate),
        "--side",
        request.side,
        "--difficulty",
        request.effective_difficulty,
        "--count",
        str(request.count),
        "--seed",
        str(request.seed),
        "--max-attempts",
        str(request.max_attempts),
        "--stockfish-nodes",
        str(request.stockfish_nodes),
        "--density",
        request.density,
        "--formats",
        *request.formats,
        "--output",
        str(output),
    )


def build_verify_argv(output: Path) -> tuple[str, ...]:
    return (str(CHESS_EXECUTABLE), "verify-output", str(output), "--deep")


def run_argv(
    argv: Sequence[str],
    *,
    timeout_seconds: int,
    cwd: Path = PROJECT_ROOT,
) -> subprocess.CompletedProcess[str]:
    """Run a trusted argv without a shell and terminate its whole process group."""

    process = subprocess.Popen(
        tuple(argv),
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        raise BridgeExecutionError(
            "generation_timeout",
            f"Puzzle generation exceeded the {timeout_seconds}-second skill timeout",
        ) from exc
    return subprocess.CompletedProcess(tuple(argv), process.returncode, stdout, stderr)


def _safe_failure(returncode: int) -> BridgeExecutionError:
    if returncode == 3:
        return BridgeExecutionError(
            "generation_budget_exhausted",
            "The exact verified puzzle count was not found within the bounded search",
        )
    if returncode == 4:
        return BridgeExecutionError(
            "output_creation_failed", "The isolated puzzle output could not be created"
        )
    if returncode == 5:
        return BridgeExecutionError(
            "stockfish_unavailable", "The explicitly requested Stockfish engine failed"
        )
    return BridgeExecutionError(
        "chess_process_failed", "The trusted chess application rejected the request"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_artifact(root: Path, relative_value: Any) -> tuple[Path, str]:
    if not isinstance(relative_value, str):
        raise BridgeExecutionError("invalid_manifest", "Artifact path is invalid")
    relative = PurePosixPath(relative_value)
    if (
        relative.is_absolute()
        or len(relative.parts) != 2
        or relative.parts[0] != "puzzles"
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise BridgeExecutionError("invalid_manifest", "Artifact path is unsafe")
    path = root.joinpath(*relative.parts)
    if path.is_symlink() or not path.is_file() or path.resolve() != path:
        raise BridgeExecutionError("invalid_manifest", "Artifact file is unavailable")
    return path, relative.as_posix()


class PublicDiversityHistory:
    """Bounded, solution-free recent-output state for ordinary chat requests."""

    _SCHEMA = "aag-anythingllm-public-diversity-history-v1"
    _LIMIT = 30

    def __init__(self, root: Path):
        self.root = root.absolute()
        self.root.mkdir(mode=0o750, parents=True, exist_ok=True)
        if (
            self.root.is_symlink()
            or not self.root.is_dir()
            or self.root.resolve(strict=True) != self.root
        ):
            raise RuntimeError("AnythingLLM diversity history root is unsafe")

    @staticmethod
    def _scope_key(scope: dict[str, str]) -> str:
        material = "|".join(
            (scope["workspace_id"], scope["thread_id"], scope["user_id"])
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _path(self, scope: dict[str, str]) -> Path:
        return self.root / f"{self._scope_key(scope)}.json"

    def _load(self, scope: dict[str, str]) -> dict[str, Any]:
        path = self._path(scope)
        if not path.exists():
            return {"schema": self._SCHEMA, "counter": 0, "entries": []}
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 262_144:
            raise BridgeExecutionError("diversity_history_invalid", "Recent puzzle history is unsafe")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BridgeExecutionError("diversity_history_invalid", "Recent puzzle history is malformed") from exc
        if (
            not isinstance(value, dict)
            or value.get("schema") != self._SCHEMA
            or isinstance(value.get("counter"), bool)
            or not isinstance(value.get("counter"), int)
            or value["counter"] < 0
            or not isinstance(value.get("entries"), list)
            or len(value["entries"]) > self._LIMIT
        ):
            raise BridgeExecutionError("diversity_history_invalid", "Recent puzzle history is invalid")
        return value

    def automatic_seed(
        self,
        scope: dict[str, str],
        request: SkillRequest,
        relaxation_attempt: int,
    ) -> int:
        history = self._load(scope)
        material = (
            f"{self._scope_key(scope)}|{history['counter']}|{relaxation_attempt}|"
            f"m{request.mate}|{request.side}|{request.difficulty}|{request.density}"
        )
        return int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest()[:8], "big") & (2**63 - 1)

    @staticmethod
    def _entry(puzzle: dict[str, Any]) -> dict[str, Any]:
        diversity = puzzle.get("diversity", {})
        density = puzzle.get("density", {})
        return {
            "public_id": puzzle.get("public_id"),
            "source_family": diversity.get("source_family"),
            "motif": diversity.get("motif"),
            "material_signature": diversity.get("material_signature"),
            "pawn_structure_sha256": diversity.get("pawn_structure_sha256"),
            "king_regions": diversity.get("king_regions"),
            "density": density.get("profile"),
        }

    @staticmethod
    def _similarity(first: dict[str, Any], second: dict[str, Any]) -> int:
        weights = {
            "material_signature": 25,
            "pawn_structure_sha256": 30,
            "king_regions": 15,
            "motif": 10,
            "source_family": 10,
            "density": 10,
        }
        return sum(weight for field, weight in weights.items() if first.get(field) == second.get(field))

    def acceptable(
        self,
        scope: dict[str, str],
        manifest: dict[str, Any],
        relaxation_attempt: int,
    ) -> bool:
        limits = (70, 86, 101)
        recent = self._load(scope)["entries"]
        candidates = [self._entry(puzzle) for puzzle in manifest.get("puzzles", [])]
        return all(
            self._similarity(candidate, previous) < limits[relaxation_attempt]
            for candidate in candidates
            for previous in recent[-20:]
            if isinstance(previous, dict)
        )

    def commit(self, scope: dict[str, str], manifest: dict[str, Any]) -> None:
        current = self._load(scope)
        entries = [
            *current["entries"],
            *(self._entry(puzzle) for puzzle in manifest.get("puzzles", [])),
        ][-self._LIMIT :]
        value = {
            "schema": self._SCHEMA,
            "counter": current["counter"] + 1,
            "entries": entries,
        }
        descriptor, temporary_name = tempfile.mkstemp(prefix=".history-", dir=self.root)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(value, output, ensure_ascii=False, sort_keys=True)
                output.write("\n")
            os.replace(temporary, self._path(scope))
        finally:
            if temporary.exists():
                temporary.unlink()


class ChessSkillService:
    def __init__(
        self,
        output_root: Path = DEFAULT_OUTPUT_ROOT,
        *,
        private_root: Path | None = None,
        diversity_root: Path | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        runner: Callable[..., subprocess.CompletedProcess[str]] = run_argv,
    ):
        self.output_root = output_root.absolute()
        self.timeout_seconds = timeout_seconds
        self.runner = runner
        self.output_root.mkdir(mode=0o750, parents=True, exist_ok=True)
        if (
            self.output_root.is_symlink()
            or not self.output_root.is_dir()
            or self.output_root.resolve(strict=True) != self.output_root
        ):
            raise RuntimeError("AnythingLLM chess output root is unsafe")
        selected_private_root = private_root or (self.output_root.parent / "private-solutions")
        self.solutions = PrivateSolutionStore(selected_private_root, self.output_root)
        selected_diversity_root = diversity_root or (
            DEFAULT_DIVERSITY_ROOT
            if self.output_root == DEFAULT_OUTPUT_ROOT
            else self.output_root.parent / "recent-diversity"
        )
        self.diversity_history = PublicDiversityHistory(selected_diversity_root)

    def _cleanup(self, output: Path) -> None:
        if (
            output.parent == self.output_root
            and output.name.startswith(_REQUEST_DIRECTORY_PREFIX)
            and output.exists()
        ):
            shutil.rmtree(output)

    def _execute_verified(
        self, request: SkillRequest
    ) -> tuple[str, str, Path, Path, dict[str, Any]]:
        request_id = str(uuid.uuid4())
        directory_name = f"{_REQUEST_DIRECTORY_PREFIX}{request_id}"
        output = self.output_root / directory_name
        generation = self.runner(
            build_generate_argv(request, output),
            timeout_seconds=self.timeout_seconds,
            cwd=PROJECT_ROOT,
        )
        if generation.returncode != 0:
            self._cleanup(output)
            raise _safe_failure(generation.returncode)
        verification = self.runner(
            build_verify_argv(output),
            timeout_seconds=min(self.timeout_seconds, 120),
            cwd=PROJECT_ROOT,
        )
        if verification.returncode != 0:
            self._cleanup(output)
            raise BridgeExecutionError(
                "integrity_verification_failed",
                "Generated puzzle artifacts failed deep integrity verification",
            )
        manifest_path = output / "manifest.json"
        if (
            manifest_path.is_symlink()
            or not manifest_path.is_file()
            or manifest_path.stat().st_size > 5_000_000
        ):
            self._cleanup(output)
            raise BridgeExecutionError("invalid_manifest", "Public manifest is unavailable")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            self._cleanup(output)
            raise BridgeExecutionError("invalid_manifest", "Public manifest is malformed") from exc
        if (
            not isinstance(manifest, dict)
            or manifest.get("request", {}).get("count") != request.count
            or manifest.get("request", {}).get("mate_moves") != request.mate
            or manifest.get("request", {}).get("side_to_move") != request.side
            or manifest.get("request", {}).get("difficulty") != request.effective_difficulty
            or manifest.get("request", {}).get("density") != request.density
            or manifest.get("generation", {}).get("generated_count") != request.count
            or not isinstance(manifest.get("puzzles"), list)
        ):
            self._cleanup(output)
            raise BridgeExecutionError("invalid_manifest", "Public manifest disagrees with request")
        return request_id, directory_name, output, manifest_path, manifest

    def generate(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise SkillRequestError("request body must be a JSON object")
        scope = validate_scope(
            payload.get(
                "_scope",
                {"workspace_id": "unknown", "thread_id": "unknown", "user_id": "unknown"},
            )
        )
        original_request = parse_skill_request(payload)
        request = original_request
        relaxation_attempt = 0
        for relaxation_attempt in range(3):
            request = original_request
            if original_request.seed_defaulted:
                request = replace(
                    original_request,
                    seed=self.diversity_history.automatic_seed(
                        scope, original_request, relaxation_attempt
                    ),
                )
            request_id, directory_name, output, manifest_path, manifest = self._execute_verified(request)
            if (
                not original_request.seed_defaulted
                or self.diversity_history.acceptable(scope, manifest, relaxation_attempt)
            ):
                break
            self._cleanup(output)
        else:
            raise AssertionError("bounded diversity relaxation did not terminate")

        artifacts: list[dict[str, Any]] = []
        public_ids: list[str] = []
        for puzzle in manifest["puzzles"]:
            if not isinstance(puzzle, dict) or not isinstance(puzzle.get("public_id"), str):
                self._cleanup(output)
                raise BridgeExecutionError("invalid_manifest", "Puzzle metadata is malformed")
            public_ids.append(puzzle["public_id"])
            for artifact in puzzle.get("artifacts", []):
                if not isinstance(artifact, dict):
                    self._cleanup(output)
                    raise BridgeExecutionError("invalid_manifest", "Artifact metadata is malformed")
                try:
                    path, relative = _safe_artifact(output, artifact.get("file"))
                except BridgeExecutionError:
                    self._cleanup(output)
                    raise
                if _sha256(path) != artifact.get("sha256"):
                    self._cleanup(output)
                    raise BridgeExecutionError("invalid_manifest", "Artifact hash is inconsistent")
                artifacts.append(
                    {
                        "relative_path": f"{directory_name}/{relative}",
                        "filename": path.name,
                        "media_type": artifact.get("media_type"),
                        "bytes": path.stat().st_size,
                        "sha256": artifact.get("sha256"),
                    }
                )

        try:
            context_token = self.solutions.create_context(directory_name, manifest, scope)
        except SolutionLookupError as exc:
            self._cleanup(output)
            raise BridgeExecutionError(exc.code, str(exc)) from exc
        self.diversity_history.commit(scope, manifest)

        return {
            "schema": "aag-anythingllm-chess-result-v1",
            "status": "success",
            "request_id": request_id,
            "generated_count": request.count,
            "mate": request.mate,
            "side": request.side,
            "requested_difficulty": request.difficulty,
            "measured_difficulty": request.effective_difficulty,
            "difficulty_adjusted": request.difficulty != request.effective_difficulty,
            "engine_requested": request.engine,
            "engine_used": manifest["generation"]["backend"],
            "formats": list(request.formats),
            "density_preference": request.density,
            "density_profiles": [
                puzzle["density"]["profile"] for puzzle in manifest["puzzles"]
            ],
            "seed": request.seed,
            "seed_mode": "automatic_conversation_diversity" if original_request.seed_defaulted else "explicit_reproducible",
            "diversity_relaxation": ("strict", "moderate", "fallback")[relaxation_attempt],
            "public_ids": public_ids,
            "artifacts": artifacts,
            "manifest": {
                "relative_path": f"{directory_name}/manifest.json",
                "filename": "manifest.json",
                "media_type": "application/json",
                "bytes": manifest_path.stat().st_size,
                "sha256": _sha256(manifest_path),
            },
            "integrity": {"status": "passed", "mode": "deep"},
            "application_version": manifest.get("application_version"),
            "context_token": context_token,
        }

    def followup(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise SkillRequestError("request body must be a JSON object")
        allowed = {"context_token", "action", "puzzle_number", "public_id", "_scope"}
        unexpected = sorted(set(payload) - allowed)
        if unexpected:
            raise SkillRequestError(f"unexpected request fields: {unexpected}")
        try:
            return self.solutions.retrieve(
                context_token=payload.get("context_token"),
                action=payload.get("action"),
                scope=payload.get("_scope"),
                puzzle_number=payload.get("puzzle_number"),
                public_id=payload.get("public_id"),
            )
        except SolutionLookupError:
            raise


class _UnixHTTPServer(socketserver.UnixStreamServer):
    service: ChessSkillService


class BridgeRequestHandler(BaseHTTPRequestHandler):
    server: _UnixHTTPServer

    def _json_response(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path != "/health":
            self._json_response(404, {"status": "error", "error": "not_found"})
            return
        self._json_response(
            200,
            {
                "schema": "aag-anythingllm-chess-health-v1",
                "status": "ok",
                "bridge_version": BRIDGE_VERSION,
                "application_version": __version__,
                "chess_executable": str(CHESS_EXECUTABLE),
            },
        )

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path not in {"/v1/generate", "/v1/followup"}:
            self._json_response(404, {"status": "error", "error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY_BYTES:
            self._json_response(413, {"status": "error", "error": "invalid_body_size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = (
                self.server.service.generate(payload)
                if self.path == "/v1/generate"
                else self.server.service.followup(payload)
            )
        except (UnicodeError, json.JSONDecodeError, SkillRequestError) as exc:
            self._json_response(
                400,
                {"status": "error", "error": "invalid_request", "message": str(exc)},
            )
            return
        except BridgeExecutionError as exc:
            self._json_response(
                422,
                {"status": "error", "error": exc.code, "message": str(exc)},
            )
            return
        except SolutionLookupError as exc:
            self._json_response(
                422,
                {"status": "error", "error": exc.code, "message": str(exc)},
            )
            return
        except Exception:
            self._json_response(
                500,
                {
                    "status": "error",
                    "error": "bridge_internal_error",
                    "message": "The local chess bridge encountered an internal error",
                },
            )
            return
        self._json_response(200, result)

    def log_message(self, format: str, *args: object) -> None:
        print(f"anythingllm-chess-bridge: {format % args}", file=sys.stderr)


def serve(
    socket_path: Path,
    output_root: Path,
    private_root: Path,
    timeout_seconds: int,
) -> None:
    socket_path = socket_path.absolute()
    socket_path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    if socket_path.is_symlink():
        raise RuntimeError("bridge socket path must not be a symlink")
    if socket_path.exists():
        if not stat.S_ISSOCK(socket_path.stat().st_mode):
            raise RuntimeError("bridge socket path exists and is not a socket")
        socket_path.unlink()
    service = ChessSkillService(
        output_root,
        private_root=private_root,
        timeout_seconds=timeout_seconds,
    )
    server = _UnixHTTPServer(str(socket_path), BridgeRequestHandler)
    server.service = service
    os.chmod(socket_path, 0o660)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        if socket_path.exists() and stat.S_ISSOCK(socket_path.stat().st_mode):
            socket_path.unlink()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the narrow AnythingLLM chess bridge")
    parser.add_argument("--socket", type=Path, default=DEFAULT_SOCKET)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        choices=range(30, 601),
        default=DEFAULT_TIMEOUT_SECONDS,
        metavar="30..600",
    )
    args = parser.parse_args(argv)
    serve(args.socket, args.output_root, args.private_root, args.timeout_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
