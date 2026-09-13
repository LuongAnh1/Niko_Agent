import json
import os
from pathlib import Path


DEFAULT_CONTEXT_WINDOW_TOKENS = 200_000
DEFAULT_CHARS_PER_TOKEN = 4.0


def claude_projects_dir() -> Path:
    configured = os.getenv("CLAUDE_PROJECTS_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()

    config_dir = os.getenv("CLAUDE_CONFIG_DIR", "").strip()
    if config_dir:
        return Path(config_dir).expanduser() / "projects"

    return Path.home() / ".claude" / "projects"


def latest_session_file(projects_dir: Path) -> Path | None:
    if not projects_dir.exists():
        return None

    sessions = [path for path in projects_dir.rglob("*.jsonl") if path.is_file()]
    if not sessions:
        return None

    return max(sessions, key=lambda path: path.stat().st_mtime)


def extract_text(value) -> str:
    if isinstance(value, str):
        return value

    if isinstance(value, list):
        return "\n".join(extract_text(item) for item in value)

    if isinstance(value, dict):
        pieces = []
        for key in ("content", "text", "summary", "toolUseResult"):
            if key in value:
                pieces.append(extract_text(value[key]))
        return "\n".join(piece for piece in pieces if piece)

    return ""


def estimate_session_tokens(session_file: Path) -> int:
    total_chars = 0
    for line in session_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            total_chars += len(line)
            continue

        total_chars += len(extract_text(event))

    chars_per_token = float(os.getenv("CLAUDE_CONTEXT_CHARS_PER_TOKEN", str(DEFAULT_CHARS_PER_TOKEN)))
    return int(total_chars / chars_per_token)


def main() -> int:
    projects_dir = claude_projects_dir()
    session_file = latest_session_file(projects_dir)
    context_window_tokens = int(os.getenv("CLAUDE_CONTEXT_WINDOW_TOKENS", str(DEFAULT_CONTEXT_WINDOW_TOKENS)))

    if session_file is None:
        print(
            json.dumps(
                {
                    "has_session": False,
                    "used_percent": 100.0,
                    "estimated_tokens": 0,
                    "context_window_tokens": context_window_tokens,
                    "projects_dir": str(projects_dir),
                },
                ensure_ascii=False,
            )
        )
        return 0

    estimated_tokens = estimate_session_tokens(session_file)
    used_percent = min(100.0, estimated_tokens / context_window_tokens * 100)
    print(
        json.dumps(
            {
                "has_session": True,
                "used_percent": used_percent,
                "estimated_tokens": estimated_tokens,
                "context_window_tokens": context_window_tokens,
                "session_file": str(session_file),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
