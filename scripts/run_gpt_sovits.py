"""在不重复装载同一 GPT-SoVITS 权重的情况下运行 api_v2。"""

from __future__ import annotations

import argparse
import functools
import os
import runpy
import sys
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import Any

_CACHE_ATTRIBUTE = "_qqchatrobot_loaded_weight_markers"


def _file_signature(path_value: object) -> tuple[str, int, int] | None:
    """用规范路径、文件大小和修改时间识别同一权重文件。"""
    try:
        path = Path(os.fspath(path_value)).expanduser().resolve(strict=True)
        stat = path.stat()
    except (OSError, TypeError, ValueError):
        return None
    if not path.is_file():
        return None
    return (os.path.normcase(str(path)), stat.st_size, stat.st_mtime_ns)


def _model_marker(
    signature: tuple[str, int, int],
    model: object,
    config_signature: tuple[str, str, bool] | None,
) -> tuple[Any, ...] | None:
    """保留弱引用和加载时的推理配置快照。"""
    try:
        model_reference = weakref.ref(model)
    except TypeError:
        return None
    if config_signature is None:
        return None
    return (*signature, model_reference, *config_signature)


def _config_signature(instance: object) -> tuple[str, str, bool] | None:
    configs = getattr(instance, "configs", None)
    if configs is None:
        return None
    try:
        return (str(configs.version), str(configs.device), bool(configs.is_half))
    except AttributeError:
        return None


def _marker_matches(
    marker: tuple[Any, ...] | None,
    signature: tuple[str, int, int] | None,
    model: object | None,
    config_signature: tuple[str, str, bool] | None,
) -> bool:
    return (
        marker is not None
        and signature is not None
        and model is not None
        and config_signature is not None
        and len(marker) == 7
        and marker[:3] == signature
        and marker[3]() is model
        and marker[4:] == config_signature
    )


def _wrap_weight_loader(
    original: Callable[..., Any],
    *,
    cache_key: str,
    model_attribute: str,
    update_config: Callable[[object, object], None],
) -> Callable[..., Any]:
    @functools.wraps(original)
    def wrapped(self: object, weights_path: object, *args: Any, **kwargs: Any) -> Any:
        cache = getattr(self, _CACHE_ATTRIBUTE, None)
        if not isinstance(cache, dict):
            return original(self, weights_path, *args, **kwargs)

        before = _file_signature(weights_path)
        current_model = getattr(self, model_attribute, None)
        config_signature = _config_signature(self)
        if _marker_matches(cache.get(cache_key), before, current_model, config_signature):
            try:
                update_config(self, weights_path)
            except BaseException:
                cache.pop(cache_key, None)
                raise
            return None

        # 重新加载前先作废旧标记，失败时不会误把旧模型当成目标权重。
        cache.pop(cache_key, None)
        try:
            result = original(self, weights_path, *args, **kwargs)
        except BaseException:
            cache.pop(cache_key, None)
            raise

        loaded_model = getattr(self, model_attribute, None)
        after = _file_signature(weights_path)
        if before is not None and after == before and loaded_model is not None:
            marker = _model_marker(after, loaded_model, _config_signature(self))
            if marker is not None:
                cache[cache_key] = marker
        return result

    return wrapped


def _track_constructor(original: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(original)
    def wrapped(self: object, *args: Any, **kwargs: Any) -> Any:
        cache: dict[str, tuple[Any, ...]] = {}
        setattr(self, _CACHE_ATTRIBUTE, cache)
        try:
            return original(self, *args, **kwargs)
        except BaseException:
            cache.clear()
            raise

    return wrapped


def _update_t2s_config(instance: Any, weights_path: object) -> None:
    configs = instance.configs
    configs.t2s_weights_path = weights_path
    configs.save_configs()
    configs.hz = 50


def _update_vits_config(instance: Any, weights_path: object) -> None:
    configs = instance.configs
    configs.vits_weights_path = weights_path
    configs.save_configs()


def _install_weight_cache() -> None:
    # 在 api_v2 创建唯一 TTS 实例前包装构造和权重加载方法。
    from GPT_SoVITS.TTS_infer_pack.TTS import TTS

    TTS.__init__ = _track_constructor(TTS.__init__)
    TTS.init_t2s_weights = _wrap_weight_loader(
        TTS.init_t2s_weights,
        cache_key="t2s",
        model_attribute="t2s_model",
        update_config=_update_t2s_config,
    )
    TTS.init_vits_weights = _wrap_weight_loader(
        TTS.init_vits_weights,
        cache_key="vits",
        model_attribute="vits_model",
        update_config=_update_vits_config,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpt-root", type=Path, required=True)
    parser.add_argument("--api-script", type=Path, required=True)
    parser.add_argument("api_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.api_args and args.api_args[0] == "--":
        args.api_args = args.api_args[1:]
    return args


def main() -> int:
    args = _parse_args()
    gpt_root = args.gpt_root.expanduser().resolve(strict=True)
    api_script = args.api_script.expanduser().resolve(strict=True)
    if not gpt_root.is_dir() or not api_script.is_file():
        raise SystemExit("GPT-SoVITS 根目录或 api_v2.py 不存在。")
    try:
        api_script.relative_to(gpt_root)
    except ValueError as exc:
        raise SystemExit("api_v2.py 必须位于 GPT-SoVITS 根目录内。") from exc

    os.chdir(gpt_root)
    sys.path.insert(0, str(gpt_root))
    sys.path.insert(0, str(gpt_root / "GPT_SoVITS"))
    api_arguments = list(args.api_args)
    sys.argv = [str(api_script), *api_arguments]
    _install_weight_cache()

    # 保留 api_v2 的 restart 命令，同时确保重启后仍经过本包装器。
    original_execl = os.execl
    launcher = Path(__file__).resolve()
    launcher_arguments = [
        str(launcher),
        "--gpt-root",
        str(gpt_root),
        "--api-script",
        str(api_script),
        "--",
        *api_arguments,
    ]

    def restart_through_launcher(file: str, *restart_args: str) -> Any:
        same_interpreter = os.path.normcase(os.fspath(file)) == os.path.normcase(sys.executable)
        is_api_restart = (
            same_interpreter
            and len(restart_args) >= 2
            and os.path.normcase(os.fspath(restart_args[1])) == os.path.normcase(str(api_script))
            and tuple(restart_args[2:]) == tuple(api_arguments)
        )
        if is_api_restart:
            return original_execl(
                sys.executable,
                sys.executable,
                *launcher_arguments,
            )
        return original_execl(file, *restart_args)

    os.execl = restart_through_launcher
    try:
        runpy.run_path(str(api_script), run_name="__main__")
    finally:
        os.execl = original_execl
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
