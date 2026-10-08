# -*- coding: utf-8 -*-
"""
여운 — 손 기울이기(틸트) 인식
=============================
OpenCV로 웹캠을 읽고 MediaPipe Hand Landmarker로 손의 21개 점을 찾은 뒤,
손목(0번) → 가운데 손가락 뿌리(9번) 방향이 세로선에서 얼마나 기울었는지(각도)를 계산해 넘김을 판정한다.

  손을 세운 상태(0°) ─ 오른쪽으로 기울이기(+) → NEXT(다음 작품)
                    └ 왼쪽으로 기울이기(−)   → PREV(이전 작품)
  (각도는 '화면 기준'. mirror=true면 카메라 좌우를 뒤집어 사용자가 보는 방향과 맞춘다)

판정 규칙 (config.json의 "gesture"에서 조절)
  1) 손이 처음 보이면 arm_time 초 동안은 판정하지 않고, 손을 한 번 '세워야'(±neutral_angle 안) 준비 완료
  2) 기울기가 tilt_angle 이상인 상태가 hold_time 초 이어지면 넘김 (작은 흔들림 무시)
  3) 넘긴 뒤에는 손을 다시 세워야 다음 넘김이 가능 → 손을 가운데로 되돌리는 동작은 넘김이 아님
  4) 한 번 넘기면 cooldown 초 동안 추가 넘김을 막는다
  (예전 방식인 좌우 스와이프는 mode="swipe"로 쓸 수 있다)

영상 처리
  - 웹캠 영상은 이 PC 안에서만 쓴다. 저장하지 않는다.
  - 전시관 화면의 '카메라 화면'은 이 PC(127.0.0.1)의 브라우저에만 MJPEG로 보여 준다. 외부로 보내지 않는다.
  - 손 인식 AI(MediaPipe)는 손의 위치만 찾는다. 작품 소개문 생성과는 관계없다.
"""
import math
import os
import sys
import time
import threading
from collections import deque

ROOT = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(ROOT, "models", "hand_landmarker.task")
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"

DEFAULTS = {
    "mode": "tilt",          # "tilt"(손 기울이기) 또는 "swipe"(좌우로 휘두르기)
    "camera_index": 0,
    "mirror": True,          # 셀카처럼 좌우를 뒤집어 '화면 기준' 방향으로 맞춤
    # --- 기울이기 ---
    "tilt_angle": 30.0,      # 이 각도(도) 이상 기울이면 넘김
    "neutral_angle": 12.0,   # 이 각도 안이면 '손을 세운 상태'
    "hold_time": 0.2,        # 기울인 채로 유지해야 하는 시간(초)
    "max_angle": 85.0,       # 이보다 더 누우면(손이 옆/아래를 향함) 무시
    # --- 스와이프 ---
    "min_distance": 0.18,
    "min_speed": 0.8,
    "window": 0.35,
    "max_vertical": 0.7,
    "return_margin": 0.10,
    "return_timeout": 3.0,
    # --- 공통 ---
    "cooldown": 1.0,         # 한 번 넘긴 뒤 대기 시간(초)
    "arm_time": 0.3,         # 손이 처음 보인 뒤 판정을 시작하기까지(초)
    "frame_width": 640,      # 처리용 프레임 폭
    "video": True,           # 전시관 화면에 카메라 화면 보여 주기 (이 PC 안에서만)
    "video_fps": 15,
    "preview": False,        # True면 OpenCV 확인 창도 띄움 (튜닝용)
}

PALM_IDS = (0, 5, 9, 13, 17)
HAND_LINKS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
              (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]


def hand_angle(lm, w, h, mirror=True):
    """손목(0)→가운데 손가락 뿌리(9)의 기울기(도). 세우면 0, 화면 기준 오른쪽으로 기울면 +."""
    dx = (lm[9].x - lm[0].x) * w
    dy = (lm[9].y - lm[0].y) * h
    if mirror:
        dx = -dx
    return math.degrees(math.atan2(dx, -dy))


class TiltDetector:
    """시간에 따른 손 기울기(도)를 받아 NEXT / PREV / None 을 돌려주는 순수 판정기."""

    def __init__(self, params=None):
        self.p = dict(DEFAULTS)
        if params:
            self.p.update({k: v for k, v in params.items() if k in DEFAULTS})
        self.reset()

    def reset(self):
        self.present_since = None
        self.need_neutral = True     # 손을 한 번 세워야 넘김 가능
        self.candidate = None
        self.cand_since = 0.0
        self.cooldown_until = 0.0
        self.last_reason = ""

    def update_params(self, params):
        self.p.update({k: v for k, v in params.items() if k in DEFAULTS})

    def feed(self, t, angle=None):
        p = self.p
        if angle is None:                       # 손이 안 보임
            self.present_since = None
            self.need_neutral = True
            self.candidate = None
            return None
        if self.present_since is None:
            self.present_since = t
        a = abs(angle)
        if a <= p["neutral_angle"]:
            if t - self.present_since >= p["arm_time"]:
                self.need_neutral = False       # 손을 세움 → 준비 완료
            self.candidate = None
            self.last_reason = "neutral"
            return None
        if t - self.present_since < p["arm_time"]:
            self.last_reason = "arming"
            return None
        if self.need_neutral:
            self.last_reason = "need_neutral"
            return None
        if t < self.cooldown_until:
            self.last_reason = "cooldown"
            return None
        if a < p["tilt_angle"] or a > p["max_angle"]:
            self.candidate = None
            self.last_reason = "small" if a < p["tilt_angle"] else "too_far"
            return None
        d = "NEXT" if angle > 0 else "PREV"
        if self.candidate != d:
            self.candidate, self.cand_since = d, t
            self.last_reason = "holding"
            return None
        if t - self.cand_since < p["hold_time"]:
            self.last_reason = "holding"
            return None
        self.cooldown_until = t + p["cooldown"]
        self.need_neutral = True
        self.candidate = None
        self.last_reason = "tilt"
        return d


class SwipeDetector:
    """(이전 방식) 손바닥 x좌표 변화로 좌우 스와이프 판정."""

    def __init__(self, params=None):
        self.p = dict(DEFAULTS)
        if params:
            self.p.update({k: v for k, v in params.items() if k in DEFAULTS})
        self.reset()

    def reset(self):
        self.hist = deque()
        self.present_since = None
        self.cooldown_until = 0.0
        self.pending_return = None
        self.last_reason = ""

    def update_params(self, params):
        self.p.update({k: v for k, v in params.items() if k in DEFAULTS})

    def feed(self, t, x=None, y=None):
        p = self.p
        if x is None:
            self.hist.clear()
            self.present_since = None
            self.pending_return = None
            return None
        if self.present_since is None:
            self.present_since = t
        self.hist.append((t, x, y))
        while self.hist and (t - self.hist[0][0] > p["window"] or self.hist[0][0] < self.present_since + p["arm_time"]):
            self.hist.popleft()
        if self.pending_return and t > self.pending_return["until"]:
            self.pending_return = None
        if t - self.present_since < p["arm_time"] or t < self.cooldown_until or len(self.hist) < 3:
            return None
        t0, x0, y0 = self.hist[0]
        dt = max(t - t0, 1e-3)
        dx, dy = x - x0, y - y0
        if abs(dx) < p["min_distance"]:
            pr = self.pending_return
            if pr and len(self.hist) >= 3:
                xs = [h[1] for h in self.hist]
                if max(xs) - min(xs) < 0.03 and abs(x - pr["home"]) < p["return_margin"]:
                    self.pending_return = None
            return None
        if abs(dx) / dt < p["min_speed"] or abs(dy) > p["max_vertical"] * abs(dx):
            return None
        direction = "NEXT" if dx > 0 else "PREV"
        pr = self.pending_return
        if pr and direction == pr["dir"]:
            beyond = (pr["home"] - x) if direction == "PREV" else (x - pr["home"])
            if beyond < p["return_margin"]:
                self.hist.clear()
                return None
        self.cooldown_until = t + p["cooldown"]
        self.pending_return = {"dir": "PREV" if direction == "NEXT" else "NEXT", "home": x0, "until": t + p["return_timeout"]}
        self.hist.clear()
        return direction


class GestureService:
    """웹캠 스레드를 켜고 끄며, 판정 결과를 emit(dict)으로 서버에 알린다."""

    def __init__(self, params, emit):
        self.params = dict(DEFAULTS)
        self.params.update({k: v for k, v in (params or {}).items() if k in DEFAULTS})
        self.emit = emit
        self.tilt = TiltDetector(self.params)
        self.swipe = SwipeDetector(self.params)
        self.thread = None
        self.stop_flag = threading.Event()
        self.lock = threading.Lock()
        self.state = "off"
        self.message = ""
        self.hand_x = None
        self.angle = None
        self.fps = 0.0
        # 카메라 화면(MJPEG)
        self.frame_cond = threading.Condition()
        self.jpeg = None
        self.frame_no = 0
        self.viewers = 0

    # ---- 상태 ----
    def status(self):
        return {"state": self.state, "message": self.message, "hand_x": self.hand_x, "angle": self.angle,
                "fps": round(self.fps, 1), "params": self.public_params()}

    def public_params(self):
        keys = ("mode", "tilt_angle", "hold_time", "neutral_angle", "min_distance", "min_speed", "cooldown",
                "mirror", "camera_index", "video")
        return {k: self.params[k] for k in keys}

    def _set_state(self, state, message=""):
        if state != self.state or message != self.message:
            self.state, self.message = state, message
            self.emit({"type": "status", **self.status()})

    # ---- 조작 ----
    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return self.status()
            self.stop_flag.clear()
            self.tilt.reset()
            self.swipe.reset()
            self._set_state("starting", "카메라와 손 인식 모델을 준비하고 있어요")
            self.thread = threading.Thread(target=self._run, name="gesture", daemon=True)
            self.thread.start()
        return self.status()

    def stop(self):
        with self.lock:
            self.stop_flag.set()
            t = self.thread
        if t:
            t.join(timeout=3)
        self.hand_x = self.angle = None
        self._set_state("off", "카메라를 껐어요")
        with self.frame_cond:
            self.jpeg = None
            self.frame_cond.notify_all()
        return self.status()

    def update_params(self, params):
        clean = {}
        for k, v in params.items():
            if k not in DEFAULTS:
                continue
            dv = DEFAULTS[k]
            if isinstance(dv, bool):
                clean[k] = bool(v)
            elif isinstance(dv, str):
                if k == "mode" and v in ("tilt", "swipe"):
                    clean[k] = v
            elif isinstance(dv, int):
                clean[k] = int(v)
            else:
                clean[k] = float(v)
        bounds = {"min_distance": (0.05, 0.6), "min_speed": (0.1, 5.0), "cooldown": (0.2, 5.0), "window": (0.1, 1.5),
                  "tilt_angle": (10, 70), "hold_time": (0.0, 1.5), "neutral_angle": (3, 30)}
        for k, (lo, hi) in bounds.items():
            if k in clean:
                clean[k] = min(hi, max(lo, clean[k]))
        if "tilt_angle" in clean or "neutral_angle" in clean:   # 세운 범위는 넘김 각도보다 작아야 함
            ta = clean.get("tilt_angle", self.params["tilt_angle"])
            na = clean.get("neutral_angle", self.params["neutral_angle"])
            if na >= ta - 5:
                clean["neutral_angle"] = max(3, ta - 10)
        self.params.update(clean)
        self.tilt.update_params(clean)
        self.swipe.update_params(clean)
        self.emit({"type": "status", **self.status()})
        return clean

    # ---- 카메라 화면 스트림 ----
    def wait_frame(self, last_no, timeout=2.0):
        with self.frame_cond:
            if self.frame_no == last_no or self.jpeg is None:
                self.frame_cond.wait(timeout)
            return self.frame_no, self.jpeg

    # ---- 카메라 루프 ----
    def _load(self):
        try:
            import cv2  # noqa
        except Exception:
            raise RuntimeError("OpenCV가 설치되어 있지 않아요. 'pip install -r requirements.txt'를 실행해 주세요.")
        try:
            import mediapipe  # noqa
            from mediapipe.tasks import python as mp_python  # noqa
            from mediapipe.tasks.python import vision  # noqa
        except Exception:
            raise RuntimeError("MediaPipe가 설치되어 있지 않아요. Python 3.10~3.12에서 'pip install -r requirements.txt'를 실행해 주세요.")
        if not os.path.exists(MODEL_PATH):
            self._set_state("starting", "손 인식 모델을 내려받는 중이에요 (처음 한 번)")
            try:
                download_model()
            except Exception as e:
                raise RuntimeError("손 인식 모델(models/hand_landmarker.task)이 없어요. 'python setup_assets.py'를 실행해 주세요. (%s)" % e)

    def _open_camera(self, cv2):
        idx = int(self.params["camera_index"])
        tries = [(idx, cv2.CAP_DSHOW), (idx, None)] if sys.platform.startswith("win") else [(idx, None)]
        for i, api in tries:
            cap = cv2.VideoCapture(i, api) if api is not None else cv2.VideoCapture(i)
            if cap is not None and cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                ok, _ = cap.read()
                if ok:
                    return cap
            if cap is not None:
                cap.release()
        return None

    def _draw(self, cv2, frame, lm, angle):
        """카메라 화면에 손 뼈대와 기울기 선을 그린다 (화면 기준으로 좌우 반전)."""
        view = cv2.flip(frame, 1) if self.params["mirror"] else frame.copy()
        h, w = view.shape[:2]
        if lm:
            pts = [(int(((1 - p.x) if self.params["mirror"] else p.x) * w), int(p.y * h)) for p in lm]
            for a, b in HAND_LINKS:
                cv2.line(view, pts[a], pts[b], (255, 255, 255), 2, cv2.LINE_AA)
            for pt in pts:
                cv2.circle(view, pt, 4, (237, 58, 124), -1, cv2.LINE_AA)
            if angle is not None:
                # 손목→가운데 손가락 뿌리 방향 선 (바이올렛), 세로 기준선 (회색)
                x0, y0 = pts[0]
                L = int(0.32 * h)
                cv2.line(view, (x0, y0), (x0, y0 - L), (180, 180, 180), 1, cv2.LINE_AA)
                rad = math.radians(angle)
                cv2.line(view, (x0, y0), (int(x0 + L * math.sin(rad)), int(y0 - L * math.cos(rad))), (237, 58, 124), 4, cv2.LINE_AA)
                txt = "%+d deg" % round(angle)
                cv2.putText(view, txt, (x0 + 12, y0 + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4, cv2.LINE_AA)
                cv2.putText(view, txt, (x0 + 12, y0 + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
        return view

    def _run(self):
        cap = landmarker = None
        preview = bool(self.params.get("preview"))
        try:
            self._load()
            import cv2
            import mediapipe as mp
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision

            with open(MODEL_PATH, "rb") as f:   # 한글 경로 문제를 피하려고 파일 내용을 직접 넘김
                model_bytes = f.read()
            opts = vision.HandLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_buffer=model_bytes),
                running_mode=vision.RunningMode.VIDEO,
                num_hands=1,
                min_hand_detection_confidence=0.5,
                min_hand_presence_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            landmarker = vision.HandLandmarker.create_from_options(opts)

            cap = self._open_camera(cv2)
            if cap is None:
                raise RuntimeError("웹캠을 열 수 없어요. 다른 프로그램이 카메라를 쓰고 있는지, camera_index 값을 확인해 주세요.")

            self._set_state("searching", "손바닥을 화면 쪽으로 펴서 보여 주세요")
            t_start = time.monotonic()
            last_ts = -1
            last_hand_emit = last_video = 0.0
            frames, fps_t = 0, time.monotonic()
            moved_until = 0.0
            while not self.stop_flag.is_set():
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError("카메라 영상이 끊겼어요.")
                fw = int(self.params["frame_width"])
                if frame.shape[1] > fw:
                    scale = fw / frame.shape[1]
                    frame = cv2.resize(frame, (fw, int(frame.shape[0] * scale)))
                h, w = frame.shape[:2]
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                now = time.monotonic()
                ts = max(int((now - t_start) * 1000), last_ts + 1)
                last_ts = ts
                result = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)

                lm = result.hand_landmarks[0] if result.hand_landmarks else None
                x = y = angle = None
                if lm:
                    x = sum(lm[i].x for i in PALM_IDS) / len(PALM_IDS)
                    y = sum(lm[i].y for i in PALM_IDS) / len(PALM_IDS)
                    if self.params["mirror"]:
                        x = 1.0 - x
                    angle = hand_angle(lm, w, h, self.params["mirror"])
                if self.params["mode"] == "swipe":
                    cmd = self.swipe.feed(now, x, y)
                else:
                    cmd = self.tilt.feed(now, angle)
                self.hand_x = None if x is None else round(min(1, max(0, x)), 3)
                self.angle = None if angle is None else round(angle, 1)

                if cmd:
                    moved_until = now + 0.9
                    self._set_state("moved", "다음 작품으로 이동" if cmd == "NEXT" else "이전 작품으로 이동")
                    self.emit({"type": "command", "cmd": cmd})
                elif now > moved_until:
                    if lm is None:
                        self._set_state("searching", "손바닥을 화면 쪽으로 펴서 보여 주세요")
                    elif self.params["mode"] == "tilt" and self.tilt.need_neutral:
                        self._set_state("detected", "손을 똑바로 세우면 준비 완료예요")
                    elif self.params["mode"] == "tilt":
                        self._set_state("detected", "손을 오른쪽으로 기울이면 다음, 왼쪽이면 이전 작품")
                    else:
                        self._set_state("detected", "손을 좌우로 크게 움직여 작품을 넘겨 보세요")

                if now - last_hand_emit > 0.1:   # 손 위치·각도 표시는 초당 10번
                    last_hand_emit = now
                    ready = lm is not None and not (self.params["mode"] == "tilt" and self.tilt.need_neutral)
                    self.emit({"type": "hand", "x": self.hand_x, "angle": self.angle, "ready": ready, "state": self.state})

                want_video = self.params.get("video") and self.viewers > 0
                if (want_video or preview) and now - last_video >= 1.0 / max(1, int(self.params["video_fps"])):
                    last_video = now
                    view = self._draw(cv2, frame, lm, angle)
                    if want_video:
                        ok2, buf = cv2.imencode(".jpg", view, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                        if ok2:
                            with self.frame_cond:
                                self.jpeg = buf.tobytes()     # 메모리에만 (파일 저장 없음)
                                self.frame_no += 1
                                self.frame_cond.notify_all()
                    if preview:
                        cv2.imshow("yeoun gesture (local preview)", view)
                        if cv2.waitKey(1) & 0xFF == 27:
                            preview = False
                            cv2.destroyAllWindows()

                frames += 1
                if now - fps_t >= 1.0:
                    self.fps = frames / (now - fps_t)
                    frames, fps_t = 0, now
        except Exception as e:  # 설치·카메라 문제는 화면에 그대로 안내
            self._set_state("error", str(e))
        finally:
            if cap is not None:
                cap.release()   # 카메라 해제
            if landmarker is not None:
                try:
                    landmarker.close()
                except Exception:
                    pass
            if preview:
                try:
                    import cv2
                    cv2.destroyAllWindows()
                except Exception:
                    pass
            self.hand_x = self.angle = None
            with self.frame_cond:
                self.jpeg = None
                self.frame_cond.notify_all()
            if self.state != "error":
                self._set_state("off", "카메라를 껐어요")


def download_model(dest=MODEL_PATH):
    import urllib.request
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    urllib.request.urlretrieve(MODEL_URL, tmp)
    if os.path.getsize(tmp) < 1_000_000:
        os.remove(tmp)
        raise RuntimeError("모델 파일 크기가 이상해요")
    os.replace(tmp, dest)
    return dest


if __name__ == "__main__":
    # 단독 실행: 카메라 확인 + 콘솔에 NEXT/PREV 출력 (확인 창 표시)
    svc = GestureService({"preview": True}, lambda e: print(e) if e.get("type") == "command" else None)
    svc.start()
    print("손을 세운 뒤 좌우로 기울여 보세요. 끝내려면 Ctrl+C")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        svc.stop()
