# -*- coding: utf-8 -*-
"""
여운 — SQLite 데이터베이스 (data/yeoun.db)
==========================================
Python 표준 라이브러리 sqlite3만 사용한다. 설치 필요 없음.

테이블
  meta(key, value)                       불러온 엑셀 정보 등
  rooms(id, name, theme, subtitle, match_json, sort)
  artists(id, real_name, display_name, name_public)
  artworks(...)                          작품 · 이미지 · 위치/상태 · 전시실 배정 · 소개문(초안/승인)
  locations(id, grp, place, count, memo) 엑셀 '위치' 시트 (위치 입력 자동 완성용)
  likes(work_id, count)                  관심 저장 수
  comments(id, work_id, text, created_at, hidden)
  events(id, type, work_id, created_at)  관심·한마디 기록 (현황판 '오늘 +n')

DB 내용을 직접 보고 싶으면 'DB Browser for SQLite' 같은 프로그램으로 data/yeoun.db를 열면 된다.
"""
import json
import os
import sqlite3
import threading
import datetime as dt

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "data", "yeoun.db")
_lock = threading.RLock()

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS rooms (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, theme TEXT, subtitle TEXT, match_json TEXT, sort INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS artists (
  id TEXT PRIMARY KEY, real_name TEXT DEFAULT '', display_name TEXT NOT NULL, name_public INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS artworks (
  id TEXT PRIMARY KEY,
  no INTEGER, source_key TEXT, origin TEXT DEFAULT 'excel',
  artist_id TEXT REFERENCES artists(id),
  title TEXT NOT NULL, year TEXT, received TEXT, theme TEXT, material TEXT, frame_note TEXT, size TEXT,
  submitted TEXT, note TEXT, excel_location TEXT, excel_row INTEGER,
  image_json TEXT,
  status TEXT, location TEXT, location_source TEXT DEFAULT 'excel', location_updated_at TEXT,
  room INTEGER REFERENCES rooms(id), sort INTEGER DEFAULT 0, online INTEGER DEFAULT 1, is_example INTEGER DEFAULT 0,
  intro_source TEXT, intro_draft TEXT, intro_approved TEXT, intro_status TEXT DEFAULT 'none',
  intro_name_replaced INTEGER DEFAULT 0, intro_updated_at TEXT, intro_approved_at TEXT, intro_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_artworks_room ON artworks(room, sort);
CREATE UNIQUE INDEX IF NOT EXISTS idx_artworks_key ON artworks(source_key) WHERE source_key <> '';
CREATE TABLE IF NOT EXISTS locations (id INTEGER PRIMARY KEY AUTOINCREMENT, grp TEXT, place TEXT, count TEXT, memo TEXT);
CREATE TABLE IF NOT EXISTS likes (work_id TEXT PRIMARY KEY REFERENCES artworks(id), count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, work_id TEXT REFERENCES artworks(id), text TEXT NOT NULL,
  created_at TEXT NOT NULL, hidden INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, work_id TEXT, created_at TEXT NOT NULL
);
"""

WORK_COLS = ["id", "no", "source_key", "origin", "artist_id", "title", "year", "received", "theme", "material",
             "frame_note", "size", "submitted", "note", "excel_location", "excel_row", "image_json", "status",
             "location", "location_source", "location_updated_at", "room", "sort", "online", "is_example",
             "intro_source", "intro_draft", "intro_approved", "intro_status", "intro_name_replaced",
             "intro_updated_at", "intro_approved_at", "intro_note"]


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


def connect(path=DB_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    return con


_con = None


def con():
    global _con
    if _con is None:
        _con = connect()
    return _con


def exists():
    return os.path.exists(DB_PATH)


# ----------------------------------------------------------------------------
# 작품 dict ↔ 행  (서버·관리자 화면은 예전 JSON과 같은 모양의 dict를 쓴다)
# ----------------------------------------------------------------------------
def row_to_work(r):
    d = dict(r)
    return {
        "id": d["id"], "no": d["no"], "source_key": d["source_key"] or "", "origin": d["origin"],
        "artist_id": d["artist_id"], "title": d["title"], "year": d["year"] or "", "received": d["received"] or "",
        "theme": d["theme"] or "", "material": d["material"] or "", "frame_note": d["frame_note"] or "",
        "size": d["size"] or "", "submitted": d["submitted"] or "", "note": d["note"] or "",
        "excel_location": d["excel_location"] or "", "excel_row": d["excel_row"],
        "image": json.loads(d["image_json"]) if d["image_json"] else None,
        "status": d["status"] or "확인 필요", "location": d["location"] or "",
        "location_source": d["location_source"] or "excel", "location_updated_at": d["location_updated_at"] or "",
        "room": d["room"], "order": d["sort"] or 0, "online": bool(d["online"]), "is_example": bool(d["is_example"]),
        "intro": {
            "source_text": d["intro_source"] or "", "draft": d["intro_draft"] or "", "approved": d["intro_approved"] or "",
            "status": d["intro_status"] or "none", "name_replaced": bool(d["intro_name_replaced"]),
            "updated_at": d["intro_updated_at"] or "", "approved_at": d["intro_approved_at"] or "",
            "reviewer_note": d["intro_note"] or "",
        },
    }


def work_to_row(w):
    it = w.get("intro") or {}
    return {
        "id": w["id"], "no": w.get("no"), "source_key": w.get("source_key") or "", "origin": w.get("origin", "excel"),
        "artist_id": w.get("artist_id"), "title": w.get("title", ""), "year": w.get("year", ""),
        "received": w.get("received", ""), "theme": w.get("theme", ""), "material": w.get("material", ""),
        "frame_note": w.get("frame_note", ""), "size": w.get("size", ""), "submitted": w.get("submitted", ""),
        "note": w.get("note", ""), "excel_location": w.get("excel_location", ""), "excel_row": w.get("excel_row"),
        "image_json": json.dumps(w["image"], ensure_ascii=False) if w.get("image") else None,
        "status": w.get("status", ""), "location": w.get("location", ""),
        "location_source": w.get("location_source", "excel"), "location_updated_at": w.get("location_updated_at", ""),
        "room": w.get("room"), "sort": w.get("order") or 0, "online": int(bool(w.get("online"))),
        "is_example": int(bool(w.get("is_example"))),
        "intro_source": it.get("source_text", ""), "intro_draft": it.get("draft", ""),
        "intro_approved": it.get("approved", ""), "intro_status": it.get("status", "none"),
        "intro_name_replaced": int(bool(it.get("name_replaced"))), "intro_updated_at": it.get("updated_at", ""),
        "intro_approved_at": it.get("approved_at", ""), "intro_note": it.get("reviewer_note", ""),
    }


def _upsert_work(c, w):
    row = work_to_row(w)
    cols = ",".join(WORK_COLS)
    qs = ",".join("?" for _ in WORK_COLS)
    upd = ",".join("%s=excluded.%s" % (k, k) for k in WORK_COLS if k != "id")
    c.execute("INSERT INTO artworks (%s) VALUES (%s) ON CONFLICT(id) DO UPDATE SET %s" % (cols, qs, upd),
              [row[k] for k in WORK_COLS])


# ----------------------------------------------------------------------------
# 읽기
# ----------------------------------------------------------------------------
def load_all():
    """예전 artworks.json과 같은 모양: {meta, rooms, artists, locations, artworks}"""
    with _lock:
        c = con()
        meta = {r["key"]: json.loads(r["value"]) for r in c.execute("SELECT key, value FROM meta")}
        rooms = []
        for r in c.execute("SELECT * FROM rooms ORDER BY sort, id"):
            rooms.append({"id": r["id"], "name": r["name"], "theme": r["theme"] or "", "subtitle": r["subtitle"] or "",
                          "match": json.loads(r["match_json"]) if r["match_json"] else {}})
        artists = {r["id"]: {"real_name": r["real_name"] or "", "display_name": r["display_name"],
                             "name_public": bool(r["name_public"])} for r in c.execute("SELECT * FROM artists")}
        locs = [{"group": r["grp"], "place": r["place"], "count": r["count"], "memo": r["memo"]}
                for r in c.execute("SELECT * FROM locations ORDER BY id")]
        works = [row_to_work(r) for r in c.execute("SELECT * FROM artworks ORDER BY room, sort, id")]
        excel_values = sorted({w["excel_location"] for w in works if w["excel_location"]})
        return {"meta": meta, "rooms": rooms, "artists": artists,
                "locations": {"sheet": locs, "excel_values": excel_values}, "artworks": works}


def get_work(wid):
    with _lock:
        r = con().execute("SELECT * FROM artworks WHERE id=?", (wid,)).fetchone()
        return row_to_work(r) if r else None


def load_reactions():
    with _lock:
        c = con()
        likes = {r["work_id"]: r["count"] for r in c.execute("SELECT * FROM likes")}
        comments = [{"id": "c%05d" % r["id"], "work": r["work_id"], "text": r["text"], "at": r["created_at"],
                     "hidden": bool(r["hidden"])} for r in c.execute("SELECT * FROM comments ORDER BY id")]
        events = [{"type": r["type"], "work": r["work_id"], "at": r["created_at"]}
                  for r in c.execute("SELECT * FROM events ORDER BY id")]
        return {"likes": likes, "comments": comments, "events": events}


# ----------------------------------------------------------------------------
# 쓰기
# ----------------------------------------------------------------------------
def save_work(w):
    with _lock:
        c = con()
        with c:
            _upsert_work(c, w)


def save_artist(aid, a):
    with _lock:
        c = con()
        with c:
            c.execute("INSERT INTO artists (id, real_name, display_name, name_public) VALUES (?,?,?,?) "
                      "ON CONFLICT(id) DO UPDATE SET real_name=excluded.real_name, display_name=excluded.display_name, "
                      "name_public=excluded.name_public",
                      (aid, a.get("real_name", ""), a["display_name"], int(bool(a.get("name_public")))))


def save_rooms(rooms):
    with _lock:
        c = con()
        with c:
            for i, r in enumerate(rooms):
                c.execute("INSERT INTO rooms (id, name, theme, subtitle, match_json, sort) VALUES (?,?,?,?,?,?) "
                          "ON CONFLICT(id) DO UPDATE SET name=excluded.name, theme=excluded.theme, "
                          "subtitle=excluded.subtitle, match_json=excluded.match_json, sort=excluded.sort",
                          (r["id"], r["name"], r.get("theme", ""), r.get("subtitle", ""),
                           json.dumps(r.get("match") or {}, ensure_ascii=False), i))


def change_like(wid, on):
    with _lock:
        c = con()
        with c:
            c.execute("INSERT INTO likes (work_id, count) VALUES (?, 0) ON CONFLICT(work_id) DO NOTHING", (wid,))
            if on:
                c.execute("UPDATE likes SET count = count + 1 WHERE work_id=?", (wid,))
            else:
                c.execute("UPDATE likes SET count = MAX(0, count - 1) WHERE work_id=?", (wid,))
            c.execute("INSERT INTO events (type, work_id, created_at) VALUES (?,?,?)", ("like" if on else "unlike", wid, now_iso()))
            return c.execute("SELECT count FROM likes WHERE work_id=?", (wid,)).fetchone()["count"]


def add_comment(wid, text):
    with _lock:
        c = con()
        with c:
            t = now_iso()
            cur = c.execute("INSERT INTO comments (work_id, text, created_at) VALUES (?,?,?)", (wid, text, t))
            c.execute("INSERT INTO events (type, work_id, created_at) VALUES ('comment',?,?)", (wid, t))
            return "c%05d" % cur.lastrowid


def update_comment(cid, hidden=None, delete=False):
    n = int(str(cid).lstrip("c"))
    with _lock:
        c = con()
        with c:
            if not c.execute("SELECT 1 FROM comments WHERE id=?", (n,)).fetchone():
                return False
            if delete:
                c.execute("DELETE FROM comments WHERE id=?", (n,))
            else:
                c.execute("UPDATE comments SET hidden=? WHERE id=?", (int(bool(hidden)), n))
            return True


def reset_reactions():
    with _lock:
        c = con()
        with c:
            c.execute("DELETE FROM likes")
            c.execute("DELETE FROM comments")
            c.execute("DELETE FROM events")


def likes_of(wid):
    with _lock:
        r = con().execute("SELECT count FROM likes WHERE work_id=?", (wid,)).fetchone()
        return r["count"] if r else 0


def replace_import(data):
    """엑셀 가져오기 결과를 한 번에 반영 (트랜잭션). 반응(관심·한마디)은 건드리지 않는다."""
    with _lock:
        c = con()
        with c:
            c.execute("DELETE FROM meta")
            for k, v in data["meta"].items():
                c.execute("INSERT INTO meta (key, value) VALUES (?,?)", (k, json.dumps(v, ensure_ascii=False)))
            for i, r in enumerate(data["rooms"]):
                c.execute("INSERT INTO rooms (id, name, theme, subtitle, match_json, sort) VALUES (?,?,?,?,?,?) "
                          "ON CONFLICT(id) DO UPDATE SET name=excluded.name, theme=excluded.theme, "
                          "subtitle=excluded.subtitle, match_json=excluded.match_json, sort=excluded.sort",
                          (r["id"], r["name"], r.get("theme", ""), r.get("subtitle", ""),
                           json.dumps(r.get("match") or {}, ensure_ascii=False), i))
            for aid, a in data["artists"].items():
                c.execute("INSERT INTO artists (id, real_name, display_name, name_public) VALUES (?,?,?,?) "
                          "ON CONFLICT(id) DO UPDATE SET real_name=excluded.real_name, display_name=excluded.display_name, "
                          "name_public=excluded.name_public",
                          (aid, a.get("real_name", ""), a["display_name"], int(bool(a.get("name_public")))))
            keep = {w["id"] for w in data["artworks"]}
            for w in data["artworks"]:
                _upsert_work(c, w)
            # 엑셀에서 사라진 작품은 반응 기록이 없을 때만 지움 (있으면 비공개로)
            for r in c.execute("SELECT id FROM artworks").fetchall():
                if r["id"] in keep:
                    continue
                used = c.execute("SELECT 1 FROM likes WHERE work_id=? AND count>0 UNION SELECT 1 FROM comments WHERE work_id=?",
                                 (r["id"], r["id"])).fetchone()
                if used:
                    c.execute("UPDATE artworks SET online=0 WHERE id=?", (r["id"],))
                else:
                    c.execute("DELETE FROM likes WHERE work_id=?", (r["id"],))
                    c.execute("DELETE FROM artworks WHERE id=?", (r["id"],))
            c.execute("DELETE FROM locations")
            for l in data.get("locations", {}).get("sheet", []):
                c.execute("INSERT INTO locations (grp, place, count, memo) VALUES (?,?,?,?)",
                          (l.get("group"), l.get("place"), l.get("count"), l.get("memo")))


def migrate_json(json_path, reactions_path):
    """예전 버전(artworks.json / reactions.json)을 DB로 옮긴다."""
    if not os.path.exists(json_path):
        return False
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    replace_import(data)
    if os.path.exists(reactions_path):
        with open(reactions_path, encoding="utf-8") as f:
            r = json.load(f)
        with _lock:
            c = con()
            with c:
                for wid, n in r.get("likes", {}).items():
                    c.execute("INSERT OR REPLACE INTO likes (work_id, count) VALUES (?,?)", (wid, n))
                for cm in r.get("comments", []):
                    c.execute("INSERT INTO comments (work_id, text, created_at, hidden) VALUES (?,?,?,?)",
                              (cm["work"], cm["text"], cm["at"], int(bool(cm.get("hidden")))))
                for e in r.get("events", []):
                    c.execute("INSERT INTO events (type, work_id, created_at) VALUES (?,?,?)", (e["type"], e["work"], e["at"]))
    return True


def save_all(data):
    """서버 메모리의 작품·작가·전시실 정보를 DB에 한 번에 저장 (관리자 수정 후)."""
    with _lock:
        c = con()
        with c:
            for i, r in enumerate(data["rooms"]):
                c.execute("UPDATE rooms SET name=?, theme=?, subtitle=?, sort=? WHERE id=?",
                          (r["name"], r.get("theme", ""), r.get("subtitle", ""), i, r["id"]))
            for aid, a in data["artists"].items():
                c.execute("INSERT INTO artists (id, real_name, display_name, name_public) VALUES (?,?,?,?) "
                          "ON CONFLICT(id) DO UPDATE SET real_name=excluded.real_name, display_name=excluded.display_name, "
                          "name_public=excluded.name_public",
                          (aid, a.get("real_name", ""), a["display_name"], int(bool(a.get("name_public")))))
            for w in data["artworks"]:
                _upsert_work(c, w)
