"""Keep the operator's .env and data in AstrBot's persistent plugin-data directory."""
import os
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile


def prepare_runtime(shipped: Path, destination: Path, *, core=True) -> Path:
    root = destination.resolve()
    if core and not (shipped / "app/main.py").is_file():
        raise RuntimeError("共享运行时安装包不完整。")
    root.mkdir(parents=True, exist_ok=True)
    for source in shipped.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(shipped)
        if (relative.parts[0] not in {"app", "integrations", "scripts", "docs", "prompts", "assets", "pyproject.toml"}
                or "__pycache__" in relative.parts):
            continue
        target = root / relative
        if not target.resolve().is_relative_to(root):
            raise ValueError("运行时目标超出插件数据目录")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() == source.read_bytes():
            continue
        with NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                with source.open("rb") as origin:
                    shutil.copyfileobj(origin, stream)
            except BaseException:
                stream.close()
                temporary.unlink(missing_ok=True)
                raise
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return root
