"""Cấu hình dùng chung cho toàn harness.

Repo này có nhiều entrypoint chạy độc lập: Telegram bot, ops dashboard,
warmup script cho Nimble, test harness. Thay vì để mỗi entrypoint tự đoán
đường dẫn và tự đọc `.env`, file này gom ba quy tắc nền:

  env files  root `.env` -> `niko/.env` -> `bots/<bot>/.env`
  override   biến môi trường thật của OS luôn thắng file `.env`
  paths      path tương đối luôn được neo vào repo root
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from typing import Any


TRUE_VALUES = {"1", "true", "yes", "on"}
RUNTIME_CONFIG_ENV = "NIKO_RUNTIME_CONFIG_FILE"
DEFAULT_RUNTIME_CONFIG_FILE = "niko/.runtime/config.json"

_FILE_ENV_KEYS: set[str] = set()
_FILE_ENV_VALUES: dict[str, str] = {}
_RUNTIME_CONFIG_CACHE: dict[str, str] = {}
_RUNTIME_CONFIG_MTIME_NS: int | None = None
_RUNTIME_CONFIG_PATH: Path | None = None
_RUNTIME_CONFIG_LOCK = threading.RLock()


def repo_root() -> Path:
    """Repo root là mốc duy nhất để resolve path tương đối."""
    return Path(__file__).resolve().parent.parent


def runtime_config_path() -> Path:
    """File config runtime do Ops dashboard ghi, tách khỏi `.env` và secrets."""
    raw_path = os.environ.get(RUNTIME_CONFIG_ENV, DEFAULT_RUNTIME_CONFIG_FILE).strip()
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return repo_root() / path


def _stringify_runtime_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if value is None:
        return ""
    return str(value)


def _read_runtime_config_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(data, dict) and isinstance(data.get("values"), dict):
        data = data["values"]
    if not isinstance(data, dict):
        return {}
    return {str(key): _stringify_runtime_value(value) for key, value in data.items()}


def read_runtime_config() -> dict[str, str]:
    """Đọc runtime config có cache theo mtime để bot đang chạy nhận thay đổi."""
    global _RUNTIME_CONFIG_CACHE, _RUNTIME_CONFIG_MTIME_NS, _RUNTIME_CONFIG_PATH
    path = runtime_config_path()
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = None

    with _RUNTIME_CONFIG_LOCK:
        if path == _RUNTIME_CONFIG_PATH and mtime_ns == _RUNTIME_CONFIG_MTIME_NS:
            return dict(_RUNTIME_CONFIG_CACHE)

        _RUNTIME_CONFIG_CACHE = _read_runtime_config_file(path)
        _RUNTIME_CONFIG_MTIME_NS = mtime_ns
        _RUNTIME_CONFIG_PATH = path
        return dict(_RUNTIME_CONFIG_CACHE)


def write_runtime_config(values: dict[str, Any]) -> dict[str, str]:
    """Ghi toàn bộ runtime config; caller chịu trách nhiệm validate allowlist."""
    global _RUNTIME_CONFIG_CACHE, _RUNTIME_CONFIG_MTIME_NS, _RUNTIME_CONFIG_PATH
    path = runtime_config_path()
    normalized = {str(key): _stringify_runtime_value(value) for key, value in values.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with _RUNTIME_CONFIG_LOCK:
        _RUNTIME_CONFIG_CACHE = dict(normalized)
        try:
            _RUNTIME_CONFIG_MTIME_NS = path.stat().st_mtime_ns
        except OSError:
            _RUNTIME_CONFIG_MTIME_NS = None
        _RUNTIME_CONFIG_PATH = path
    return dict(normalized)


def update_runtime_config(updates: dict[str, Any]) -> dict[str, str]:
    current = read_runtime_config()
    for key, value in updates.items():
        current[str(key)] = _stringify_runtime_value(value)
    return write_runtime_config(current)


def reset_runtime_config(keys: set[str] | list[str] | tuple[str, ...] | None = None) -> dict[str, str]:
    if keys is None:
        return write_runtime_config({})
    current = read_runtime_config()
    for key in keys:
        current.pop(str(key), None)
    return write_runtime_config(current)


def load_env_file(path: Path = Path(".env"), override_keys: set[str] | None = None) -> set[str]:
    """Nạp một file `.env` đơn giản và trả về những key đã được đọc.

    `override_keys` chỉ cho phép file sau ghi đè những key do file trước nạp.
    Biến đã có sẵn trong OS không bị đụng tới, để máy dev/production có thể
    override secret hoặc cấu hình tạm thời mà không sửa file.
    """
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
            _FILE_ENV_KEYS.add(key)
            _FILE_ENV_VALUES[key] = value
            loaded_keys.add(key)
    return loaded_keys


def load_env_files(bot_name: str | None = None) -> None:
    """Nạp chuỗi env theo đúng thứ tự vận hành của Niko."""
    root = repo_root()
    root_keys = load_env_file(root / ".env")
    niko_keys = load_env_file(root / "niko" / ".env", override_keys=root_keys)
    loaded_file_keys = root_keys | niko_keys
    if bot_name:
        load_env_file(root / "bots" / bot_name / ".env", override_keys=loaded_file_keys)


def _candidate_names(name: str, legacy_name: str | None = None) -> list[str]:
    return [candidate for candidate in (name, legacy_name) if candidate]


def _is_os_env_override(name: str) -> bool:
    if name not in os.environ:
        return False
    return name not in _FILE_ENV_KEYS or os.environ.get(name, "") != _FILE_ENV_VALUES.get(name, "")


def env_value_with_source(name: str, default: str = "", legacy_name: str | None = None) -> tuple[str, str, str]:
    """Trả về value, source và key thực sự dùng để dashboard hiển thị rõ ràng."""
    candidates = _candidate_names(name, legacy_name)
    for candidate in candidates:
        if _is_os_env_override(candidate):
            return os.getenv(candidate, default), "os", candidate

    runtime_config = read_runtime_config()
    for candidate in candidates:
        if candidate in runtime_config:
            return runtime_config[candidate], "runtime", candidate

    for candidate in candidates:
        if candidate in os.environ:
            return os.getenv(candidate, default), "env", candidate

    return default, "default", name


def env_value(name: str, default: str = "", legacy_name: str | None = None) -> str:
    """Đọc env có hỗ trợ runtime config và tên legacy."""
    value, _, _ = env_value_with_source(name, default, legacy_name)
    return value


def env_flag(name: str, default: str = "0", legacy_name: str | None = None) -> bool:
    """Biến boolean trong `.env`: chỉ các giá trị trong TRUE_VALUES mới bật."""
    return env_value(name, default, legacy_name).strip().lower() in TRUE_VALUES


def resolve_project_path(raw_path: str) -> Path:
    """Chuyển path người dùng cấu hình thành path tuyệt đối trong repo."""
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return repo_root() / path
