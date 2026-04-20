import json
import logging
import uuid
from datetime import datetime, timezone

import aiosqlite

from server.session.models import SessionRecord

logger = logging.getLogger(__name__)

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    status        TEXT NOT NULL
                  CHECK(status IN ('active', 'completed', 'abandoned')),
    session_type  TEXT NOT NULL DEFAULT 'focus',
    started_at    TEXT NOT NULL,
    completed_at  TEXT,
    abandoned_at  TEXT,
    duration_ms   INTEGER,
    intent_transcript    TEXT,
    completion_transcript TEXT,
    summary       TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    session_id      TEXT,
    timestamp       TEXT NOT NULL,
    payload         TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_VIEW_SQL = """
CREATE VIEW IF NOT EXISTS timeline AS
SELECT
    event_id AS entry_id,
    session_id,
    CASE
        WHEN event_type = 'session.completed'
            THEN 'Completed ' || CAST(
                ROW_NUMBER() OVER (
                    PARTITION BY event_type
                    ORDER BY timestamp ASC
                ) AS TEXT
            ) || CASE
                WHEN ROW_NUMBER() OVER (PARTITION BY event_type ORDER BY timestamp ASC) = 1
                    THEN ' session'
                ELSE ' sessions'
            END
        WHEN event_type = 'session.abandoned'
            THEN 'Session abandoned'
        ELSE event_type
    END AS text,
    timestamp,
    CASE
        WHEN event_type = 'session.completed' THEN 'completed'
        WHEN event_type = 'session.abandoned' THEN 'abandoned'
        ELSE 'other'
    END AS type
FROM events
WHERE event_type IN ('session.completed', 'session.abandoned')
ORDER BY timestamp DESC;
"""


class EventStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.db: aiosqlite.Connection | None = None

    async def initialize(self):
        logger.debug("Opening event store database at %s", self.db_path)
        self.db = await aiosqlite.connect(self.db_path)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.executescript(CREATE_TABLES_SQL)
        # Recreate view to pick up any schema changes
        await self.db.execute("DROP VIEW IF EXISTS timeline")
        await self.db.executescript(CREATE_VIEW_SQL)
        await self.db.commit()

        # Migrate: add summary column if missing (existing DBs)
        cursor = await self.db.execute("PRAGMA table_info(sessions)")
        columns = [row[1] for row in await cursor.fetchall()]
        if "summary" not in columns:
            await self.db.execute("ALTER TABLE sessions ADD COLUMN summary TEXT")
            await self.db.commit()
            logger.debug("Applied event store migration: added sessions.summary")

        logger.info("Event store initialized at %s", self.db_path)

    async def insert_session(self, session: SessionRecord):
        logger.debug("Inserting session %s (%s)", session.session_id, session.status.value)
        await self.db.execute(
            "INSERT INTO sessions (session_id, status, session_type, started_at, intent_transcript) VALUES (?, ?, ?, ?, ?)",
            (
                session.session_id,
                session.status.value,
                session.session_type,
                session.started_at,
                session.intent_transcript,
            ),
        )
        await self.db.commit()
        logger.debug("Inserted session %s", session.session_id)

    async def update_session(self, session: SessionRecord):
        logger.debug(
            "Updating session %s: status=%s duration_ms=%s",
            session.session_id,
            session.status.value,
            session.duration_ms,
        )
        await self.db.execute(
            "UPDATE sessions SET status=?, completed_at=?, abandoned_at=?, duration_ms=?, completion_transcript=? WHERE session_id=?",
            (
                session.status.value,
                session.completed_at,
                session.abandoned_at,
                session.duration_ms,
                session.completion_transcript,
                session.session_id,
            ),
        )
        await self.db.commit()
        logger.debug("Updated session %s", session.session_id)

    async def append_event(
        self, event_type: str, session_id: str | None, payload: dict
    ):
        event_id = str(uuid.uuid4())
        idempotency_key = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        logger.debug(
            "Appending event %s for session=%s payload_keys=%s",
            event_type,
            session_id,
            sorted(payload.keys()),
        )
        try:
            await self.db.execute(
                "INSERT INTO events (event_id, event_type, session_id, timestamp, payload, idempotency_key) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    event_type,
                    session_id,
                    timestamp,
                    json.dumps(payload),
                    idempotency_key,
                ),
            )
            await self.db.commit()
            logger.debug("Appended event %s (%s)", event_id, event_type)
        except aiosqlite.IntegrityError:
            logger.debug("Skipped duplicate event insert for idempotency_key=%s", idempotency_key)

    async def get_timeline(self) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT entry_id, session_id, text, timestamp, type FROM timeline LIMIT 100"
        )
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        result = [dict(zip(columns, row)) for row in rows]
        logger.debug("Loaded timeline entries: %d", len(result))
        return result

    async def get_sessions(self) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT session_id, status, session_type, started_at, completed_at, duration_ms, summary FROM sessions ORDER BY started_at DESC"
        )
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        result = [dict(zip(columns, row)) for row in rows]
        logger.debug("Loaded sessions: %d", len(result))
        return result

    async def get_session(self, session_id: str) -> dict | None:
        cursor = await self.db.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            logger.debug("Session not found: %s", session_id)
            return None
        columns = [desc[0] for desc in cursor.description]
        session = dict(zip(columns, row))
        logger.debug("Loaded session %s", session_id)
        return session

    async def get_recent_summaries(self, limit: int = 3) -> list[dict]:
        """Retrieve the N most recent completed session summaries."""
        cursor = await self.db.execute(
            "SELECT session_id, session_type, started_at, summary "
            "FROM sessions "
            "WHERE status = 'completed' AND summary IS NOT NULL "
            "ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        result = [dict(zip(columns, row)) for row in rows]
        logger.debug("Loaded recent summaries: %d", len(result))
        return result

    async def update_session_summary(self, session_id: str, summary: str):
        logger.debug("Updating session summary for %s (%d chars)", session_id, len(summary))
        await self.db.execute(
            "UPDATE sessions SET summary = ? WHERE session_id = ?",
            (summary, session_id),
        )
        await self.db.commit()
        logger.debug("Updated session summary for %s", session_id)

    async def close(self):
        if self.db:
            logger.debug("Closing event store database at %s", self.db_path)
            await self.db.close()
