"""Điều khiển các bot/runtime được bấm từ tab Bots của dashboard.

Module này chỉ quản lý vòng đời process local và health snapshot cho UI. Telegram
gateway vẫn nằm ở `bots.telegram`, còn decision model vẫn nằm ở
`bots.decision_model`; dashboard chỉ gọi start/stop/warmup qua các ranh giới đó.
"""

from __future__ import annotations

from dataclasses import replace
import subprocess
import sys
import threading
import time
from typing import Any

from bots.telegram.instance_guard import read_active_instance
from niko.config import env_flag, env_value, env_value_with_source, repo_root, runtime_subprocess_env
from niko.harness.runtime_log import RuntimeEventLogger, default_runtime_logger
from niko.ops.config_schema import positive_float_config


def bots_snapshot(bot_manager: Any) -> list[dict[str, Any]]:
    """Chuẩn hóa danh sách bot để dashboard dùng được cả manager thật và fake trong test."""
    if hasattr(bot_manager, "bots"):
        return bot_manager.bots()
    status = bot_manager.status()
    return [
        {
            **status,
            "id": status.get("id", "telegram"),
            "name": "Telegram Bot",
            "kind": "process",
            "description": "Long polling gateway nhan tin Telegram va day vao GatewayRunner.",
            "health": "running" if status.get("running") else "stopped",
            "actions": ["start", "stop", "restart"],
        }
    ]


def run_bot_action(bot_manager: Any, bot_id: str, action: str) -> dict[str, Any]:
    """Điều phối action từ URL `/api/bots/{id}/{action}` về đúng method của manager."""
    if bot_id == "telegram":
        if action == "start":
            return bot_manager.start()
        if action == "stop":
            return bot_manager.stop()
        if action == "restart" and hasattr(bot_manager, "restart"):
            return bot_manager.restart()
    if bot_id == "decision":
        if action == "warmup" and hasattr(bot_manager, "warmup_decision"):
            return bot_manager.warmup_decision()
        if action == "stop" and hasattr(bot_manager, "stop_decision"):
            return bot_manager.stop_decision()
    raise ValueError(f"Không hỗ trợ action {action!r} cho bot {bot_id!r}.")


class TelegramBotProcessManager:
    """Quản lý các runtime bot/action do dashboard start.

    Manager này giữ state tối thiểu trong tiến trình dashboard: process Telegram
    do dashboard mở, trạng thái stop bất đồng bộ, và health của lệnh warmup/stop
    decision model. Nó không thay thế single-instance guard của Telegram bot.
    """

    def __init__(self, runtime_logger: RuntimeEventLogger | None = None) -> None:
        self._lock = threading.RLock()
        # `_process` chỉ là process do dashboard start; bot chạy từ terminal được phát hiện qua instance guard.
        self._process: subprocess.Popen | None = None
        self._stopping = False
        self._stop_thread: threading.Thread | None = None
        self._runtime_logger = runtime_logger or default_runtime_logger()
        # Decision model không có process riêng ở đây; Ollama giữ model, dashboard chỉ gửi request warmup/unload.
        self._decision_warmup: threading.Thread | None = None
        self._decision_generation = 0
        self._decision_health: dict[str, Any] = {
            "state": "unknown",
            "message": "Chua warmup trong phien dashboard nay.",
        }

    def _log(self, event: str, message: str, *, level: str = "info", data: dict[str, Any] | None = None) -> None:
        """Ghi log vận hành của dashboard vào runtime log chung."""
        self._runtime_logger.event("dashboard", event, message, level=level, data=data or {})

    def status(self) -> dict[str, Any]:
        """Trả trạng thái Telegram bot, gồm cả process chạy ngoài dashboard."""
        with self._lock:
            process = self._process
            stopping = self._stopping
            if process is None:
                # Nếu anh chạy bot bằng terminal, dashboard vẫn phải nhìn thấy để tránh start trùng token.
                active = read_active_instance(cleanup_stale=True)
                if active is not None:
                    return {
                        "id": "telegram",
                        "managed": False,
                        "external": True,
                        "running": True,
                        "stopping": False,
                        "pid": active.pid,
                        "returncode": None,
                        "command": active.command,
                    }
                return {
                    "id": "telegram",
                    "managed": False,
                    "external": False,
                    "running": False,
                    "stopping": stopping,
                    "pid": None,
                    "returncode": None,
                }
            running = process.poll() is None
            active = read_active_instance(cleanup_stale=True)
            external = active is not None and active.pid != process.pid
            if external and not running and not stopping:
                # Process do dashboard mở đã chết, nhưng lock cho thấy có instance khác đang chạy.
                return {
                    "id": "telegram",
                    "managed": False,
                    "external": True,
                    "running": True,
                    "stopping": False,
                    "pid": active.pid,
                    "returncode": None,
                    "command": active.command,
                }
            return {
                "id": "telegram",
                "managed": True,
                "external": external,
                "running": running or stopping or external,
                "stopping": stopping,
                "pid": process.pid,
                "returncode": process.poll(),
                "active_pid": active.pid if active is not None else None,
                "command": active.command if active is not None else "",
            }

    def start(self) -> dict[str, Any]:
        """Start Telegram long-polling bot bằng Python hiện tại của dashboard."""
        with self._lock:
            if self._stopping:
                return self.status()
            active = read_active_instance(cleanup_stale=True)
            if active is not None and (self._process is None or active.pid != self._process.pid):
                # Telegram getUpdates chỉ cho một poller/token, nên phải chặn start khi có instance ngoài.
                self._log(
                    "telegram_bot_start_blocked",
                    f"Khong start Telegram bot vi dang co instance khac pid={active.pid}.",
                    level="warning",
                    data=active.to_dict(),
                )
                return self.status()
            if self._process is not None and self._process.poll() is None:
                return self.status()
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            # runtime_subprocess_env đưa dashboard config vào child process mà không cần ghi .env mới.
            self._process = subprocess.Popen(
                [sys.executable, "-m", "bots.telegram.bot"],
                cwd=str(repo_root()),
                env=runtime_subprocess_env(),
                creationflags=creationflags,
            )
            self._log(
                "telegram_bot_start_requested",
                "Dashboard da start Telegram bot.",
                data={"pid": self._process.pid},
            )
            return self.status()

    def stop(self) -> dict[str, Any]:
        """Yêu cầu dừng Telegram bot và trả về ngay để UI hiển thị trạng thái stopping."""
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                return self.status()
            if self._stopping:
                return self.status()
            self._stopping = True
            # Stop chạy nền vì long polling có thể mất vài giây để thoát sạch.
            self._stop_thread = threading.Thread(target=self._stop_process_worker, args=(process,), daemon=True)
            self._stop_thread.start()
            self._log(
                "telegram_bot_stop_requested",
                "Dashboard dang dung Telegram bot.",
                data={"pid": process.pid},
            )
            return self.status()

    def _stop_process_worker(self, process: subprocess.Popen) -> None:
        """Dừng process theo thứ tự terminate -> kill và chờ instance guard được dọn."""
        returncode = process.poll()
        if returncode is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            returncode = process.poll()
        deadline = time.monotonic() + 2
        # Đợi lock file biến mất để nút Start không mở lại quá sớm rồi gặp conflict.
        while read_active_instance(cleanup_stale=True) is not None and time.monotonic() < deadline:
            time.sleep(0.1)
        with self._lock:
            self._stopping = False
        self._log(
            "telegram_bot_stop_finished",
            "Dashboard da dung Telegram bot.",
            data={"pid": process.pid, "returncode": returncode},
        )

    def restart(self) -> dict[str, Any]:
        """Restart an toàn: nếu stop còn đang chạy thì UI giữ trạng thái stopping."""
        status = self.stop()
        if status.get("stopping"):
            return status
        return self.start()

    def telegram_bot(self) -> dict[str, Any]:
        """Định dạng card Telegram Bot cho tab Bots."""
        status = self.status()
        token_value, token_source, _ = env_value_with_source("TELEGRAM_BOT_TOKEN", "")
        status.update(
            {
                "name": "Telegram Bot",
                "description": "Long polling gateway nhan tin Telegram va day vao GatewayRunner.",
                "kind": "process",
                "health": "stopping" if status.get("stopping") else "running" if status["running"] else "stopped",
                "token_set": bool(token_value.strip()),
                "token_source": token_source,
                "group_mode": env_value("TELEGRAM_GROUP_MODE", "mentions"),
                "actions": ["start", "stop", "restart"],
            }
        )
        if status.get("external"):
            status["health"] = "external"
            status["message"] = (
                f"Telegram bot dang chay ngoai dashboard voi pid={status.get('pid')}. "
                "Hay dung process do truoc khi start bang dashboard."
            )
        elif not status["token_set"]:
            status["health"] = "config_error"
            status["message"] = "Chua co TELEGRAM_BOT_TOKEN trong dashboard config."
        elif status.get("stopping"):
            status["message"] = "Dang dung Telegram bot; dashboard se mo lai Start khi process thoat han."
        elif status["managed"] and not status["running"] and status["returncode"] not in {None, 0}:
            status["health"] = "error"
            status["message"] = f"Process da thoat voi code {status['returncode']}."
        return status

    def decision_bot(self) -> dict[str, Any]:
        """Định dạng card Decision Model dựa trên config và health gần nhất."""
        enabled = env_flag("NIKO_DECISION_MODEL_ENABLED", "0")
        model = env_value("NIKO_DECISION_MODEL_NAME", "nimble").strip() or "nimble"
        base_url = env_value("NIKO_DECISION_MODEL_BASE_URL", "http://localhost:11434").strip()
        keep_alive = env_value("NIKO_DECISION_MODEL_KEEP_ALIVE", "-1").strip()
        warmup_timeout = positive_float_config("NIKO_DECISION_MODEL_WARMUP_TIMEOUT_SECONDS", 90.0)
        stop_timeout = positive_float_config("NIKO_DECISION_MODEL_STOP_TIMEOUT_SECONDS", 15.0)
        with self._lock:
            health = dict(self._decision_health)
            warming = self._decision_warmup is not None and self._decision_warmup.is_alive()
        if warming and health.get("state") != "stopping":
            # Thread warmup là nguồn sự thật cho trạng thái đang tải model trong phiên dashboard này.
            health["state"] = "warming"
            health.setdefault("message", f"Dang warmup model {model}.")
        if health.get("state") == "unknown":
            health["state"] = "enabled" if enabled else "disabled"
            health["message"] = "Decision model dang bat, chua warmup." if enabled else "Decision model dang tat."
        running = health.get("state") in {"warming", "ready", "stopping"}
        return {
            "id": "decision",
            "name": "Decision Model",
            "description": "Ollama/Nimble local cho triage reply_now/send_to_deep va sticker mood.",
            "kind": "ollama",
            "managed": False,
            "enabled": enabled,
            "running": running,
            "pid": None,
            "returncode": None,
            "health": health.get("state", "unknown"),
            "message": health.get("message", ""),
            "model": model,
            "base_url": base_url,
            "keep_alive": keep_alive,
            "warmup_timeout": warmup_timeout,
            "stop_timeout": stop_timeout,
            "actions": ["warmup", "stop"],
        }

    def bots(self) -> list[dict[str, Any]]:
        """Danh sách bot hiện tại mà frontend render trong tab Bots."""
        return [self.telegram_bot(), self.decision_bot()]

    def warmup_decision(self) -> dict[str, Any]:
        """Gửi một request triage nhẹ để Ollama giữ model decision trong RAM/VRAM."""
        if not env_flag("NIKO_DECISION_MODEL_ENABLED", "0"):
            with self._lock:
                self._decision_health = {
                    "state": "disabled",
                    "message": "Decision model dang tat; bat NIKO_DECISION_MODEL_ENABLED truoc khi warmup.",
                }
                message = self._decision_health["message"]
            self._log(
                "decision_model_warmup_skipped",
                message,
                level="warning",
            )
            return self.decision_bot()

        timeout_seconds = positive_float_config("NIKO_DECISION_MODEL_WARMUP_TIMEOUT_SECONDS", 90.0)
        model = env_value("NIKO_DECISION_MODEL_NAME", "nimble").strip() or "nimble"
        with self._lock:
            if self._decision_warmup is not None and self._decision_warmup.is_alive():
                return self.decision_bot()
            self._decision_generation += 1
            generation = self._decision_generation
            # generation giúp bỏ kết quả warmup cũ nếu người dùng bấm Stop trong lúc thread còn chạy.
            self._decision_health = {
                "state": "warming",
                "message": f"Dang warmup model {model}; lan dau co the mat toi {timeout_seconds:.0f}s.",
                "model": model,
                "timeout_seconds": timeout_seconds,
            }
            self._decision_warmup = threading.Thread(
                target=self._warmup_decision_worker,
                args=(generation, timeout_seconds),
                daemon=True,
            )
            self._decision_warmup.start()
            message = self._decision_health["message"]
        self._log(
            "decision_model_warmup_started",
            message,
            data={"model": model, "timeout_seconds": timeout_seconds},
        )
        return self.decision_bot()

    def _warmup_decision_worker(self, generation: int, timeout_seconds: float) -> None:
        """Worker warmup chạy nền để request HTTP tới Ollama không khóa dashboard."""
        try:
            from bots.decision_model.client import load_decision_model_config
            from bots.decision_model.triage import decide_fast_route

            config = replace(load_decision_model_config(), timeout_seconds=timeout_seconds)
            decision = decide_fast_route("hello", config=config)
            confidence = None if decision.confidence is None else round(decision.confidence, 3)
            next_health = {
                "state": "ready",
                "message": f"Warmup OK: route={decision.route}, model={decision.model or '(unknown)'}.",
                "route": decision.route,
                "model": decision.model,
                "confidence": confidence,
                "timeout_seconds": timeout_seconds,
            }
            event = "decision_model_warmup_finished"
            level = "info"
        except Exception as exc:
            next_health = {
                "state": "error",
                "message": f"Warmup loi: {exc}",
                "error": str(exc),
                "timeout_seconds": timeout_seconds,
            }
            event = "decision_model_warmup_failed"
            level = "error"
        with self._lock:
            if generation != self._decision_generation:
                # Kết quả đã lỗi thời vì có action mới hơn, ví dụ Stop hoặc Warmup khác.
                return
            self._decision_health = next_health
            self._decision_warmup = None
        self._log(event, next_health["message"], level=level, data=next_health)

    def stop_decision(self) -> dict[str, Any]:
        """Yêu cầu Ollama unload model decision bằng keep_alive=0 qua client hiện có."""
        timeout_seconds = positive_float_config("NIKO_DECISION_MODEL_STOP_TIMEOUT_SECONDS", 15.0)
        model = env_value("NIKO_DECISION_MODEL_NAME", "nimble").strip() or "nimble"
        with self._lock:
            self._decision_generation += 1
            # Không kill Ollama; chỉ làm mất hiệu lực warmup đang chạy rồi gửi unload tới model.
            self._decision_warmup = None
            self._decision_health = {
                "state": "stopping",
                "message": f"Dang yeu cau Ollama unload model {model}.",
                "model": model,
                "timeout_seconds": timeout_seconds,
            }
            message = self._decision_health["message"]
        self._log(
            "decision_model_stop_requested",
            message,
            data={"model": model, "timeout_seconds": timeout_seconds},
        )
        try:
            from bots.decision_model.client import load_decision_model_config, unload_decision_model

            config = replace(load_decision_model_config(), timeout_seconds=timeout_seconds)
            response = unload_decision_model(config=config, timeout_seconds=timeout_seconds)
            with self._lock:
                self._decision_health = {
                    "state": "stopped",
                    "message": f"Da yeu cau Ollama unload model {model}.",
                    "model": model,
                    "response": response,
                }
                message = self._decision_health["message"]
            self._log(
                "decision_model_stop_finished",
                message,
                data={"model": model, "response": response},
            )
        except Exception as exc:
            with self._lock:
                self._decision_health = {
                    "state": "error",
                    "message": f"Stop loi: {exc}",
                    "error": str(exc),
                    "model": model,
                }
                message = self._decision_health["message"]
            self._log(
                "decision_model_stop_failed",
                message,
                level="error",
                data={"model": model, "error": str(exc)},
            )
        return self.decision_bot()

