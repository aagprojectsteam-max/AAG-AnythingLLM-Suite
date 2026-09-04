"""Private, capability-scoped solution follow-ups for the AnythingLLM skill.

No proof or move is copied into the public puzzle directory.  A stored context
record contains only the output-directory name and its ordered public IDs.  On
every hint or solution request, the public batch is deep-verified and the
authoritative :class:`MateVerifier` recreates the proof for the selected FEN.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import uuid
from pathlib import Path
from typing import Any

import chess

from . import VERIFIER_VERSION
from .application import verify_output
from .position import parse_board
from .verifier import MateVerifier


PRIVATE_SOLUTION_SCHEMA = "aag-anythingllm-private-context-v1"
PRIVATE_LATEST_SCHEMA = "aag-anythingllm-private-latest-v1"
FOLLOWUP_RESULT_SCHEMA = "aag-anythingllm-chess-followup-v1"
_TOKEN = re.compile(r"^ctx_[0-9a-f]{64}$")
_PUBLIC_ID = re.compile(r"^aag-[0-9a-f]{20}$")
_REQUEST_DIRECTORY = re.compile(
    r"^request-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_SCOPE_VALUE = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
_MAX_PRIVATE_RECORD_BYTES = 64_000
_MAX_SOLUTION_LINES = 256
_LTR_ISOLATE = "\u2066"
_POP_DIRECTIONAL_ISOLATE = "\u2069"


class SolutionLookupError(ValueError):
    """A follow-up is invalid, unauthorized, or no longer available."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def validate_scope(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {
        "workspace_id",
        "thread_id",
        "user_id",
    }:
        raise SolutionLookupError("invalid_scope", "Conversation scope is invalid")
    scope: dict[str, str] = {}
    for key in ("workspace_id", "thread_id", "user_id"):
        item = value.get(key)
        if not isinstance(item, str) or not _SCOPE_VALUE.fullmatch(item):
            raise SolutionLookupError("invalid_scope", "Conversation scope is invalid")
        scope[key] = item
    return scope


def _read_json_nofollow(path: Path) -> dict[str, Any]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SolutionLookupError(
            "solution_context_unavailable",
            "The prior puzzle context is unavailable; generate a new puzzle",
        ) from exc
    try:
        details = os.fstat(descriptor)
        if details.st_size > _MAX_PRIVATE_RECORD_BYTES:
            raise SolutionLookupError("invalid_private_context", "Private context is invalid")
        with os.fdopen(descriptor, encoding="utf-8") as source:
            descriptor = -1
            value = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SolutionLookupError("invalid_private_context", "Private context is invalid") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(value, dict):
        raise SolutionLookupError("invalid_private_context", "Private context is invalid")
    return value


def _exclusive_json(path: Path, value: dict[str, Any]) -> None:
    content = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        _exclusive_json(temporary, value)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def _proof_lines(proof: dict[str, Any]) -> tuple[list[list[tuple[str, str]]], bool]:
    lines: list[list[tuple[str, str]]] = []
    truncated = False

    def walk(node: dict[str, Any], line: list[tuple[str, str]]) -> None:
        nonlocal truncated
        if len(lines) >= _MAX_SOLUTION_LINES:
            truncated = True
            return
        if node.get("terminal") == "checkmate":
            lines.append(line)
            return
        moves = node.get("moves")
        if not isinstance(moves, list):
            raise SolutionLookupError("invalid_verified_proof", "Verified proof is malformed")
        for branch in moves:
            if not isinstance(branch, dict):
                raise SolutionLookupError("invalid_verified_proof", "Verified proof is malformed")
            san, uci, child = branch.get("san"), branch.get("uci"), branch.get("child")
            if not isinstance(san, str) or not isinstance(uci, str) or not isinstance(child, dict):
                raise SolutionLookupError("invalid_verified_proof", "Verified proof is malformed")
            walk(child, [*line, (san, uci)])

    walk(proof, [])
    return lines, truncated


def _solution_text(
    puzzle_number: int,
    batch_size: int,
    verification: Any,
) -> tuple[str, dict[str, Any]]:
    if verification.proof is None or len(verification.key_moves) != 1:
        raise SolutionLookupError("verification_failed", "No authoritative proof is available")
    lines, truncated = _proof_lines(verification.proof)
    if not lines:
        raise SolutionLookupError("invalid_verified_proof", "Verified proof has no mate line")
    key_branch = verification.proof["moves"][0]
    key_san, key_uci = key_branch["san"], key_branch["uci"]
    heading = "הפתרון:" if batch_size == 1 else f"הפתרון לחידה {puzzle_number}:"
    rendered_lines = [heading, "", _isolated_move(1, key_san, key=True)]
    tree_lines, tree_truncated = _vertical_tree(key_branch["child"], move_number=1)
    rendered_lines.extend(tree_lines)
    rendered_lines.extend(["", "מט בכל ההסתעפויות."])
    if truncated or tree_truncated:
        rendered_lines.extend(["", "חלק מההסתעפויות קוצרו לתצוגה."])
    answer = "\n\n".join(line for line in rendered_lines if line)
    return answer, {
        "key_san": key_san,
        "key_uci": key_uci,
        "lines_san": [[move[0] for move in line] for line in lines],
        "lines_uci": [[move[1] for move in line] for line in lines],
        "branches_truncated": truncated,
    }


def _ltr(value: str) -> str:
    """Isolate chess notation from the surrounding Hebrew RTL paragraph."""

    return f"{_LTR_ISOLATE}{value}{_POP_DIRECTIONAL_ISOLATE}"


def _annotated_key(san: str) -> str:
    if san.endswith("#"):
        return san
    return f"{san}!"


def _isolated_move(
    move_number: int,
    san: str,
    *,
    defense: bool = False,
    key: bool = False,
) -> str:
    notation = _annotated_key(san) if key else san
    prefix = f"{move_number}..." if defense else f"{move_number}."
    return _ltr(f"{prefix} {notation}")


def _vertical_tree(
    node: dict[str, Any],
    *,
    move_number: int,
) -> tuple[list[str], bool]:
    rendered: list[str] = []
    state = {"mates": 0, "truncated": False}

    def child_moves(value: dict[str, Any]) -> list[dict[str, Any]]:
        moves = value.get("moves")
        if not isinstance(moves, list):
            raise SolutionLookupError("invalid_verified_proof", "Verified proof is malformed")
        return moves

    def attacker(value: dict[str, Any], number: int, indent: str) -> None:
        moves = child_moves(value)
        alternatives = len(moves) > 1
        for branch in moves:
            if state["mates"] >= _MAX_SOLUTION_LINES:
                state["truncated"] = True
                return
            san, child = branch.get("san"), branch.get("child")
            if not isinstance(san, str) or not isinstance(child, dict):
                raise SolutionLookupError("invalid_verified_proof", "Verified proof is malformed")
            label = _isolated_move(number, san)
            rendered.append(f"{indent}{'אפשרות: ' if alternatives else ''}{label}")
            if child.get("terminal") == "checkmate":
                state["mates"] += 1
            else:
                defender(child, number, indent + ("↳ " if alternatives else ""))

    def defender(value: dict[str, Any], number: int, indent: str) -> None:
        moves = child_moves(value)
        branches = len(moves) > 1
        for branch in moves:
            if state["mates"] >= _MAX_SOLUTION_LINES:
                state["truncated"] = True
                return
            san, child = branch.get("san"), branch.get("child")
            if not isinstance(san, str) or not isinstance(child, dict):
                raise SolutionLookupError("invalid_verified_proof", "Verified proof is malformed")
            defense = _isolated_move(number, san, defense=True)
            if branches:
                rendered.extend(["", f"{indent}אם {defense}:"])
                attacker(child, number + 1, indent + "↳ ")
            else:
                rendered.append(f"{indent}{defense}")
                attacker(child, number + 1, indent)

    if node.get("terminal") == "checkmate":
        state["mates"] = 1
    else:
        defender(node, move_number, "")
    return rendered, bool(state["truncated"])


_PIECE_NAMES = {
    chess.KING: "המלך",
    chess.QUEEN: "המלכה",
    chess.ROOK: "הצריח",
    chess.BISHOP: "הרץ",
    chess.KNIGHT: "הפרש",
    chess.PAWN: "הרגלי",
}


def _hint_text(
    hint_level: int,
    puzzle_number: int,
    batch_size: int,
    fen: str,
    verification: Any,
) -> str:
    if verification.proof is None or len(verification.key_moves) != 1:
        raise SolutionLookupError("verification_failed", "No authoritative hint is available")
    branch = verification.proof["moves"][0]
    key = chess.Move.from_uci(branch["uci"])
    board = parse_board(fen)
    piece = board.piece_at(key.from_square)
    if piece is None:
        raise SolutionLookupError("invalid_verified_proof", "Verified key move is malformed")
    board.push(key)
    check_clue = "מסע המפתח נותן שח" if board.is_check() else "מסע המפתח הוא מסע שקט שאינו נותן שח מיד"
    prefix = "רמז:" if batch_size == 1 else f"רמז לחידה {puzzle_number}:"
    if hint_level == 1:
        return prefix + "\n\n" + check_clue + ". חפש את רשת המט בלי לחשוף עדיין את המסע."
    if hint_level == 2:
        return (
            prefix
            + "\n\n"
            + f"{check_clue}. הכלי שמבצע אותו הוא {_PIECE_NAMES[piece.piece_type]} "
            + f"מהמשבצת {_ltr(chess.square_name(key.from_square))}. היעד עדיין לא נחשף."
        )
    return prefix + "\n\n" + f"מסע המפתח הוא {_ltr(branch['san'])}. ההמשך המלא עדיין לא נחשף."


class PrivateSolutionStore:
    """Store opaque context capabilities outside the public artifact tree."""

    def __init__(self, private_root: Path, output_root: Path):
        self.private_root = private_root.absolute()
        self.output_root = output_root.absolute()
        self.private_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.private_root, 0o700)
        if (
            self.private_root.is_symlink()
            or not self.private_root.is_dir()
            or self.private_root.resolve(strict=True) != self.private_root
            or self.private_root == self.output_root
            or self.private_root.is_relative_to(self.output_root)
            or self.output_root.is_relative_to(self.private_root)
        ):
            raise RuntimeError("AnythingLLM private solution root is unsafe")

    def create_context(
        self,
        request_directory: str,
        manifest: dict[str, Any],
        scope_value: Any,
    ) -> str:
        scope = validate_scope(scope_value)
        if not _REQUEST_DIRECTORY.fullmatch(request_directory):
            raise SolutionLookupError("invalid_private_context", "Request directory is invalid")
        puzzles = manifest.get("puzzles")
        if not isinstance(puzzles, list) or not puzzles:
            raise SolutionLookupError("invalid_private_context", "Puzzle list is invalid")
        ordered = []
        for expected, puzzle in enumerate(puzzles, start=1):
            if not isinstance(puzzle, dict):
                raise SolutionLookupError("invalid_private_context", "Puzzle metadata is invalid")
            public_id = puzzle.get("public_id")
            if (
                puzzle.get("sequence") != expected
                or not isinstance(public_id, str)
                or not _PUBLIC_ID.fullmatch(public_id)
            ):
                raise SolutionLookupError("invalid_private_context", "Puzzle identity is invalid")
            ordered.append({"sequence": expected, "public_id": public_id})
        token = f"ctx_{secrets.token_hex(32)}"
        record = {
            "schema": PRIVATE_SOLUTION_SCHEMA,
            "token": token,
            "request_directory": request_directory,
            "scope": scope,
            "puzzles": ordered,
            "hint_counts": {item["public_id"]: 0 for item in ordered},
        }
        record_path = self.private_root / f"{token}.json"
        _exclusive_json(record_path, record)
        latest = {
            "schema": PRIVATE_LATEST_SCHEMA,
            "scope": scope,
            "token": token,
        }
        try:
            _atomic_json(self._latest_path(scope), latest)
        except Exception:
            record_path.unlink(missing_ok=True)
            raise
        return token

    def _latest_path(self, scope: dict[str, str]) -> Path:
        canonical = json.dumps(scope, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return self.private_root / f"latest-{digest}.json"

    def _latest_token(self, scope: dict[str, str]) -> str:
        latest = _read_json_nofollow(self._latest_path(scope))
        token = latest.get("token")
        if (
            latest.get("schema") != PRIVATE_LATEST_SCHEMA
            or latest.get("scope") != scope
            or not isinstance(token, str)
            or not _TOKEN.fullmatch(token)
        ):
            raise SolutionLookupError("invalid_private_context", "Private context is invalid")
        return token

    def _load(self, token: Any, scope_value: Any) -> tuple[Path, dict[str, Any]]:
        scope = validate_scope(scope_value)
        if token is None:
            token = self._latest_token(scope)
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise SolutionLookupError("invalid_solution_context", "Puzzle context is invalid")
        path = self.private_root / f"{token}.json"
        record = _read_json_nofollow(path)
        if (
            record.get("schema") != PRIVATE_SOLUTION_SCHEMA
            or record.get("token") != token
            or record.get("scope") != scope
        ):
            raise SolutionLookupError(
                "solution_context_forbidden",
                "The prior puzzle does not belong to this conversation",
            )
        return path, record

    @staticmethod
    def _select(record: dict[str, Any], puzzle_number: Any, public_id: Any) -> dict[str, Any]:
        if puzzle_number is not None and public_id is not None:
            raise SolutionLookupError("invalid_puzzle_selector", "Select by number or public ID, not both")
        puzzles = record.get("puzzles")
        if not isinstance(puzzles, list) or not puzzles:
            raise SolutionLookupError("invalid_private_context", "Private puzzle list is invalid")
        if puzzle_number is not None:
            if isinstance(puzzle_number, bool) or not isinstance(puzzle_number, int):
                raise SolutionLookupError("invalid_puzzle_selector", "Puzzle number must be an integer")
            if not 1 <= puzzle_number <= len(puzzles):
                raise SolutionLookupError(
                    "puzzle_not_found", f"Puzzle number must be between 1 and {len(puzzles)}"
                )
            selected = puzzles[puzzle_number - 1]
        elif public_id is not None:
            if not isinstance(public_id, str) or not _PUBLIC_ID.fullmatch(public_id):
                raise SolutionLookupError("invalid_puzzle_selector", "Public puzzle ID is invalid")
            selected = next((item for item in puzzles if item.get("public_id") == public_id), None)
            if selected is None:
                raise SolutionLookupError("puzzle_not_found", "Puzzle is not in this conversation")
        else:
            selected = puzzles[-1]
        if not isinstance(selected, dict):
            raise SolutionLookupError("invalid_private_context", "Private puzzle entry is invalid")
        return selected

    def retrieve(
        self,
        *,
        context_token: Any,
        action: Any,
        scope: Any,
        puzzle_number: Any = None,
        public_id: Any = None,
    ) -> dict[str, Any]:
        if action not in {"hint", "solution"}:
            raise SolutionLookupError("invalid_followup_action", "Action must be hint or solution")
        record_path, record = self._load(context_token, scope)
        selected = self._select(record, puzzle_number, public_id)
        directory = record.get("request_directory")
        if not isinstance(directory, str) or not _REQUEST_DIRECTORY.fullmatch(directory):
            raise SolutionLookupError("invalid_private_context", "Private request directory is invalid")
        output = self.output_root / directory
        if output.is_symlink() or not output.is_dir() or output.resolve(strict=True) != output:
            raise SolutionLookupError("solution_context_unavailable", "Puzzle output is unavailable")
        report = verify_output(output, deep=True)
        if not report.valid:
            raise SolutionLookupError("integrity_verification_failed", "Puzzle integrity verification failed")
        try:
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SolutionLookupError("invalid_public_manifest", "Puzzle manifest is unavailable") from exc
        public_id_value = selected.get("public_id")
        puzzle = next(
            (item for item in manifest.get("puzzles", []) if item.get("public_id") == public_id_value),
            None,
        )
        if not isinstance(puzzle, dict) or puzzle.get("sequence") != selected.get("sequence"):
            raise SolutionLookupError("puzzle_not_found", "Puzzle identity does not match the batch")
        fen, mate = puzzle.get("normalized_fen"), puzzle.get("mate_moves")
        if not isinstance(fen, str) or mate not in {1, 2, 3}:
            raise SolutionLookupError("invalid_public_manifest", "Puzzle metadata is invalid")
        verification = MateVerifier().verify(fen, mate, dual_policy="warning")
        if (
            not verification.accepted
            or verification.puzzle_hash != puzzle.get("identity_sha256")
            or verification.proof is None
        ):
            raise SolutionLookupError("verification_failed", "Authoritative puzzle verification failed")
        number = int(selected["sequence"])
        batch_size = len(record["puzzles"])
        if action == "hint":
            counts = record.get("hint_counts")
            if not isinstance(counts, dict):
                raise SolutionLookupError("invalid_private_context", "Private hint state is invalid")
            previous = counts.get(public_id_value, 0)
            if isinstance(previous, bool) or not isinstance(previous, int) or previous < 0:
                raise SolutionLookupError("invalid_private_context", "Private hint state is invalid")
            level = min(previous + 1, 3)
            answer = _hint_text(level, number, batch_size, fen, verification)
            counts[public_id_value] = previous + 1
            _atomic_json(record_path, record)
            return {
                "schema": FOLLOWUP_RESULT_SCHEMA,
                "status": "success",
                "action": "hint",
                "puzzle_number": number,
                "public_id": public_id_value,
                "hint_level": level,
                "answer_he": answer,
                "verified_source": {
                    "authority": "AAG MateVerifier",
                    "verifier_version": VERIFIER_VERSION,
                    "certificate_sha256": verification.certificate_sha256,
                },
            }
        answer, structured = _solution_text(number, batch_size, verification)
        return {
            "schema": FOLLOWUP_RESULT_SCHEMA,
            "status": "success",
            "action": "solution",
            "puzzle_number": number,
            "public_id": public_id_value,
            "answer_he": answer,
            "solution": structured,
            "verified_source": {
                "authority": "AAG MateVerifier",
                "verifier_version": VERIFIER_VERSION,
                "certificate_sha256": verification.certificate_sha256,
            },
        }
