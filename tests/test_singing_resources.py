import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import singing
from app.services import singing_resources as resources

GIB = 1024**3


@pytest.fixture
def memory(monkeypatch):
    status = SimpleNamespace(
        total_phys=16 * GIB, available_phys=8 * GIB,
        total_commit=40 * GIB, available_commit=20 * GIB,
    )
    # 替换模块引用，不修改操作系统全局的 os.name。
    monkeypatch.setattr(resources, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(resources, "_read_memory_status", lambda: status)
    monkeypatch.setattr(resources.shutil, "disk_usage", lambda _: SimpleNamespace(free=100 * GIB))
    return status


def test_startup_and_running_memory_limits_are_distinct(memory, caplog):
    memory.available_phys = 3 * GIB
    reason = resources.singing_resource_problem(Path.cwd(), starting=True, stage="startup")
    assert "当前 3.00 GiB" in reason and "4 GiB" in reason
    snapshot = json.loads(caplog.records[-1].getMessage().split(" ", 1)[1])
    assert snapshot["reason"] == "low_physical_memory"
    assert snapshot["available_phys_bytes"] == 3 * GIB
    assert snapshot["minimum_phys_bytes"] == 4 * GIB
    assert snapshot["stage"] == "startup"
    assert resources.singing_resource_problem(Path.cwd(), stage="run_singing_model") is None


def test_running_guard_records_measured_memory_and_command_before_abort(memory, caplog):
    memory.available_phys = GIB
    reason = resources.singing_resource_problem(Path.cwd(), stage="run_singing_separation")
    assert "当前 1.00 GiB" in reason and "1.5 GiB" in reason
    snapshot = caplog.records[-1].resource_snapshot
    assert snapshot["available_commit_bytes"] == 20 * GIB
    assert snapshot["stage"] == "run_singing_separation"
    memory.available_phys = int(1.5 * GIB)
    assert resources.singing_resource_problem(Path.cwd()) is None


def test_commit_and_disk_pressure_are_distinguished(memory, monkeypatch, caplog):
    memory.available_commit = 6 * GIB
    reason = resources.singing_resource_problem(Path.cwd())
    assert "34.00/40.00 GiB" in reason and "85%" in reason
    assert caplog.records[-1].resource_snapshot["reason"] == "high_commit_usage"
    memory.available_commit = 20 * GIB
    monkeypatch.setattr(resources.shutil, "disk_usage", lambda _: SimpleNamespace(free=10 * GIB))
    assert "空间不足" in resources.singing_resource_problem(Path.cwd())
    assert caplog.records[-1].resource_snapshot["reason"] == "low_disk_space"


def test_unreadable_memory_remains_a_separate_error(memory, monkeypatch, caplog):
    monkeypatch.setattr(resources, "_read_memory_status", lambda: None)
    assert "无法读取内存状态" in resources.singing_resource_problem(Path.cwd())
    assert caplog.records[-1].resource_snapshot["reason"] == "memory_status_unavailable"


async def test_audio_guard_logs_command_and_does_not_launch_when_memory_is_low(memory, monkeypatch, caplog):
    memory.available_phys = GIB
    execute = AsyncMock()
    monkeypatch.setattr(singing, "_run_audio_command", execute)
    with pytest.raises(singing.SingingPipelineError, match="当前 1.00 GiB"):
        await singing.run_audio_command(["python.exe", "scripts/run_singing_model.py"])
    execute.assert_not_awaited()
    assert caplog.records[-1].resource_snapshot["stage"] == "run_singing_model"


async def test_startup_guard_blocks_generation_before_any_gpu_work(memory, monkeypatch, caplog):
    memory.available_phys = 3 * GIB
    generate = AsyncMock()
    monkeypatch.setattr(singing, "_generate_singing_cover_unlocked", generate)
    with pytest.raises(singing.SingingPipelineError, match="4 GiB"):
        await singing.generate_singing_cover(
            "synthetic song", "test", "0" * 32, SimpleNamespace(), AsyncMock(), bot_self_id="test",
        )
    generate.assert_not_awaited()
    assert caplog.records[-1].resource_snapshot["stage"] == "startup"


async def test_running_memory_drop_cancels_command_and_keeps_trigger_reading(memory, monkeypatch, caplog):
    stopped = asyncio.Event()

    async def execute(*args, **kwargs):
        memory.available_phys = GIB
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(singing, "_run_audio_command", execute)
    with pytest.raises(singing.SingingPipelineError, match="当前 1.00 GiB"):
        await asyncio.wait_for(
            singing.run_audio_command(["python.exe", "scripts/run_singing_separation.py"]), 3,
        )
    assert stopped.is_set()
    snapshot = caplog.records[-1].resource_snapshot
    assert snapshot["reason"] == "low_physical_memory"
    assert snapshot["stage"] == "run_singing_separation"


async def test_expected_pipeline_error_is_logged_with_business_stage(monkeypatch, caplog):
    from app import main
    from app.services.singing_jobs import SingingError

    monkeypatch.setattr(main, "settings", SimpleNamespace(singing_job_timeout_seconds=10))
    monkeypatch.setattr(main, "generate_singing_cover", AsyncMock(side_effect=singing.SingingPipelineError("safe resource reason")))
    monkeypatch.setattr(main, "send_group_message", AsyncMock())
    monkeypatch.setattr(main, "cleanup_singing_job", lambda _: None)
    job = SimpleNamespace(id="0" * 32, key=("local", "42", "test"), query="song", progress="separating")
    with pytest.raises(SingingError, match="safe resource reason"):
        await main._run_singing_job(job, "test", "test")
    assert any(
        "stage=separating error_type=SingingPipelineError reason=safe resource reason" in record.getMessage()
        for record in caplog.records
    )
