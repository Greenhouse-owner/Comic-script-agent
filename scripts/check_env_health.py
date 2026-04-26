#!/usr/bin/env python3
"""Environment health check for Apple Silicon arm64 setup."""

from __future__ import annotations

import importlib
import platform
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"


def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def main() -> int:
    checks = []

    host_arch = run(["uname", "-m"])
    py_arch = platform.machine()
    checks.append(("host_arch", host_arch))
    checks.append(("python_arch", py_arch))

    if VENV_PYTHON.exists():
        py_file_info = run(["file", str(VENV_PYTHON)])
        checks.append(("venv_python_file", py_file_info))

    if host_arch != "arm64":
        checks.append(("host_arch_warning", "shell appears to be under Rosetta, switch terminal to native arm64"))

    binary_modules = [
        ("jiter", "jiter.jiter"),
        ("pydantic_core", "pydantic_core._pydantic_core"),
    ]

    for label, import_name in binary_modules:
        try:
            module = importlib.import_module(import_name)
            module_path = Path(getattr(module, "__file__", ""))
            if module_path.exists():
                checks.append((f"{label}_binary", run(["file", str(module_path)])))
            else:
                checks.append((f"{label}_binary", "not_found"))
        except Exception as exc:  # pragma: no cover - diagnostics path
            checks.append((f"{label}_import", f"FAILED: {exc}"))

    print("=== Environment Health Check ===")
    for key, value in checks:
        print(f"{key}: {value}")

    failed = []
    if py_arch != "arm64":
        failed.append("Python interpreter is not arm64")
    for key, value in checks:
        if key.endswith("_binary") and "arm64" not in value:
            failed.append(f"{key} is not arm64")
        if key.endswith("_import") and value.startswith("FAILED"):
            failed.append(f"{key} import failed")

    if failed:
        print("\n❌ FAILED")
        for item in failed:
            print(f"- {item}")
        return 1

    print("\n✅ PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
