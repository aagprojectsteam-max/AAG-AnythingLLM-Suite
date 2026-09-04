import json
import io
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import chess
import chess.engine
import pytest
from PIL import Image

import aag_chess.application as application_module
import aag_chess.cli as cli_module
import aag_chess.stockfish as stockfish_module
from aag_chess import STOCKFISH_DISCOVERY_VERSION
from aag_chess.application import generate_public_batch, verify_output
from aag_chess.density import classify_piece_count, density_plan
from aag_chess.generator import GenerationRequest
from aag_chess.stockfish import (
    StockfishConfig,
    StockfishError,
    StockfishFilterResult,
    StockfishInfo,
    StockfishSession,
    StockfishUnavailableError,
    find_stockfish,
    generate_stockfish_batch,
    inspect_stockfish,
    symmetry_identity,
)


def request(**changes):
    values = {
        "mate_moves": 1,
        "side_to_move": "white",
        "difficulty": "easy",
        "seed": 1001,
        "max_candidate_attempts": 100,
    }
    values.update(changes)
    return GenerationRequest(**values)


class FakeSession:
    mate_score = None
    instances = []

    def __init__(self, binary, config):
        self.binary = Path(binary)
        self.config = config
        self.info = StockfishInfo(True, self.binary, "fake-1", "Stockfish fake-1")
        self.closed = False
        self.calls = 0
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def analyse_mate(self, unused_board):
        self.calls += 1
        return StockfishFilterResult(self.mate_score, 100, 1)

    def __exit__(self, unused_type, unused_value, unused_traceback):
        self.closed = True


def _install_fake(monkeypatch, mate_score):
    class ConfiguredFake(FakeSession):
        pass

    ConfiguredFake.mate_score = mate_score
    ConfiguredFake.instances = []
    monkeypatch.setattr(stockfish_module, "StockfishSession", ConfiguredFake)
    return ConfiguredFake


@pytest.mark.stockfish_real
def test_real_stockfish_binary_and_version_are_detected():
    binary = find_stockfish()
    if binary is None:
        pytest.skip("real local Stockfish is unavailable")
    info = inspect_stockfish(binary)
    assert info.available
    assert info.binary == binary
    assert info.version
    assert info.name and info.name.startswith("Stockfish")


def test_find_stockfish_unavailable(monkeypatch):
    monkeypatch.setattr(stockfish_module.shutil, "which", lambda unused: None)
    monkeypatch.setattr(
        stockfish_module,
        "_KNOWN_PATHS",
        (Path("/definitely/missing/stockfish"),),
    )
    assert find_stockfish() is None


@pytest.mark.parametrize(
    "changes",
    [
        {"nodes_per_candidate": 99},
        {"nodes_per_candidate": 1_000_001},
        {"hash_mb": 0},
        {"hash_mb": 257},
        {"threads": 2},
        {"startup_timeout": 0},
        {"analysis_timeout": 61},
    ],
)
def test_stockfish_config_is_strictly_bounded(changes):
    with pytest.raises(ValueError):
        StockfishConfig(**changes)


def test_stockfish_session_configures_deterministically_and_quits(monkeypatch, tmp_path):
    binary = tmp_path / "stockfish"
    binary.write_text("fake")
    binary.chmod(0o700)

    class Engine:
        id = {"name": "Stockfish fake"}

        def __init__(self):
            self.timeout = None
            self.configurations = []
            self.quit_called = False

        def configure(self, values):
            self.configurations.append(values)

        def analyse(self, board, limit, game):
            assert limit.nodes == 1234
            assert game is not None
            return {
                "score": chess.engine.PovScore(chess.engine.Mate(1), board.turn),
                "nodes": 1234,
            }

        def quit(self):
            self.quit_called = True

        def close(self):
            self.quit_called = True

    engine = Engine()
    monkeypatch.setattr(
        stockfish_module.chess.engine.SimpleEngine,
        "popen_uci",
        lambda path, timeout: engine,
    )
    config = StockfishConfig(nodes_per_candidate=1234, hash_mb=8)
    with StockfishSession(binary, config) as session:
        result = session.analyse_mate(chess.Board())
        assert result.mate_score == 1
        assert engine.timeout == config.analysis_timeout
        assert engine.configurations[0] == {
            "Threads": 1,
            "Hash": 8,
            "UCI_LimitStrength": False,
            "Clear Hash": None,
        }
        assert engine.configurations[1] == {"Clear Hash": None}
    assert engine.quit_called


def test_stockfish_startup_timeout_is_controlled(monkeypatch, tmp_path):
    binary = tmp_path / "stockfish"
    binary.write_text("fake")
    binary.chmod(0o700)
    monkeypatch.setattr(
        stockfish_module.chess.engine.SimpleEngine,
        "popen_uci",
        lambda path, timeout: (_ for _ in ()).throw(TimeoutError("startup")),
    )
    with pytest.raises(StockfishError, match="could not start"):
        with StockfishSession(binary, StockfishConfig()):
            pass


def test_stockfish_configuration_failure_closes_started_process(monkeypatch, tmp_path):
    binary = tmp_path / "stockfish"
    binary.write_text("fake")
    binary.chmod(0o700)

    class Engine:
        id = {"name": "Stockfish fake"}
        closed = False

        def configure(self, unused_values):
            raise chess.engine.EngineError("bad option")

        def close(self):
            self.closed = True

    engine = Engine()
    monkeypatch.setattr(
        stockfish_module.chess.engine.SimpleEngine,
        "popen_uci",
        lambda path, timeout: engine,
    )
    with pytest.raises(StockfishError, match="could not start"):
        with StockfishSession(binary, StockfishConfig()):
            pass
    assert engine.closed


def test_stockfish_analysis_timeout_is_controlled_and_process_closes(
    monkeypatch, tmp_path
):
    binary = tmp_path / "stockfish"
    binary.write_text("fake")
    binary.chmod(0o700)

    class Engine:
        id = {"name": "Stockfish fake"}
        timeout = None
        quit_called = False

        def configure(self, unused_values):
            pass

        def analyse(self, unused_board, unused_limit, game):
            raise TimeoutError("analysis")

        def quit(self):
            self.quit_called = True

        def close(self):
            self.quit_called = True

    engine = Engine()
    monkeypatch.setattr(
        stockfish_module.chess.engine.SimpleEngine,
        "popen_uci",
        lambda path, timeout: engine,
    )
    with pytest.raises(StockfishError, match="analysis failed"):
        with StockfishSession(binary, StockfishConfig()) as session:
            session.analyse_mate(chess.Board())
    assert engine.quit_called


def test_stockfish_filter_can_never_call_verifier(monkeypatch):
    fake = _install_fake(monkeypatch, None)

    def forbidden_verify(*args, **kwargs):
        raise AssertionError("verifier must not run for Stockfish-filtered candidates")

    monkeypatch.setattr(stockfish_module.MateVerifier, "verify", forbidden_verify)
    result = generate_stockfish_batch(
        request(max_candidate_attempts=12),
        1,
        binary=Path("/usr/games/stockfish"),
    ).batch
    assert not result.success
    assert result.accounting.candidates_attempted == 12
    assert result.accounting.stockfish_rejected == 12
    assert result.accounting.verifier_submitted == 0
    assert fake.instances[0].closed


def test_fake_stockfish_claims_do_not_manufacture_acceptance(monkeypatch):
    fake = _install_fake(monkeypatch, 1)
    result = generate_stockfish_batch(
        request(max_candidate_attempts=100),
        1,
        binary=Path("/usr/games/stockfish"),
    ).batch
    assert result.success
    assert result.accounting.verifier_submitted > result.accounting.accepted
    assert result.accounting.verifier_rejected > 0
    assert all(puzzle.verification.accepted for puzzle in result.puzzles)
    assert result.generator_version == STOCKFISH_DISCOVERY_VERSION
    assert fake.instances[0].closed


def test_fake_stockfish_discovery_is_bounded_and_reproducible(monkeypatch):
    _install_fake(monkeypatch, 1)
    first = generate_stockfish_batch(
        request(max_candidate_attempts=100), 1, binary=Path("/usr/games/stockfish")
    ).batch
    second = generate_stockfish_batch(
        request(max_candidate_attempts=100), 1, binary=Path("/usr/games/stockfish")
    ).batch
    assert first.puzzles[0].puzzle_identity == second.puzzles[0].puzzle_identity
    assert first.puzzles[0].candidate == second.puzzles[0].candidate
    assert first.accounting.candidates_attempted <= 100
    assert second.accounting.candidates_attempted <= 100


@pytest.mark.parametrize(
    ("density", "minimum", "maximum"),
    [("sparse", 5, 9), ("normal", 10, 16), ("rich", 17, 26)],
)
def test_density_profiles_construct_verified_complete_positions(
    monkeypatch, density, minimum, maximum
):
    _install_fake(monkeypatch, 1)
    result = generate_stockfish_batch(
        request(max_candidate_attempts=200, density=density),
        1,
        binary=Path("/usr/games/stockfish"),
    ).batch
    assert result.success
    puzzle = result.puzzles[0]
    board = chess.Board(puzzle.normalized_fen)
    assert minimum <= len(board.piece_map()) <= maximum
    assert board.is_valid()
    assert puzzle.verification.accepted
    assert classify_piece_count(len(board.piece_map())).profile == density
    assert puzzle.difficulty.label == "easy"


def test_auto_batch_follows_balanced_density_plan(monkeypatch):
    _install_fake(monkeypatch, 1)
    generation_request = request(seed=44, max_candidate_attempts=500, density="auto")
    result = generate_stockfish_batch(
        generation_request, 5, binary=Path("/usr/games/stockfish")
    ).batch
    assert result.success
    actual = tuple(
        classify_piece_count(len(chess.Board(puzzle.normalized_fen).piece_map())).profile
        for puzzle in result.puzzles
    )
    assert actual == density_plan(
        generation_request.seed,
        5,
        "auto",
        context="stockfish|mate=1|side=white|difficulty=easy",
    )
    assert all(actual[index : index + 3] != ("sparse",) * 3 for index in range(3))


def test_symmetry_identity_removes_rotations_but_not_new_material():
    first = chess.Board("3k4/8/3K2Q1/8/8/8/8/8 w - - 0 1")
    rotated = chess.Board("8/8/8/5K1k/8/8/5Q2/8 w - - 0 1")
    extra_rook = first.copy()
    extra_rook.set_piece_at(chess.A1, chess.Piece(chess.ROOK, chess.WHITE))
    assert symmetry_identity(first, 1) == symmetry_identity(rotated, 1)
    assert symmetry_identity(first, 1) != symmetry_identity(extra_rook, 1)


def test_auto_source_schedule_contains_four_independent_families():
    assert set(stockfish_module._SOURCE_SCHEDULE) == {
        "game_like",
        "tactical_mutation",
        "material_constructed",
        "composition",
    }
    assert stockfish_module._SOURCE_SCHEDULE.count("composition") < len(
        stockfish_module._SOURCE_SCHEDULE
    ) / 3


def test_auto_falls_back_to_builtin_without_stockfish(monkeypatch, tmp_path):
    monkeypatch.setattr(application_module, "find_stockfish", lambda: None)
    result = generate_public_batch(
        mate_moves=1,
        side_to_move="white",
        difficulty="easy",
        count=1,
        seed=1,
        output=tmp_path / "auto-fallback",
        engine="auto",
    )
    manifest = json.loads(result.manifest_path.read_text())
    assert result.backend == "builtin"
    assert manifest["request"]["engine_requested"] == "auto"
    assert manifest["generation"]["backend"] == "builtin"
    assert verify_output(result.output_directory).valid


def test_explicit_stockfish_fails_when_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(application_module, "find_stockfish", lambda: None)
    with pytest.raises(StockfishUnavailableError, match="unavailable"):
        generate_public_batch(
            mate_moves=1,
            side_to_move="white",
            difficulty="easy",
            count=1,
            seed=1,
            output=tmp_path / "unavailable",
            engine="stockfish",
        )


def _stockfish_processes() -> set[int]:
    processes = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "comm").read_text().strip() == "stockfish":
                processes.add(int(entry.name))
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return processes


@pytest.mark.stockfish_real
def test_real_inspection_leaves_no_stockfish_process():
    if find_stockfish() is None:
        pytest.skip("real local Stockfish is unavailable")
    before = _stockfish_processes()
    assert inspect_stockfish().available
    assert _stockfish_processes() == before


def test_engine_info_cli_reports_diagnostic(monkeypatch, capsys):
    monkeypatch.setattr(
        cli_module,
        "inspect_stockfish",
        lambda: StockfishInfo(
            True,
            Path("/usr/games/stockfish"),
            "17.1",
            "Stockfish 17.1",
        ),
    )
    assert cli_module.main(["engine-info"]) == 0
    output = capsys.readouterr().out
    assert "Stockfish available" in output
    assert "Binary: /usr/games/stockfish" in output
    assert "Version: 17.1" in output


@pytest.fixture(scope="module")
def real_stockfish_matrix():
    binary = find_stockfish()
    if binary is None:
        pytest.skip("real local Stockfish is unavailable")
    cases = (
        (1, "white", "easy", 1001, 100, 20_000),
        (2, "white", "medium", 1002, 500, 20_000),
        (3, "white", "hard", 1003, 100, 50_000),
        (2, "black", "medium", 1002, 200, 20_000),
    )
    results = {}
    for mate, side, difficulty, seed, attempts, nodes in cases:
        results[mate, side] = generate_stockfish_batch(
            request(
                mate_moves=mate,
                side_to_move=side,
                difficulty=difficulty,
                seed=seed,
                max_candidate_attempts=attempts,
            ),
            1,
            binary=binary,
            config=StockfishConfig(nodes_per_candidate=nodes),
        )
    return results


@pytest.mark.stockfish_real
def test_real_stockfish_mate_depths_white_and_black(real_stockfish_matrix):
    for (mate, side), result in real_stockfish_matrix.items():
        assert result.batch.success
        puzzle = result.batch.puzzles[0]
        assert puzzle.verification.accepted
        assert puzzle.verification.exact_mate_plies == 2 * mate - 1
        assert puzzle.request.side_to_move == side
        assert result.batch.accounting.stockfish_rejected > 0
        assert result.batch.accounting.verifier_submitted > 0


@pytest.mark.stockfish_real
def test_real_stockfish_fixed_seed_replays(real_stockfish_matrix):
    expected = real_stockfish_matrix[1, "white"].batch.puzzles[0]
    replay = generate_stockfish_batch(
        request(max_candidate_attempts=100),
        1,
        binary=find_stockfish(),
        config=StockfishConfig(nodes_per_candidate=20_000),
    ).batch.puzzles[0]
    assert replay.puzzle_identity == expected.puzzle_identity
    assert replay.normalized_fen == expected.normalized_fen


@pytest.mark.stockfish_real
def test_real_stockfish_public_output_has_no_private_analysis(
    real_stockfish_matrix, tmp_path
):
    processes_before = _stockfish_processes()
    result = generate_public_batch(
        mate_moves=1,
        side_to_move="white",
        difficulty="easy",
        count=1,
        seed=1001,
        output=tmp_path / "public-stockfish",
        engine="stockfish",
        max_candidate_attempts=100,
        stockfish_nodes=20_000,
    )
    replay = generate_public_batch(
        mate_moves=1,
        side_to_move="white",
        difficulty="easy",
        count=1,
        seed=1001,
        output=tmp_path / "public-stockfish-replay",
        engine="stockfish",
        max_candidate_attempts=100,
        stockfish_nodes=20_000,
    )
    internal = real_stockfish_matrix[1, "white"].batch.puzzles[0].verification
    public_bytes = b"\n".join(
        path.read_bytes()
        for path in sorted(result.output_directory.rglob("*"))
        if path.is_file()
    )
    secrets = (
        *internal.key_moves,
        internal.certificate_sha256,
        json.dumps(internal.proof, ensure_ascii=False, sort_keys=True),
    )
    assert all(secret and secret.encode() not in public_bytes for secret in secrets)
    manifest = json.loads(result.manifest_path.read_text())
    serialized = json.dumps(manifest, sort_keys=True).lower()
    for forbidden in ("bestmove", "mate_score", "principal_variation", "proof", "key_moves"):
        assert forbidden not in serialized
    assert manifest["generation"]["backend"] == "stockfish"
    assert manifest["generation"]["stockfish_version"] == "17.1"
    assert result.manifest_path.read_bytes() == replay.manifest_path.read_bytes()
    assert [
        path.read_bytes()
        for path in sorted((result.output_directory / "puzzles").iterdir())
    ] == [
        path.read_bytes()
        for path in sorted((replay.output_directory / "puzzles").iterdir())
    ]
    assert verify_output(result.output_directory).valid
    assert verify_output(result.output_directory, deep=True).valid
    assert _stockfish_processes() == processes_before


@pytest.mark.stockfish_real
def test_real_rich_source_renders_png_svg_and_deep_verifies(tmp_path):
    result = generate_public_batch(
        mate_moves=1,
        side_to_move="white",
        difficulty="easy",
        count=1,
        seed=0,
        output=tmp_path / "rich-render",
        formats=("png", "svg"),
        engine="stockfish",
        density="rich",
        max_candidate_attempts=300,
        stockfish_nodes=10_000,
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    puzzle = manifest["puzzles"][0]
    assert puzzle["density"]["profile"] == "rich"
    assert 17 <= puzzle["density"]["piece_count"] <= 26
    assert puzzle["provenance"]["classification"] in {
        "stockfish_assisted_game_like",
        "stockfish_assisted_tactical_mutation",
        "stockfish_assisted_material_constructed",
        "stockfish_assisted_composition",
    }
    assert puzzle["diversity"]["source_family"] in {
        "game_like", "tactical_mutation", "material_constructed", "composition"
    }
    artifacts = {Path(item["file"]).suffix: item for item in puzzle["artifacts"]}
    png = (result.output_directory / artifacts[".png"]["file"]).read_bytes()
    svg = (result.output_directory / artifacts[".svg"]["file"]).read_bytes()
    image = Image.open(io.BytesIO(png))
    image.load()
    assert image.format == "PNG" and image.size == (896, 1044)
    assert ET.fromstring(svg).tag.endswith("svg")
    assert verify_output(result.output_directory, deep=True).valid
