
@echo off
setlocal
 
REM ---- pick the right Python: prefer a local venv, else whatever's on PATH ----
set PY=python
if exist build-venv\Scripts\python.exe set PY=build-venv\Scripts\python.exe
 
echo === Using interpreter ===
%PY% --version
if errorlevel 1 ( echo Python not found. & exit /b 1 )
 
echo === Ensuring PyInstaller is installed ===
%PY% -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    %PY% -m pip install pyinstaller
    if errorlevel 1 ( echo pip install of pyinstaller FAILED. & exit /b 1 )
)
 
echo === Ensuring face_landmarker.task is present ===
if not exist face_landmarker.task (
    echo Downloading model...
    %PY% -c "import urllib.request; urllib.request.urlretrieve('https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task','face_landmarker.task')"
    if errorlevel 1 ( echo Model download FAILED. & exit /b 1 )
)
 
echo === Building (windowed release: custom icon + version info) ===
REM NOTE: invoked as "-m PyInstaller", not bare "pyinstaller", so PATH doesn't matter.
REM --icon       gives the exe + window the periscope icon (see assets\make_icon.py)
REM --version-file embeds Name/Description/Version into Properties > Details (version.txt)
REM --windowed   hides the console for release; drop it if you need to see tracebacks.
%PY% -m PyInstaller --noconfirm --onefile --windowed --name Periscope ^
  --icon "assets\periscope.ico" ^
  --version-file "version.txt" ^
  --add-data "face_landmarker.task;." ^
  --collect-data customtkinter ^
  --collect-all mediapipe ^
  HeadTracker.py
 
if errorlevel 1 (
    echo.
    echo *** BUILD FAILED -- read the error above. No exe was produced. ***
    exit /b 1
)
 
echo.
echo === Build complete ===
echo Executable: dist\Periscope.exe
echo First launch is slow (~10-15s); onefile extracts MediaPipe to a temp folder.
endlocal
 