import hashlib
import io
import json
import xml.etree.ElementTree as ET
from dataclasses import replace

import chess
import pytest
from PIL import Image, ImageColor

from aag_chess.renderer import (
    BACKGROUND_COLOR,
    BOARD_PIXELS,
    BOARD_X,
    BOARD_Y,
    CARD_HEIGHT,
    CARD_WIDTH,
    DARK_SQUARE_COLOR,
    LIGHT_SQUARE_COLOR,
    SQUARE_SIZE,
    RenderRequest,
    RendererError,
    difficulty_he,
    display_squares,
    render_png,
    render_svg,
    write_png_artifact,
    write_svg_artifact,
)
from aag_chess.verifier import MateVerifier, VerificationResult


WHITE_MATE_1 = "3k4/8/3K2Q1/8/8/8/8/8 w - - 0 1"
BLACK_MATE_1 = "8/8/8/8/8/3k2q1/8/3K4 b - - 0 1"
NO_MATE = "7k/8/8/8/8/8/8/K7 w - - 0 1"
SVG_NAMESPACE = {"svg": "http://www.w3.org/2000/svg"}


@pytest.fixture(scope="module")
def accepted_white() -> VerificationResult:
    result = MateVerifier().verify(WHITE_MATE_1, 1)
    assert result.accepted
    return result


@pytest.fixture(scope="module")
def accepted_black() -> VerificationResult:
    result = MateVerifier().verify(BLACK_MATE_1, 1)
    assert result.accepted
    return result


@pytest.fixture(scope="module")
def white_request(accepted_white: VerificationResult) -> RenderRequest:
    return RenderRequest(accepted_white, "phase2-mate-1", "easy")


def _svg_root(svg: bytes) -> ET.Element:
    return ET.fromstring(svg)


def _png_image(png: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(png))
    image.load()
    return image


@pytest.mark.parametrize("renderer", [render_svg, render_png])
def test_accepted_verification_result_is_required(renderer):
    with pytest.raises(RendererError, match="VerificationResult"):
        renderer(RenderRequest(None, "phase2-mate-1", "easy"))  # type: ignore[arg-type]
    with pytest.raises(RendererError, match="RenderRequest"):
        renderer(None)  # type: ignore[arg-type]


@pytest.mark.parametrize("renderer", [render_svg, render_png])
def test_rejected_verification_is_rejected(renderer):
    rejected = MateVerifier().verify(NO_MATE, 1)
    assert not rejected.accepted
    with pytest.raises(RendererError, match="accepted"):
        renderer(RenderRequest(rejected, "phase2-no-mate", "easy"))


@pytest.mark.parametrize(
    "puzzle_id",
    ["", "../escape", "with space", "/absolute", "bad/slash", "x" * 65, "\ncontrol"],
)
def test_invalid_puzzle_id_is_rejected(accepted_white, puzzle_id):
    with pytest.raises(RendererError, match="puzzle_id"):
        render_svg(RenderRequest(accepted_white, puzzle_id, "easy"))


@pytest.mark.parametrize("difficulty", ["", "Easy", "expert", None, 3])
def test_invalid_difficulty_is_rejected(accepted_white, difficulty):
    with pytest.raises(RendererError, match="difficulty"):
        render_png(RenderRequest(accepted_white, "phase2-mate-1", difficulty))


def test_canonical_normalized_fen_is_enforced(accepted_white):
    noncanonical = replace(
        accepted_white,
        normalized_fen=f"{accepted_white.normalized_fen} 0 1",
    )
    with pytest.raises(RendererError, match="not canonical"):
        render_svg(RenderRequest(noncanonical, "phase2-mate-1", "easy"))


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"structurally_valid": False}, "structurally valid"),
        ({"requested_mate_moves": 4}, "unsupported mate depth"),
        ({"target_plies": 3}, "target depth is inconsistent"),
        ({"exact_mate_plies": None}, "does not prove"),
        ({"normalized_fen": "not a FEN"}, "invalid normalized FEN"),
    ],
)
def test_inconsistent_accepted_verification_is_rejected(accepted_white, changes, message):
    inconsistent = replace(accepted_white, **changes)
    with pytest.raises(RendererError, match=message):
        render_png(RenderRequest(inconsistent, "phase2-mate-1", "easy"))


def test_display_mapping_has_exactly_64_unique_squares(accepted_white):
    board = chess.Board(accepted_white.normalized_fen)
    mapping = display_squares(board)
    assert len(mapping) == 64
    assert len({item.square for item in mapping}) == 64
    assert [(item.display_file, item.display_rank) for item in mapping] == [
        (file_index, rank_index)
        for rank_index in range(8)
        for file_index in range(8)
    ]


def test_exact_piece_to_square_mapping(accepted_white):
    board = chess.Board(accepted_white.normalized_fen)
    pieces = {
        chess.square_name(item.square): item.piece_symbol
        for item in display_squares(board)
        if item.piece_symbol is not None
    }
    assert pieces == {"d8": "k", "d6": "K", "g6": "Q"}


def test_white_orientation_and_coordinates(accepted_white):
    mapping = display_squares(chess.Board(accepted_white.normalized_fen))
    assert [item.file_label for item in mapping[:8]] == list("abcdefgh")
    assert [item.rank_label for item in mapping[::8]] == list("87654321")
    assert chess.square_name(mapping[0].square) == "a8"
    assert chess.square_name(mapping[-1].square) == "h1"


def test_black_orientation_and_coordinates(accepted_black):
    mapping = display_squares(chess.Board(accepted_black.normalized_fen))
    assert [item.file_label for item in mapping[:8]] == list("hgfedcba")
    assert [item.rank_label for item in mapping[::8]] == list("12345678")
    assert chess.square_name(mapping[0].square) == "h1"
    assert chess.square_name(mapping[-1].square) == "a8"


@pytest.mark.parametrize(
    "verification, expected_files, expected_ranks",
    [
        ("accepted_white", "abcdefgh", "87654321"),
        ("accepted_black", "hgfedcba", "12345678"),
    ],
)
def test_svg_coordinate_labels_follow_orientation(
    request, verification, expected_files, expected_ranks
):
    result = request.getfixturevalue(verification)
    root = _svg_root(render_svg(RenderRequest(result, "phase2-coordinates", "medium")))
    files = root.findall(".//svg:text[@data-coordinate='file']", SVG_NAMESPACE)
    ranks = root.findall(".//svg:text[@data-coordinate='rank']", SVG_NAMESPACE)
    assert "".join(element.text or "" for element in files) == expected_files
    assert "".join(element.text or "" for element in ranks) == expected_ranks


def test_svg_is_valid_xml_with_exact_dimensions_and_mapping(white_request):
    root = _svg_root(render_svg(white_request))
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert (int(root.attrib["width"]), int(root.attrib["height"])) == (
        CARD_WIDTH,
        CARD_HEIGHT,
    )
    squares = root.findall(".//svg:rect[@data-square]", SVG_NAMESPACE)
    assert len(squares) == 64
    pieces = {
        element.attrib["data-square"]: element.attrib["data-piece"]
        for element in root.findall(".//svg:text[@data-piece]", SVG_NAMESPACE)
    }
    assert pieces == {"d8": "k", "d6": "K", "g6": "Q"}


def test_svg_bytes_and_hash_are_deterministic(white_request):
    first = render_svg(white_request)
    second = render_svg(white_request)
    assert first == second
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


def test_png_is_valid_nonempty_and_has_exact_dimensions(white_request):
    png = render_png(white_request)
    assert len(png) > 1_000
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    image = _png_image(png)
    assert image.format == "PNG"
    assert image.mode == "RGB"
    assert image.size == (CARD_WIDTH, CARD_HEIGHT)


def test_png_exact_piece_to_square_placement(accepted_white, white_request):
    image = _png_image(render_png(white_request))
    mapping = display_squares(chess.Board(accepted_white.normalized_fen))
    observed_piece_squares = set()
    for item in mapping:
        x = BOARD_X + item.display_file * SQUARE_SIZE
        y = BOARD_Y + item.display_rank * SQUARE_SIZE
        background = ImageColor.getrgb(
            LIGHT_SQUARE_COLOR
            if (chess.square_file(item.square) + chess.square_rank(item.square)) % 2
            else DARK_SQUARE_COLOR
        )
        crop = image.crop((x + 8, y + 8, x + SQUARE_SIZE - 8, y + SQUARE_SIZE - 8))
        if any(pixel != background for pixel in crop.getdata()):
            observed_piece_squares.add(chess.square_name(item.square))
    assert observed_piece_squares == {"d8", "d6", "g6"}


def test_png_contains_all_coordinate_regions(white_request):
    image = _png_image(render_png(white_request))
    white = ImageColor.getrgb(BACKGROUND_COLOR)
    file_y = BOARD_Y + BOARD_PIXELS + SQUARE_SIZE // 4
    for display_file in range(8):
        x = BOARD_X + display_file * SQUARE_SIZE + SQUARE_SIZE // 2
        crop = image.crop((x - 12, file_y - 14, x + 12, file_y + 14))
        assert any(pixel != white for pixel in crop.getdata())
    rank_x = BOARD_X - SQUARE_SIZE // 4
    for display_rank in range(8):
        y = BOARD_Y + display_rank * SQUARE_SIZE + SQUARE_SIZE // 2
        crop = image.crop((rank_x - 12, y - 14, rank_x + 12, y + 14))
        assert any(pixel != white for pixel in crop.getdata())


def test_png_bytes_and_hash_are_deterministic(white_request):
    first = render_png(white_request)
    second = render_png(white_request)
    assert first == second
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


def test_svg_and_png_card_metadata_and_hebrew_labels(white_request):
    svg = render_svg(white_request).decode("utf-8")
    assert "phase2-mate-1" not in svg
    assert "חידת שחמט" in svg
    assert "מט ב־1 מסעים" in svg
    assert "הלבן נוסע" in svg
    assert "רמת קושי: קל" in svg
    assert "aag-card" not in svg
    assert ">easy<" not in svg

    image = _png_image(render_png(white_request))
    assert image.info["Title"] == "חידת שחמט"
    assert image.info["PuzzleID"] == "phase2-mate-1"
    assert image.info["Mate"] == "מט ב־1 מסעים"
    assert image.info["SideToMove"] == "הלבן נוסע"
    assert image.info["Difficulty"] == "easy"
    assert image.info["RendererVersion"].startswith("aag-card-v")


@pytest.mark.parametrize(
    "difficulty, label",
    [("easy", "קל"), ("medium", "בינוני"), ("hard", "קשה")],
)
def test_hebrew_difficulty_labels_are_presentation_only(difficulty, label):
    assert difficulty_he(difficulty) == label


def test_rendered_outputs_do_not_leak_solution_proof_or_certificate(accepted_white):
    key_secret = "solution-key-marker-g6g8"
    proof_secret = "proof-tree-marker-never-render"
    certificate_secret = accepted_white.certificate_sha256
    marked = replace(
        accepted_white,
        key_moves=(key_secret,),
        proof={"private": proof_secret, "original": accepted_white.proof},
    )
    request = RenderRequest(marked, "phase2-no-leak", "hard")
    svg = render_svg(request)
    png = render_png(request)
    png_metadata = json.dumps(_png_image(png).info, ensure_ascii=False)

    for secret in (key_secret, proof_secret, certificate_secret):
        encoded = secret.encode("utf-8")
        assert encoded not in svg
        assert encoded not in png
        assert secret not in png_metadata
    assert b"data-key" not in svg
    assert b"data-proof" not in svg
    assert b"data-certificate" not in svg


def test_artifact_writers_return_correct_metadata_and_exact_hashes(tmp_path, white_request):
    svg_metadata = write_svg_artifact(white_request, tmp_path, "puzzle.svg")
    png_metadata = write_png_artifact(white_request, tmp_path, "puzzle.png")

    for metadata, media_type, dimensions in (
        (svg_metadata, "image/svg+xml", (CARD_WIDTH, CARD_HEIGHT)),
        (png_metadata, "image/png", (CARD_WIDTH, CARD_HEIGHT)),
    ):
        artifact_bytes = metadata.path.read_bytes()
        assert metadata.media_type == media_type
        assert (metadata.width, metadata.height) == dimensions
        assert metadata.sha256 == hashlib.sha256(artifact_bytes).hexdigest()
    assert svg_metadata.path.read_bytes() == render_svg(white_request)
    assert png_metadata.path.read_bytes() == render_png(white_request)


@pytest.mark.parametrize(
    "writer, filename",
    [
        (write_svg_artifact, "../escape.svg"),
        (write_svg_artifact, "/tmp/escape.svg"),
        (write_svg_artifact, "nested/puzzle.svg"),
        (write_svg_artifact, "wrong.png"),
        (write_png_artifact, "../escape.png"),
        (write_png_artifact, "wrong.svg"),
    ],
)
def test_artifact_writers_reject_unsafe_or_invalid_paths(
    tmp_path, white_request, writer, filename
):
    with pytest.raises(RendererError, match="artifact filename"):
        writer(white_request, tmp_path, filename)


def test_artifact_writer_rejects_relative_root(tmp_path, monkeypatch, white_request):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RendererError, match="absolute path"):
        write_svg_artifact(white_request, "artifacts", "puzzle.svg")


def test_artifact_writer_rejects_symlink_target(tmp_path, white_request):
    destination = tmp_path / "destination.svg"
    destination.write_bytes(b"preserve")
    (tmp_path / "puzzle.svg").symlink_to(destination)
    with pytest.raises(RendererError, match="symbolic link"):
        write_svg_artifact(white_request, tmp_path, "puzzle.svg")
    assert destination.read_bytes() == b"preserve"
