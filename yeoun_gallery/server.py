# -*- coding: utf-8 -*-
"""
여운 — 로컬 서버
================
- 웹 화면 제공:  /  (3D 전시관),  /admin  (관리자),  /guide  (작동 가이드)
- 데이터 API:    /api/gallery, /api/react/*, /api/admin/*
- 제스처 연결:   /api/gesture/start · stop · config · stream(SSE)

Python 표준 라이브러리만으로 동작한다. (제스처 모드만 opencv-python, mediapipe 필요)
같은 PC에서 브라우저와 함께 쓰는 로컬 시연용이다. 기본으로 127.0.0.1에만 열린다.

실행:  python server.py
"""
import base64
import copy
import datetime as dt
import json
import mimetypes
import os
import queue
import re
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
DATA = os.path.join(ROOT, "data")
CONFIG_PATH = os.path.join(ROOT, "config.json")
ARTWORKS_PATH = os.path.join(DATA, "artworks.json")     # 예전 버전 (있으면 DB로 옮김)
REACTIONS_PATH = os.path.join(DATA, "reactions.json")
GUIDE_PATH = os.path.join(ROOT, "작동가이드.html")

sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

import db  # noqa: E402  (SQLite: data/yeoun.db)
import gesture  # noqa: E402  (opencv/mediapipe는 '제스처 모드 시작' 때만 불러옴)

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/webp", ".webp")


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


CONFIG = load_json(CONFIG_PATH, {})


def save_config():
    save_json(CONFIG_PATH, CONFIG)


# ----------------------------------------------------------------------------
# 데이터 저장소
# ----------------------------------------------------------------------------
class Store:
    def __init__(self):
        self.lock = threading.RLock()
        self.reload()

    def reload(self):
        with self.lock:
            self.data = db.load_all()
            self.reactions = db.load_reactions()
            self.reactions.setdefault("likes", {})
            self.reactions.setdefault("comments", [])
            self.reactions.setdefault("events", [])

    def save(self):
        db.save_all(self.data)

    def reload_reactions(self):
        self.reactions = db.load_reactions()

    def work(self, wid):
        return next((w for w in self.data["artworks"] if w["id"] == wid), None)

    def artist_display(self, aid):
        a = self.data["artists"].get(aid) or {}
        if a.get("name_public") and a.get("real_name"):
            return a["real_name"]
        return a.get("display_name") or "작가 미상"

    # ---- 공개용 (방문자) ----
    def public(self):
        with self.lock:
            rooms = []
            works = []
            for w in sorted(self.data["artworks"], key=lambda w: (w.get("room") or 0, w.get("order") or 0)):
                if not w.get("online") or not w.get("image"):
                    continue
                intro = w.get("intro") or {}
                img = w["image"]
                ver = img.get("ver", "")
                works.append({
                    "id": w["id"],
                    "title": w["title"],
                    "artist": self.artist_display(w["artist_id"]),
                    "year": w.get("year", ""),
                    "theme": w.get("theme", ""),
                    "material": w.get("material", ""),
                    "size": w.get("size", ""),
                    "room": w.get("room"),
                    "image": {
                        "full": img["full"] + ("?v=%s" % ver if ver else ""),
                        "thumb": img["thumb"] + ("?v=%s" % ver if ver else ""),
                        "w": img.get("w"), "h": img.get("h"),
                    },
                    "intro": intro.get("approved", "") if intro.get("status") == "approved" else "",
                    "status": w.get("status", ""),
                    "location": w.get("location", ""),
                    "location_updated_at": w.get("location_updated_at") or self.data["meta"].get("imported_at", ""),
                    "is_example": bool(w.get("is_example")),
                    "likes": int(self.reactions["likes"].get(w["id"], 0)),
                })
            for r in self.data["rooms"]:
                rooms.append({k: r.get(k) for k in ("id", "name", "theme", "subtitle")})
                rooms[-1]["count"] = sum(1 for x in works if x["room"] == r["id"])
            meta = self.data.get("meta", {})
            return {"rooms": rooms, "works": works,
                    "meta": {"imported_at": meta.get("imported_at"), "total": len(works)}}

    # ---- 관리자용 ----
    def admin(self):
        with self.lock:
            d = copy.deepcopy(self.data)
            d["reactions"] = copy.deepcopy(self.reactions)
            d["config"] = {"comment_max_length": CONFIG.get("comment_max_length", 100),
                           "excel_path": CONFIG.get("excel_path", "")}
            return d


STORE = Store()


# ----------------------------------------------------------------------------
# 제스처 이벤트 허브 (SSE)
# ----------------------------------------------------------------------------
class Hub:
    def __init__(self):
        self.lock = threading.Lock()
        self.subs = []
        self.seq = 0

    def subscribe(self):
        q = queue.Queue(maxsize=200)
        with self.lock:
            self.subs.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)

    def publish(self, event):
        with self.lock:
            self.seq += 1
            ev = dict(event, id=self.seq, ts=int(time.time() * 1000))
            for q in list(self.subs):
                try:
                    q.put_nowait(ev)
                except queue.Full:
                    pass
        return ev


HUB = Hub()
GESTURE = gesture.GestureService(CONFIG.get("gesture", {}), HUB.publish)


# ----------------------------------------------------------------------------
# 이미지 저장 (작품 등록·교체)
# ----------------------------------------------------------------------------
def save_uploaded_image(data_url, wid):
    m = re.match(r"^data:image/(png|jpe?g|webp|gif);base64,(.+)$", data_url or "", re.S)
    if not m:
        raise ValueError("이미지는 PNG·JPG·WEBP 파일만 올릴 수 있어요.")
    raw = base64.b64decode(m.group(2))
    if len(raw) > 20 * 1024 * 1024:
        raise ValueError("이미지가 너무 커요 (20MB 이하).")
    import import_excel
    ext = "." + m.group(1).replace("jpeg", "jpg")
    info = import_excel.save_image(raw, wid, ext)
    info["source"] = "관리자 업로드"
    info["source_sheet"] = "관리자 업로드"
    info["ratio_ok"] = True
    info["ver"] = str(int(time.time()))
    return info


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
STATIC_ROUTES = {
    "/": os.path.join(WEB, "index.html"),
    "/index.html": os.path.join(WEB, "index.html"),
    "/admin": os.path.join(WEB, "admin.html"),
    "/admin.html": os.path.join(WEB, "admin.html"),
    "/guide": GUIDE_PATH,
}


def safe_join(base, rel):
    p = os.path.normpath(os.path.join(base, rel.lstrip("/\\")))
    return p if p.startswith(os.path.normpath(base) + os.sep) else None


class Handler(BaseHTTPRequestHandler):
    server_version = "Yeoun/1.0"

    def log_message(self, fmt, *args):  # 조용히 (오류만 출력)
        if args and str(args[1] if len(args) > 1 else "").startswith(("4", "5")):
            sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    # ---- 응답 도우미 ----
    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, code, msg):
        self.send_json({"ok": False, "error": msg}, code)

    def send_file(self, path):
        if not path or not os.path.isfile(path):
            return self.send_error_json(404, "파일을 찾을 수 없어요: " + self.path)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        is_image = path.startswith(os.path.join(DATA, "images"))
        self.send_header("Cache-Control", "max-age=3600" if is_image else "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 30 * 1024 * 1024:
            raise ValueError("요청이 너무 커요.")
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def is_admin(self):
        pin = str(CONFIG.get("admin_pin", ""))
        return not pin or self.headers.get("X-Admin-Pin", "") == pin

    # ---- GET ----
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(url.path)
        try:
            if path in STATIC_ROUTES:
                return self.send_file(STATIC_ROUTES[path])
            if path.startswith("/images/"):
                return self.send_file(safe_join(os.path.join(DATA, "images"), path[len("/images/"):]))
            if path.startswith(("/css/", "/js/", "/vendor/", "/assets/")):
                return self.send_file(safe_join(WEB, path))
            if path == "/api/gallery":
                return self.send_json(STORE.public())
            if path == "/api/gesture/status":
                return self.send_json(GESTURE.status())
            if path == "/api/gesture/stream":
                return self.stream()
            if path == "/api/gesture/video":
                return self.video()
            if path == "/api/admin/data":
                if not self.is_admin():
                    return self.send_error_json(401, "관리자 PIN이 맞지 않아요.")
                return self.send_json(STORE.admin())
            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            return self.send_error_json(404, "없는 주소예요: " + path)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q = HUB.subscribe()
        try:
            first = dict(type="status", id=HUB.seq, ts=int(time.time() * 1000), **GESTURE.status())
            self.wfile.write(("retry: 1500\nevent: status\ndata: %s\n\n" % json.dumps(first, ensure_ascii=False)).encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                    msg = "id: %d\nevent: %s\ndata: %s\n\n" % (ev["id"], ev.get("type", "message"), json.dumps(ev, ensure_ascii=False))
                except queue.Empty:
                    msg = ": ping\n\n"
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            HUB.unsubscribe(q)

    def video(self):
        """카메라 화면(MJPEG). 이 PC(127.0.0.1)에서 연 브라우저에만 보여 준다. 저장하지 않는다."""
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            return self.send_error_json(403, "카메라 화면은 이 PC에서만 볼 수 있어요.")
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        GESTURE.viewers += 1
        last = -1
        try:
            idle = 0
            while True:
                no, jpg = GESTURE.wait_frame(last, timeout=2.0)
                if jpg is None or no == last:
                    idle += 1
                    if GESTURE.state in ("off", "error") or idle > 15:
                        break
                    continue
                idle = 0
                last = no
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            GESTURE.viewers = max(0, GESTURE.viewers - 1)

    # ---- POST ----
    def do_POST(self):
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        try:
            body = self.read_body()
        except Exception as e:
            return self.send_error_json(400, "요청을 읽을 수 없어요: %s" % e)
        try:
            if path.startswith("/api/gesture/"):
                return self.gesture_api(path, body)
            if path.startswith("/api/react/"):
                return self.react_api(path, body)
            if path.startswith("/api/admin/"):
                if not self.is_admin():
                    return self.send_error_json(401, "관리자 PIN이 맞지 않아요.")
                return self.admin_api(path, body)
            return self.send_error_json(404, "없는 주소예요: " + path)
        except ValueError as e:
            return self.send_error_json(400, str(e))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # 예기치 못한 오류도 화면에 보이게
            import traceback
            traceback.print_exc()
            return self.send_error_json(500, "서버 오류: %s" % e)

    # ---- 제스처 ----
    def gesture_api(self, path, body):
        if path == "/api/gesture/start":
            return self.send_json(GESTURE.start())
        if path == "/api/gesture/stop":
            return self.send_json(GESTURE.stop())
        if path == "/api/gesture/config":
            applied = GESTURE.update_params(body)
            CONFIG.setdefault("gesture", {}).update(applied)
            save_config()
            return self.send_json({"ok": True, "params": GESTURE.public_params()})
        if path == "/api/gesture/test":
            # 웹캠 없이 웹 쪽 연결을 확인하는 테스트용 명령
            cmd = body.get("cmd")
            if cmd not in ("NEXT", "PREV"):
                raise ValueError("cmd는 NEXT 또는 PREV")
            ev = HUB.publish({"type": "command", "cmd": cmd, "test": True})
            return self.send_json({"ok": True, "event": ev})
        return self.send_error_json(404, "없는 주소예요")

    # ---- 관람 반응 ----
    def react_api(self, path, body):
        wid = body.get("id")
        with STORE.lock:
            w = STORE.work(wid)
            if not w:
                raise ValueError("작품을 찾을 수 없어요.")
            if path == "/api/react/like":
                on = bool(body.get("on", True))
                n = db.change_like(wid, on)
                STORE.reload_reactions()
                return self.send_json({"ok": True, "likes": n})
            if path == "/api/react/comment":
                text = re.sub(r"\s+", " ", str(body.get("text", ""))).strip()
                limit = int(CONFIG.get("comment_max_length", 100))
                if not text:
                    raise ValueError("한마디를 입력해 주세요.")
                if len(text) > limit:
                    raise ValueError("한마디는 %d자까지 쓸 수 있어요." % limit)
                cid = db.add_comment(wid, text)
                STORE.reload_reactions()
                return self.send_json({"ok": True, "id": cid})
        return self.send_error_json(404, "없는 주소예요")

    # ---- 관리자 ----
    def admin_api(self, path, body):
        d = STORE.data
        with STORE.lock:
            m = re.match(r"^/api/admin/work/([\w\-]+)$", path)
            if m:
                return self.send_json(self.update_work(m.group(1), body))
            m = re.match(r"^/api/admin/work/([\w\-]+)/intro$", path)
            if m:
                return self.send_json(self.update_intro(m.group(1), body))
            m = re.match(r"^/api/admin/work/([\w\-]+)/image$", path)
            if m:
                w = STORE.work(m.group(1))
                if not w:
                    raise ValueError("작품을 찾을 수 없어요.")
                w["image"] = save_uploaded_image(body.get("image"), w["id"])
                w["online"] = True if body.get("online", True) else w.get("online")
                STORE.save()
                return self.send_json({"ok": True, "image": w["image"]})
            if path == "/api/admin/intro/bulk":
                done = 0
                for wid in body.get("ids", []):
                    try:
                        self.update_intro(wid, {"action": body.get("action", "approve")})
                        done += 1
                    except ValueError:
                        pass
                return self.send_json({"ok": True, "done": done})
            if path == "/api/admin/work":
                return self.send_json(self.create_work(body))
            m = re.match(r"^/api/admin/artist/([\w\-]+)$", path)
            if m:
                a = d["artists"].get(m.group(1))
                if not a:
                    raise ValueError("작가를 찾을 수 없어요.")
                if "display_name" in body:
                    name = str(body["display_name"]).strip()[:30]
                    if not name:
                        raise ValueError("표시명을 비울 수 없어요.")
                    a["display_name"] = name
                if "name_public" in body:
                    a["name_public"] = bool(body["name_public"]) and bool(a.get("real_name"))
                STORE.save()
                return self.send_json({"ok": True, "artist": a})
            if path == "/api/admin/rooms":
                for upd in body.get("rooms", []):
                    r = next((x for x in d["rooms"] if x["id"] == upd.get("id")), None)
                    if r:
                        for k in ("name", "theme", "subtitle"):
                            if k in upd:
                                r[k] = str(upd[k]).strip()[:40]
                STORE.save()
                return self.send_json({"ok": True, "rooms": d["rooms"]})
            if path == "/api/admin/reorder":
                room = int(body.get("room"))
                ids = body.get("ids", [])
                for i, wid in enumerate(ids):
                    w = STORE.work(wid)
                    if w and w.get("room") == room:
                        w["order"] = i
                STORE.save()
                return self.send_json({"ok": True})
            m = re.match(r"^/api/admin/comment/(c\d+)$", path)
            if m:
                if not db.update_comment(m.group(1), hidden=body.get("hidden"), delete=bool(body.get("delete"))):
                    raise ValueError("한마디를 찾을 수 없어요.")
                STORE.reload_reactions()
                return self.send_json({"ok": True})
            if path == "/api/admin/reactions/reset":
                db.reset_reactions()
                STORE.reload_reactions()
                return self.send_json({"ok": True})
            if path == "/api/admin/reimport":
                import import_excel
                excel = CONFIG.get("excel_path", "")
                excel = excel if os.path.isabs(excel) else os.path.normpath(os.path.join(ROOT, excel))
                try:
                    if not os.path.exists(excel):
                        excel = import_excel.default_excel_path()
                    STORE.save()
                    result = import_excel.run(excel, quiet=True)
                except SystemExit as e:   # 엑셀을 못 찾는 등
                    raise ValueError(str(e))
                STORE.reload()
                return self.send_json({"ok": True, "meta": result["meta"]})
        return self.send_error_json(404, "없는 주소예요")

    def update_work(self, wid, body):
        w = STORE.work(wid)
        if not w:
            raise ValueError("작품을 찾을 수 없어요.")
        if "status" in body:
            if body["status"] not in ("보관 중", "설치됨", "대여·외부 전시", "확인 필요"):
                raise ValueError("상태 값이 올바르지 않아요.")
            w["status"] = body["status"]
            w["location_source"] = "admin"
            w["location_updated_at"] = now_iso()
        if "location" in body:
            w["location"] = str(body["location"]).strip()[:60]
            w["location_source"] = "admin"
            w["location_updated_at"] = now_iso()
        if "room" in body:
            room = int(body["room"])
            if not any(r["id"] == room for r in STORE.data["rooms"]):
                raise ValueError("없는 전시실이에요.")
            if room != w.get("room"):
                w["room"] = room
                w["order"] = 1 + max([x.get("order") or 0 for x in STORE.data["artworks"] if x.get("room") == room] or [-1])
        if "order" in body:
            w["order"] = int(body["order"])
        if "online" in body:
            w["online"] = bool(body["online"])
        if "is_example" in body:
            w["is_example"] = bool(body["is_example"])
        # 관리자가 직접 등록한 작품만 기본 정보 수정 가능 (엑셀 작품은 엑셀에서 고친 뒤 다시 불러오기)
        if w.get("origin") == "admin":
            for k in ("title", "theme", "year", "material", "size"):
                if k in body:
                    w[k] = str(body[k]).strip()[:80]
        STORE.save()
        return {"ok": True, "work": w}

    def update_intro(self, wid, body):
        w = STORE.work(wid)
        if not w:
            raise ValueError("작품을 찾을 수 없어요.")
        intro = w.setdefault("intro", {"draft": "", "approved": "", "status": "none", "source_text": ""})
        action = body.get("action", "save")
        if "text" in body:
            intro["draft"] = str(body["text"]).strip()[:2000]
        if "note" in body:
            intro["reviewer_note"] = str(body["note"]).strip()[:300]
        if action == "approve":
            if not intro.get("draft"):
                raise ValueError("승인할 소개문 내용이 없어요.")
            intro["approved"] = intro["draft"]
            intro["status"] = "approved"
            intro["approved_at"] = now_iso()
        elif action == "reject":
            intro["approved"] = ""
            intro["status"] = "rejected"
        elif action == "unpublish":
            intro["approved"] = ""
            intro["status"] = "draft" if intro.get("draft") else "none"
        elif action == "save":
            if intro.get("status") in ("none", "rejected") and intro.get("draft"):
                intro["status"] = "draft"
        intro["updated_at"] = now_iso()
        STORE.save()
        return {"ok": True, "intro": intro}

    def create_work(self, body):
        title = str(body.get("title", "")).strip()[:80]
        artist_name = str(body.get("artist", "")).strip()[:30]
        if not title or not artist_name:
            raise ValueError("작품명과 작가 표시명을 입력해 주세요.")
        d = STORE.data
        n = 1
        while STORE.work("n%03d" % n):
            n += 1
        wid = "n%03d" % n
        aid = next((k for k, a in d["artists"].items() if a.get("display_name") == artist_name), None)
        if not aid:
            k = 1
            while "m%02d" % k in d["artists"]:
                k += 1
            aid = "m%02d" % k
            d["artists"][aid] = {"real_name": "", "display_name": artist_name, "name_public": False}
        image = save_uploaded_image(body.get("image"), wid)
        room = int(body.get("room") or d["rooms"][0]["id"])
        desc = str(body.get("desc", "")).strip()[:2000]
        w = {
            "id": wid, "no": None, "source_key": "", "origin": "admin", "artist_id": aid,
            "title": title, "year": str(body.get("year", "")).strip()[:10], "received": "",
            "theme": str(body.get("theme", "")).strip()[:20], "material": str(body.get("material", "")).strip()[:80],
            "frame_note": "", "size": str(body.get("size", "")).strip()[:40], "submitted": "", "note": "",
            "excel_location": "", "excel_row": None, "image": image,
            "intro": {"draft": desc, "approved": "", "status": "draft" if desc else "none", "source_text": desc,
                      "name_replaced": False, "updated_at": now_iso(), "reviewer_note": ""},
            "status": body.get("status") or "보관 중", "location": str(body.get("location", "")).strip()[:60],
            "location_source": "admin", "location_updated_at": now_iso(),
            "room": room,
            "order": 1 + max([x.get("order") or 0 for x in d["artworks"] if x.get("room") == room] or [-1]),
            "online": True, "is_example": bool(body.get("is_example", True)),
        }
        d["artworks"].append(w)
        STORE.save()
        return {"ok": True, "work": w}


def main():
    host = CONFIG.get("host", "127.0.0.1")
    port = int(CONFIG.get("port", 8000))
    if not db.exists() or not STORE.data["artworks"]:
        if db.migrate_json(ARTWORKS_PATH, REACTIONS_PATH):
            print("예전 JSON 데이터를 DB(data/yeoun.db)로 옮겼어요.")
        else:
            print("DB가 비어 있어 엑셀을 먼저 불러옵니다…")
            import import_excel
            import_excel.run(import_excel.default_excel_path())
        STORE.reload()
    httpd = None
    for p in range(port, port + 10):
        try:
            httpd = ThreadingHTTPServer((host, p), Handler)
            port = p
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit("사용할 수 있는 포트를 찾지 못했어요 (%d~%d)." % (port, port + 9))
    httpd.daemon_threads = True
    url = "http://%s:%d/" % ("127.0.0.1" if host in ("0.0.0.0", "") else host, port)
    print("=" * 56)
    print(" 여운 — 손짓으로 만나는 3D 전시관")
    print(" 전시관   : " + url)
    print(" 관리자   : " + url + "admin   (PIN: config.json의 admin_pin)")
    print(" 작동 가이드: " + url + "guide")
    print(" 끝내려면 이 창에서 Ctrl+C")
    print("=" * 56)
    if CONFIG.get("open_browser", True) and "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        GESTURE.stop()
        httpd.server_close()
        print("서버를 종료했어요. 카메라도 해제했어요.")


if __name__ == "__main__":
    main()
