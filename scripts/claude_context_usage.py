import argparse
import json
import os
from pathlib import Path


DEFAULT_CONTEXT_WINDOW_TOKENS = 1_000_000
DEFAULT_CHARS_PER_TOKEN = 4.0


def load_env_file(path: Path = Path(".env")) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


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


def context_window_tokens() -> int:
    raw_value = os.getenv("CLAUDE_CONTEXT_WINDOW_TOKENS", "").strip().lower()
    if not raw_value or raw_value == "auto":
        return DEFAULT_CONTEXT_WINDOW_TOKENS

    return int(raw_value.replace("_", ""))


def build_usage() -> dict:
    projects_dir = claude_projects_dir()
    session_file = latest_session_file(projects_dir)
    window_tokens = context_window_tokens()

    if session_file is None:
        return {
            "has_session": False,
            "used_percent": 100.0,
            "estimated_tokens": 0,
            "context_window_tokens": window_tokens,
            "projects_dir": str(projects_dir),
            "note": "No Claude JSONL session found; starting a new session is safest.",
        }

    estimated_tokens = estimate_session_tokens(session_file)
    used_percent = min(100.0, estimated_tokens / window_tokens * 100)
    return {
        "has_session": True,
        "used_percent": used_percent,
        "estimated_tokens": estimated_tokens,
        "context_window_tokens": window_tokens,
        "session_file": str(session_file),
        "note": "Estimated from saved Claude transcript text, not exact provider token accounting.",
    }


def print_text(usage: dict) -> None:
    print(f"has_session: {str(usage['has_session']).lower()}")
    print(f"used_percent: {usage['used_percent']:.2f}%")
    print(f"estimated_tokens: {usage['estimated_tokens']:,}")
    print(f"context_window_tokens: {usage['context_window_tokens']:,}")
    if usage.get("session_file"):
        print(f"session_file: {usage['session_file']}")
    else:
        print(f"projects_dir: {usage['projects_dir']}")
    print(f"note: {usage['note']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Estimate the latest Claude Code session context usage.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    load_env_file()
    usage = build_usage()
    if args.json:
        print(json.dumps(usage, ensure_ascii=False))
    else:
        print_text(usage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
