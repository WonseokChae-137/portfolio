@echo off
cd /d "%~dp0"
echo ============================================
echo   Portfolio Dashboard Update
echo ============================================
echo.

echo [1/3] Generating dashboard (fetching prices)...
python portfolio_dashboard.py
if errorlevel 1 (
    echo.
    echo [!] python failed, retrying with python3...
    python3 portfolio_dashboard.py
)
echo.

echo [2/3] Preparing GitHub upload...
git add .
git commit -m "update dashboard"
echo.

echo [3/3] Uploading to GitHub...
git push origin main
echo.

echo ============================================
echo   DONE! Site updates in 1-2 min:
echo   https://wonseokchae-137.github.io/portfolio/dashboard.html
echo ============================================
echo.
pause
