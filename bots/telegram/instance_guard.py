"""Single-instance guard cho Telegram long polling bot.

Telegram chỉ cho một consumer `getUpdates` trên cùng bot token. Guard này tạo
lock/PID local để cả dashboard và terminal đều phát hiện bot đang chạy trước khi
đụng Telegram API.
"""

from __future__ import annotations

import atexit
import ctypes
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any

from niko.config import env_value, resolve_project_path


LOCK_FILE_NAME = "telegram_bot.lock"
STILL_ACTIVE = 259


@dataclass(frozen=True)
class TelegramBotInstanceInfo:
    pid: int
    command: str
    lock_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TelegramBotAlreadyRunning(RuntimeError):
    """Raised khi một Telegram bot process khác còn giữ single-instance lock."""

    def __init__(self, info: TelegramBotInstanceInfo) -> None:
        super().__init__(
            f"Telegram bot da dang chay voi pid={info.pid}. "
            "Dung process cu truoc khi start instance moi."
        )
        self.info = info


class TelegramBotInstanceLock:
    """Handle lock của process hiện tại, release khi process thoát bình thường."""

    def __init__(self, info: TelegramBotInstanceInfo) -> None:
        self.info = info
        self._released = False
        atexit.register(self.release)

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        path = Path(self.info.lock_path)
        try:
            current = read_instance_info(path)
        except OSError:
            current = None
        if current is not None and current.pid == self.info.pid:
            try:
                path.unlink()
            except OSError:
                pass


def telegram_bot_lock_path() -> Path:
    state_dir = resolve_project_path(env_value("NIKO_STATE_DIR", "niko/.runtime").strip() or "niko/.runtime")
    return state_dir / LOCK_FILE_NAME


def current_process_info(path: Path | None = None) -> TelegramBotInstanceInfo:
    lock_path = path or telegram_bot_lock_path()
    return TelegramBotInstanceInfo(
        pid=os.getpid(),
        command=" ".join(sys.argv),
        lock_path=str(lock_path),
    )


def read_instance_info(path: Path | None = None) -> TelegramBotInstanceInfo | None:
    lock_path = path or telegram_bot_lock_path()
    if not lock_path.exists():
        return None
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    return TelegramBotInstanceInfo(
        pid=int(data.get("pid", 0)),
        command=str(data.get("command", "")),
        lock_path=str(lock_path),
    )


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def read_active_instance(*, cleanup_stale: bool = True) -> TelegramBotInstanceInfo | None:
    path = telegram_bot_lock_path()
    try:
        info = read_instance_info(path)
    except (OSError, ValueError, json.JSONDecodeError):
        info = None
    if info is None:
        if cleanup_stale:
            cleanup_lock_file(path)
        return None
    if process_is_running(info.pid):
        return info
    if cleanup_stale:
        cleanup_lock_file(path)
    return None


def cleanup_lock_file(path: Path | None = None) -> None:
    lock_path = path or telegram_bot_lock_path()
    try:
        lock_path.unlink()
    except OSError:
        pass


def acquire_telegram_bot_instance() -> TelegramBotInstanceLock:
    path = telegram_bot_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    for _ in range(2):
        active = read_active_instance(cleanup_stale=True)
        if active is not None:
            raise TelegramBotAlreadyRunning(active)

        info = current_process_info(path)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(path, flags)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(info.to_dict(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        return TelegramBotInstanceLock(info)

    active = read_active_instance(cleanup_stale=False)
    if active is not None:
        raise TelegramBotAlreadyRunning(active)
    raise RuntimeError(f"Khong tao duoc Telegram bot lock tai {path}.")

