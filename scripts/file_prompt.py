from pathlib import Path
import subprocess
import sys


INPUT_FILE = Path("examples/input.txt")
OUTPUT_FILE = Path("output.txt")
CLAUDE_COMMAND = ["fcc-claude", "-p"]


def main() -> int:
    if not INPUT_FILE.exists():
        print(f"Khong tim thay file: {INPUT_FILE}", file=sys.stderr)
        return 1

    prompt = INPUT_FILE.read_text(encoding="utf-8").strip()
    if not prompt:
        print(f"File {INPUT_FILE} dang trong.", file=sys.stderr)
        return 1

    try:
        result = subprocess.run(
            [*CLAUDE_COMMAND, prompt],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        print("Khong tim thay lenh fcc-claude. Hay kiem tra PATH.", file=sys.stderr)
        return 1

    if result.returncode != 0:
        print(result.stderr.strip() or result.stdout.strip(), file=sys.stderr)
        return result.returncode

    OUTPUT_FILE.write_text(result.stdout.strip() + "\n", encoding="utf-8")
    print(f"Da ghi cau tra loi vao {OUTPUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
