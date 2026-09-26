from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading
import unicodedata
from typing import Any

from niko.config import env_value, resolve_project_path


DEFAULT_NIKO_STATE_DIR = "niko/.runtime"
DEFAULT_MEMORY_DB_NAME = "niko_memory.sqlite3"
SEARCH_STOPWORDS = {
    "anh",
    "chi",
    "em",
    "co",
    "cua",
    "dang",
    "de",
    "fact",
    "facts",
    "gi",
    "hom",
    "khong",
    "la",
    "luu",
    "memory",
    "mot",
    "nao",
    "nay",
    "nay",
    "nhe",
    "noi",
    "semantic",
    "su",
    "that",
    "thi",
    "thong",
    "tin",
    "toi",
    "trong",
    "ve",
    "voi",
    "vua",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_state_dir() -> Path:
    raw_path = env_value("NIKO_STATE_DIR", DEFAULT_NIKO_STATE_DIR)
    path = resolve_project_path(raw_path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_memory_db_path() -> Path:
    return default_state_dir() / DEFAULT_MEMORY_DB_NAME


def _json_dumps(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _json_loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _words(query: str, limit: int = 12) -> list[str]:
    normalized_query = _normalize_for_search(query)
    words = re.findall(r"[\w]+", normalized_query, flags=re.UNICODE)
    seen: set[str] = set()
    unique_words: list[str] = []
    for word in words:
        if len(word) < 2 or word in SEARCH_STOPWORDS or word in seen:
            continue
        seen.add(word)
        unique_words.append(word)
        if len(unique_words) >= limit:
            break
    return unique_words


def _normalize_for_search(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    normalized = "".join(char for char in decomposed if not unicodedata.combining(char))
    return normalized.replace("đ", "d")


def _fts_query(query: str) -> str:
    return " OR ".join(f'"{word}"' for word in _words(query))


def _like_patterns(query: str) -> list[str]:
    return [f"%{word}%" for word in _words(query, limit=8)]


@dataclass(frozen=True)
class Fact:
    id: int
    subject: str
    content: str
    source: str
    created_at: str
    meta: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "content": self.content,
            "source": self.source,
            "created_at": self.created_at,
            "meta": self.meta,
        }


@dataclass(frozen=True)
class Episode:
    id: int
    summary: str
    happened_at: str
    source: str
    created_at: str
    meta: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "summary": self.summary,
            "happened_at": self.happened_at,
            "source": self.source,
            "created_at": self.created_at,
            "meta": self.meta,
        }


class MemoryStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else default_memory_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.ensure_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def connection(self):
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        with self._lock, self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS chat_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT '',
                    meta_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_chat_log_session_created
                    ON chat_log(session_id, created_at);

                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'manual',
                    meta_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    summary TEXT NOT NULL,
                    happened_at TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'niko',
                    meta_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                """
            )
            self._ensure_fts(conn)

    def _ensure_fts(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(subject, content)")
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(summary)")
        except sqlite3.OperationalError:
            return

        if self._table_count(conn, "facts_fts") == 0:
            for row in conn.execute("SELECT id, subject, content FROM facts"):
                conn.execute(
                    "INSERT OR REPLACE INTO facts_fts(rowid, subject, content) VALUES (?, ?, ?)",
                    (row["id"], row["subject"], row["content"]),
                )
        if self._table_count(conn, "episodes_fts") == 0:
            for row in conn.execute("SELECT id, summary FROM episodes"):
                conn.execute(
                    "INSERT OR REPLACE INTO episodes_fts(rowid, summary) VALUES (?, ?)",
                    (row["id"], row["summary"]),
                )

    def _table_exists(self, conn: sqlite3.Connection, name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'virtual table') AND name = ?",
            (name,),
        ).fetchone()
        return row is not None

    def _table_count(self, conn: sqlite3.Connection, name: str) -> int:
        if not self._table_exists(conn, name):
            return 0
        return int(conn.execute(f"SELECT count(*) AS count FROM {name}").fetchone()["count"])

    def log_chat(
        self,
        session_id: str,
        role: str,
        content: str,
        source: str = "telegram",
        meta: dict[str, Any] | None = None,
    ) -> int:
        content = str(content).strip()
        if not content:
            return 0
        with self._lock, self.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO chat_log(session_id, role, content, source, meta_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, role, content, source, _json_dumps(meta), utc_now()),
            )
            return int(cursor.lastrowid)

    def add_fact(
        self,
        subject: str,
        content: str,
        source: str = "manual",
        meta: dict[str, Any] | None = None,
    ) -> int:
        subject = subject.strip()
        content = content.strip()
        if not subject or not content:
            raise ValueError("subject and content are required")

        with self._lock, self.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO facts(subject, content, source, meta_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (subject, content, source, _json_dumps(meta), utc_now()),
            )
            fact_id = int(cursor.lastrowid)
            self._upsert_fact_fts(conn, fact_id, subject, content)
            return fact_id

    def update_fact(
        self,
        fact_id: int,
        subject: str,
        content: str,
        source: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> bool:
        subject = subject.strip()
        content = content.strip()
        if not subject or not content:
            raise ValueError("subject and content are required")

        with self._lock, self.connection() as conn:
            old = conn.execute("SELECT source, meta_json FROM facts WHERE id = ?", (fact_id,)).fetchone()
            if old is None:
                return False
            conn.execute(
                """
                UPDATE facts
                SET subject = ?, content = ?, source = ?, meta_json = ?
                WHERE id = ?
                """,
                (
                    subject,
                    content,
                    source if source is not None else old["source"],
                    _json_dumps(meta if meta is not None else _json_loads(old["meta_json"])),
                    fact_id,
                ),
            )
            self._upsert_fact_fts(conn, fact_id, subject, content)
            return True

    def delete_fact(self, fact_id: int) -> bool:
        with self._lock, self.connection() as conn:
            cursor = conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            self._delete_fts_row(conn, "facts_fts", fact_id)
            return cursor.rowcount > 0

    def list_facts(self, limit: int = 50) -> list[Fact]:
        with self._lock, self.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM facts
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
            return [self._row_to_fact(row) for row in rows]

    def search_facts(self, query: str, top_k: int = 5) -> list[Fact]:
        if not query.strip():
            return self.list_facts(top_k)

        with self._lock, self.connection() as conn:
            fts = _fts_query(query)
            if fts and self._table_exists(conn, "facts_fts"):
                try:
                    rows = conn.execute(
                        """
                        SELECT facts.*
                        FROM facts_fts
                        JOIN facts ON facts_fts.rowid = facts.id
                        WHERE facts_fts MATCH ?
                        ORDER BY bm25(facts_fts)
                        LIMIT ?
                        """,
                        (fts, max(1, top_k)),
                    ).fetchall()
                    if rows:
                        return [self._row_to_fact(row) for row in rows]
                except sqlite3.OperationalError:
                    pass
            return self._search_facts_like(conn, query, top_k)

    def _search_facts_like(self, conn: sqlite3.Connection, query: str, top_k: int) -> list[Fact]:
        patterns = _like_patterns(query)
        if not patterns:
            return []
        where = " OR ".join(["lower(subject || ' ' || content) LIKE ?"] * len(patterns))
        rows = conn.execute(
            f"""
            SELECT * FROM facts
            WHERE {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            (*patterns, max(1, top_k)),
        ).fetchall()
        if rows:
            return [self._row_to_fact(row) for row in rows]
        return self._search_facts_normalized(conn, query, top_k)

    def _search_facts_normalized(self, conn: sqlite3.Connection, query: str, top_k: int) -> list[Fact]:
        words = _words(query, limit=8)
        if not words:
            return []
        matches: list[tuple[int, sqlite3.Row]] = []
        for row in conn.execute("SELECT * FROM facts ORDER BY id DESC"):
            haystack = _normalize_for_search(f"{row['subject']} {row['content']}")
            score = sum(1 for word in words if word in haystack)
            if score:
                matches.append((score, row))
        matches.sort(key=lambda item: (-item[0], -int(item[1]["id"])))
        return [self._row_to_fact(row) for _, row in matches[: max(1, top_k)]]

    def add_episode(
        self,
        summary: str,
        happened_at: str | None = None,
        source: str = "niko",
        meta: dict[str, Any] | None = None,
    ) -> int:
        summary = summary.strip()
        if not summary:
            raise ValueError("summary is required")
        happened_at = happened_at or utc_now()
        with self._lock, self.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO episodes(summary, happened_at, source, meta_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (summary, happened_at, source, _json_dumps(meta), utc_now()),
            )
            episode_id = int(cursor.lastrowid)
            self._upsert_episode_fts(conn, episode_id, summary)
            return episode_id

    def list_episodes(self, limit: int = 50) -> list[Episode]:
        with self._lock, self.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM episodes
                ORDER BY happened_at DESC, id DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
            return [self._row_to_episode(row) for row in rows]

    def recent_episodes(self, limit: int = 5) -> list[Episode]:
        return self.list_episodes(limit)

    def search_episodes(self, query: str, top_k: int = 5) -> list[Episode]:
        if not query.strip():
            return self.recent_episodes(top_k)

        with self._lock, self.connection() as conn:
            fts = _fts_query(query)
            if fts and self._table_exists(conn, "episodes_fts"):
                try:
                    rows = conn.execute(
                        """
                        SELECT episodes.*
                        FROM episodes_fts
                        JOIN episodes ON episodes_fts.rowid = episodes.id
                        WHERE episodes_fts MATCH ?
                        ORDER BY bm25(episodes_fts)
                        LIMIT ?
                        """,
                        (fts, max(1, top_k)),
                    ).fetchall()
                    if rows:
                        return [self._row_to_episode(row) for row in rows]
                except sqlite3.OperationalError:
                    pass
            return self._search_episodes_like(conn, query, top_k)

    def _search_episodes_like(self, conn: sqlite3.Connection, query: str, top_k: int) -> list[Episode]:
        patterns = _like_patterns(query)
        if not patterns:
            return []
        where = " OR ".join(["lower(summary) LIKE ?"] * len(patterns))
        rows = conn.execute(
            f"""
            SELECT * FROM episodes
            WHERE {where}
            ORDER BY happened_at DESC, id DESC
            LIMIT ?
            """,
            (*patterns, max(1, top_k)),
        ).fetchall()
        return [self._row_to_episode(row) for row in rows]

    def chat_history(self, session_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with self._lock, self.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM chat_log
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, max(1, limit)),
            ).fetchall()
            return [self._row_to_chat(row) for row in reversed(rows)]

    def recent_chat(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM chat_log
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
            return [self._row_to_chat(row) for row in rows]

    def snapshot(self, limit: int = 20) -> dict[str, Any]:
        with self._lock, self.connection() as conn:
            fact_count = int(conn.execute("SELECT count(*) AS count FROM facts").fetchone()["count"])
            episode_count = int(conn.execute("SELECT count(*) AS count FROM episodes").fetchone()["count"])
            chat_count = int(conn.execute("SELECT count(*) AS count FROM chat_log").fetchone()["count"])
        return {
            "db_path": str(self.db_path),
            "counts": {
                "facts": fact_count,
                "episodes": episode_count,
                "chat_log": chat_count,
            },
            "facts": [fact.to_dict() for fact in self.list_facts(limit)],
            "episodes": [episode.to_dict() for episode in self.list_episodes(limit)],
            "chat_log": self.recent_chat(limit),
        }

    def _upsert_fact_fts(self, conn: sqlite3.Connection, fact_id: int, subject: str, content: str) -> None:
        if not self._table_exists(conn, "facts_fts"):
            return
        try:
            conn.execute("DELETE FROM facts_fts WHERE rowid = ?", (fact_id,))
            conn.execute(
                "INSERT INTO facts_fts(rowid, subject, content) VALUES (?, ?, ?)",
                (fact_id, subject, content),
            )
        except sqlite3.OperationalError:
            pass

    def _upsert_episode_fts(self, conn: sqlite3.Connection, episode_id: int, summary: str) -> None:
        if not self._table_exists(conn, "episodes_fts"):
            return
        try:
            conn.execute("DELETE FROM episodes_fts WHERE rowid = ?", (episode_id,))
            conn.execute(
                "INSERT INTO episodes_fts(rowid, summary) VALUES (?, ?)",
                (episode_id, summary),
            )
        except sqlite3.OperationalError:
            pass

    def _delete_fts_row(self, conn: sqlite3.Connection, table_name: str, row_id: int) -> None:
        if not self._table_exists(conn, table_name):
            return
        try:
            conn.execute(f"DELETE FROM {table_name} WHERE rowid = ?", (row_id,))
        except sqlite3.OperationalError:
            pass

    def _row_to_fact(self, row: sqlite3.Row) -> Fact:
        return Fact(
            id=int(row["id"]),
            subject=str(row["subject"]),
            content=str(row["content"]),
            source=str(row["source"]),
            created_at=str(row["created_at"]),
            meta=_json_loads(row["meta_json"]),
        )

    def _row_to_episode(self, row: sqlite3.Row) -> Episode:
        return Episode(
            id=int(row["id"]),
            summary=str(row["summary"]),
            happened_at=str(row["happened_at"]),
            source=str(row["source"]),
            created_at=str(row["created_at"]),
            meta=_json_loads(row["meta_json"]),
        )

    def _row_to_chat(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "session_id": str(row["session_id"]),
            "role": str(row["role"]),
            "content": str(row["content"]),
            "source": str(row["source"]),
            "created_at": str(row["created_at"]),
            "meta": _json_loads(row["meta_json"]),
        }


_DEFAULT_MEMORY_STORE: MemoryStore | None = None
_DEFAULT_MEMORY_STORE_LOCK = threading.Lock()


def default_memory_store() -> MemoryStore:
    global _DEFAULT_MEMORY_STORE
    with _DEFAULT_MEMORY_STORE_LOCK:
        path = default_memory_db_path()
        if _DEFAULT_MEMORY_STORE is None or _DEFAULT_MEMORY_STORE.db_path != path:
            _DEFAULT_MEMORY_STORE = MemoryStore(path)
        return _DEFAULT_MEMORY_STORE
