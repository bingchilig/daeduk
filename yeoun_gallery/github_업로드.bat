@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo GitHub 저장소(bingchilig/daeduk)에 코드를 올립니다.
echo - data 폴더(실명·사내 위치·작품 이미지)와 엑셀은 .gitignore로 제외돼요.
echo - 처음이면 GitHub 로그인 창이 떠요.
echo.
where git > nul 2>&1
if errorlevel 1 (
  echo [!] Git이 없어요. https://git-scm.com/download/win 에서 설치한 뒤 다시 실행해 주세요.
  pause
  exit /b 1
)
if not exist ".git" (
  git init -b main
  git remote add origin https://github.com/bingchilig/daeduk.git
  git fetch origin main
  git reset --soft origin/main
)
git add -A
git -c user.name="4team-44xla" -c user.email="44xla@users.noreply.github.com" commit -m "여운 3D 전시관: 손 기울이기 인식, 카메라 화면, SQLite DB"
git push -u origin main
echo.
echo 끝났어요. https://github.com/bingchilig/daeduk 에서 확인하세요.
pause
