"""Position parsing, structural validation, and stable normalization."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import chess


class PositionError(ValueError):
    """Raised when a supplied position is syntactically or structurally invalid."""


@dataclass(frozen=True)
class PositionFacts:
    original_fen: str
    normalized_fen: str
    puzzle_hash: str
    structurally_valid: bool
    status: int


def parse_board(fen: str) -> chess.Board:
    if not isinstance(fen, str) or not fen.strip() or len(fen) > 200:
        raise PositionError("FEN must be a non-empty string of at most 200 characters")
    if "\x00" in fen or any(ord(char) < 32 for char in fen):
        raise PositionError("FEN contains control characters")
    try:
        board = chess.Board(fen.strip())
    except ValueError as exc:
        raise PositionError(f"Invalid FEN: {exc}") from exc
    if not board.is_valid():
        raise PositionError(f"Structurally invalid position (status={int(board.status())})")
    return board


def normalized_fen(board: chess.Board) -> str:
    """Normalize problem-relevant fields and omit move counters.

    The board, side, castling rights, and only a legally effective en-passant
    square define legal play for this bounded mate problem. Move counters do
    not affect legal move generation within the supported five-ply horizon.
    """

    fields = board.fen(en_passant="legal").split()
    return " ".join(fields[:4])


def puzzle_hash(board: chess.Board, mate_moves: int) -> str:
    material = f"aag-chess-problem-v1|{normalized_fen(board)}|mate={mate_moves}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def inspect_position(fen: str, mate_moves: int) -> PositionFacts:
    board = parse_board(fen)
    return PositionFacts(
        original_fen=fen.strip(),
        normalized_fen=normalized_fen(board),
        puzzle_hash=puzzle_hash(board, mate_moves),
        structurally_valid=True,
        status=int(board.status()),
    )

