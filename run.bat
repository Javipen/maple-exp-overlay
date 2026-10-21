@echo off
rem Runs Maple Classic Exp Overlay from the source code. Most players should use the download instead
rem (see the README). This needs Python with the packages from requirements.txt.
cd /d "%~dp0"
for %%P in (python "py -3") do (
    %%~P -c "import cv2, mss, numpy, pytesseract, PIL" >nul 2>nul && (
        %%~P maple_exp.py
        goto :eof
    )
)
echo Couldn't find Python with the packages this needs.
echo Install Python from python.org, then run this once:
echo     py -3 -m pip install -r requirements.txt
pause
