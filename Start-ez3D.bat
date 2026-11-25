@echo off
setlocal
pushd "%~dp0"
if not exist "Runtime\PythonEnv\Scripts\pythonw.exe" (
  echo O ambiente do ez3D nao foi instalado. Execute ez3D-Setup.exe novamente.
  pause
  exit /b 1
)
start "ez3D" "Runtime\PythonEnv\Scripts\pythonw.exe" "Resources\Studio\desktop_app.py"
popd
