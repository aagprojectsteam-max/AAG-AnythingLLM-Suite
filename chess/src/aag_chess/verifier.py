"""Deterministic bounded exact-mate proof verifier.

Stockfish is intentionally not used here. Legal moves and terminal state come
from python-chess; this module explicitly enumerates attacker and defender
branches under a node/time budget and emits a stable compact certificate.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import chess

from . import VERIFIER_VERSION
from .position import PositionError, normalized_fen, parse_board, puzzle_hash


class VerificationBudgetExceeded(RuntimeError):
    """The verifier exceeded its explicit deterministic resource bound."""


@dataclass
class _Budget:
    max_nodes: int
    max_seconds: float
    started: float = field(default_factory=time.monotonic)
    nodes: int = 0

    def visit(self) -> None:
        self.nodes += 1
        if self.nodes > self.max_nodes:
            raise VerificationBudgetExceeded("verification node budget exceeded")
        if time.monotonic() - self.started > self.max_seconds:
            raise VerificationBudgetExceeded("verification wall-time budget exceeded")


@dataclass(frozen=True)
class _Solved:
    distance: int | None
    winning_moves: tuple[str, ...]


@dataclass(frozen=True)
class VerificationResult:
    accepted: bool
    reason: str
    requested_mate_moves: int
    target_plies: int
    exact_mate_plies: int | None
    unique_key: bool
    key_moves: tuple[str, ...]
    dual_policy: str
    duals: tuple[dict[str, Any], ...]
    structurally_valid: bool
    normalized_fen: str | None
    puzzle_hash: str | None
    nodes: int
    elapsed_ms: int
    proof: dict[str, Any] | None
    certificate_sha256: str | None
    verifier_version: str = VERIFIER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "requested_mate_moves": self.requested_mate_moves,
            "target_plies": self.target_plies,
            "exact_mate_plies": self.exact_mate_plies,
            "unique_key": self.unique_key,
            "key_moves": list(self.key_moves),
            "dual_policy": self.dual_policy,
            "duals": list(self.duals),
            "structurally_valid": self.structurally_valid,
            "normalized_fen": self.normalized_fen,
            "puzzle_hash": self.puzzle_hash,
            "nodes": self.nodes,
            "elapsed_ms": self.elapsed_ms,
            "proof": self.proof,
            "certificate_sha256": self.certificate_sha256,
            "verifier_version": self.verifier_version,
        }


class MateVerifier:
    """Proves exact mate in 1, 2, or 3 with a minimax legal-move search."""

    def __init__(self, *, max_nodes: int = 500_000, max_seconds: float = 10.0):
        if not 1 <= max_nodes <= 5_000_000:
            raise ValueError("max_nodes must be between 1 and 5,000,000")
        if not 0.01 <= max_seconds <= 60:
            raise ValueError("max_seconds must be between 0.01 and 60")
        self.max_nodes = max_nodes
        self.max_seconds = max_seconds
        self._memo: dict[tuple[str, bool, int], _Solved] = {}
        self._budget = _Budget(max_nodes, max_seconds)
        self._attacker = chess.WHITE

    def verify(
        self,
        fen: str,
        mate_moves: int,
        *,
        require_unique_key: bool = True,
        dual_policy: Literal["forbid", "warning", "allow"] = "forbid",
    ) -> VerificationResult:
        started = time.monotonic()
        if mate_moves not in (1, 2, 3):
            raise ValueError("mate_moves must be 1, 2, or 3")
        if dual_policy not in ("forbid", "warning", "allow"):
            raise ValueError("dual_policy must be forbid, warning, or allow")
        target = 2 * mate_moves - 1
        try:
            board = parse_board(fen)
        except PositionError as exc:
            return self._failure(
                str(exc), mate_moves, target, dual_policy, started, structurally_valid=False
            )
        self._attacker = board.turn
        self._memo = {}
        self._budget = _Budget(self.max_nodes, self.max_seconds)
        norm = normalized_fen(board)
        phash = puzzle_hash(board, mate_moves)
        try:
            solved = self._solve(board, target)
            exact = solved.distance
            keys = solved.winning_moves if exact is not None and exact <= target else ()
            unique = len(keys) == 1
            proof = None
            duals: tuple[dict[str, Any], ...] = ()
            reason = "accepted"
            accepted = exact == target
            if exact is None:
                reason = "no_forced_mate_within_bound"
            elif exact != target:
                reason = "mate_distance_not_exact"
            elif require_unique_key and not unique:
                accepted = False
                reason = "key_not_unique"
            if exact == target and keys:
                proof = self._build_proof(board, target, keys)
                duals = tuple(self._collect_duals(proof))
                if duals and dual_policy == "forbid":
                    accepted = False
                    reason = "second_move_duals_forbidden"
                elif duals and dual_policy == "warning" and accepted:
                    reason = "accepted_with_dual_warning"
            certificate = self._certificate(proof) if proof is not None else None
            return VerificationResult(
                accepted=accepted,
                reason=reason,
                requested_mate_moves=mate_moves,
                target_plies=target,
                exact_mate_plies=exact,
                unique_key=unique,
                key_moves=tuple(keys),
                dual_policy=dual_policy,
                duals=duals,
                structurally_valid=True,
                normalized_fen=norm,
                puzzle_hash=phash,
                nodes=self._budget.nodes,
                elapsed_ms=round((time.monotonic() - started) * 1000),
                proof=proof,
                certificate_sha256=certificate,
            )
        except VerificationBudgetExceeded as exc:
            return self._failure(
                str(exc), mate_moves, target, dual_policy, started,
                structurally_valid=True, norm=norm, phash=phash,
            )

    def _failure(
        self,
        reason: str,
        mate_moves: int,
        target: int,
        dual_policy: str,
        started: float,
        *,
        structurally_valid: bool,
        norm: str | None = None,
        phash: str | None = None,
    ) -> VerificationResult:
        return VerificationResult(
            accepted=False,
            reason=reason,
            requested_mate_moves=mate_moves,
            target_plies=target,
            exact_mate_plies=None,
            unique_key=False,
            key_moves=(),
            dual_policy=dual_policy,
            duals=(),
            structurally_valid=structurally_valid,
            normalized_fen=norm,
            puzzle_hash=phash,
            nodes=getattr(self, "_budget", _Budget(1, 1)).nodes,
            elapsed_ms=round((time.monotonic() - started) * 1000),
            proof=None,
            certificate_sha256=None,
        )

    @staticmethod
    def _key(board: chess.Board, remaining: int) -> tuple[str, bool, int]:
        return normalized_fen(board), board.turn, remaining

    def _solve(self, board: chess.Board, remaining: int) -> _Solved:
        self._budget.visit()
        key = self._key(board, remaining)
        cached = self._memo.get(key)
        if cached is not None:
            return cached
        if board.is_checkmate():
            result = _Solved(0 if board.turn != self._attacker else None, ())
            self._memo[key] = result
            return result
        if board.is_stalemate() or board.is_insufficient_material() or remaining == 0:
            result = _Solved(None, ())
            self._memo[key] = result
            return result
        moves = sorted(board.legal_moves, key=lambda move: move.uci())
        if not moves:
            result = _Solved(None, ())
            self._memo[key] = result
            return result
        children: list[tuple[str, int | None]] = []
        for move in moves:
            board.push(move)
            child = self._solve(board, remaining - 1)
            board.pop()
            children.append((move.uci(), child.distance))
        if board.turn == self._attacker:
            winning = [(move, distance) for move, distance in children if distance is not None]
            if not winning:
                result = _Solved(None, ())
            else:
                best = min(distance for _, distance in winning)
                best_moves = tuple(move for move, distance in winning if distance == best)
                result = _Solved(best + 1, best_moves)
        else:
            if any(distance is None for _, distance in children):
                result = _Solved(None, ())
            else:
                longest = max(int(distance) for _, distance in children)
                result = _Solved(longest + 1, tuple(move for move, _ in children))
        self._memo[key] = result
        return result

    def _build_proof(
        self, board: chess.Board, remaining: int, selected_moves: tuple[str, ...] | None = None
    ) -> dict[str, Any]:
        if board.is_checkmate():
            return {"terminal": "checkmate", "fen": normalized_fen(board)}
        solved = self._memo[self._key(board, remaining)]
        if solved.distance is None:
            raise AssertionError("cannot build a proof for an unsolved node")
        role = "attacker" if board.turn == self._attacker else "defender"
        if role == "attacker":
            moves = selected_moves or solved.winning_moves
        else:
            moves = tuple(move.uci() for move in sorted(board.legal_moves, key=lambda m: m.uci()))
        branches = []
        for uci in moves:
            move = chess.Move.from_uci(uci)
            san = board.san(move)
            board.push(move)
            child_solved = self._memo.get(self._key(board, remaining - 1))
            if child_solved is None or child_solved.distance is None:
                board.pop()
                raise AssertionError("proof branch is not a forced mate")
            child = self._build_proof(board, remaining - 1)
            board.pop()
            branches.append({"uci": uci, "san": san, "distance": child_solved.distance, "child": child})
        return {
            "role": role,
            "fen": normalized_fen(board),
            "distance": solved.distance,
            "moves": branches,
        }

    @staticmethod
    def _collect_duals(proof: dict[str, Any]) -> list[dict[str, Any]]:
        duals: list[dict[str, Any]] = []

        def walk(node: dict[str, Any], ply: int, line: list[str]) -> None:
            if node.get("terminal"):
                return
            moves = node.get("moves", [])
            if node.get("role") == "attacker" and ply >= 2 and len(moves) > 1:
                duals.append({"ply": ply, "after": list(line), "moves": [m["uci"] for m in moves]})
            for branch in moves:
                walk(branch["child"], ply + 1, [*line, branch["uci"]])

        walk(proof, 0, [])
        return duals

    @staticmethod
    def _certificate(proof: dict[str, Any]) -> str:
        payload = json.dumps(proof, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def principal_variation(proof: dict[str, Any]) -> list[str]:
    """Return a stable longest-defense line from a proof certificate."""

    line: list[str] = []
    node = proof
    while node and not node.get("terminal"):
        moves = node.get("moves", [])
        if not moves:
            break
        branch = sorted(moves, key=lambda item: (-int(item["distance"]), item["uci"]))[0]
        line.append(branch["uci"])
        node = branch["child"]
    return line

