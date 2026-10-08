@echo off
chcp 65001 > nul
cd /d "%~dp0"
title 여운 3D 전시관 서버

where python > nul 2>&1
if errorlevel 1 (
  echo [!] Python이 없어요. https://www.python.org 에서 Python 3.11을 설치하고
  echo     설치 화면에서 "Add python.exe to PATH"를 꼭 체크해 주세요.
  pause
  exit /b 1
)

if not exist ".setup_done" (
  echo [1/2] 제스처용 라이브러리 설치 중... ^(처음 한 번, 몇 분 걸려요^)
  python -m pip install -r requirements.txt && echo done > .pipdone
  echo [2/2] 3D 라이브러리와 손 인식 모델 내려받는 중...
  python setup_assets.py
  if exist ".pipdone" echo done > .setup_done
)

echo.
echo 서버를 켭니다. 브라우저가 자동으로 열려요. 끝내려면 이 창에서 Ctrl+C
python server.py
pause
