@echo off
REM Daily backup of the Money Tracker database.
REM Used by the Windows Task Scheduler job installed by
REM scripts\install_scheduled_backup.ps1 - do not run it by hand.
REM
REM Note: no parenthesised blocks are used on purpose - inside a block cmd
REM expands %variables% before the block runs, which hides the real exit
REM code of the backup.
setlocal
cd /d "%~dp0.."
if not exist "backups" mkdir "backups"
set "LOG=backups\scheduler.log"

set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY set "PY=python"

echo [%date% %time%] backup starting >> "%LOG%"
"%PY%" "scripts\backup.py" --keep 14 >> "%LOG%" 2>&1 < nul
set "RC=%errorlevel%"
echo [%date% %time%] backup exit code %RC% >> "%LOG%"

if not "%RC%"=="0" goto failed
echo [%date% %time%] Backup OK >> "%LOG%"
exit /b 0

:failed
echo [%date% %time%] Backup FAILED >> "%LOG%"
exit /b 1
