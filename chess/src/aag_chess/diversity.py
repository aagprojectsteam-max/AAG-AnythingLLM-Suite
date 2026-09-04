"""Deterministic, public-safe structural diversity and selection features.

These features never establish chess truth.  They describe and rank positions
which have already crossed the authoritative :class:`MateVerifier` boundary.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from typing import Any

import chess


DIVERSITY_FINGERPRINT_VERSION = "aag-position-diversity-v1"


def _transform_square(square: chess.Square, transform: int) -> chess.Square:
    file_index = chess.square_file(square)
    rank_index = chess.square_rank(square)
    variants = (
        (file_index, rank_index),
        (7 - file_index, rank_index),
        (file_index, 7 - rank_index),
        (7 - file_index, 7 - rank_index),
        (rank_index, file_index),
        (7 - rank_index, file_index),
        (rank_index, 7 - file_index),
        (7 - rank_index, 7 - file_index),
    )
    return chess.square(*variants[transform])


def symmetry_class(board: chess.Board) -> str:
    """Return a visual equivalence hash across the eight board symmetries."""

    variants: list[str] = []
    for transform in range(8):
        rows = sorted(
            f"{piece.symbol()}@{chess.square_name(_transform_square(square, transform))}"
            for square, piece in board.piece_map().items()
        )
        variants.append("|".join(rows))
    return hashlib.sha256(min(variants).encode("ascii")).hexdigest()


def _material_signature(board: chess.Board, *, pawns: bool = True) -> str:
    counts = Counter(
        piece.symbol()
        for piece in board.piece_map().values()
        if pawns or piece.piece_type != chess.PAWN
    )
    return "".join(f"{symbol}{counts[symbol]}" for symbol in sorted(counts))


def _pawn_signature(board: chess.Board) -> str:
    rows = sorted(
        f"{'w' if piece.color else 'b'}{chess.square_name(square)}"
        for square, piece in board.piece_map().items()
        if piece.piece_type == chess.PAWN
    )
    return hashlib.sha256("|".join(rows).encode("ascii")).hexdigest()


def _king_region(square: chess.Square) -> str:
    file_index = chess.square_file(square)
    rank_index = chess.square_rank(square)
    horizontal = "queenside" if file_index <= 2 else "kingside" if file_index >= 5 else "center"
    vertical = "back" if rank_index in (0, 7) else "advanced" if rank_index in (1, 6) else "middle"
    return f"{horizontal}-{vertical}"


def _longest_pawn_row(board: chess.Board) -> int:
    longest = 0
    for color in chess.COLORS:
        for rank_index in range(8):
            files = sorted(
                chess.square_file(square)
                for square in board.pieces(chess.PAWN, color)
                if chess.square_rank(square) == rank_index
            )
            run = previous = -2
            for file_index in files:
                run = run + 1 if file_index == previous + 1 else 1
                longest = max(longest, run)
                previous = file_index
    return longest


def _pawn_wall_ratio(board: chess.Board) -> float:
    pawns = [
        (square, piece)
        for square, piece in board.piece_map().items()
        if piece.piece_type == chess.PAWN
    ]
    if not pawns:
        return 0.0
    paired = 0
    for square, piece in pawns:
        file_index = chess.square_file(square)
        rank_index = chess.square_rank(square)
        for delta in (-1, 1):
            other_rank = rank_index + delta
            if 0 <= other_rank <= 7:
                other = board.piece_at(chess.square(file_index, other_rank))
                if other and other.piece_type == chess.PAWN and other.color != piece.color:
                    paired += 1
                    break
    return paired / len(pawns)


def _mirror_symmetry(board: chess.Board) -> float:
    pieces = board.piece_map()
    if not pieces:
        return 1.0
    matched = 0
    for square, piece in pieces.items():
        mirror = chess.square(7 - chess.square_file(square), chess.square_rank(square))
        if pieces.get(mirror) == piece:
            matched += 1
    return matched / len(pieces)


def classify_motif(board: chess.Board, proof: dict[str, Any] | None) -> str:
    """Classify broad verified-proof geometry, conservatively."""

    if not proof or not proof.get("moves"):
        return "mixed_other"
    mating_symbols: set[str] = set()

    def walk(node: dict[str, Any]) -> None:
        for branch in node.get("moves", []):
            san = str(branch.get("san", ""))
            if san.endswith("#"):
                symbol = san.lstrip("KQRBN")
                mating_symbols.add(san[0] if san and san[0] in "KQRBN" else "P")
            child = branch.get("child")
            if isinstance(child, dict):
                walk(child)

    walk(proof)
    if len(mating_symbols) > 1:
        return "double_piece_coordination"
    symbol = next(iter(mating_symbols), None)
    defender = not board.turn
    defender_king = board.king(defender)
    if symbol in {"Q", "R"} and defender_king is not None and chess.square_rank(defender_king) in (0, 7):
        return "back_rank_or_edge_net"
    return {
        "Q": "queen_mating_net",
        "R": "rook_file_or_rank_mate",
        "B": "bishop_line_mate",
        "N": "knight_mating_pattern",
        "P": "pawn_supported_mate",
        "K": "king_box_net",
    }.get(symbol, "mixed_other")


@dataclass(frozen=True)
class PositionDiversity:
    fingerprint: str
    symmetry_class: str
    source_family: str
    motif: str
    material_signature: str
    nonpawn_material_signature: str
    pawn_structure_signature: str
    king_regions: tuple[str, str]
    piece_vector: tuple[int, ...]
    piece_count: int
    pawn_count: int
    occupied_files: int
    occupied_ranks: int
    center_occupancy: int
    queenside_occupancy: int
    kingside_occupancy: int
    longest_pawn_row: int
    pawn_wall_ratio: float
    symmetry_score: float
    quality_score: int

    def public_dict(self) -> dict[str, Any]:
        return {
            "fingerprint_version": DIVERSITY_FINGERPRINT_VERSION,
            "source_family": self.source_family,
            "motif": self.motif,
            "quality_score": self.quality_score,
            "material_signature": self.material_signature,
            "pawn_structure_sha256": self.pawn_structure_signature,
            "king_regions": list(self.king_regions),
            "occupied_files": self.occupied_files,
            "occupied_ranks": self.occupied_ranks,
        }


def describe_position(
    board: chess.Board,
    *,
    source_family: str,
    proof: dict[str, Any] | None = None,
) -> PositionDiversity:
    pieces = board.piece_map()
    files = {chess.square_file(square) for square in pieces}
    ranks = {chess.square_rank(square) for square in pieces}
    vector = tuple(
        len(board.pieces(piece_type, color))
        for color in chess.COLORS
        for piece_type in range(chess.PAWN, chess.KING + 1)
    )
    pawn_count = sum(vector[color * 6] for color in range(2))
    white_king = board.king(chess.WHITE)
    black_king = board.king(chess.BLACK)
    if white_king is None or black_king is None:
        raise ValueError("both kings are required for a diversity descriptor")
    pawn_row = _longest_pawn_row(board)
    pawn_rank_concentration = max(
        (
            sum(
                1
                for square, piece in pieces.items()
                if piece.piece_type == chess.PAWN and chess.square_rank(square) == rank_index
            )
            for rank_index in range(8)
        ),
        default=0,
    )
    wall_ratio = _pawn_wall_ratio(board)
    symmetry = _mirror_symmetry(board)
    motif = classify_motif(board, proof)
    source_bonus = {
        "game_like": 14,
        "tactical_mutation": 10,
        "material_constructed": 5,
        "composition": 0,
        "builtin": -5,
    }.get(source_family, 0)
    quality = 48 + source_bonus
    quality += min(10, 2 * len({piece.piece_type for piece in pieces.values()}))
    quality += min(8, len(files) + len(ranks) - 8)
    if pawn_row >= 6:
        quality -= 25
    elif pawn_row >= 4:
        quality -= 8
    if pawn_rank_concentration >= 6:
        quality -= 20
    elif pawn_rank_concentration >= 4:
        quality -= 10
    if pawn_count >= 8 and wall_ratio >= 0.7:
        quality -= 8
    if symmetry >= 0.75 and len(pieces) >= 10:
        quality -= 12
    for color in chess.COLORS:
        quality -= 7 * max(0, len(board.pieces(chess.QUEEN, color)) - 1)
        quality -= 4 * max(0, len(board.pieces(chess.ROOK, color)) - 2)
    if proof:
        defender_branches = sum(
            len(node.get("moves", []))
            for node in _proof_nodes(proof)
            if node.get("role") == "defender"
        )
        quality += min(8, defender_branches)
    material = _material_signature(board)
    nonpawn = _material_signature(board, pawns=False)
    pawn_signature = _pawn_signature(board)
    king_regions = (_king_region(white_king), _king_region(black_king))
    structural = "|".join(
        (
            DIVERSITY_FINGERPRINT_VERSION,
            symmetry_class(board),
            material,
            pawn_signature,
            ",".join(king_regions),
            ",".join(map(str, vector)),
            source_family,
            motif,
        )
    )
    return PositionDiversity(
        fingerprint=hashlib.sha256(structural.encode("utf-8")).hexdigest(),
        symmetry_class=symmetry_class(board),
        source_family=source_family,
        motif=motif,
        material_signature=material,
        nonpawn_material_signature=nonpawn,
        pawn_structure_signature=pawn_signature,
        king_regions=king_regions,
        piece_vector=vector,
        piece_count=len(pieces),
        pawn_count=pawn_count,
        occupied_files=len(files),
        occupied_ranks=len(ranks),
        center_occupancy=sum(bool(chess.BB_SQUARES[square] & chess.BB_CENTER) for square in pieces),
        queenside_occupancy=sum(chess.square_file(square) <= 2 for square in pieces),
        kingside_occupancy=sum(chess.square_file(square) >= 5 for square in pieces),
        longest_pawn_row=pawn_row,
        pawn_wall_ratio=round(wall_ratio, 3),
        symmetry_score=round(symmetry, 3),
        quality_score=max(0, min(100, quality)),
    )


def _proof_nodes(root: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [root]
    for branch in root.get("moves", []):
        child = branch.get("child")
        if isinstance(child, dict):
            nodes.extend(_proof_nodes(child))
    return nodes


def structural_similarity(first: PositionDiversity, second: PositionDiversity) -> int:
    """Explainable 0..100 near-clone score for selection/history gates."""

    score = 0
    score += 20 if first.symmetry_class == second.symmetry_class else 0
    score += 15 if first.material_signature == second.material_signature else 0
    score += 10 if first.nonpawn_material_signature == second.nonpawn_material_signature else 0
    score += 20 if first.pawn_structure_signature == second.pawn_structure_signature else 0
    score += 10 if first.king_regions == second.king_regions else 0
    score += 8 if first.piece_vector == second.piece_vector else 0
    score += 7 if first.motif == second.motif else 0
    score += 5 if first.source_family == second.source_family else 0
    score += max(0, 5 - abs(first.occupied_files - second.occupied_files))
    return min(100, score)
