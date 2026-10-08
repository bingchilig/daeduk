# -*- coding: utf-8 -*-
"""
처음 한 번 실행: 인터넷 없이도 시연할 수 있게 필요한 파일을 내려받는다.
  - web/vendor/  : three.js r147 (3D 화면)
  - models/      : MediaPipe 손 인식 모델 hand_landmarker.task
이미 있으면 건너뛴다.  실행: python setup_assets.py
"""
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
CDN = "https://cdn.jsdelivr.net/npm/three@0.147.0/"
FILES = [
    (CDN + "build/three.min.js", "web/vendor/three.min.js", 300_000),
    (CDN + "examples/js/controls/OrbitControls.js", "web/vendor/OrbitControls.js", 10_000),
    (CDN + "examples/js/renderers/CSS2DRenderer.js", "web/vendor/CSS2DRenderer.js", 2_000),
    ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task",
     "models/hand_landmarker.task", 1_000_000),
]


def main():
    ok = True
    for url, rel, min_size in FILES:
        dest = os.path.join(ROOT, rel)
        if os.path.exists(dest) and os.path.getsize(dest) >= min_size:
            print("  있음   ", rel)
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            print("  받는 중", rel, "...", end=" ", flush=True)
            tmp = dest + ".part"
            urllib.request.urlretrieve(url, tmp)
            if os.path.getsize(tmp) < min_size:
                raise RuntimeError("파일 크기가 이상해요")
            os.replace(tmp, dest)
            print("완료")
        except Exception as e:
            ok = False
            print("실패:", e)
    if not ok:
        print("\n일부 파일을 받지 못했어요. 인터넷 연결을 확인하거나 작동가이드의 '수동 다운로드'를 따라 주세요.")
        print("(three.js가 없어도 인터넷이 되면 전시관은 CDN으로 열려요)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
