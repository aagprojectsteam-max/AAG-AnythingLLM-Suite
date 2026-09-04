import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PIL import Image

from aag_chess import (
    DENSITY_VERSION,
    DIVERSITY_VERSION,
    GENERATOR_VERSION,
    RENDERER_VERSION,
    SCORER_VERSION,
    STOCKFISH_DISCOVERY_VERSION,
    VERIFIER_VERSION,
    __version__,
)
from aag_chess.application import (
    ApplicationGenerationError,
    OutputCollisionError,
    generate_public_batch,
    verify_output,
)
from aag_chess.cli import main
from aag_chess.generator import GenerationRequest, GenerationRequestError, generate_batch
from aag_chess.renderer import CARD_HEIGHT, CARD_WIDTH


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = Path(sys.executable).with_name("aag-chess")


@pytest.fixture(scope="module")
def public_matrix(tmp_path_factory):
    root = tmp_path_factory.mktemp("public-matrix")
    matrix = {}
    rows = (
        (1, "white", "easy", 1101),
        (2, "white", "medium", 1201),
        (3, "white", "hard", 1301),
        (1, "black", "easy", 2101),
    )
    for mate, side, difficulty, seed in rows:
        output = root / f"m{mate}-{side}"
        result = generate_public_batch(
            mate_moves=mate,
            side_to_move=side,
            difficulty=difficulty,
            count=1,
            seed=seed,
            output=output,
        )
        matrix[mate, side] = (result, json.loads(result.manifest_path.read_text()))
    return matrix


def test_application_generates_mate_1_2_3_and_black(public_matrix):
    for mate, side in ((1, "white"), (2, "white"), (3, "white"), (1, "black")):
        result, manifest = public_matrix[mate, side]
        assert result.generated_count == result.requested_count == 1
        assert manifest["request"]["mate_moves"] == mate
        assert manifest["request"]["side_to_move"] == side
        assert manifest["puzzles"][0]["side_to_move"] == side


def test_manifest_versions_shape_and_public_metadata(public_matrix):
    unused_result, manifest = public_matrix[2, "white"]
    assert manifest["schema_version"] == "aag-public-batch-v4"
    assert manifest["application_version"] == __version__ == "1.6.0"
    assert manifest["component_versions"] == {
        "generator": GENERATOR_VERSION,
        "stockfish_discovery": STOCKFISH_DISCOVERY_VERSION,
        "renderer": RENDERER_VERSION,
        "scorer": SCORER_VERSION,
        "verifier": VERIFIER_VERSION,
        "density": DENSITY_VERSION,
        "diversity": DIVERSITY_VERSION,
    }
    assert manifest["generation"]["generated_count"] == 1
    assert manifest["generation"]["accounting"]["accepted"] == 1
    assert manifest["generation"]["accounting"]["verifier_submitted"] >= 1
    assert manifest["puzzles"][0]["difficulty"]["label"] == "medium"
    assert manifest["request"]["density"] == "auto"
    assert manifest["puzzles"][0]["density"] == {
        "profile": "sparse",
        "piece_count": 3,
        "classifier_version": DENSITY_VERSION,
    }
    assert manifest["puzzles"][0]["provenance"] == {
        "classification": "arbitrary_composition_template",
        "retro_legality_proven": False,
    }
    assert manifest["puzzles"][0]["diversity"]["source_family"] == "builtin"
    assert 0 <= manifest["puzzles"][0]["diversity"]["quality_score"] <= 100


def test_svg_png_and_artifact_hashes_are_valid(public_matrix):
    result, manifest = public_matrix[1, "white"]
    puzzle = manifest["puzzles"][0]
    for artifact in puzzle["artifacts"]:
        path = result.output_directory / artifact["file"]
        content = path.read_bytes()
        assert artifact["sha256"] == hashlib.sha256(content).hexdigest()
        assert artifact["bytes"] == len(content)
        assert (artifact["width"], artifact["height"]) == (CARD_WIDTH, CARD_HEIGHT)
        if path.suffix == ".svg":
            assert ET.fromstring(content).tag.endswith("svg")
        else:
            image = Image.open(io.BytesIO(content))
            image.load()
            assert image.format == "PNG"
            assert image.size == (CARD_WIDTH, CARD_HEIGHT)


def test_integrity_and_deep_verification(public_matrix):
    result, unused_manifest = public_matrix[3, "white"]
    shallow = verify_output(result.output_directory)
    deep = verify_output(result.output_directory, deep=True)
    assert shallow.valid and not shallow.deep_verified
    assert deep.valid and deep.deep_verified
    assert shallow.checked_artifacts == deep.checked_artifacts == 2


def test_deterministic_replay_has_identical_manifest_and_artifacts(tmp_path):
    arguments = dict(
        mate_moves=2,
        side_to_move="white",
        difficulty="medium",
        count=3,
        seed=887766,
        formats=("png", "svg"),
    )
    first = generate_public_batch(output=tmp_path / "first", **arguments)
    second = generate_public_batch(output=tmp_path / "second", **arguments)
    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()
    first_files = sorted((first.output_directory / "puzzles").iterdir())
    second_files = sorted((second.output_directory / "puzzles").iterdir())
    assert [path.name for path in first_files] == [path.name for path in second_files]
    assert [path.read_bytes() for path in first_files] == [
        path.read_bytes() for path in second_files
    ]


def test_ten_mate_two_puzzles_are_unique_and_exact(tmp_path):
    result = generate_public_batch(
        mate_moves=2,
        side_to_move="white",
        difficulty="medium",
        count=10,
        seed=42,
        output=tmp_path / "ten",
        formats=("svg",),
    )
    manifest = json.loads(result.manifest_path.read_text())
    identities = [puzzle["identity_sha256"] for puzzle in manifest["puzzles"]]
    assert len(identities) == len(set(identities)) == 10
    assert manifest["generation"]["generated_count"] == 10
    assert manifest["generation"]["finite_candidate_universe_size"] == 16
    assert verify_output(result.output_directory).valid


@pytest.mark.parametrize(
    "changes",
    [
        {"mate_moves": 4},
        {"side_to_move": "blue"},
        {"difficulty": "expert"},
        {"count": 0},
        {"seed": -1},
        {"formats": ()},
        {"formats": ("svg", "svg")},
        {"density": "crowded"},
    ],
)
def test_invalid_application_requests_fail_before_output(tmp_path, changes):
    values = {
        "mate_moves": 1,
        "side_to_move": "white",
        "difficulty": "easy",
        "count": 1,
        "seed": 1,
        "formats": ("svg",),
        "output": tmp_path / f"invalid-{len(list(tmp_path.iterdir()))}",
    }
    values.update(changes)
    output = values["output"]
    with pytest.raises(GenerationRequestError):
        generate_public_batch(**values)
    assert not output.exists()


def test_impossible_count_fails_honestly_without_partial_output(tmp_path):
    output = tmp_path / "impossible"
    with pytest.raises(ApplicationGenerationError) as captured:
        generate_public_batch(
            mate_moves=2,
            side_to_move="white",
            difficulty="medium",
            count=17,
            seed=8,
            output=output,
            formats=("svg",),
        )
    assert captured.value.generated_count == 16
    assert captured.value.requested_count == 17
    assert captured.value.failure_reason == "candidate_stream_exhausted"
    assert not output.exists()


def test_attempt_bound_partial_success_is_not_published(tmp_path):
    output = tmp_path / "bounded"
    with pytest.raises(ApplicationGenerationError) as captured:
        generate_public_batch(
            mate_moves=2,
            side_to_move="white",
            difficulty="medium",
            count=2,
            seed=3,
            output=output,
            max_candidate_attempts=1,
        )
    assert captured.value.generated_count == 1
    assert "no partial output" in str(captured.value)
    assert not output.exists()


def test_output_collision_refuses_overwrite(tmp_path, public_matrix):
    existing = tmp_path / "existing"
    existing.mkdir()
    marker = existing / "keep.txt"
    marker.write_text("preserve")
    with pytest.raises(OutputCollisionError, match="refusing to overwrite"):
        generate_public_batch(
            mate_moves=1,
            side_to_move="white",
            difficulty="easy",
            count=1,
            seed=1,
            output=existing,
        )
    assert marker.read_text() == "preserve"


def test_output_rejects_symlinked_parent(tmp_path):
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(OutputCollisionError, match="symbolic links"):
        generate_public_batch(
            mate_moves=1,
            side_to_move="white",
            difficulty="easy",
            count=1,
            seed=1,
            output=linked_parent / "output",
        )
    assert not (real_parent / "output").exists()


def test_integrity_detects_hash_mismatch_and_unexpected_file(tmp_path, public_matrix):
    source, unused_manifest = public_matrix[1, "black"]
    tampered = tmp_path / "tampered"
    shutil.copytree(source.output_directory, tampered)
    artifact = next((tampered / "puzzles").iterdir())
    artifact.write_bytes(b"tampered")
    report = verify_output(tampered)
    assert not report.valid
    assert any("mismatch" in error for error in report.errors)

    extra = tmp_path / "extra"
    shutil.copytree(source.output_directory, extra)
    (extra / "unexpected.txt").write_text("unexpected")
    report = verify_output(extra)
    assert not report.valid
    assert any("file set mismatch" in error for error in report.errors)


def test_integrity_detects_missing_and_malformed_manifest(tmp_path):
    missing = tmp_path / "missing"
    missing.mkdir()
    assert not verify_output(missing).valid
    malformed = tmp_path / "malformed"
    malformed.mkdir()
    (malformed / "manifest.json").write_text('{"puzzles": [}')
    report = verify_output(malformed)
    assert not report.valid
    assert any("malformed" in error for error in report.errors)


def test_integrity_detects_inconsistent_public_count(tmp_path, public_matrix):
    source, unused_manifest = public_matrix[1, "white"]
    output = tmp_path / "bad-count"
    shutil.copytree(source.output_directory, output)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["generation"]["generated_count"] = 2
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report = verify_output(output)
    assert not report.valid
    assert any("counts are inconsistent" in error for error in report.errors)


def test_integrity_rejects_symlinked_puzzles_directory(tmp_path, public_matrix):
    source, unused_manifest = public_matrix[1, "white"]
    output = tmp_path / "linked-puzzles"
    output.mkdir()
    shutil.copy2(source.manifest_path, output / "manifest.json")
    (output / "puzzles").symlink_to(
        source.output_directory / "puzzles", target_is_directory=True
    )
    report = verify_output(output)
    assert not report.valid
    assert any("puzzles directory" in error for error in report.errors)


def test_public_application_boundary_has_no_solution_leak(tmp_path):
    output = tmp_path / "no-leak"
    public = generate_public_batch(
        mate_moves=3,
        side_to_move="white",
        difficulty="hard",
        count=1,
        seed=30301,
        output=output,
    )
    internal = generate_batch(
        GenerationRequest(3, "white", "hard", 30301, 10_000), 1
    )
    assert internal.success
    verification = internal.puzzles[0].verification
    public_bytes = b"\n".join(
        path.read_bytes() for path in sorted(public.output_directory.rglob("*")) if path.is_file()
    )
    secrets = (
        *verification.key_moves,
        verification.certificate_sha256,
        json.dumps(verification.proof, ensure_ascii=False, sort_keys=True),
    )
    for secret in secrets:
        assert secret is not None
        assert secret.encode("utf-8") not in public_bytes
    manifest = json.loads(public.manifest_path.read_text())
    serialized = json.dumps(manifest, sort_keys=True).lower()
    for forbidden in (
        "key_moves",
        "proof",
        "certificate",
        "continuation",
        "solution",
        "uci",
        "san",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "arguments",
    [
        (
            "generate", "--mate", "0", "--side", "white", "--difficulty", "easy",
            "--count", "1", "--output", "unused",
        ),
        (
            "generate", "--mate", "1", "--side", "invalid", "--difficulty", "easy",
            "--count", "1", "--output", "unused",
        ),
        (
            "generate", "--mate", "1", "--side", "white", "--difficulty", "invalid",
            "--count", "1", "--output", "unused",
        ),
        (
            "generate", "--mate", "1", "--side", "white", "--difficulty", "easy",
            "--count", "0", "--output", "unused",
        ),
    ],
)
def test_cli_invalid_arguments_exit_two(arguments):
    completed = subprocess.run(
        [str(ENTRYPOINT), *arguments],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "error" in completed.stderr.lower()


def test_cli_success_collision_integrity_and_repository_root_invocation(tmp_path):
    output = tmp_path / "cli-output"
    arguments = (
        "generate",
        "--engine",
        "builtin",
        "--mate",
        "1",
        "--side",
        "white",
        "--difficulty",
        "easy",
        "--count",
        "1",
        "--formats",
        "svg",
        "png",
        "--output",
        str(output),
    )
    first = subprocess.run(
        [str(ENTRYPOINT), *arguments], cwd=PROJECT_ROOT, text=True, capture_output=True
    )
    assert first.returncode == 0
    assert "Generated 1/1" in first.stdout
    collision = subprocess.run(
        [str(ENTRYPOINT), *arguments], cwd=PROJECT_ROOT, text=True, capture_output=True
    )
    assert collision.returncode == 4
    assert "refusing to overwrite" in collision.stderr
    integrity = subprocess.run(
        [str(ENTRYPOINT), "verify-output", str(output)],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
    )
    assert integrity.returncode == 0
    assert "PASS" in integrity.stdout


def test_cli_impossible_batch_exit_three_and_no_output(tmp_path, capsys):
    output = tmp_path / "cli-impossible"
    exit_code = main(
        [
            "generate",
            "--engine",
            "builtin",
            "--mate",
            "1",
            "--side",
            "white",
            "--difficulty",
            "easy",
            "--count",
            "9",
            "--formats",
            "svg",
            "--output",
            str(output),
        ]
    )
    assert exit_code == 3
    assert "only 8" in capsys.readouterr().err
    assert not output.exists()
