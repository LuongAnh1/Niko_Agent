from __future__ import annotations

import os
from pathlib import Path


TRUE_VALUES = {"1", "true", "yes", "on"}


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_env_file(path: Path = Path(".env"), override_keys: set[str] | None = None) -> set[str]:
    loaded_keys: set[str] = set()
    override_keys = override_keys or set()
    if not path.exists():
        return loaded_keys

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and (key not in os.environ or key in override_keys):
            os.environ[key] = value
            loaded_keys.add(key)
    return loaded_keys


def load_env_files(bot_name: str | None = None) -> None:
    root = repo_root()
    root_keys = load_env_file(root / ".env")
    niko_keys = load_env_file(root / "niko" / ".env", override_keys=root_keys)
    loaded_file_keys = root_keys | niko_keys
    if bot_name:
        load_env_file(root / "bots" / bot_name / ".env", override_keys=loaded_file_keys)


def env_value(name: str, default: str = "", legacy_name: str | None = None) -> str:
    if name in os.environ:
        return os.getenv(name, default)
    if legacy_name and legacy_name in os.environ:
        return os.getenv(legacy_name, default)
    return default


def env_flag(name: str, default: str = "0", legacy_name: str | None = None) -> bool:
    return env_value(name, default, legacy_name).strip().lower() in TRUE_VALUES


def resolve_project_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return repo_root() / path
