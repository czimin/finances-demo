@echo off
rem clear_statements.bat - delete the bank/broker statements from data\ once
rem they've been imported (python import_statement.py) and snapshotted
rem (python snapshot.py). They carry your name, address and account numbers.
rem
rem Deletes ONLY *.pdf and *.csv directly inside data\. Keeps what the project
rem needs: finances.db, backups\, prices.json. Asks before deleting anything.
setlocal
cd /d "%~dp0data" 2>nul || (echo No data\ folder next to this script. & pause & exit /b 1)

set "found="
echo Statements in data\:
for %%F in (*.pdf *.csv) do (
    echo   %%F
    set "found=1"
)
if not defined found (
    echo   none - nothing to delete.
    goto :end
)

echo.
echo Kept: finances.db, backups\, prices.json
echo Make sure these have been imported and snapshotted first - the database
echo will be the only record, and deleted files do NOT go to the Recycle Bin.
set "ans="
set /p "ans=Delete the files listed above? [y/N]: "
if /i not "%ans%"=="y" (
    echo Nothing deleted.
    goto :end
)
del /q *.pdf *.csv
echo Deleted.

:end
endlocal
pause
