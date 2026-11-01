"""Run a local GPT-SoVITS preprocessing or training entry point on Windows."""
import argparse
import os
import runpy
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpt-root", type=Path, required=True)
    parser.add_argument("script")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    root = options.gpt_root.absolute()
    script = root / options.script
    if not script.is_file() or not script.absolute().is_relative_to(root):
        parser.error("Task script must be inside the GPT-SoVITS installation.")
    os.chdir(root)
    sys.path[:0] = [str(script.parent), str(root), str(root / "GPT_SoVITS")]
    from tools.portable_runtime import activate
    activate(change_cwd=False)
    try:
        import pyopenjtalk
    except ImportError:
        pass
    else:
        dictionary = Path(pyopenjtalk.OPEN_JTALK_DICT_DIR.decode("utf-8"))
        if dictionary.is_relative_to(root.resolve()):
            alias = root / dictionary.relative_to(root.resolve())
            if str(alias).isascii() and alias.is_dir():
                pyopenjtalk.OPEN_JTALK_DICT_DIR = str(alias).encode("utf-8")
    sys.argv = [str(script), *options.args]
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
