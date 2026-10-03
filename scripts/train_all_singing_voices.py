"""Train sequential, review-only Seed-VC candidates for configured voices.

The batch reuses one run name per profile so an interrupted run can resume.
It never activates checkpoints and skips profiles whose candidate registry
already contains a different run or an accepted model.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SINGING_ROOT = PROJECT_ROOT / "data" / "singing"
DEFAULT_PROFILES = (
    "murasame",
    "yoshino",
    "mako",
    "aimisi",
    "lena",
    "roka",
    "koharu",
)
DEFAULT_ROUNDS = 2
DEFAULT_MAX_STEPS = 100


def _checkpoint_exists(project_root: Path, profile: str, run_name: str) -> bool:
    checkpoint_dir = project_root / "data" / "singing" / "checkpoints" / profile / run_name
    return (checkpoint_dir / "ft_model.pth").is_file() or any(
        checkpoint_dir.glob("DiT_epoch_*_step_*.pth")
    )


def _read_candidate_registry(project_root: Path) -> dict[str, Any]:
    registry = project_root / "data" / "singing" / "candidates.json"
    if not registry.exists():
        return {}
    data = json.loads(registry.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"Candidate registry must be a JSON object: {registry}")
    return data


def _preflight_profile(project_root: Path, profile: str, run_name: str) -> str | None:
    try:
        candidate = _read_candidate_registry(project_root).get(profile)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        return f"cannot read candidate registry: {error}"
    if candidate is None:
        return None
    if not isinstance(candidate, dict):
        return "candidate registry entry is not an object"
    if candidate.get("status") == "accepted" or candidate.get("accepted") is True:
        return "an accepted singing model already exists; refusing to replace it"
    if candidate.get("run_name") != run_name:
        return (
            f"candidate uses run {candidate.get('run_name')!r}; archive or review it "
            f"before starting fixed batch run {run_name!r}"
        )
    return None


def _training_command(
    python: Path,
    project_root: Path,
    profile: str,
    run_name: str,
    max_steps: int,
    *,
    resume: bool,
) -> list[str]:
    command = [
        str(python),
        str(project_root / "scripts" / "train_singing_voices.py"),
        "--profile",
        profile,
        "--run-name",
        run_name,
        "--max-steps",
        str(max_steps),
    ]
    if resume:
        command.append("--resume")
    return command


def run_training_batch(
    project_root: Path,
    *,
    profiles: tuple[str, ...] = DEFAULT_PROFILES,
    rounds: int = DEFAULT_ROUNDS,
    max_steps: int = DEFAULT_MAX_STEPS,
    python: Path = Path(sys.executable),
    runner: Any = subprocess.run,
) -> list[dict[str, str]]:
    """Run all requested training rounds serially and collect per-profile results."""
    if rounds < 1:
        raise ValueError("rounds must be at least one")
    if max_steps not in {100, 300, 1000}:
        raise ValueError("max_steps must be 100, 300, or 1000")

    results: list[dict[str, str]] = []
    for profile in profiles:
        run_name = f"{profile}-svc-batch"
        reason = _preflight_profile(project_root, profile, run_name)
        if reason:
            results.append({"profile": profile, "status": "failed", "reason": reason})
            continue

        run_root = project_root / "data" / "singing" / "runs" / profile / run_name
        checkpoint_dir = project_root / "data" / "singing" / "checkpoints" / profile / run_name
        has_artifacts = run_root.exists() or checkpoint_dir.exists()
        has_checkpoint = _checkpoint_exists(project_root, profile, run_name)
        try:
            candidate_already_exists = profile in _read_candidate_registry(project_root)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
            results.append(
                {
                    "profile": profile,
                    "status": "failed",
                    "reason": f"cannot read candidate registry: {error}",
                }
            )
            continue
        if candidate_already_exists and not has_checkpoint:
            results.append(
                {
                    "profile": profile,
                    "status": "failed",
                    "reason": "candidate exists but its fixed batch run has no resumable checkpoint",
                }
            )
            continue
        if has_artifacts and not has_checkpoint:
            results.append(
                {
                    "profile": profile,
                    "status": "failed",
                    "reason": "batch run artifacts exist without a resumable checkpoint",
                }
            )
            continue

        completed_rounds = 0
        failure: str | None = None
        for _round_number in range(1, rounds + 1):
            resume = _checkpoint_exists(project_root, profile, run_name)
            command = _training_command(
                python, project_root, profile, run_name, max_steps, resume=resume
            )
            try:
                process = runner(command, check=False)
            except (OSError, subprocess.SubprocessError) as error:
                failure = f"could not run training process: {error}"
                break
            if process.returncode != 0:
                failure = f"training process exited with code {process.returncode}"
                break
            completed_rounds += 1
            if not _checkpoint_exists(project_root, profile, run_name):
                failure = "training returned success without a resumable checkpoint"
                break
            registry_reason = _preflight_profile(project_root, profile, run_name)
            if registry_reason:
                failure = f"candidate registry validation failed: {registry_reason}"
                break

        if failure:
            results.append(
                {
                    "profile": profile,
                    "status": "failed",
                    "completed_rounds": str(completed_rounds),
                    "reason": failure,
                }
            )
        else:
            results.append(
                {
                    "profile": profile,
                    "status": "completed",
                    "completed_rounds": str(completed_rounds),
                    "run_name": run_name,
                }
            )
    return results


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least one")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profiles",
        nargs="+",
        choices=DEFAULT_PROFILES,
        default=DEFAULT_PROFILES,
        help="profiles to train in order (default: all seven configured profiles)",
    )
    parser.add_argument("--rounds", type=_positive_int, default=DEFAULT_ROUNDS)
    parser.add_argument(
        "--max-steps", type=int, choices=(100, 300, 1000), default=DEFAULT_MAX_STEPS
    )
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    results = run_training_batch(
        args.project_root.resolve(),
        profiles=tuple(args.profiles),
        rounds=args.rounds,
        max_steps=args.max_steps,
        python=args.python.resolve(),
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return int(any(result["status"] == "failed" for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
