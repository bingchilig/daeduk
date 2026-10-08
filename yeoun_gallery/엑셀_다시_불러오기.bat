@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo 엑셀을 다시 읽어 data 폴더를 새로 만듭니다 ^(관리자 수정 내용은 유지^)
python import_excel.py
pause
