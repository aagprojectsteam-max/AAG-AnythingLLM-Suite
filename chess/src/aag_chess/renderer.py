"""Deterministic chess puzzle renderer."""

from __future__ import annotations

import hashlib
import io
import os
import re
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

import chess
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

from . import RENDERER_VERSION
from .position import normalized_fen, parse_board
from .verifier import VerificationResult


BOARD_SIZE = 8
SQUARE_SIZE = 96
BOARD_X = 64
BOARD_Y = 180
BOARD_PIXELS = BOARD_SIZE * SQUARE_SIZE
CARD_WIDTH = BOARD_PIXELS + 128
CARD_HEIGHT = BOARD_Y + BOARD_PIXELS + 96

PIECE_GLYPHS = {
    "K": "♔",
    "Q": "♕",
    "R": "♖",
    "B": "♗",
    "N": "♘",
    "P": "♙",
    "k": "♚",
    "q": "♛",
    "r": "♜",
    "b": "♝",
    "n": "♞",
    "p": "♟",
}

BACKGROUND_COLOR = "#ffffff"
LIGHT_SQUARE_COLOR = "#f0d9b5"
DARK_SQUARE_COLOR = "#b58863"
TEXT_COLOR = "#1b1b1b"

TITLE_FONT_SIZE = 30
METADATA_FONT_SIZE = 20
COORDINATE_FONT_SIZE = 20
PIECE_FONT_SIZE = 72

_PUZZLE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_DIFFICULTY_HE = {
    "easy": "קל",
    "medium": "בינוני",
    "hard": "קשה",
}


def xml_text(value: str) -> str:
    return escape(value, {'"': "&quot;", "'": "&apos;"})


class RendererError(ValueError):
    pass


@dataclass(frozen=True)
class RenderRequest:
    verification: VerificationResult
    puzzle_id: str
    difficulty: str


def svg_metadata_elements(request: RenderRequest, board: chess.Board) -> tuple[str, ...]:
    mate_moves = request.verification.requested_mate_moves
    side_to_move = "הלבן נוסע" if board.turn == chess.WHITE else "השחור נוסע"
    return (
        "<text x='448' y='44' text-anchor='middle' font-family='DejaVu Sans' direction='rtl'>חידת שחמט</text>",
        f"<text x='448' y='82' text-anchor='middle' font-family='DejaVu Sans' direction='rtl'>מט ב־{mate_moves} מסעים</text>",
        f"<text x='448' y='120' text-anchor='middle' font-family='DejaVu Sans' direction='rtl'>רמת קושי: {difficulty_he(request.difficulty)}</text>",
        f"<text x='448' y='158' text-anchor='middle' font-family='DejaVu Sans' direction='rtl'>{side_to_move}</text>",
    )


def difficulty_he(difficulty: str) -> str:
    try:
        return _DIFFICULTY_HE[difficulty]
    except KeyError as exc:
        raise RendererError("difficulty must be easy, medium, or hard") from exc


def validate_render_request(request: RenderRequest) -> chess.Board:
    if not isinstance(request, RenderRequest):
        raise RendererError("request must be a RenderRequest")
    if not isinstance(request.verification, VerificationResult):
        raise RendererError("verification must be a VerificationResult")
    if request.verification.accepted is not True:
        raise RendererError("verification must be accepted")
    if request.verification.structurally_valid is not True:
        raise RendererError("verification must be structurally valid")
    if request.verification.normalized_fen is None:
        raise RendererError("verification must include a normalized FEN")
    if not isinstance(request.puzzle_id, str) or _PUZZLE_ID_PATTERN.fullmatch(
        request.puzzle_id
    ) is None:
        raise RendererError(
            "puzzle_id must contain 1-64 ASCII letters, digits, dots, underscores, or hyphens"
        )
    if request.difficulty not in {"easy", "medium", "hard"}:
        raise RendererError("difficulty must be easy, medium, or hard")
    mate_moves = request.verification.requested_mate_moves
    if mate_moves not in (1, 2, 3):
        raise RendererError("verification contains an unsupported mate depth")
    if request.verification.target_plies != 2 * mate_moves - 1:
        raise RendererError("verification target depth is inconsistent")
    if request.verification.exact_mate_plies != request.verification.target_plies:
        raise RendererError("verification does not prove the requested exact mate depth")

    try:
        board = parse_board(request.verification.normalized_fen)
    except (TypeError, ValueError) as exc:
        raise RendererError("verification contains an invalid normalized FEN") from exc
    if normalized_fen(board) != request.verification.normalized_fen:
        raise RendererError("verification normalized FEN is not canonical")
    return board


@dataclass(frozen=True)
class ArtifactMetadata:
    path: Path
    sha256: str
    media_type: str
    width: int
    height: int


@dataclass(frozen=True)
class DisplaySquare:
    square: chess.Square
    file_label: str
    rank_label: str
    display_file: int
    display_rank: int
    piece_symbol: str | None


def board_orientation(board: chess.Board) -> chess.Color:
    return board.turn


def display_squares(board: chess.Board) -> tuple[DisplaySquare, ...]:
    orientation = board_orientation(board)
    squares: list[DisplaySquare] = []

    for display_rank in range(BOARD_SIZE):
        for display_file in range(BOARD_SIZE):
            if orientation == chess.WHITE:
                file_index = display_file
                rank_index = BOARD_SIZE - 1 - display_rank
            else:
                file_index = BOARD_SIZE - 1 - display_file
                rank_index = display_rank

            square = chess.square(file_index, rank_index)
            piece = board.piece_at(square)
            squares.append(
                DisplaySquare(
                    square=square,
                    file_label=chess.FILE_NAMES[file_index],
                    rank_label=chess.RANK_NAMES[rank_index],
                    display_file=display_file,
                    display_rank=display_rank,
                    piece_symbol=piece.symbol() if piece is not None else None,
                )
            )

    return tuple(squares)


def svg_coordinate_elements(board: chess.Board) -> tuple[str, ...]:
    squares = display_squares(board)
    elements: list[str] = []

    for display_square in squares[:BOARD_SIZE]:
        x = BOARD_X + display_square.display_file * SQUARE_SIZE + SQUARE_SIZE // 2
        y = BOARD_Y + BOARD_PIXELS + SQUARE_SIZE // 4
        elements.append(
            f"<text data-coordinate='file' x='{x}' y='{y}' text-anchor='middle' "
            f"font-family='DejaVu Sans'>{display_square.file_label}</text>"
        )

    for display_square in squares[::BOARD_SIZE]:
        x = BOARD_X - SQUARE_SIZE // 4
        y = BOARD_Y + display_square.display_rank * SQUARE_SIZE + SQUARE_SIZE // 2
        elements.append(
            f"<text data-coordinate='rank' x='{x}' y='{y}' text-anchor='middle' "
            f"dominant-baseline='central' font-family='DejaVu Sans'>"
            f"{display_square.rank_label}</text>"
        )

    return tuple(elements)


def svg_board_elements(board: chess.Board) -> tuple[str, ...]:
    elements: list[str] = []
    for display_square in display_squares(board):
        square_name = chess.square_name(display_square.square)
        file_index = chess.square_file(display_square.square)
        rank_index = chess.square_rank(display_square.square)
        x = BOARD_X + display_square.display_file * SQUARE_SIZE
        y = BOARD_Y + display_square.display_rank * SQUARE_SIZE
        fill = LIGHT_SQUARE_COLOR if (file_index + rank_index) % 2 else DARK_SQUARE_COLOR
        elements.append(
            f"<rect data-square='{square_name}' data-file='{display_square.file_label}' "
            f"data-rank='{display_square.rank_label}' x='{x}' y='{y}' "
            f"width='{SQUARE_SIZE}' height='{SQUARE_SIZE}' fill='{fill}'/>"
        )
        if display_square.piece_symbol is not None:
            glyph = PIECE_GLYPHS[display_square.piece_symbol]
            center_x = x + SQUARE_SIZE // 2
            center_y = y + SQUARE_SIZE // 2
            elements.append(
                f"<text data-piece='{display_square.piece_symbol}' data-square='{square_name}' "
                f"x='{center_x}' y='{center_y}' text-anchor='middle' "
                f"dominant-baseline='central' font-family='DejaVu Sans'>{glyph}</text>"
            )
    return tuple(elements)


FONT_PATH = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')


def render_svg(request: RenderRequest) -> bytes:
    board = validate_render_request(request)
    elements = (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{CARD_WIDTH}' height='{CARD_HEIGHT}'>",
        f"<rect width='{CARD_WIDTH}' height='{CARD_HEIGHT}' fill='{BACKGROUND_COLOR}'/>",
        *svg_metadata_elements(request, board),
        *svg_board_elements(board),
        *svg_coordinate_elements(board),
        "</svg>",
    )
    return "\n".join(elements).encode("utf-8")


def _font(size: int) -> ImageFont.FreeTypeFont:
    if not FONT_PATH.is_file():
        raise RendererError(f"required font is unavailable: {FONT_PATH}")
    try:
        return ImageFont.truetype(
            str(FONT_PATH),
            size=size,
            layout_engine=ImageFont.Layout.RAQM,
        )
    except (OSError, ValueError) as exc:
        raise RendererError(f"could not load required font: {FONT_PATH}") from exc


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    value: str,
    font: ImageFont.FreeTypeFont,
    *,
    hebrew: bool = False,
) -> None:
    options: dict[str, str] = {"direction": "rtl", "language": "he"} if hebrew else {}
    draw.text(xy, value, fill=TEXT_COLOR, font=font, anchor="mm", **options)


def _png_metadata(request: RenderRequest, board: chess.Board) -> PngImagePlugin.PngInfo:
    mate_label = f"מט ב־{request.verification.requested_mate_moves} מסעים"
    side_to_move = "הלבן נוסע" if board.turn == chess.WHITE else "השחור נוסע"
    metadata = PngImagePlugin.PngInfo()
    metadata.add_itxt("Title", "חידת שחמט", lang="he")
    metadata.add_text("PuzzleID", request.puzzle_id)
    metadata.add_itxt("Mate", mate_label, lang="he")
    metadata.add_itxt("SideToMove", side_to_move, lang="he")
    metadata.add_text("Difficulty", request.difficulty)
    metadata.add_text("RendererVersion", RENDERER_VERSION)
    return metadata


def render_png(request: RenderRequest) -> bytes:
    """Render a deterministic RGB PNG directly from the verified position."""

    board = validate_render_request(request)
    image = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), BACKGROUND_COLOR)
    draw = ImageDraw.Draw(image)

    title_font = _font(TITLE_FONT_SIZE)
    metadata_font = _font(METADATA_FONT_SIZE)
    coordinate_font = _font(COORDINATE_FONT_SIZE)
    piece_font = _font(PIECE_FONT_SIZE)

    mate_moves = request.verification.requested_mate_moves
    side_to_move = "הלבן נוסע" if board.turn == chess.WHITE else "השחור נוסע"
    _draw_centered_text(draw, (CARD_WIDTH // 2, 44), "חידת שחמט", title_font, hebrew=True)
    _draw_centered_text(
        draw,
        (CARD_WIDTH // 2, 82),
        f"מט ב־{mate_moves} מסעים",
        metadata_font,
        hebrew=True,
    )
    _draw_centered_text(
        draw,
        (CARD_WIDTH // 2, 120),
        f"רמת קושי: {difficulty_he(request.difficulty)}",
        metadata_font,
        hebrew=True,
    )
    _draw_centered_text(
        draw,
        (CARD_WIDTH // 2, 158),
        side_to_move,
        metadata_font,
        hebrew=True,
    )

    squares = display_squares(board)
    for display_square in squares:
        x = BOARD_X + display_square.display_file * SQUARE_SIZE
        y = BOARD_Y + display_square.display_rank * SQUARE_SIZE
        file_index = chess.square_file(display_square.square)
        rank_index = chess.square_rank(display_square.square)
        fill = LIGHT_SQUARE_COLOR if (file_index + rank_index) % 2 else DARK_SQUARE_COLOR
        draw.rectangle((x, y, x + SQUARE_SIZE - 1, y + SQUARE_SIZE - 1), fill=fill)
        if display_square.piece_symbol is not None:
            _draw_centered_text(
                draw,
                (x + SQUARE_SIZE // 2, y + SQUARE_SIZE // 2),
                PIECE_GLYPHS[display_square.piece_symbol],
                piece_font,
            )

    for display_square in squares[:BOARD_SIZE]:
        x = BOARD_X + display_square.display_file * SQUARE_SIZE + SQUARE_SIZE // 2
        y = BOARD_Y + BOARD_PIXELS + SQUARE_SIZE // 4
        _draw_centered_text(draw, (x, y), display_square.file_label, coordinate_font)

    for display_square in squares[::BOARD_SIZE]:
        x = BOARD_X - SQUARE_SIZE // 4
        y = BOARD_Y + display_square.display_rank * SQUARE_SIZE + SQUARE_SIZE // 2
        _draw_centered_text(draw, (x, y), display_square.rank_label, coordinate_font)

    output = io.BytesIO()
    image.save(
        output,
        format="PNG",
        pnginfo=_png_metadata(request, board),
        optimize=False,
        compress_level=9,
    )
    return output.getvalue()


def _artifact_target(
    artifact_root: str | os.PathLike[str],
    filename: str | os.PathLike[str],
    expected_suffix: str,
) -> Path:
    try:
        root = Path(artifact_root)
        relative = Path(filename)
    except TypeError as exc:
        raise RendererError("artifact root and filename must be path-like") from exc
    if not root.is_absolute():
        raise RendererError("artifact root must be an absolute path")
    if root.is_symlink() or not root.is_dir():
        raise RendererError("artifact root must be an existing non-symlink directory")
    if relative.is_absolute() or len(relative.parts) != 1 or relative.name in {"", ".", ".."}:
        raise RendererError("artifact filename must be one safe relative filename")
    if not relative.name.isprintable() or any(ord(char) < 32 for char in relative.name):
        raise RendererError("artifact filename contains invalid characters")
    if relative.suffix != expected_suffix:
        raise RendererError(f"artifact filename must end with {expected_suffix}")

    target = root.resolve(strict=True) / relative.name
    if target.is_symlink():
        raise RendererError("artifact target must not be a symbolic link")
    return target


def _write_artifact(
    artifact_bytes: bytes,
    target: Path,
    *,
    media_type: str,
) -> ArtifactMetadata:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(target, flags, 0o644)
        with os.fdopen(descriptor, "wb") as output:
            output.write(artifact_bytes)
    except OSError as exc:
        raise RendererError(f"could not write artifact: {target}") from exc

    return ArtifactMetadata(
        path=target,
        sha256=hashlib.sha256(artifact_bytes).hexdigest(),
        media_type=media_type,
        width=CARD_WIDTH,
        height=CARD_HEIGHT,
    )


def write_svg_artifact(
    request: RenderRequest,
    artifact_root: str | os.PathLike[str],
    filename: str | os.PathLike[str],
) -> ArtifactMetadata:
    target = _artifact_target(artifact_root, filename, ".svg")
    return _write_artifact(
        render_svg(request),
        target,
        media_type="image/svg+xml",
    )


def write_png_artifact(
    request: RenderRequest,
    artifact_root: str | os.PathLike[str],
    filename: str | os.PathLike[str],
) -> ArtifactMetadata:
    target = _artifact_target(artifact_root, filename, ".png")
    return _write_artifact(
        render_png(request),
        target,
        media_type="image/png",
    )
