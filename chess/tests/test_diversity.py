import chess

from aag_chess.diversity import (
    DIVERSITY_FINGERPRINT_VERSION,
    classify_motif,
    describe_position,
    structural_similarity,
    symmetry_class,
)


def test_symmetry_class_collapses_visual_transformations():
    first = chess.Board("3k4/8/3K2Q1/8/8/8/8/8 w - - 0 1")
    mirrored = first.transform(chess.flip_horizontal)
    different = first.copy()
    different.set_piece_at(chess.A2, chess.Piece(chess.KNIGHT, chess.WHITE))
    assert symmetry_class(first) == symmetry_class(mirrored)
    assert symmetry_class(first) != symmetry_class(different)


def test_position_descriptor_penalizes_artificial_pawn_walls():
    wall = chess.Board("4k3/8/8/8/pppppppp/PPPPPPPP/8/4K2Q w - - 0 1")
    varied = chess.Board("4k3/2p3p1/5p2/p7/3P4/P4P2/1P4P1/4K2Q w - - 0 1")
    wall_descriptor = describe_position(wall, source_family="composition")
    varied_descriptor = describe_position(varied, source_family="material_constructed")
    assert wall_descriptor.longest_pawn_row == 8
    assert wall_descriptor.pawn_wall_ratio == 1
    assert wall_descriptor.quality_score < varied_descriptor.quality_score
    assert varied_descriptor.fingerprint
    assert varied_descriptor.public_dict()["fingerprint_version"] == DIVERSITY_FINGERPRINT_VERSION


def test_structural_similarity_detects_near_clone_but_not_material_change():
    board = chess.Board("4k3/2p3p1/5p2/p7/3P4/P4P2/1P4P1/4K2Q w - - 0 1")
    same = describe_position(board, source_family="material_constructed")
    changed = board.copy()
    changed.remove_piece_at(chess.H1)
    changed.set_piece_at(chess.H1, chess.Piece(chess.KNIGHT, chess.WHITE))
    changed_descriptor = describe_position(changed, source_family="material_constructed")
    assert structural_similarity(same, same) == 100
    assert structural_similarity(same, changed_descriptor) < 80


def test_motif_comes_from_verified_proof_shape_and_can_remain_unknown():
    board = chess.Board("3k4/8/3K2Q1/8/8/8/8/8 w - - 0 1")
    proof = {
        "moves": [
            {"san": "Qg8#", "child": {"terminal": "checkmate"}},
        ]
    }
    assert classify_motif(board, proof) == "back_rank_or_edge_net"
    assert classify_motif(board, None) == "mixed_other"
