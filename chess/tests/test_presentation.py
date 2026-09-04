from types import SimpleNamespace

from aag_chess.anythingllm_solution import _hint_text, _solution_text
from aag_chess.verifier import MateVerifier


LRI = "\u2066"
PDI = "\u2069"


def _terminal():
    return {"terminal": "checkmate", "fen": "ignored"}


def _branching_verification():
    proof = {
        "role": "attacker",
        "moves": [
            {
                "san": "Rc4",
                "uci": "a1c1",
                "child": {
                    "role": "defender",
                    "moves": [
                        {
                            "san": "Kf7",
                            "uci": "e8f7",
                            "child": {
                                "role": "attacker",
                                "moves": [
                                    {"san": "Rf4#", "uci": "c4f4", "child": _terminal()}
                                ],
                            },
                        },
                        {
                            "san": "Kd7",
                            "uci": "e8d7",
                            "child": {
                                "role": "attacker",
                                "moves": [
                                    {"san": "Rd4#", "uci": "c4d4", "child": _terminal()}
                                ],
                            },
                        },
                    ],
                },
            }
        ],
    }
    return SimpleNamespace(proof=proof, key_moves=("a1c1",))


def test_solution_is_vertical_branched_san_without_visible_internals():
    answer, structured = _solution_text(2, 3, _branching_verification())

    assert answer.startswith("הפתרון לחידה 2:\n\n")
    assert f"{LRI}1. Rc4!{PDI}" in answer
    assert f"אם {LRI}1... Kf7{PDI}:\n\n↳ {LRI}2. Rf4#{PDI}" in answer
    assert f"אם {LRI}1... Kd7{PDI}:\n\n↳ {LRI}2. Rd4#{PDI}" in answer
    assert answer.endswith("מט בכל ההסתעפויות.")
    assert "UCI" not in answer
    assert "a1c1" not in answer
    assert "aag-" not in answer
    assert structured["key_uci"] == "a1c1"
    assert structured["lines_san"] == [["Rc4", "Kf7", "Rf4#"], ["Rc4", "Kd7", "Rd4#"]]


def test_simple_solution_preserves_check_and_mate_on_separate_ltr_lines():
    proof = {
        "role": "attacker",
        "moves": [
            {
                "san": "Qe5+",
                "uci": "e2e5",
                "child": {
                    "role": "defender",
                    "moves": [
                        {
                            "san": "Kf8",
                            "uci": "g8f8",
                            "child": {
                                "role": "attacker",
                                "moves": [
                                    {"san": "Qb8#", "uci": "e5b8", "child": _terminal()}
                                ],
                            },
                        }
                    ],
                },
            }
        ],
    }
    answer, _ = _solution_text(1, 1, SimpleNamespace(proof=proof, key_moves=("e2e5",)))
    assert answer.splitlines()[:9] == [
        "הפתרון:",
        "",
        f"{LRI}1. Qe5+!{PDI}",
        "",
        f"{LRI}1... Kf8{PDI}",
        "",
        f"{LRI}2. Qb8#{PDI}",
        "",
        "מט בכל ההסתעפויות.",
    ]


def test_hint_is_clean_hebrew_progressive_and_verified_data_derived():
    fen = "3k4/8/3K2Q1/8/8/8/8/8 w - - 0 1"
    verification = MateVerifier().verify(fen, 1)
    assert verification.accepted and verification.proof is not None

    first = _hint_text(1, 1, 1, fen, verification)
    strong = _hint_text(3, 1, 1, fen, verification)
    key = verification.proof["moves"][0]
    assert first.startswith("רמז:\n\n")
    assert key["san"] not in first and key["uci"] not in first
    assert "UCI" not in strong and key["uci"] not in strong
    assert f"{LRI}{key['san']}{PDI}" in strong
    assert "aag-" not in first + strong
