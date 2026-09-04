"""Public V1 batch application boundary and artifact integrity checks."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Literal

import chess

from . import (
    DENSITY_VERSION,
    DIVERSITY_VERSION,
    GENERATOR_VERSION,
    RENDERER_VERSION,
    SCORER_VERSION,
    STOCKFISH_DISCOVERY_VERSION,
    VERIFIER_VERSION,
    __version__,
)
from .difficulty import assess_difficulty
from .density import classify_piece_count, density_plan
from .diversity import describe_position
from .generator import (
    BatchGenerationResult,
    GenerationRequest,
    GenerationRequestError,
    candidate_sequence,
    generate_batch,
)
from .position import normalized_fen, parse_board, puzzle_hash
from .renderer import (
    CARD_HEIGHT,
    CARD_WIDTH,
    ArtifactMetadata,
    write_png_artifact,
    write_svg_artifact,
)
from .verifier import MateVerifier
from .stockfish import (
    StockfishConfig,
    StockfishInfo,
    StockfishUnavailableError,
    find_stockfish,
    generate_stockfish_batch,
)


PUBLIC_MANIFEST_VERSION = "aag-public-batch-v4"
ArtifactFormat = Literal["svg", "png"]
GenerationBackend = Literal["auto", "builtin", "stockfish"]
_FORMATS = frozenset({"svg", "png"})
_SHA256_LENGTH = 64
_MAX_MANIFEST_BYTES = 5_000_000
_MAX_ARTIFACT_BYTES = 20_000_000


class ApplicationError(RuntimeError):
    """A safe, expected public application failure."""


class OutputCollisionError(ApplicationError):
    """The requested output location is unsafe or already occupied."""


class ApplicationGenerationError(ApplicationError):
    """A bounded request could not be satisfied exactly."""

    def __init__(self, result: BatchGenerationResult):
        self.requested_count = result.requested_count
        self.generated_count = len(result.puzzles)
        self.failure_reason = result.failure_reason or "generation_failed"
        self.accounting = result.accounting
        super().__init__(
            f"could not generate requested count {self.requested_count}: "
            f"only {self.generated_count} verified unique puzzle(s) were available "
            f"({self.failure_reason}); no partial output was written"
        )


@dataclass(frozen=True)
class PublicBatchResult:
    output_directory: Path
    manifest_path: Path
    requested_count: int
    generated_count: int
    public_ids: tuple[str, ...]
    manifest_sha256: str
    backend: str


@dataclass(frozen=True)
class IntegrityReport:
    valid: bool
    output_directory: Path
    checked_artifacts: int
    puzzle_count: int
    deep_verified: bool
    errors: tuple[str, ...]


def _normalized_formats(formats: Iterable[str]) -> tuple[ArtifactFormat, ...]:
    try:
        supplied = tuple(formats)
    except TypeError as exc:
        raise GenerationRequestError("formats must be an iterable of svg and/or png") from exc
    if not supplied:
        raise GenerationRequestError("at least one artifact format is required")
    if any(not isinstance(value, str) or value not in _FORMATS for value in supplied):
        raise GenerationRequestError("formats may contain only svg and png")
    if len(set(supplied)) != len(supplied):
        raise GenerationRequestError("artifact formats must not be duplicated")
    return tuple(sorted(supplied))  # type: ignore[return-value]


def _new_output_path(value: str | os.PathLike[str]) -> Path:
    try:
        requested = Path(value)
    except TypeError as exc:
        raise OutputCollisionError("output must be a path") from exc
    absolute = requested.absolute()
    if absolute == Path(absolute.anchor):
        raise OutputCollisionError("output must not be a filesystem root")
    if absolute.exists() or absolute.is_symlink():
        raise OutputCollisionError(f"output already exists; refusing to overwrite: {absolute}")
    if (
        not 1 <= len(absolute.name) <= 100
        or not absolute.name.isprintable()
        or any(ord(character) < 32 for character in absolute.name)
    ):
        raise OutputCollisionError("output directory name must be 1-100 printable characters")
    parent = absolute.parent
    if not parent.exists() or not parent.is_dir():
        raise OutputCollisionError(f"output parent must be an existing directory: {parent}")
    if parent.is_symlink() or parent.resolve(strict=True) != parent:
        raise OutputCollisionError("output path must not traverse symbolic links")
    return absolute


def _artifact_dict(metadata: ArtifactMetadata, puzzle_root: Path) -> dict[str, Any]:
    return {
        "file": metadata.path.relative_to(puzzle_root.parent).as_posix(),
        "media_type": metadata.media_type,
        "sha256": metadata.sha256,
        "bytes": metadata.path.stat().st_size,
        "width": metadata.width,
        "height": metadata.height,
    }


def _public_accounting(batch: BatchGenerationResult) -> dict[str, int]:
    accounting = asdict(batch.accounting)
    accounting.pop("stockfish_analysis_ms")
    accounting.pop("verifier_analysis_ms")
    return accounting


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _exclusive_write(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o644)
    with os.fdopen(descriptor, "wb") as output:
        output.write(content)


def generate_public_batch(
    *,
    mate_moves: int,
    side_to_move: str,
    difficulty: str,
    count: int,
    seed: int,
    output: str | os.PathLike[str],
    formats: Iterable[str] = ("svg", "png"),
    max_candidate_attempts: int = 10_000,
    engine: GenerationBackend = "builtin",
    stockfish_nodes: int = 50_000,
    stockfish_binary: Path | None = None,
    density: str = "auto",
) -> PublicBatchResult:
    """Generate one complete public batch, or leave no output directory.

    Verification and scoring finish before filesystem mutation. Artifacts are
    built in a private staging directory and renamed into place only after the
    complete deterministic manifest has been written.
    """

    target = _new_output_path(output)
    selected_formats = _normalized_formats(formats)
    request = GenerationRequest(
        mate_moves=mate_moves,
        side_to_move=side_to_move,  # type: ignore[arg-type]
        difficulty=difficulty,  # type: ignore[arg-type]
        seed=seed,
        max_candidate_attempts=max_candidate_attempts,
        density=density,  # type: ignore[arg-type]
    )
    if engine not in {"auto", "builtin", "stockfish"}:
        raise GenerationRequestError("engine must be auto, builtin, or stockfish")
    selected_binary = stockfish_binary or find_stockfish()
    resolved_engine = engine
    if engine == "auto":
        resolved_engine = "stockfish" if selected_binary is not None else "builtin"
    stockfish_info = StockfishInfo(False, None, None, None)
    if resolved_engine == "stockfish":
        if selected_binary is None:
            raise StockfishUnavailableError(
                "Stockfish is unavailable; use --engine builtin or --engine auto"
            )
        stockfish_result = generate_stockfish_batch(
            request,
            count,
            binary=selected_binary,
            config=StockfishConfig(nodes_per_candidate=stockfish_nodes),
        )
        batch = stockfish_result.batch
        stockfish_info = stockfish_result.stockfish
    else:
        StockfishConfig(nodes_per_candidate=stockfish_nodes)
        if request.density not in {"auto", "sparse"}:
            raise GenerationRequestError(
                "builtin density is sparse-only; use stockfish or auto for normal/rich"
            )
        batch = generate_batch(request, count)
    if not batch.success:
        raise ApplicationGenerationError(batch)

    staging = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=str(target.parent))
    )
    try:
        puzzles_directory = staging / "puzzles"
        puzzles_directory.mkdir(mode=0o755)
        public_puzzles: list[dict[str, Any]] = []
        public_ids: list[str] = []
        for sequence, puzzle in enumerate(batch.puzzles, start=1):
            public_id = f"aag-{puzzle.puzzle_identity[:20]}"
            public_ids.append(public_id)
            stem = f"puzzle-{sequence:04d}"
            render_request = puzzle.to_render_request(public_id)
            artifact_rows: list[dict[str, Any]] = []
            for artifact_format in selected_formats:
                if artifact_format == "svg":
                    metadata = write_svg_artifact(
                        render_request, puzzles_directory, f"{stem}.svg"
                    )
                else:
                    metadata = write_png_artifact(
                        render_request, puzzles_directory, f"{stem}.png"
                    )
                artifact_rows.append(_artifact_dict(metadata, puzzles_directory))

            board = parse_board(puzzle.normalized_fen)
            density_assessment = classify_piece_count(len(board.piece_map()))
            provenance_classification = {
                "stockfish_assisted_composition": "stockfish_assisted_composition",
                "stockfish_assisted_game_like": "stockfish_assisted_game_like",
                "stockfish_assisted_tactical_mutation": "stockfish_assisted_tactical_mutation",
                "stockfish_assisted_material_constructed": "stockfish_assisted_material_constructed",
            }.get(puzzle.provenance, "arbitrary_composition_template")
            descriptor = describe_position(
                board,
                source_family=puzzle.source_family,
                proof=puzzle.verification.proof,
            )
            public_puzzles.append(
                {
                    "sequence": sequence,
                    "public_id": public_id,
                    "identity_sha256": puzzle.puzzle_identity,
                    "normalized_fen": puzzle.normalized_fen,
                    "mate_moves": request.mate_moves,
                    "side_to_move": "white" if board.turn == chess.WHITE else "black",
                    "difficulty": puzzle.difficulty.public_dict(),
                    "density": density_assessment.public_dict(),
                    "provenance": {
                        "classification": provenance_classification,
                        "retro_legality_proven": puzzle.provenance_verified,
                    },
                    "diversity": descriptor.public_dict(),
                    "artifacts": artifact_rows,
                }
            )

        manifest = {
            "schema_version": PUBLIC_MANIFEST_VERSION,
            "application_version": __version__,
            "component_versions": {
                "generator": GENERATOR_VERSION,
                "stockfish_discovery": STOCKFISH_DISCOVERY_VERSION,
                "renderer": RENDERER_VERSION,
                "scorer": SCORER_VERSION,
                "verifier": VERIFIER_VERSION,
                "density": DENSITY_VERSION,
                "diversity": DIVERSITY_VERSION,
            },
            "request": {
                "mate_moves": request.mate_moves,
                "side_to_move": request.side_to_move,
                "difficulty": request.difficulty,
                "count": count,
                "seed": request.seed,
                "formats": list(selected_formats),
                "engine_requested": engine,
                "max_candidate_attempts": request.max_candidate_attempts,
                "stockfish_nodes": stockfish_nodes,
                "density": request.density,
            },
            "generation": {
                "generated_count": len(batch.puzzles),
                "finite_candidate_universe_size": (
                    request.max_candidate_attempts
                    if resolved_engine == "stockfish"
                    else len(candidate_sequence(request))
                ),
                "backend": resolved_engine,
                "backend_version": batch.generator_version,
                "stockfish_version": stockfish_info.version,
                "accounting": _public_accounting(batch),
            },
            "puzzles": public_puzzles,
        }
        manifest_content = _json_bytes(manifest)
        manifest_path = staging / "manifest.json"
        _exclusive_write(manifest_path, manifest_content)
        manifest_sha256 = hashlib.sha256(manifest_content).hexdigest()
        os.rename(staging, target)
        return PublicBatchResult(
            output_directory=target,
            manifest_path=target / "manifest.json",
            requested_count=count,
            generated_count=len(batch.puzzles),
            public_ids=tuple(public_ids),
            manifest_sha256=manifest_sha256,
            backend=resolved_engine,
        )
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _exact_keys(value: Any, expected: set[str], location: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{location} has invalid fields: expected {sorted(expected)}, got {sorted(actual)}"
        )


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _safe_manifest_artifact_path(value: Any) -> PurePosixPath:
    if not isinstance(value, str):
        raise ValueError("artifact file must be a string")
    if not value.isprintable() or any(ord(character) < 32 for character in value):
        raise ValueError("artifact file contains invalid characters")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or len(relative.parts) != 2
        or relative.parts[0] != "puzzles"
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"unsafe artifact path: {value!r}")
    return relative


def _load_manifest(root: Path) -> dict[str, Any]:
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("manifest.json is missing or is not a regular file")
    if manifest_path.stat().st_size > _MAX_MANIFEST_BYTES:
        raise ValueError("manifest.json exceeds the 5 MB integrity-check limit")
    try:
        return json.loads(
            manifest_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
        )
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
    ) as exc:
        raise ValueError(f"manifest.json is malformed: {exc}") from exc


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_manifest(root: Path, manifest: dict[str, Any], *, deep: bool) -> tuple[int, int]:
    puzzles_root = root / "puzzles"
    if puzzles_root.is_symlink() or not puzzles_root.is_dir():
        raise ValueError("puzzles directory is missing, invalid, or a symbolic link")
    _exact_keys(
        manifest,
        {
            "schema_version",
            "application_version",
            "component_versions",
            "request",
            "generation",
            "puzzles",
        },
        "manifest",
    )
    if manifest["schema_version"] != PUBLIC_MANIFEST_VERSION:
        raise ValueError("unsupported manifest schema version")
    if manifest["application_version"] != __version__:
        raise ValueError("manifest application version does not match this application")
    components = manifest["component_versions"]
    _exact_keys(
        components,
        {"generator", "stockfish_discovery", "renderer", "scorer", "verifier", "density", "diversity"},
        "component_versions",
    )
    if components != {
        "generator": GENERATOR_VERSION,
        "stockfish_discovery": STOCKFISH_DISCOVERY_VERSION,
        "renderer": RENDERER_VERSION,
        "scorer": SCORER_VERSION,
        "verifier": VERIFIER_VERSION,
        "density": DENSITY_VERSION,
        "diversity": DIVERSITY_VERSION,
    }:
        raise ValueError("manifest component versions do not match this application")

    request = manifest["request"]
    _exact_keys(
        request,
        {
            "mate_moves",
            "side_to_move",
            "difficulty",
            "count",
            "seed",
            "formats",
            "engine_requested",
            "max_candidate_attempts",
            "stockfish_nodes",
            "density",
        },
        "request",
    )
    generation_request = GenerationRequest(
        mate_moves=request["mate_moves"],
        side_to_move=request["side_to_move"],
        difficulty=request["difficulty"],
        seed=request["seed"],
        max_candidate_attempts=request["max_candidate_attempts"],
        density=request["density"],
    )
    if request["engine_requested"] not in {"auto", "builtin", "stockfish"}:
        raise ValueError("manifest requested engine is invalid")
    StockfishConfig(nodes_per_candidate=request["stockfish_nodes"])
    if (
        isinstance(request["count"], bool)
        or not isinstance(request["count"], int)
        or not 1 <= request["count"] <= 100
    ):
        raise ValueError("manifest request count must be an integer between 1 and 100")
    formats = _normalized_formats(request["formats"])
    if list(formats) != request["formats"]:
        raise ValueError("manifest formats are not in canonical order")

    generation = manifest["generation"]
    _exact_keys(
        generation,
        {
            "generated_count",
            "finite_candidate_universe_size",
            "backend",
            "backend_version",
            "stockfish_version",
            "accounting",
        },
        "generation",
    )
    backend = generation["backend"]
    if backend not in {"builtin", "stockfish"}:
        raise ValueError("manifest resolved backend is invalid")
    if request["engine_requested"] == "builtin" and backend != "builtin":
        raise ValueError("manifest backend disagrees with explicit request")
    if request["engine_requested"] == "stockfish" and backend != "stockfish":
        raise ValueError("manifest backend disagrees with explicit request")
    expected_backend_version = (
        STOCKFISH_DISCOVERY_VERSION if backend == "stockfish" else GENERATOR_VERSION
    )
    if generation["backend_version"] != expected_backend_version:
        raise ValueError("manifest backend version is inconsistent")
    if backend == "stockfish":
        if not isinstance(generation["stockfish_version"], str) or not generation[
            "stockfish_version"
        ]:
            raise ValueError("manifest Stockfish version is missing")
    elif generation["stockfish_version"] is not None:
        raise ValueError("builtin manifest must not claim a Stockfish version")
    accounting = generation["accounting"]
    _exact_keys(
        accounting,
        {
            "candidates_attempted",
            "structurally_rejected",
            "duplicate_rejected",
            "verifier_rejected",
            "difficulty_rejected",
            "accepted",
            "stockfish_rejected",
            "symmetry_duplicate_rejected",
            "verifier_submitted",
            "stockfish_nodes",
            "quality_rejected",
            "similarity_rejected",
        },
        "generation.accounting",
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in accounting.values()
    ):
        raise ValueError("generation accounting values must be nonnegative integers")
    rejection_total = (
        accounting["structurally_rejected"]
        + accounting["duplicate_rejected"]
        + accounting["verifier_rejected"]
        + accounting["difficulty_rejected"]
        + accounting["stockfish_rejected"]
        + accounting["symmetry_duplicate_rejected"]
        + accounting["quality_rejected"]
        + accounting["similarity_rejected"]
    )
    if (
        accounting["candidates_attempted"]
        != rejection_total + accounting["accepted"]
    ):
        raise ValueError("generation accounting totals are inconsistent")
    if accounting["verifier_submitted"] != (
        accounting["verifier_rejected"]
        + accounting["difficulty_rejected"]
        + accounting["quality_rejected"]
        + accounting["similarity_rejected"]
        + accounting["accepted"]
    ):
        raise ValueError("verifier-submission accounting is inconsistent")
    if backend == "builtin" and any(
        accounting[field] != 0
        for field in (
            "stockfish_rejected",
            "symmetry_duplicate_rejected",
            "stockfish_nodes",
            "quality_rejected",
            "similarity_rejected",
        )
    ):
        raise ValueError("builtin manifest contains Stockfish accounting")
    puzzles = manifest["puzzles"]
    if not isinstance(puzzles, list):
        raise ValueError("puzzles must be an array")
    expected_count = request["count"]
    if not (
        len(puzzles)
        == generation["generated_count"]
        == accounting["accepted"]
        == expected_count
    ):
        raise ValueError("manifest puzzle counts are inconsistent")
    expected_universe = (
        generation_request.max_candidate_attempts
        if backend == "stockfish"
        else len(candidate_sequence(generation_request))
    )
    if generation["finite_candidate_universe_size"] != expected_universe:
        raise ValueError("finite candidate universe size is inconsistent")
    if accounting["candidates_attempted"] > generation["finite_candidate_universe_size"]:
        raise ValueError("candidate attempts exceed the finite candidate universe")

    expected_files = {"manifest.json"}
    identities: set[str] = set()
    public_ids: set[str] = set()
    artifact_count = 0
    expected_density_plan = (
        density_plan(
            generation_request.seed,
            expected_count,
            generation_request.density,
            context=(
                f"stockfish|mate={generation_request.mate_moves}|"
                f"side={generation_request.side_to_move}|"
                f"difficulty={generation_request.difficulty}"
            ),
        )
        if backend == "stockfish"
        else None
    )
    for sequence, puzzle in enumerate(puzzles, start=1):
        _exact_keys(
            puzzle,
            {
                "sequence",
                "public_id",
                "identity_sha256",
                "normalized_fen",
                "mate_moves",
                "side_to_move",
                "difficulty",
                "density",
                "provenance",
                "diversity",
                "artifacts",
            },
            f"puzzles[{sequence - 1}]",
        )
        if puzzle["sequence"] != sequence:
            raise ValueError("puzzle sequence is not contiguous")
        identity = puzzle["identity_sha256"]
        public_id = puzzle["public_id"]
        if not _is_sha256(identity) or identity in identities:
            raise ValueError("puzzle identity is malformed or duplicated")
        if public_id != f"aag-{identity[:20]}" or public_id in public_ids:
            raise ValueError("public puzzle ID is malformed or duplicated")
        identities.add(identity)
        public_ids.add(public_id)
        board = parse_board(puzzle["normalized_fen"])
        if normalized_fen(board) != puzzle["normalized_fen"]:
            raise ValueError("puzzle FEN is not canonical")
        side = "white" if board.turn == chess.WHITE else "black"
        if (
            puzzle["mate_moves"] != generation_request.mate_moves
            or puzzle["side_to_move"] != generation_request.side_to_move
            or puzzle["side_to_move"] != side
            or puzzle_hash(board, puzzle["mate_moves"]) != identity
        ):
            raise ValueError("puzzle public chess metadata is inconsistent")
        difficulty = puzzle["difficulty"]
        _exact_keys(difficulty, {"label", "score", "scorer_version"}, "puzzle difficulty")
        if (
            difficulty["label"] != generation_request.difficulty
            or difficulty["scorer_version"] != SCORER_VERSION
            or isinstance(difficulty["score"], bool)
            or not isinstance(difficulty["score"], int)
            or not 0 <= difficulty["score"] <= 100
        ):
            raise ValueError("puzzle difficulty metadata is inconsistent")
        density = puzzle["density"]
        _exact_keys(
            density,
            {"profile", "piece_count", "classifier_version"},
            "puzzle density",
        )
        expected_density = classify_piece_count(len(board.piece_map())).public_dict()
        if density != expected_density:
            raise ValueError("puzzle density metadata is inconsistent")
        if generation_request.density != "auto" and density["profile"] != generation_request.density:
            raise ValueError("puzzle density disagrees with explicit request")
        if (
            expected_density_plan is not None
            and density["profile"] != expected_density_plan[sequence - 1]
        ):
            raise ValueError("puzzle density disagrees with deterministic batch plan")
        provenance = puzzle["provenance"]
        _exact_keys(provenance, {"classification", "retro_legality_proven"}, "puzzle provenance")
        valid_provenance = (
            {
                ("stockfish_assisted_composition", False),
                ("stockfish_assisted_material_constructed", False),
                ("stockfish_assisted_game_like", True),
                ("stockfish_assisted_tactical_mutation", True),
            }
            if backend == "stockfish"
            else {("arbitrary_composition_template", False)}
        )
        if (provenance["classification"], provenance["retro_legality_proven"]) not in valid_provenance:
            raise ValueError("puzzle provenance metadata is invalid")
        source_family = {
            "stockfish_assisted_composition": "composition",
            "stockfish_assisted_material_constructed": "material_constructed",
            "stockfish_assisted_game_like": "game_like",
            "stockfish_assisted_tactical_mutation": "tactical_mutation",
            "arbitrary_composition_template": "builtin",
        }[provenance["classification"]]
        diversity = puzzle["diversity"]
        _exact_keys(
            diversity,
            {
                "fingerprint_version", "source_family", "motif", "quality_score",
                "material_signature", "pawn_structure_sha256", "king_regions",
                "occupied_files", "occupied_ranks",
            },
            "puzzle diversity",
        )
        expected_diversity = describe_position(board, source_family=source_family).public_dict()
        # Motif and proof-sensitive quality are checked in --deep mode by solving again.
        for field in (
            "fingerprint_version", "source_family", "material_signature",
            "pawn_structure_sha256", "king_regions", "occupied_files", "occupied_ranks",
        ):
            if diversity.get(field) != expected_diversity.get(field):
                raise ValueError("puzzle diversity metadata is inconsistent")
        if (
            not isinstance(diversity.get("motif"), str)
            or not diversity["motif"]
            or len(diversity["motif"]) > 64
            or isinstance(diversity.get("quality_score"), bool)
            or not isinstance(diversity.get("quality_score"), int)
            or not 0 <= diversity["quality_score"] <= 100
        ):
            raise ValueError("puzzle diversity selection metadata is invalid")
        artifacts = puzzle["artifacts"]
        if not isinstance(artifacts, list) or len(artifacts) != len(formats):
            raise ValueError("puzzle artifact count does not match request formats")
        observed_formats: set[str] = set()
        for artifact in artifacts:
            _exact_keys(
                artifact,
                {"file", "media_type", "sha256", "bytes", "width", "height"},
                "artifact",
            )
            relative = _safe_manifest_artifact_path(artifact["file"])
            suffix = relative.suffix.removeprefix(".")
            if suffix not in formats or suffix in observed_formats:
                raise ValueError("artifact format is unexpected or duplicated")
            expected_name = f"puzzle-{sequence:04d}.{suffix}"
            if relative.name != expected_name:
                raise ValueError("artifact filename is inconsistent with puzzle sequence")
            observed_formats.add(suffix)
            media_type = "image/svg+xml" if suffix == "svg" else "image/png"
            if (
                artifact["media_type"] != media_type
                or artifact["width"] != CARD_WIDTH
                or artifact["height"] != CARD_HEIGHT
                or isinstance(artifact["bytes"], bool)
                or not isinstance(artifact["bytes"], int)
                or artifact["bytes"] <= 0
                or not _is_sha256(artifact["sha256"])
            ):
                raise ValueError("artifact metadata is invalid")
            disk_path = root.joinpath(*relative.parts)
            if disk_path.is_symlink() or not disk_path.is_file():
                raise ValueError(f"artifact is missing or not a regular file: {relative}")
            disk_size = disk_path.stat().st_size
            if disk_size > _MAX_ARTIFACT_BYTES:
                raise ValueError(f"artifact exceeds the 20 MB integrity-check limit: {relative}")
            if disk_size != artifact["bytes"]:
                raise ValueError(f"artifact size mismatch: {relative}")
            if _file_sha256(disk_path) != artifact["sha256"]:
                raise ValueError(f"artifact hash mismatch: {relative}")
            expected_files.add(relative.as_posix())
            artifact_count += 1
        if observed_formats != set(formats):
            raise ValueError("puzzle does not contain every requested artifact format")

        if deep:
            verification = MateVerifier().verify(
                puzzle["normalized_fen"], puzzle["mate_moves"], dual_policy="warning"
            )
            if not verification.accepted or verification.puzzle_hash != identity:
                raise ValueError(f"deep chess verification failed for {public_id}")
            assessment = assess_difficulty(verification)
            if assessment.public_dict() != difficulty:
                raise ValueError(f"deep difficulty verification failed for {public_id}")
            expected_deep_diversity = describe_position(
                board, source_family=source_family, proof=verification.proof
            ).public_dict()
            if expected_deep_diversity != diversity:
                raise ValueError(f"deep diversity verification failed for {public_id}")

    actual_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual_files != expected_files:
        extra = sorted(actual_files - expected_files)
        missing = sorted(expected_files - actual_files)
        raise ValueError(f"output file set mismatch; extra={extra}, missing={missing}")
    actual_directories = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_dir() and not path.is_symlink()
    }
    if actual_directories != {"puzzles"}:
        raise ValueError(
            f"output directory set mismatch: {sorted(actual_directories)}"
        )
    return len(puzzles), artifact_count


def verify_output(
    output: str | os.PathLike[str], *, deep: bool = False
) -> IntegrityReport:
    """Verify one public output directory without solving unless ``deep``."""

    try:
        root = Path(output).absolute()
    except TypeError:
        return IntegrityReport(False, Path("."), 0, 0, deep, ("output must be a path",))
    errors: list[str] = []
    puzzles = artifacts = 0
    if (
        root.is_symlink()
        or not root.is_dir()
        or root.resolve(strict=True) != root
    ):
        errors.append("output directory is missing, is not a directory, or is a symlink")
    else:
        try:
            manifest = _load_manifest(root)
            puzzles, artifacts = _validate_manifest(root, manifest, deep=deep)
        except (ValueError, OSError, GenerationRequestError) as exc:
            errors.append(str(exc))
    return IntegrityReport(
        valid=not errors,
        output_directory=root,
        checked_artifacts=artifacts,
        puzzle_count=puzzles,
        deep_verified=deep,
        errors=tuple(errors),
    )
