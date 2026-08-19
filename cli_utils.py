"""Cross-platform helpers for invoking npm-installed command-line tools."""

import os
import shutil
from pathlib import Path


def cli_environment() -> dict[str, str]:
    """Return an environment containing common user-level CLI locations."""
    env = os.environ.copy()
    candidates = [
        Path.home() / ".local" / "bin",
        Path.home() / ".npm-global" / "bin",
    ]
    if os.name == "nt":
        appdata = env.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / "npm")

    current_path = env.get("PATH", "")
    entries = [entry for entry in current_path.split(os.pathsep) if entry]
    normalized = {os.path.normcase(os.path.normpath(entry)) for entry in entries}
    for candidate in reversed(candidates):
        value = str(candidate)
        key = os.path.normcase(os.path.normpath(value))
        if key not in normalized:
            entries.insert(0, value)
            normalized.add(key)
    env["PATH"] = os.pathsep.join(entries)
    return env


def cli_available(name: str, env: dict[str, str] | None = None) -> bool:
    """Check whether a CLI can be resolved in the same environment we run it."""
    search_env = env or cli_environment()
    return shutil.which(name, path=search_env.get("PATH")) is not None


def cli_command(
    name: str, *args: str, env: dict[str, str] | None = None
) -> list[str]:
    """Build a subprocess command for an executable or an npm Windows shim.

    npm exposes commands as ``.cmd``/``.ps1`` files on Windows. Those files
    cannot always be passed directly to CreateProcess, so use the matching
    PowerShell shim when no native executable is available.
    """
    search_env = env or cli_environment()
    executable = shutil.which(name, path=search_env.get("PATH"))
    if executable is None:
        return [name, *args]

    path = Path(executable)
    if os.name == "nt" and path.suffix.lower() in {".cmd", ".bat"}:
        ps1_path = path.with_suffix(".ps1")
        powershell = shutil.which("powershell.exe", path=search_env.get("PATH"))
        if ps1_path.exists() and powershell:
            return [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ps1_path),
                *args,
            ]

    return [executable, *args]
