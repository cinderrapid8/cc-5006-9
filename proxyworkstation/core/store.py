"""SQLite-backed project store.

Persistence model:

* ``sources``  - one row per *logical shot*. Identity is the content hash,
  never the filename; ``current_path`` is just the last known location and can
  change after relocation without changing identity.
* ``proxies``  - proxy files bound to a source by content hash, carrying the
  full generation parameters and the persisted FrameMap JSON.
* ``clips``    - timeline entries referencing a logical shot and a *source
  frame interval*. In/out points are stored in source-frame coordinates, so
  switching preview resolution never changes them.
"""

import json
import os
import sqlite3

from .identity import verify_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    content_hash    TEXT PRIMARY KEY,
    shot_id         TEXT NOT NULL UNIQUE,
    current_path    TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    width           INTEGER NOT NULL,
    height          INTEGER NOT NULL,
    fps             REAL NOT NULL,
    frame_count     INTEGER NOT NULL,
    codec           TEXT NOT NULL,
    imported_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS proxies (
    proxy_hash      TEXT PRIMARY KEY,
    content_hash    TEXT NOT NULL REFERENCES sources(content_hash),
    proxy_path      TEXT NOT NULL,
    height          INTEGER NOT NULL,
    crf             INTEGER NOT NULL,
    preset          TEXT NOT NULL,
    fps             REAL NOT NULL,
    frame_count     INTEGER NOT NULL,
    width           INTEGER NOT NULL,
    frame_map_json  TEXT NOT NULL,
    verify_report   TEXT NOT NULL,
    verified_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS clips (
    clip_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    track_order     INTEGER NOT NULL,
    content_hash    TEXT NOT NULL REFERENCES sources(content_hash),
    source_in       INTEGER NOT NULL,
    source_out      INTEGER NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class ProjectStore:
    def __init__(self, db_path):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # -- sources -----------------------------------------------------------

    def add_source(self, identity, shot_id, meta, current_path):
        self.conn.execute(
            """INSERT INTO sources
               (content_hash, shot_id, current_path, size_bytes, width, height,
                fps, frame_count, codec)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(content_hash) DO UPDATE SET
                 shot_id=excluded.shot_id, current_path=excluded.current_path""",
            (
                identity["content_hash"],
                shot_id,
                os.path.abspath(current_path),
                identity["size_bytes"],
                meta["width"],
                meta["height"],
                meta["fps"],
                meta["frame_count"],
                meta["codec"],
            ),
        )
        self.conn.commit()

    def get_source_by_hash(self, content_hash):
        row = self.conn.execute(
            "SELECT * FROM sources WHERE content_hash=?", (content_hash,)
        ).fetchone()
        return dict(row) if row else None

    def get_source_by_shot(self, shot_id):
        row = self.conn.execute(
            "SELECT * FROM sources WHERE shot_id=?", (shot_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_sources(self):
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM sources ORDER BY shot_id"
            ).fetchall()
        ]

    def update_source_path(self, content_hash, new_path):
        self.conn.execute(
            "UPDATE sources SET current_path=? WHERE content_hash=?",
            (os.path.abspath(new_path), content_hash),
        )
        self.conn.commit()

    def source_status(self, source):
        """Return 'online' / 'offline' / 'moved-content' for a stored source."""
        path = source["current_path"]
        if not os.path.isfile(path):
            return "offline"
        ok, _ = verify_hash(path, source["content_hash"])
        return "online" if ok else "moved-content"

    # -- proxies -----------------------------------------------------------

    def add_proxy(self, proxy_identity, content_hash, proxy_path, params, frame_map, report):
        self.conn.execute(
            """INSERT INTO proxies
               (proxy_hash, content_hash, proxy_path, height, crf, preset, fps,
                frame_count, width, frame_map_json, verify_report)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(proxy_hash) DO UPDATE SET
                 proxy_path=excluded.proxy_path, verify_report=excluded.verify_report,
                 verified_at=datetime('now')""",
            (
                proxy_identity["content_hash"],
                content_hash,
                os.path.abspath(proxy_path),
                params["height"],
                params["crf"],
                params["preset"],
                params["fps"],
                params["frame_count"],
                params["width"],
                json.dumps(frame_map.to_dict()),
                json.dumps(report),
            ),
        )
        self.conn.commit()

    def update_proxy_path(self, proxy_hash, new_path):
        self.conn.execute(
            "UPDATE proxies SET proxy_path=? WHERE proxy_hash=?",
            (os.path.abspath(new_path), proxy_hash),
        )
        self.conn.commit()

    def get_proxy_for(self, content_hash):
        row = self.conn.execute(
            "SELECT * FROM proxies WHERE content_hash=? ORDER BY verified_at DESC LIMIT 1",
            (content_hash,),
        ).fetchone()
        return self._proxy_row(row) if row else None

    def list_proxies(self):
        return [self._proxy_row(r) for r in self.conn.execute("SELECT * FROM proxies")]

    def _proxy_row(self, row):
        d = dict(row)
        d["frame_map"] = json.loads(d.pop("frame_map_json"))
        d["verify_report"] = json.loads(d["verify_report"])
        return d

    # -- timeline ----------------------------------------------------------

    def add_clip(self, content_hash, source_in, source_out):
        if source_out <= source_in:
            raise ValueError("clip out point must be greater than in point")
        order = self.conn.execute(
            "SELECT COALESCE(MAX(track_order), -1) + 1 FROM clips"
        ).fetchone()[0]
        cur = self.conn.execute(
            """INSERT INTO clips (track_order, content_hash, source_in, source_out)
               VALUES (?, ?, ?, ?)""",
            (order, content_hash, int(source_in), int(source_out)),
        )
        self.conn.commit()
        return cur.lastrowid

    def remove_clip(self, clip_id):
        self.conn.execute("DELETE FROM clips WHERE clip_id=?", (clip_id,))
        self.conn.commit()

    def list_clips(self):
        rows = self.conn.execute(
            """SELECT c.*, s.shot_id, s.current_path, s.width AS src_width,
                      s.height AS src_height, s.fps AS src_fps,
                      s.frame_count AS src_frame_count, s.content_hash
               FROM clips c JOIN sources s ON s.content_hash = c.content_hash
               ORDER BY c.track_order"""
        ).fetchall()
        return [dict(r) for r in rows]
