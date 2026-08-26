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
C:\ProgramData\miniconda3\Scripts\conda.exe run -n deepstock python scripts\record_paper_observation.py --skip-duplicate --plan artifacts\paper\defensive-etf\latest.json >>%LOG% 2>&1
if errorlevel 1 (
  echo [%date% %time%] observation recording failed>>%LOG%
  exit /b 1
)
C:\ProgramData\miniconda3\Scripts\conda.exe run -n deepstock python scripts\run_defensive_etf_backtest.py --prices artifacts\research\norgate\defensive_etf_prices.csv --output-dir artifacts\research\strategy-governance\adaptive-defensive-latest >>%LOG% 2>&1
if errorlevel 1 (
  echo [%date% %time%] governance backtest failed>>%LOG%
  exit /b 1
)
C:\ProgramData\miniconda3\Scripts\conda.exe run -n deepstock python scripts\run_adaptive_defensive_walkforward.py --prices artifacts\research\norgate\defensive_etf_prices.csv --output-dir artifacts\research\strategy-governance\adaptive-defensive-walkforward >>%LOG% 2>&1
if errorlevel 1 (
  echo [%date% %time%] governance walk-forward failed>>%LOG%
  exit /b 1
)
C:\ProgramData\miniconda3\Scripts\conda.exe run -n deepstock python scripts\build_defensive_governance_snapshot.py --prices artifacts\research\norgate\defensive_etf_prices.csv --daily artifacts\research\strategy-governance\adaptive-defensive-latest\daily_results.csv --walkforward artifacts\research\strategy-governance\adaptive-defensive-walkforward\walkforward_results.csv --manifest artifacts\research\strategy-governance\adaptive-defensive-walkforward\manifest.json --plan artifacts\paper\defensive-etf\latest.json --observations artifacts\paper\defensive-etf\observations.jsonl --output artifacts\research\strategy-governance\adaptive-defensive-snapshot.json >>%LOG% 2>&1
if errorlevel 1 (
  echo [%date% %time%] governance snapshot failed>>%LOG%
  exit /b 1
)
C:\ProgramData\miniconda3\Scripts\conda.exe run -n deepstock python scripts\evaluate_strategy_registry.py --snapshots artifacts\research\strategy-governance\adaptive-defensive-snapshot.json --skip-duplicate >>%LOG% 2>&1
if errorlevel 1 (
  echo [%date% %time%] governance evaluation failed>>%LOG%
  exit /b 1
)
echo [%date% %time%] observation completed>>%LOG%
exit /b 0
