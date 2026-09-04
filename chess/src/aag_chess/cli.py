"""Command-line interface for AAG Chess Puzzle Agent V1."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__
from .application import (
    ApplicationError,
    ApplicationGenerationError,
    OutputCollisionError,
    generate_public_batch,
    verify_output,
)
from .generator import GenerationRequestError
from .stockfish import StockfishError, inspect_stockfish


def _bounded_integer(name: str, minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            parsed = int(value, 10)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(
                f"{name} must be between {minimum} and {maximum}"
            )
        return parsed

    return parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aag-chess",
        description="Generate deterministic, verifier-approved unsolved chess puzzles.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser(
        "generate",
        help="generate one complete public puzzle batch",
        description=(
            "Generate verified unsolved puzzle images and a public manifest. "
            "The output path must not already exist; partial batches are never written."
        ),
    )
    generate.add_argument("--mate", required=True, type=_bounded_integer("mate", 1, 3))
    generate.add_argument("--side", required=True, choices=("white", "black"))
    generate.add_argument(
        "--difficulty", required=True, choices=("easy", "medium", "hard")
    )
    generate.add_argument(
        "--count", required=True, type=_bounded_integer("count", 1, 100)
    )
    generate.add_argument(
        "--seed", type=_bounded_integer("seed", 0, 2**63 - 1), default=0
    )
    generate.add_argument(
        "--formats",
        nargs="+",
        choices=("svg", "png"),
        default=("svg", "png"),
        metavar="FORMAT",
        help="one or both of: svg png (default: svg png)",
    )
    generate.add_argument(
        "--engine",
        choices=("auto", "builtin", "stockfish"),
        default="auto",
        help="candidate backend (default: auto, prefers local Stockfish)",
    )
    generate.add_argument(
        "--density",
        choices=("auto", "sparse", "normal", "rich"),
        default="auto",
        help="board-density preference (default: auto; weighted toward normal/rich)",
    )
    generate.add_argument(
        "--stockfish-nodes",
        type=_bounded_integer("stockfish-nodes", 100, 1_000_000),
        default=50_000,
        help="deterministic node limit per Stockfish candidate (default: 50000)",
    )
    generate.add_argument(
        "--max-attempts",
        type=_bounded_integer("max-attempts", 1, 10_000),
        default=2_000,
        help="bounded candidate-attempt limit (default: 2000)",
    )
    generate.add_argument("--output", required=True, help="new output directory")

    verify = subparsers.add_parser(
        "verify-output",
        help="check manifest structure and artifact hashes",
        description=(
            "Verify manifest structure, file set, metadata, sizes, and SHA-256 hashes. "
            "Use --deep to also re-run bounded chess verification and difficulty scoring."
        ),
    )
    verify.add_argument("output", help="generated output directory")
    verify.add_argument(
        "--deep", action="store_true", help="also re-prove each chess puzzle"
    )

    subparsers.add_parser(
        "engine-info",
        help="show detected local Stockfish path and version",
    )
    return parser


def _generate(args: argparse.Namespace) -> int:
    result = generate_public_batch(
        mate_moves=args.mate,
        side_to_move=args.side,
        difficulty=args.difficulty,
        count=args.count,
        seed=args.seed,
        output=args.output,
        formats=args.formats,
        max_candidate_attempts=args.max_attempts,
        engine=args.engine,
        stockfish_nodes=args.stockfish_nodes,
        density=args.density,
    )
    print(
        f"Generated {result.generated_count}/{result.requested_count} verified "
        f"unsolved puzzle(s): {result.output_directory}"
    )
    print(f"Manifest: {result.manifest_path}")
    print(f"Manifest SHA-256: {result.manifest_sha256}")
    print("Public IDs: " + ", ".join(result.public_ids))
    print(f"Generation backend: {result.backend}")
    return 0


def _verify(args: argparse.Namespace) -> int:
    report = verify_output(args.output, deep=args.deep)
    if not report.valid:
        for error in report.errors:
            print(f"Integrity error: {error}", file=sys.stderr)
        return 1
    mode = "deep chess + artifact" if report.deep_verified else "artifact"
    print(
        f"PASS: {mode} verification; {report.puzzle_count} puzzle(s), "
        f"{report.checked_artifacts} artifact(s): {report.output_directory}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            return _generate(args)
        if args.command == "engine-info":
            info = inspect_stockfish()
            if not info.available:
                print("Stockfish unavailable")
                return 1
            print("Stockfish available")
            print(f"Binary: {info.binary}")
            print(f"Version: {info.version}")
            return 0
        return _verify(args)
    except ApplicationGenerationError as exc:
        print(f"Generation failed: {exc}", file=sys.stderr)
        return 3
    except OutputCollisionError as exc:
        print(f"Output error: {exc}", file=sys.stderr)
        return 4
    except (ApplicationError, GenerationRequestError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except StockfishError as exc:
        print(f"Stockfish error: {exc}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
