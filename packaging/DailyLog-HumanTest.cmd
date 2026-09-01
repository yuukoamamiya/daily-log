@echo off
setlocal
if not defined DAILY_LOG_EXISTING_DATA_DIR set "DAILY_LOG_EXISTING_DATA_DIR=%LOCALAPPDATA%\DailyLog"
if not exist "%DAILY_LOG_EXISTING_DATA_DIR%\daily-log.db" (
  echo Existing Daily Log data was not found: "%DAILY_LOG_EXISTING_DATA_DIR%"
  echo Set DAILY_LOG_EXISTING_DATA_DIR to an existing profile containing daily-log.db.
  exit /b 2
)
set "DAILY_LOG_STATE_DIR=%DAILY_LOG_EXISTING_DATA_DIR%"
"%~dp0DailyLog.exe" %*
exit /b %ERRORLEVEL%
