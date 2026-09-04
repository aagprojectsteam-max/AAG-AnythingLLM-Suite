"""Controlled local Stockfish-assisted candidate discovery.

Stockfish is a private filter only. Every public puzzle produced by this module
must independently cross the existing :class:`MateVerifier` boundary.
"""

from __future__ import annotations

import hashlib
import math
import os
import random
import shutil
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import chess
import chess.engine

from . import STOCKFISH_DISCOVERY_VERSION
from .difficulty import assess_difficulty
from .density import DensityProfile, classify_piece_count, density_plan
from .diversity import PositionDiversity, describe_position, structural_similarity
from .generator import (
    BatchGenerationResult,
    Candidate,
    CandidateRejection,
    GeneratedPuzzle,
    GenerationAccounting,
    GenerationRequest,
)
from .position import normalized_fen, parse_board, puzzle_hash
from .verifier import MateVerifier


_KNOWN_PATHS = (Path("/usr/games/stockfish"), Path("/usr/bin/stockfish"))
_MAX_STOCKFISH_NODES = 1_000_000
_MAX_HASH_MB = 256
_MAX_RELEASE_VERIFIER_NODES = 150_000


class StockfishError(RuntimeError):
    """A controlled Stockfish discovery failure."""


class StockfishUnavailableError(StockfishError):
    """No usable local Stockfish executable was found."""


@dataclass(frozen=True)
class StockfishInfo:
    available: bool
    binary: Path | None
    version: str | None
    name: str | None


@dataclass(frozen=True)
class StockfishConfig:
    nodes_per_candidate: int = 50_000
    hash_mb: int = 16
    threads: int = 1
    startup_timeout: float = 5.0
    analysis_timeout: float = 10.0

    def __post_init__(self) -> None:
        if (
            isinstance(self.nodes_per_candidate, bool)
            or not isinstance(self.nodes_per_candidate, int)
            or not 100 <= self.nodes_per_candidate <= _MAX_STOCKFISH_NODES
        ):
            raise ValueError("Stockfish nodes must be an integer between 100 and 1,000,000")
        if (
            isinstance(self.hash_mb, bool)
            or not isinstance(self.hash_mb, int)
            or not 1 <= self.hash_mb <= _MAX_HASH_MB
        ):
            raise ValueError("Stockfish hash must be an integer between 1 and 256 MB")
        if self.threads != 1:
            raise ValueError("Stockfish threads must be exactly 1 for reproducibility")
        if not isinstance(self.startup_timeout, (int, float)) or not (
            0.1 <= self.startup_timeout <= 30
        ):
            raise ValueError("Stockfish startup timeout must be between 0.1 and 30 seconds")
        if not isinstance(self.analysis_timeout, (int, float)) or not (
            0.1 <= self.analysis_timeout <= 60
        ):
            raise ValueError("Stockfish analysis timeout must be between 0.1 and 60 seconds")


@dataclass(frozen=True)
class StockfishFilterResult:
    mate_score: int | None
    nodes: int
    elapsed_ms: int


@dataclass(frozen=True)
class StockfishBatchResult:
    batch: BatchGenerationResult
    stockfish: StockfishInfo
    config: StockfishConfig


def _usable_binary(path: Path) -> Path | None:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        return None
    return resolved


def find_stockfish() -> Path | None:
    """Return the real absolute local Stockfish path, if available."""

    candidates: list[Path] = []
    discovered = shutil.which("stockfish")
    if discovered:
        candidates.append(Path(discovered))
    candidates.extend(_KNOWN_PATHS)
    seen: set[Path] = set()
    for candidate in candidates:
        usable = _usable_binary(candidate)
        if usable is not None and usable not in seen:
            return usable
        if usable is not None:
            seen.add(usable)
    return None


class StockfishSession:
    """One bounded synchronous UCI process with deterministic configuration."""

    def __init__(self, binary: Path, config: StockfishConfig):
        usable = _usable_binary(binary)
        if usable is None:
            raise StockfishUnavailableError(f"Stockfish binary is not executable: {binary}")
        self.binary = usable
        self.config = config
        self.engine: chess.engine.SimpleEngine | None = None
        self.info: StockfishInfo | None = None

    def __enter__(self) -> StockfishSession:
        engine: chess.engine.SimpleEngine | None = None
        try:
            engine = chess.engine.SimpleEngine.popen_uci(
                str(self.binary), timeout=self.config.startup_timeout
            )
            engine.timeout = self.config.analysis_timeout
            engine.configure(
                {
                    "Threads": self.config.threads,
                    "Hash": self.config.hash_mb,
                    "UCI_LimitStrength": False,
                    "Clear Hash": None,
                }
            )
        except (OSError, TimeoutError, chess.engine.EngineError) as exc:
            if engine is not None:
                engine.close()
            raise StockfishError(f"could not start/configure Stockfish: {exc}") from exc
        self.engine = engine
        name = str(engine.id.get("name", "Stockfish")).strip()
        version = name.removeprefix("Stockfish").strip() or "unknown"
        self.info = StockfishInfo(True, self.binary, version, name)
        return self

    def analyse_mate(self, board: chess.Board) -> StockfishFilterResult:
        if self.engine is None:
            raise StockfishError("Stockfish session is not running")
        started = time.monotonic()
        try:
            self.engine.configure({"Clear Hash": None})
            result = self.engine.analyse(
                board,
                chess.engine.Limit(nodes=self.config.nodes_per_candidate),
                game=object(),
            )
        except (TimeoutError, chess.engine.EngineError) as exc:
            raise StockfishError(f"Stockfish analysis failed: {exc}") from exc
        score = result.get("score")
        mate_score = score.pov(board.turn).mate() if score is not None else None
        nodes = result.get("nodes", 0)
        return StockfishFilterResult(
            mate_score=mate_score,
            nodes=int(nodes) if isinstance(nodes, int) else 0,
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )

    def close(self) -> None:
        engine, self.engine = self.engine, None
        if engine is None:
            return
        try:
            engine.quit()
        except (OSError, TimeoutError, chess.engine.EngineError):
            engine.close()

    def __exit__(self, unused_type, unused_value, unused_traceback) -> None:
        self.close()


def inspect_stockfish(binary: Path | None = None) -> StockfishInfo:
    selected = binary or find_stockfish()
    if selected is None:
        return StockfishInfo(False, None, None, None)
    with StockfishSession(selected, StockfishConfig()) as session:
        if session.info is None:
            raise StockfishError("Stockfish did not report identity information")
        return session.info


# Bounded material families. Uppercase letters describe attacker and defender
# pieces independently of requested colour; no family is trusted as a puzzle.
_MATERIAL_PROFILES: tuple[tuple[str, str, str], ...] = (
    ("queen", "Q", ""),
    ("rook", "R", ""),
    ("queen-bishop", "QB", ""),
    ("queen-knight", "QN", ""),
    ("queen-rook", "QR", ""),
    ("two-rooks", "RR", ""),
    ("rook-bishop", "RB", ""),
    ("rook-knight", "RN", ""),
    ("queen-v-rook", "Q", "R"),
    ("queen-v-bishop", "Q", "B"),
    ("queen-v-knight", "Q", "N"),
    ("queen-rook-v-rook", "QR", "R"),
    ("queen-bishop-v-rook", "QB", "R"),
    ("queen-knight-v-rook", "QN", "R"),
    ("queen-v-pawn", "Q", "P"),
)

_SOURCE_SCHEDULE = (
    "material_constructed",
    "game_like",
    "tactical_mutation",
    "material_constructed",
    "game_like",
    "composition",
    "material_constructed",
    "tactical_mutation",
    "game_like",
    "material_constructed",
    "game_like",
    "tactical_mutation",
    "material_constructed",
    "composition",
    "game_like",
    "material_constructed",
    "tactical_mutation",
    "material_constructed",
    "game_like",
    "material_constructed",
)

_DEEP_MATE_SOURCE_SCHEDULE = (
    "composition",
    "material_constructed",
    "composition",
    "game_like",
    "composition",
    "material_constructed",
    "tactical_mutation",
    "composition",
    "material_constructed",
    "game_like",
)


def _colored_piece(symbol: str, color: chess.Color) -> chess.Piece:
    return chess.Piece.from_symbol(symbol if color == chess.WHITE else symbol.lower())


def _pawn_group(rank_index: int, parity: int) -> tuple[tuple[int, int], ...]:
    """Four mutually locked white/black pawn pairs on alternating files."""

    return tuple(
        (
            chess.square(file_index, rank_index),
            chess.square(file_index, rank_index + 1),
        )
        for file_index in range(parity, 8, 2)
    )


_PAWN_GROUPS = tuple(
    _pawn_group(rank_index, parity)
    for rank_index in range(1, 6)
    for parity in (0, 1)
)
_RICH_PAWN_SHELLS = tuple(
    first + second
    for first_index, first in enumerate(_PAWN_GROUPS)
    for second in _PAWN_GROUPS[first_index + 1 :]
    if abs(chess.square_rank(first[0][0]) - chess.square_rank(second[0][0])) >= 2
)


def _density_shell(
    rng: random.Random, profile: DensityProfile
) -> tuple[tuple[int, int], ...]:
    if profile == "sparse":
        group = rng.choice(_PAWN_GROUPS)
        return (rng.choice(group),)
    if profile == "normal":
        return rng.choice(_PAWN_GROUPS)
    return rng.choice(_RICH_PAWN_SHELLS)


def _weighted_playout_move(
    board: chess.Board, rng: random.Random, *, tactical: bool
) -> chess.Move:
    weighted: list[tuple[int, str, chess.Move]] = []
    for move in board.legal_moves:
        piece = board.piece_at(move.from_square)
        target_rank = chess.square_rank(move.to_square)
        target_file = chess.square_file(move.to_square)
        center = target_file in (2, 3, 4, 5) and target_rank in (2, 3, 4, 5)
        weight = 10
        weight += 20 * int(board.is_capture(move))
        weight += (24 if tactical else 8) * int(board.gives_check(move))
        weight += 20 * int(move.promotion is not None)
        weight += 8 * int(board.is_castling(move))
        weight += 4 * int(center)
        if piece and piece.piece_type in (chess.KNIGHT, chess.BISHOP):
            home_rank = 0 if piece.color == chess.WHITE else 7
            weight += 5 * int(chess.square_rank(move.from_square) == home_rank)
        if piece and piece.piece_type == chess.QUEEN and board.fullmove_number < 8:
            weight -= 5
        weighted.append((max(1, weight), move.uci(), move))
    weighted.sort(key=lambda item: item[1])
    total = sum(item[0] for item in weighted)
    ticket = rng.randrange(total)
    for weight, unused_uci, move in weighted:
        if ticket < weight:
            return move
        ticket -= weight
    raise AssertionError("weighted legal-move selection exhausted")


def _construct_legal_playout(
    rng: random.Random,
    request: GenerationRequest,
    ordinal: int,
    density: DensityProfile,
    *,
    tactical: bool,
) -> Candidate | None:
    """Construct a reachable game-like candidate using bounded legal plies."""

    if density == "sparse":
        return None
    minimum, maximum = {"normal": (10, 16), "rich": (17, 26)}[density]
    expected_turn = chess.WHITE if request.side_to_move == "white" else chess.BLACK
    for unused_game in range(4):
        board = chess.Board()
        suitable_positions: list[chess.Board] = []
        for ply in range(140):
            if board.is_game_over(claim_draw=False):
                break
            board.push(_weighted_playout_move(board, rng, tactical=tactical))
            piece_count = len(board.piece_map())
            suitable = (
                ply >= 18
                and minimum <= piece_count <= maximum
                and board.turn == expected_turn
                and board.is_valid()
                and not board.is_game_over(claim_draw=False)
            )
            if suitable:
                suitable_positions.append(board.copy(stack=False))
                if len(suitable_positions) >= 5:
                    break
        if suitable_positions:
            chosen = suitable_positions[rng.randrange(len(suitable_positions))]
            family = "tactical_mutation" if tactical else "game_like"
            return Candidate(
                ordinal=ordinal,
                strategy=f"stockfish/{density}/{family}",
                fen=chosen.fen(en_passant="legal"),
            )
    return None


def _empty_squares(board: chess.Board, *, pawn: bool = False) -> tuple[chess.Square, ...]:
    return tuple(
        square
        for square in chess.SQUARES
        if board.piece_at(square) is None
        and (not pawn or chess.square_rank(square) not in (0, 7))
    )


def _place_varied_pawns(
    board: chess.Board, rng: random.Random, total: int
) -> bool:
    """Place asymmetric, non-wall pawn structures with legal per-colour caps."""

    counts = (total // 2, total - total // 2)
    if rng.randrange(2):
        counts = counts[::-1]
    for color, count in zip((chess.WHITE, chess.BLACK), counts):
        files = list(range(8))
        rng.shuffle(files)
        placed = 0
        for file_index in files:
            if placed >= count:
                break
            ranks = list(range(1, 6) if color == chess.WHITE else range(2, 7))
            rng.shuffle(ranks)
            ranks.sort(
                key=lambda rank: (
                    sum(
                        1
                        for square, piece in board.piece_map().items()
                        if piece.piece_type == chess.PAWN
                        and piece.color == color
                        and chess.square_rank(square) == rank
                    ),
                    rng.random(),
                )
            )
            for rank_index in ranks:
                square = chess.square(file_index, rank_index)
                if board.piece_at(square) is not None:
                    continue
                opposite = board.piece_at(
                    chess.square(file_index, rank_index + (-1 if color else 1))
                ) if (color and rank_index > 0) or (not color and rank_index < 7) else None
                if opposite and opposite.piece_type == chess.PAWN and opposite.color != color:
                    continue
                board.set_piece_at(square, chess.Piece(chess.PAWN, color))
                placed += 1
                break
        if placed != count:
            return False
    return True


def _place_staggered_locked_pawns(
    board: chess.Board, rng: random.Random, pair_count: int
) -> bool:
    """Place immobile composition pairs without a repeated rank scaffold."""

    files = list(range(8))
    rng.shuffle(files)
    ranks = [1, 2, 3, 4, 5]
    rng.shuffle(ranks)
    for index, file_index in enumerate(files[:pair_count]):
        white_rank = ranks[index % len(ranks)]
        if index >= len(ranks):
            white_rank = ranks[(index + 2) % len(ranks)]
        white_square = chess.square(file_index, white_rank)
        black_square = chess.square(file_index, white_rank + 1)
        if board.piece_at(white_square) or board.piece_at(black_square):
            return False
        board.set_piece_at(white_square, chess.Piece(chess.PAWN, chess.WHITE))
        board.set_piece_at(black_square, chess.Piece(chess.PAWN, chess.BLACK))
    return True


def _construct_material_candidate(
    rng: random.Random,
    request: GenerationRequest,
    ordinal: int,
    density: DensityProfile,
) -> Candidate | None:
    attacker = chess.WHITE if request.side_to_move == "white" else chess.BLACK
    defender = not attacker
    bounds = {"sparse": (5, 9), "normal": (10, 16), "rich": (17, 26)}[density]
    profile_name, attacker_material, defender_material = _MATERIAL_PROFILES[
        (ordinal * 7 + request.seed) % len(_MATERIAL_PROFILES)
    ]
    for unused in range(100):
        board = chess.Board(None)
        board.turn = attacker
        target_count = rng.randint(*bounds)
        edge = [
            square for square in chess.SQUARES
            if chess.square_file(square) in (0, 7) or chess.square_rank(square) in (0, 7)
        ]
        defender_pool = edge if rng.random() < 0.72 else list(chess.SQUARES)
        defender_king = rng.choice(defender_pool)
        board.set_piece_at(defender_king, chess.Piece(chess.KING, defender))
        king_choices = [
            square for square in chess.SQUARES
            if board.piece_at(square) is None
            and 2 <= chess.square_distance(square, defender_king) <= 6
        ]
        if not king_choices:
            continue
        board.set_piece_at(rng.choice(king_choices), chess.Piece(chess.KING, attacker))
        base_material = [(symbol, attacker) for symbol in attacker_material]
        base_material += [(symbol, defender) for symbol in defender_material]
        if len(base_material) + 2 > target_count:
            continue
        for symbol, color in base_material:
            choices = _empty_squares(board, pawn=symbol == "P")
            if not choices:
                break
            board.set_piece_at(rng.choice(choices), _colored_piece(symbol, color))
        else:
            remaining = target_count - len(board.piece_map())
            pawn_target = min(
                remaining,
                rng.randint(
                    1 if density == "sparse" else 3 if density == "normal" else 7,
                    min(remaining, 4 if density == "sparse" else 9 if density == "normal" else 14),
                ) if remaining else 0,
            )
            if pawn_target and not _place_varied_pawns(board, rng, pawn_target):
                continue
            remaining = target_count - len(board.piece_map())
            piece_cycle = ["N", "B", "R", "N", "B", "Q", "R"]
            rng.shuffle(piece_cycle)
            index = 0
            while remaining > 0:
                color = attacker if index % 2 == 0 else defender
                symbol = piece_cycle[index % len(piece_cycle)]
                if symbol == "Q" and len(board.pieces(chess.QUEEN, color)) >= 1:
                    symbol = "N" if index % 3 else "B"
                if symbol == "R" and len(board.pieces(chess.ROOK, color)) >= 2:
                    symbol = "B"
                if symbol == "N" and len(board.pieces(chess.KNIGHT, color)) >= 2:
                    symbol = "B"
                if symbol == "B" and len(board.pieces(chess.BISHOP, color)) >= 2:
                    symbol = "N"
                choices = _empty_squares(board)
                if not choices:
                    break
                board.set_piece_at(rng.choice(choices), _colored_piece(symbol, color))
                remaining -= 1
                index += 1
            if remaining == 0 and board.is_valid() and not board.is_game_over(claim_draw=False):
                return Candidate(
                    ordinal=ordinal,
                    strategy=f"stockfish/{density}/material_constructed/{profile_name}",
                    fen=board.fen(en_passant="legal"),
                )
    return None


def _construct_composition_candidate(
    rng: random.Random,
    request: GenerationRequest,
    ordinal: int,
    density: DensityProfile,
) -> Candidate | None:
    """Preserve the proven legacy shell as a low-weight fallback source."""

    attacker = chess.WHITE if request.side_to_move == "white" else chess.BLACK
    defender = not attacker
    profile_name, attacker_material, defender_material = _MATERIAL_PROFILES[
        (ordinal - 1) % len(_MATERIAL_PROFILES)
    ]
    if density == "rich":
        defender_material = defender_material.replace("P", "N")
    edge_squares = tuple(
        square for square in chess.SQUARES
        if chess.square_file(square) in (0, 7) or chess.square_rank(square) in (0, 7)
    )
    for unused in range(100):
        board = chess.Board(None)
        board.turn = attacker
        occupied: set[int] = set()
        if density == "sparse":
            for white_square, black_square in _density_shell(rng, density):
                board.set_piece_at(white_square, chess.Piece(chess.PAWN, chess.WHITE))
                board.set_piece_at(black_square, chess.Piece(chess.PAWN, chess.BLACK))
                occupied.update((white_square, black_square))
        else:
            pair_count = 4 if density == "normal" else 8
            if not _place_staggered_locked_pawns(board, rng, pair_count):
                continue
            occupied.update(board.piece_map())
        defender_king = rng.choice(tuple(square for square in edge_squares if square not in occupied))
        board.set_piece_at(defender_king, chess.Piece(chess.KING, defender))
        attacker_king = rng.choice(tuple(
            square for square in chess.SQUARES
            if square not in occupied and 2 <= chess.square_distance(square, defender_king) <= 5
        ))
        board.set_piece_at(attacker_king, chess.Piece(chess.KING, attacker))
        occupied.update((defender_king, attacker_king))
        for symbol, color in (
            *[(symbol, attacker) for symbol in attacker_material],
            *[(symbol, defender) for symbol in defender_material],
        ):
            choices = tuple(
                square for square in chess.SQUARES
                if square not in occupied and (symbol != "P" or chess.square_rank(square) not in (0, 7))
            )
            square = rng.choice(choices)
            occupied.add(square)
            board.set_piece_at(square, _colored_piece(symbol, color))
        if board.is_valid() and not board.is_game_over(claim_draw=False):
            return Candidate(
                ordinal=ordinal,
                strategy=f"stockfish/{density}/composition/{profile_name}",
                fen=board.fen(en_passant="legal"),
            )
    return None


def _construct_candidate(
    rng: random.Random,
    request: GenerationRequest,
    ordinal: int,
    density: DensityProfile,
) -> Candidate | None:
    schedule = _DEEP_MATE_SOURCE_SCHEDULE if request.mate_moves == 3 else _SOURCE_SCHEDULE
    source = schedule[(ordinal - 1) % len(schedule)]
    if source == "game_like":
        return _construct_legal_playout(
            rng, request, ordinal, density, tactical=False
        )
    if source == "tactical_mutation":
        return _construct_legal_playout(
            rng, request, ordinal, density, tactical=True
        )
    if source == "composition":
        return _construct_composition_candidate(rng, request, ordinal, density)
    return _construct_material_candidate(rng, request, ordinal, density)


def _source_family(candidate: Candidate) -> str:
    parts = candidate.strategy.split("/")
    return parts[2] if len(parts) >= 3 else "composition"


def _transform_square(square: chess.Square, transform: int) -> chess.Square:
    file_index = chess.square_file(square)
    rank_index = chess.square_rank(square)
    coordinates = (
        (file_index, rank_index),
        (7 - rank_index, file_index),
        (7 - file_index, 7 - rank_index),
        (rank_index, 7 - file_index),
        (7 - file_index, rank_index),
        (file_index, 7 - rank_index),
        (rank_index, file_index),
        (7 - rank_index, 7 - file_index),
    )
    transformed_file, transformed_rank = coordinates[transform]
    return chess.square(transformed_file, transformed_rank)


def symmetry_identity(board: chess.Board, mate_moves: int) -> str:
    """Canonical hash across the eight geometric board symmetries."""

    variants: list[str] = []
    for transform in range(8):
        transformed = chess.Board(None)
        transformed.turn = board.turn
        for square, piece in board.piece_map().items():
            transformed.set_piece_at(_transform_square(square, transform), piece)
        variants.append(normalized_fen(transformed))
    material = f"aag-symmetry-v1|mate={mate_moves}|{min(variants)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _accounting(
    *,
    attempted: int,
    structural: int,
    duplicates: int,
    symmetry_duplicates: int,
    stockfish_rejected: int,
    verifier_submitted: int,
    verifier_rejected: int,
    difficulty_rejected: int,
    accepted: int,
    stockfish_nodes: int,
    stockfish_ms: int,
    verifier_ms: int,
    quality_rejected: int,
    similarity_rejected: int,
) -> GenerationAccounting:
    return GenerationAccounting(
        candidates_attempted=attempted,
        structurally_rejected=structural,
        duplicate_rejected=duplicates,
        verifier_rejected=verifier_rejected,
        difficulty_rejected=difficulty_rejected,
        accepted=accepted,
        stockfish_rejected=stockfish_rejected,
        symmetry_duplicate_rejected=symmetry_duplicates,
        verifier_submitted=verifier_submitted,
        stockfish_nodes=stockfish_nodes,
        stockfish_analysis_ms=stockfish_ms,
        verifier_analysis_ms=verifier_ms,
        quality_rejected=quality_rejected,
        similarity_rejected=similarity_rejected,
    )


def generate_stockfish_batch(
    request: GenerationRequest,
    count: int,
    *,
    binary: Path | None = None,
    config: StockfishConfig | None = None,
) -> StockfishBatchResult:
    """Discover a bounded batch; Stockfish filters and MateVerifier accepts."""

    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 100:
        raise ValueError("count must be an integer between 1 and 100")
    selected = binary or find_stockfish()
    if selected is None:
        raise StockfishUnavailableError("Stockfish is unavailable; use the builtin engine")
    effective_config = config or StockfishConfig()
    rng = random.Random(request.seed)
    attempted = structural = duplicates = symmetry_duplicates = 0
    stockfish_rejected = verifier_submitted = verifier_rejected = 0
    difficulty_rejected = stockfish_nodes = stockfish_ms = verifier_ms = 0
    quality_rejected = similarity_rejected = 0
    puzzles: list[GeneratedPuzzle] = []
    rejections: list[CandidateRejection] = []
    seen_identities: set[str] = set()
    seen_symmetries: set[str] = set()
    selected_descriptors: list[PositionDiversity] = []
    selected_sources: Counter[str] = Counter()
    planned_density = density_plan(
        request.seed,
        count,
        request.density,
        context=(
            f"stockfish|mate={request.mate_moves}|side={request.side_to_move}|"
            f"difficulty={request.difficulty}"
        ),
    )

    with StockfishSession(selected, effective_config) as session:
        if session.info is None:
            raise StockfishError("Stockfish identity was unavailable after startup")
        for ordinal in range(1, request.max_candidate_attempts + 1):
            attempted += 1
            target_density = planned_density[len(puzzles)]
            candidate = _construct_candidate(rng, request, ordinal, target_density)
            if candidate is None:
                structural += 1
                continue
            board = parse_board(candidate.fen)
            legal_move_count = board.legal_moves.count()
            if (
                (request.mate_moves == 3 and legal_move_count > 26)
                or (request.mate_moves == 2 and legal_move_count > 45)
            ):
                structural += 1
                continue
            identity = puzzle_hash(board, request.mate_moves)
            if identity in seen_identities:
                duplicates += 1
                continue
            seen_identities.add(identity)
            symmetry = symmetry_identity(board, request.mate_moves)
            if symmetry in seen_symmetries:
                symmetry_duplicates += 1
                continue
            seen_symmetries.add(symmetry)

            filtered = session.analyse_mate(board)
            stockfish_nodes += filtered.nodes
            stockfish_ms += filtered.elapsed_ms
            if filtered.mate_score != request.mate_moves:
                stockfish_rejected += 1
                continue

            verifier_submitted += 1
            verification = MateVerifier(
                max_nodes=request.verifier_max_nodes,
                max_seconds=float(request.verifier_max_seconds),
            ).verify(
                candidate.fen,
                request.mate_moves,
                require_unique_key=request.require_unique_key,
                dual_policy=request.dual_policy,
            )
            verifier_ms += verification.elapsed_ms
            if not verification.accepted:
                verifier_rejected += 1
                continue
            # Leave a deterministic complexity margin for later --deep checks.
            # This is a release-quality filter after acceptance, not chess truth.
            if verification.nodes > _MAX_RELEASE_VERIFIER_NODES:
                verifier_rejected += 1
                continue
            if (
                verification.normalized_fen is None
                or verification.puzzle_hash is None
                or verification.certificate_sha256 is None
            ):
                raise RuntimeError("accepted verifier result is missing required metadata")
            if verification.puzzle_hash != identity:
                raise RuntimeError("verifier identity disagrees with Stockfish candidate")
            difficulty = assess_difficulty(verification)
            if difficulty.label != request.difficulty:
                difficulty_rejected += 1
                continue
            measured_density = classify_piece_count(len(board.piece_map()))
            if measured_density.profile != target_density:
                raise RuntimeError("accepted candidate density disagrees with density plan")
            source_family = _source_family(candidate)
            descriptor = describe_position(
                board,
                source_family=source_family,
                proof=verification.proof,
            )
            progress = attempted / request.max_candidate_attempts
            minimum_quality = 45 if progress < 0.70 else 32
            if descriptor.quality_score < minimum_quality:
                quality_rejected += 1
                continue
            similarity_limit = 76 if progress < 0.75 else 91
            if any(
                structural_similarity(descriptor, previous) >= similarity_limit
                for previous in selected_descriptors[-20:]
            ):
                similarity_rejected += 1
                continue
            if (
                count >= 5
                and progress < 0.80
                and selected_sources[source_family] >= math.ceil(count * 0.60)
            ):
                similarity_rejected += 1
                continue
            puzzles.append(
                GeneratedPuzzle(
                    request=request,
                    candidate=candidate,
                    attempt_number=attempted,
                    normalized_fen=verification.normalized_fen,
                    puzzle_identity=verification.puzzle_hash,
                    verification=verification,
                    difficulty=difficulty,
                    provenance=f"stockfish_assisted_{source_family}",
                    provenance_verified=source_family in {"game_like", "tactical_mutation"},
                    generator_version=STOCKFISH_DISCOVERY_VERSION,
                    source_family=source_family,
                    motif=descriptor.motif,
                    quality_score=descriptor.quality_score,
                    diversity_fingerprint=descriptor.fingerprint,
                )
            )
            selected_descriptors.append(descriptor)
            selected_sources[source_family] += 1
            if len(puzzles) == count:
                batch = BatchGenerationResult(
                    success=True,
                    requested_count=count,
                    puzzles=tuple(puzzles),
                    accounting=_accounting(
                        attempted=attempted,
                        structural=structural,
                        duplicates=duplicates,
                        symmetry_duplicates=symmetry_duplicates,
                        stockfish_rejected=stockfish_rejected,
                        verifier_submitted=verifier_submitted,
                        verifier_rejected=verifier_rejected,
                        difficulty_rejected=difficulty_rejected,
                        accepted=len(puzzles),
                        stockfish_nodes=stockfish_nodes,
                        stockfish_ms=stockfish_ms,
                        verifier_ms=verifier_ms,
                        quality_rejected=quality_rejected,
                        similarity_rejected=similarity_rejected,
                    ),
                    failure_reason=None,
                    rejections=tuple(rejections),
                    generator_version=STOCKFISH_DISCOVERY_VERSION,
                )
                return StockfishBatchResult(batch, session.info, effective_config)

        batch = BatchGenerationResult(
            success=False,
            requested_count=count,
            puzzles=tuple(puzzles),
            accounting=_accounting(
                attempted=attempted,
                structural=structural,
                duplicates=duplicates,
                symmetry_duplicates=symmetry_duplicates,
                stockfish_rejected=stockfish_rejected,
                verifier_submitted=verifier_submitted,
                verifier_rejected=verifier_rejected,
                difficulty_rejected=difficulty_rejected,
                accepted=len(puzzles),
                stockfish_nodes=stockfish_nodes,
                stockfish_ms=stockfish_ms,
                verifier_ms=verifier_ms,
                quality_rejected=quality_rejected,
                similarity_rejected=similarity_rejected,
            ),
            failure_reason="candidate_attempt_budget_exhausted",
            rejections=tuple(rejections),
            generator_version=STOCKFISH_DISCOVERY_VERSION,
        )
        return StockfishBatchResult(batch, session.info, effective_config)
