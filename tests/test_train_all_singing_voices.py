from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from train_all_singing_voices import (
    DEFAULT_MAX_STEPS,
    DEFAULT_PROFILES,
    DEFAULT_ROUNDS,
    run_training_batch,
)


def _mock_successful_training(project_root: Path, calls: list[list[str]]):
    def runner(command: list[str], *, check: bool):
        assert check is False
        calls.append(command)
        profile = command[command.index("--profile") + 1]
        run_name = command[command.index("--run-name") + 1]
        checkpoint_dir = (
            project_root / "data" / "singing" / "checkpoints" / profile / run_name
        )
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        (checkpoint_dir / "ft_model.pth").write_bytes(b"mock checkpoint")
        registry = project_root / "data" / "singing" / "candidates.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        candidates = json.loads(registry.read_text(encoding="utf-8")) if registry.exists() else {}
        candidates[profile] = {
            "run_name": run_name,
            "status": "candidate_requires_human_review",
            "accepted": False,
        }
        registry.write_text(json.dumps(candidates), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    return runner


def test_defaults_cover_seven_profiles_two_rounds_and_one_hundred_steps() -> None:
    assert DEFAULT_PROFILES == (
        "murasame",
        "yoshino",
        "mako",
        "aimisi",
        "lena",
        "roka",
        "koharu",
    )
    assert DEFAULT_ROUNDS == 2
    assert DEFAULT_MAX_STEPS == 100


def test_batch_runs_profiles_and_rounds_serially_with_resume(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    results = run_training_batch(
        tmp_path,
        profiles=("murasame", "yoshino"),
        rounds=2,
        max_steps=100,
        python=Path(sys.executable),
        runner=_mock_successful_training(tmp_path, calls),
    )

    assert [result["status"] for result in results] == ["completed", "completed"]
    assert len(calls) == 4
    assert [command[command.index("--profile") + 1] for command in calls] == [
        "murasame",
        "murasame",
        "yoshino",
        "yoshino",
    ]
    assert "--resume" not in calls[0]
    assert "--resume" in calls[1]
    assert "--resume" not in calls[2]
    assert "--resume" in calls[3]
    registry = json.loads(
        (tmp_path / "data" / "singing" / "candidates.json").read_text(encoding="utf-8")
    )
    assert set(registry) == {"murasame", "yoshino"}
    assert all(candidate["accepted"] is False for candidate in registry.values())


def test_failed_profile_is_recorded_and_later_profiles_continue(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    successful = _mock_successful_training(tmp_path, calls)

    def runner(command: list[str], *, check: bool):
        profile = command[command.index("--profile") + 1]
        if profile == "murasame":
            calls.append(command)
            return SimpleNamespace(returncode=2)
        return successful(command, check=check)

    results = run_training_batch(
        tmp_path,
        profiles=("murasame", "yoshino"),
        rounds=2,
        runner=runner,
    )

    assert results[0]["status"] == "failed"
    assert "code 2" in results[0]["reason"]
    assert results[1]["status"] == "completed"
    assert [command[command.index("--profile") + 1] for command in calls] == [
        "murasame",
        "yoshino",
        "yoshino",
    ]


def test_existing_different_run_is_preserved_and_not_started(tmp_path: Path) -> None:
    registry = tmp_path / "data" / "singing" / "candidates.json"
    registry.parent.mkdir(parents=True)
    existing = {
        "murasame": {
            "run_name": "murasame-svc-manual",
            "status": "candidate_requires_human_review",
            "checkpoint": "old-candidate.pth",
        }
    }
    registry.write_text(json.dumps(existing), encoding="utf-8")
    calls: list[list[str]] = []

    results = run_training_batch(
        tmp_path,
        profiles=("murasame",),
        runner=lambda command, *, check: calls.append(command)
        or SimpleNamespace(returncode=0),
    )

    assert results[0]["status"] == "failed"
    assert "murasame-svc-manual" in results[0]["reason"]
    assert calls == []
    assert json.loads(registry.read_text(encoding="utf-8")) == existing
