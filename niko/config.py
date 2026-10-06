"""Cấu hình dùng chung cho toàn harness.

Repo này có nhiều entrypoint chạy độc lập: Telegram bot, ops dashboard,
warmup script cho Nimble, test harness. Thay vì để mỗi entrypoint tự đoán
đường dẫn và tự đọc `.env`, file này gom ba quy tắc nền:

  env files  root `.env` -> `niko/.env` -> `bots/<bot>/.env`
  override   biến môi trường thật của OS luôn thắng file `.env`
  paths      path tương đối luôn được neo vào repo root
"""

from __future__ import annotations

import os
from pathlib import Path


TRUE_VALUES = {"1", "true", "yes", "on"}


def repo_root() -> Path:
    """Repo root là mốc duy nhất để resolve path tương đối."""
    return Path(__file__).resolve().parent.parent


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


def env_value(name: str, default: str = "", legacy_name: str | None = None) -> str:
    """Đọc env có hỗ trợ tên legacy để đổi tên biến mà không phá cấu hình cũ."""
    if name in os.environ:
        return os.getenv(name, default)
    if legacy_name and legacy_name in os.environ:
        return os.getenv(legacy_name, default)
    return default


def env_flag(name: str, default: str = "0", legacy_name: str | None = None) -> bool:
    """Biến boolean trong `.env`: chỉ các giá trị trong TRUE_VALUES mới bật."""
    return env_value(name, default, legacy_name).strip().lower() in TRUE_VALUES


def resolve_project_path(raw_path: str) -> Path:
    """Chuyển path người dùng cấu hình thành path tuyệt đối trong repo."""
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return repo_root() / path
