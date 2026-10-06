"""Load frontend template cho Niko Ops dashboard.

HTML/CSS/JS được tách khỏi `dashboard.py` để server Python chỉ còn chịu trách
nhiệm API. Module nhỏ này giữ đường dẫn template ở một nơi và cache nội dung để
mỗi request `/` không phải đọc file lại.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def dashboard_html() -> str:
    """Đọc HTML dashboard từ template riêng và cache trong vòng đời process."""
    return (Path(__file__).resolve().parent / "templates" / "dashboard.html").read_text(encoding="utf-8")
