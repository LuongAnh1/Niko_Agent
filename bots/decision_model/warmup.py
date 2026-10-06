"""Warmup Nimble để giảm cold-start cho Telegram bot.

Chạy script này trước demo hoặc khi bật máy:

    rtk python -m bots.decision_model.warmup

Nếu `NIKO_DECISION_MODEL_KEEP_ALIVE=-1`, request đầu tiên sẽ giữ model loaded
trong Ollama cho tới khi `ollama stop nimble` hoặc restart Ollama.
"""

from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bots.decision_model.triage import decide_fast_route
from niko.config import load_env_files


def main() -> int:
    """Nạp env rồi gửi một prompt nhỏ đủ rõ để kích hoạt model."""
    load_env_files()
    decision = decide_fast_route("hello")
    confidence = "" if decision.confidence is None else f" confidence={decision.confidence:.3f}"
    print(
        "Decision model warmed up: "
        f"provider={decision.provider} model={decision.model or '(unknown)'} "
        f"route={decision.route}{confidence}"
    )
    print("If NIKO_DECISION_MODEL_KEEP_ALIVE=-1, Ollama should keep the model loaded until stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
