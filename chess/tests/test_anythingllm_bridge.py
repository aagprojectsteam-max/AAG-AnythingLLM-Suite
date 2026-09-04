import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from aag_chess.anythingllm_bridge import (
    BridgeExecutionError,
    CHESS_EXECUTABLE,
    ChessSkillService,
    SkillRequestError,
    build_generate_argv,
    parse_skill_request,
    run_argv,
)
from aag_chess.verifier import MateVerifier


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCOPE = {"workspace_id": "workspace_test", "thread_id": "thread_test", "user_id": "user_test"}


def test_natural_language_contract_defaults_are_complete():
    request = parse_skill_request({"mate": 3, "side": "black"})
    assert request.count == 1
    assert request.engine == "auto"
    assert request.difficulty == "medium"
    assert request.formats == ("png",)
    assert request.seed == 0
    assert request.max_attempts == 1_000
    assert request.stockfish_nodes == 20_000
    assert request.density == "auto"
    assert request.difficulty_defaulted
    assert request.seed_defaulted
    assert request.effective_difficulty == "hard"


def test_explicit_difficulty_is_not_rewritten():
    request = parse_skill_request({"mate": 3, "difficulty": "medium"})
    assert not request.difficulty_defaulted
    assert request.effective_difficulty == "medium"


def test_explicit_seed_preserves_reproducible_mode():
    request = parse_skill_request({"mate": 2, "seed": 0})
    assert request.seed == 0
    assert not request.seed_defaulted


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"mate": 0},
        {"mate": 4},
        {"mate": True},
        {"mate": 2, "side": "white; touch /tmp/pwned"},
        {"mate": 2, "difficulty": "expert"},
        {"mate": 2, "count": 0},
        {"mate": 2, "count": 11},
        {"mate": 2, "engine": "shell"},
        {"mate": 2, "formats": "pdf"},
        {"mate": 2, "formats": ["png", "png"]},
        {"mate": 2, "seed": -1},
        {"mate": 2, "max_attempts": 2_001},
        {"mate": 2, "stockfish_nodes": 99},
        {"mate": 2, "density": "crowded"},
        {"mate": 2, "output": "/tmp/escape"},
    ],
)
def test_request_validation_rejects_invalid_or_unsafe_values(payload):
    with pytest.raises(SkillRequestError):
        parse_skill_request(payload)


def test_generate_argv_is_explicit_and_has_no_shell(tmp_path):
    request = parse_skill_request(
        {
            "mate": 2,
            "side": "black",
            "difficulty": "hard",
            "count": 3,
            "engine": "builtin",
            "formats": "svg+png",
            "seed": 77,
            "max_attempts": 400,
            "stockfish_nodes": 3000,
            "density": "rich",
        }
    )
    argv = build_generate_argv(request, tmp_path / "request-safe")
    assert argv[0] == str(CHESS_EXECUTABLE)
    assert argv[1] == "generate"
    assert "--output" in argv
    assert argv[argv.index("--density") + 1] == "rich"
    assert argv[-1] == str(tmp_path / "request-safe")
    assert argv[argv.index("--formats") + 1 : argv.index("--output")] == (
        "png",
        "svg",
    )
    assert all(isinstance(item, str) for item in argv)


def test_cli_failure_is_safe_and_partial_directory_is_removed(tmp_path):
    created = []

    def failing_runner(argv, **unused):
        output = Path(argv[argv.index("--output") + 1])
        output.mkdir()
        created.append(output)
        return subprocess.CompletedProcess(argv, 3, "internal detail", "private trace")

    service = ChessSkillService(tmp_path / "outputs", runner=failing_runner)
    with pytest.raises(BridgeExecutionError) as captured:
        service.generate({"mate": 2, "engine": "builtin"})
    assert captured.value.code == "generation_budget_exhausted"
    assert "private trace" not in str(captured.value)
    assert created and not created[0].exists()


def test_process_timeout_is_bounded_and_child_is_reaped():
    started = time.monotonic()
    with pytest.raises(BridgeExecutionError) as captured:
        run_argv(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout_seconds=0.05,
        )
    assert captured.value.code == "generation_timeout"
    assert time.monotonic() - started < 3


def test_output_root_rejects_symlink(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(RuntimeError, match="unsafe"):
        ChessSkillService(linked)


def test_public_diversity_history_root_rejects_symlink(tmp_path):
    real = tmp_path / "real-history"
    real.mkdir()
    linked = tmp_path / "linked-history"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(RuntimeError, match="history root is unsafe"):
        ChessSkillService(tmp_path / "outputs", diversity_root=linked)


def test_real_builtin_bridge_generation_is_deep_verified_and_public(tmp_path):
    service = ChessSkillService(tmp_path / "outputs")
    result = service.generate(
        {
            "mate": 1,
            "side": "white",
            "engine": "builtin",
            "formats": "png",
            "seed": 41,
            "max_attempts": 100,
        }
    )
    assert result["status"] == "success"
    assert result["generated_count"] == 1
    assert result["requested_difficulty"] == "medium"
    assert result["measured_difficulty"] == "easy"
    assert result["density_preference"] == "auto"
    assert result["density_profiles"] == ["sparse"]
    assert result["integrity"] == {"status": "passed", "mode": "deep"}
    artifact = result["artifacts"][0]
    assert artifact["filename"].endswith(".png")
    assert (service.output_root / artifact["relative_path"]).is_file()
    serialized = json.dumps(result, sort_keys=True).lower()
    for forbidden in (
        "solution",
        "key_move",
        "proof_tree",
        "certificate",
        "stockfish_pv",
        "bestmove",
    ):
        assert forbidden not in serialized


def test_ordinary_conversation_uses_bounded_public_diversity_history(tmp_path):
    service = ChessSkillService(
        tmp_path / "outputs",
        private_root=tmp_path / "private",
        diversity_root=tmp_path / "recent-diversity",
    )
    first = service.generate({"mate": 1, "engine": "builtin", "_scope": SCOPE})
    second = service.generate({"mate": 1, "engine": "builtin", "_scope": SCOPE})
    assert first["seed_mode"] == second["seed_mode"] == "automatic_conversation_diversity"
    assert first["seed"] != second["seed"]
    records = list((tmp_path / "recent-diversity").glob("*.json"))
    assert len(records) == 1
    history = json.loads(records[0].read_text(encoding="utf-8"))
    assert history["counter"] == 2
    assert len(history["entries"]) == 2
    serialized = json.dumps(history, sort_keys=True).lower()
    for forbidden in ("fen", "solution", "proof", "key_move", "certificate", "pv"):
        assert forbidden not in serialized


def test_explicit_seed_bypasses_conversation_seed_derivation(tmp_path):
    service = ChessSkillService(
        tmp_path / "outputs",
        private_root=tmp_path / "private",
        diversity_root=tmp_path / "recent-diversity",
    )
    result = service.generate(
        {"mate": 1, "engine": "builtin", "seed": 7, "_seed_defaulted": False, "_scope": SCOPE}
    )
    assert result["seed"] == 7
    assert result["seed_mode"] == "explicit_reproducible"
    assert result["diversity_relaxation"] == "strict"


def test_private_context_is_separate_and_normal_generation_remains_unsolved(tmp_path):
    output_root = tmp_path / "outputs"
    private_root = tmp_path / "private-solutions"
    service = ChessSkillService(output_root, private_root=private_root)
    result = service.generate(
        {"mate": 1, "engine": "builtin", "seed": 141, "_scope": SCOPE}
    )
    assert result["context_token"].startswith("ctx_")
    records = list(private_root.glob("ctx_*.json"))
    assert len(records) == 1
    assert not private_root.is_relative_to(output_root)
    private_text = records[0].read_text(encoding="utf-8").lower()
    for forbidden in ("normalized_fen", "key_move", "proof", "solution", "uci", "san"):
        assert forbidden not in private_text
    request_directory = result["manifest"]["relative_path"].split("/", 1)[0]
    public_text = "\n".join(
        path.read_bytes().decode("latin-1", errors="ignore").lower()
        for path in (output_root / request_directory).rglob("*")
        if path.is_file()
    )
    for forbidden in ("key_move", "proof_tree", "certificate", "stockfish_pv", "solution"):
        assert forbidden not in public_text


def test_hint_and_solution_followups_are_reverified_and_select_last_puzzle(tmp_path):
    service = ChessSkillService(
        tmp_path / "outputs", private_root=tmp_path / "private-solutions"
    )
    generated = service.generate(
        {"mate": 2, "engine": "builtin", "seed": 242, "_scope": SCOPE}
    )
    output = service.output_root / generated["manifest"]["relative_path"].split("/", 1)[0]
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    puzzle = manifest["puzzles"][0]
    independent = MateVerifier().verify(
        puzzle["normalized_fen"], puzzle["mate_moves"], dual_policy="warning"
    )
    assert independent.accepted and independent.proof is not None
    key = independent.proof["moves"][0]

    hint = service.followup(
        {"action": "hint", "_scope": SCOPE}
    )
    assert hint["puzzle_number"] == 1
    assert hint["public_id"] == puzzle["public_id"]
    assert hint["verified_source"]["certificate_sha256"] == independent.certificate_sha256
    assert key["uci"] not in hint["answer_he"]
    assert key["san"] not in hint["answer_he"]

    stronger = service.followup(
        {"action": "hint", "_scope": SCOPE}
    )
    from_square = key["uci"][:2]
    assert stronger["hint_level"] == 2
    assert from_square in stronger["answer_he"]
    assert key["uci"] not in stronger["answer_he"]

    solution = service.followup(
        {"action": "solution", "_scope": SCOPE}
    )
    assert solution["solution"]["key_uci"] == key["uci"]
    assert solution["solution"]["key_san"] == key["san"]
    assert solution["verified_source"]["authority"] == "AAG MateVerifier"
    assert solution["verified_source"]["certificate_sha256"] == independent.certificate_sha256
    assert "מט" in solution["answer_he"]
    assert generated["context_token"] not in solution["answer_he"]
    assert puzzle["public_id"] not in solution["answer_he"]
    assert "UCI" not in solution["answer_he"]
    assert "\n" in solution["answer_he"]


def test_batch_followup_selects_exact_puzzle_number_and_public_id(tmp_path):
    service = ChessSkillService(
        tmp_path / "outputs", private_root=tmp_path / "private-solutions"
    )
    generated = service.generate(
        {
            "mate": 2,
            "count": 3,
            "engine": "builtin",
            "seed": 0,
            "_scope": SCOPE,
        }
    )
    selected_id = generated["public_ids"][1]
    by_number = service.followup(
        {
            "context_token": generated["context_token"],
            "action": "solution",
            "puzzle_number": 2,
            "_scope": SCOPE,
        }
    )
    assert by_number["puzzle_number"] == 2
    assert by_number["public_id"] == selected_id
    by_id = service.followup(
        {
            "context_token": generated["context_token"],
            "action": "solution",
            "public_id": selected_id,
            "_scope": SCOPE,
        }
    )
    assert by_id["solution"] == by_number["solution"]
    assert by_id["verified_source"] == by_number["verified_source"]


@pytest.mark.parametrize(
    "payload",
    [
        {"context_token": "../../etc/passwd", "action": "solution", "_scope": SCOPE},
        {"context_token": "ctx_" + "a" * 64, "action": "solution", "_scope": SCOPE},
        {
            "context_token": "ctx_" + "a" * 64,
            "action": "solution",
            "public_id": "../../etc/passwd",
            "_scope": SCOPE,
        },
    ],
)
def test_followup_rejects_path_traversal_and_unrelated_context(tmp_path, payload):
    service = ChessSkillService(
        tmp_path / "outputs", private_root=tmp_path / "private-solutions"
    )
    with pytest.raises((SkillRequestError, ValueError)):
        service.followup(payload)


def test_followup_context_is_conversation_scoped(tmp_path):
    service = ChessSkillService(
        tmp_path / "outputs", private_root=tmp_path / "private-solutions"
    )
    generated = service.generate(
        {"mate": 1, "engine": "builtin", "seed": 343, "_scope": SCOPE}
    )
    foreign_scope = {**SCOPE, "thread_id": "other_thread"}
    with pytest.raises(ValueError, match="does not belong"):
        service.followup(
            {
                "context_token": generated["context_token"],
                "action": "solution",
                "_scope": foreign_scope,
            }
        )


def test_latest_private_context_is_selected_by_conversation_scope(tmp_path):
    service = ChessSkillService(
        tmp_path / "outputs", private_root=tmp_path / "private-solutions"
    )
    first = service.generate(
        {"mate": 1, "engine": "builtin", "seed": 10, "_scope": SCOPE}
    )
    second = service.generate(
        {"mate": 2, "engine": "builtin", "seed": 20, "_scope": SCOPE}
    )
    assert first["public_ids"] != second["public_ids"]

    latest = service.followup({"action": "solution", "_scope": SCOPE})
    assert latest["public_id"] == second["public_ids"][-1]
    assert latest["puzzle_number"] == 1

    latest_records = list((tmp_path / "private-solutions").glob("latest-*.json"))
    assert len(latest_records) == 1
    private_text = latest_records[0].read_text(encoding="utf-8").lower()
    for forbidden in ("normalized_fen", "key_move", "proof", "solution", "uci", "san"):
        assert forbidden not in private_text


def test_each_request_uses_an_isolated_collision_free_directory(tmp_path):
    service = ChessSkillService(tmp_path / "outputs")
    first = service.generate({"mate": 1, "engine": "builtin", "seed": 55})
    second = service.generate({"mate": 1, "engine": "builtin", "seed": 55})
    assert first["request_id"] != second["request_id"]
    assert first["manifest"]["relative_path"] != second["manifest"]["relative_path"]
    assert len(list(service.output_root.glob("request-*"))) == 2


def test_anythingllm_handler_security_contract_runs_under_node():
    completed = subprocess.run(
        ["node", "integrations/anythingllm/tests/test-handler.js"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
        env={**os.environ, "NODE_ENV": "test"},
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "anythingllm handler tests: PASS" in completed.stdout
