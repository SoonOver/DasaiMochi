#!/usr/bin/env python3
"""Quick cross-platform launcher for Terminal Dasai Mochi."""

import os
import subprocess
import sys
from pathlib import Path


def _ensure_project_venv() -> None:
    root = Path(__file__).resolve().parent
    if os.name == "nt":
        venv_python = root / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = root / ".venv" / "bin" / "python"

    if venv_python.exists() and Path(sys.prefix).resolve() != (root / ".venv").resolve():
        if os.environ.get("MOCHI_NO_VENV_REEXEC") != "1":
            env = os.environ.copy()
            env["MOCHI_NO_VENV_REEXEC"] = "1"
            cmd = [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]]
            if os.name == "nt":
                raise SystemExit(subprocess.call(cmd, env=env))
            os.execve(str(venv_python), cmd, env)


if __name__ == "__main__":
    _ensure_project_venv()
    from dasai_mochi.cli import main

    raise SystemExit(main())
