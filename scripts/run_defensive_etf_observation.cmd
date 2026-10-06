@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."
cd /d "%PROJECT_ROOT%"

if not exist artifacts\paper\defensive-etf mkdir artifacts\paper\defensive-etf
set LOG=artifacts\paper\defensive-etf\scheduler.log

set "PYTHON_EXE="
if defined DEEPSTOCK_PYTHON set "PYTHON_EXE=%DEEPSTOCK_PYTHON%"
if not defined PYTHON_EXE if exist D:\workspace\conda-envs\deepstock\python.exe set "PYTHON_EXE=D:\workspace\conda-envs\deepstock\python.exe"
if not defined PYTHON_EXE if exist C:\ProgramData\miniconda3\envs\deepstock\python.exe set "PYTHON_EXE=C:\ProgramData\miniconda3\envs\deepstock\python.exe"
if not exist "%PYTHON_EXE%" (
  echo [%date% %time%] deepstock Python environment unavailable>>%LOG%
  exit /b 1
)

echo [%date% %time%] observation started>>%LOG%
"%PYTHON_EXE%" scripts\download_norgate_defensive_etfs.py >>%LOG% 2>&1
if errorlevel 1 (
  echo [%date% %time%] export failed>>%LOG%
  exit /b 1
)
"%PYTHON_EXE%" scripts\clean_existing_data.py >>%LOG% 2>&1
if errorlevel 1 exit /b 1
"%PYTHON_EXE%" scripts\publish_data_catalog.py >>%LOG% 2>&1
if errorlevel 1 exit /b 1
"%PYTHON_EXE%" scripts\generate_defensive_etf_plan.py --prices artifacts\research\norgate\defensive_etf_prices.csv >>%LOG% 2>&1
if errorlevel 1 (
  echo [%date% %time%] plan generation failed>>%LOG%
  exit /b 1
)
"%PYTHON_EXE%" scripts\record_paper_observation.py --skip-duplicate --plan artifacts\paper\defensive-etf\latest.json >>%LOG% 2>&1
if errorlevel 1 (
  echo [%date% %time%] observation recording failed>>%LOG%
  exit /b 1
)
"%PYTHON_EXE%" scripts\run_defensive_etf_backtest.py --profile adaptive --prices artifacts\research\norgate\defensive_etf_prices.csv --output-dir artifacts\research\strategy-governance\adaptive-defensive-latest >>%LOG% 2>&1
if errorlevel 1 (
  echo [%date% %time%] governance backtest failed>>%LOG%
  exit /b 1
)
"%PYTHON_EXE%" scripts\run_adaptive_defensive_walkforward.py --prices artifacts\research\norgate\defensive_etf_prices.csv --output-dir artifacts\research\strategy-governance\adaptive-defensive-walkforward >>%LOG% 2>&1
if errorlevel 1 (
  echo [%date% %time%] governance walk-forward failed>>%LOG%
  exit /b 1
)
"%PYTHON_EXE%" scripts\build_defensive_governance_snapshot.py --prices artifacts\research\norgate\defensive_etf_prices.csv --daily artifacts\research\strategy-governance\adaptive-defensive-latest\daily_results.csv --walkforward artifacts\research\strategy-governance\adaptive-defensive-walkforward\walkforward_results.csv --manifest artifacts\research\strategy-governance\adaptive-defensive-walkforward\manifest.json --plan artifacts\paper\defensive-etf\latest.json --observations artifacts\paper\defensive-etf\observations.jsonl --output artifacts\research\strategy-governance\adaptive-defensive-snapshot.json >>%LOG% 2>&1
if errorlevel 1 (
  echo [%date% %time%] governance snapshot failed>>%LOG%
  exit /b 1
)
"%PYTHON_EXE%" scripts\evaluate_strategy_registry.py --snapshots artifacts\research\strategy-governance\adaptive-defensive-snapshot.json --skip-duplicate >>%LOG% 2>&1
if errorlevel 1 (
  echo [%date% %time%] governance evaluation failed>>%LOG%
  exit /b 1
)
"%PYTHON_EXE%" scripts\publish_defensive_observation.py >>%LOG% 2>&1
if errorlevel 1 (
  echo [%date% %time%] observation publishing failed>>%LOG%
  exit /b 1
)
echo [%date% %time%] observation completed>>%LOG%
exit /b 0
