@echo off
setlocal
cd /d F:\workspace\deepstock

if not exist artifacts\paper\defensive-etf mkdir artifacts\paper\defensive-etf
set LOG=artifacts\paper\defensive-etf\scheduler.log

echo [%date% %time%] observation started>>%LOG%
C:\ProgramData\miniconda3\Scripts\conda.exe run -n deepstock python scripts\download_norgate_defensive_etfs.py >>%LOG% 2>&1
if errorlevel 1 (
  echo [%date% %time%] export failed>>%LOG%
  exit /b 1
)
C:\ProgramData\miniconda3\Scripts\conda.exe run -n deepstock python scripts\generate_defensive_etf_plan.py --prices artifacts\research\norgate\defensive_etf_prices.csv >>%LOG% 2>&1
if errorlevel 1 (
  echo [%date% %time%] plan generation failed>>%LOG%
  exit /b 1
)
C:\ProgramData\miniconda3\Scripts\conda.exe run -n deepstock python scripts\record_paper_observation.py --plan artifacts\paper\defensive-etf\latest.json >>%LOG% 2>&1
if errorlevel 1 (
  echo [%date% %time%] observation recording failed>>%LOG%
  exit /b 1
)
echo [%date% %time%] observation completed>>%LOG%
exit /b 0
