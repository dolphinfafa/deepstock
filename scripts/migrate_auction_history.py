"""One-time read-only export; Darwen is never required by runtime research."""
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path
from scripts.sync_csi300_auction_data import synchronize
from scripts.clean_existing_data import run

EXPORT = r'''
from pathlib import Path
from sqlalchemy import select
import pandas as pd
from backend.database import engine
from backend.models import Company, Security, MarketBar
root = Path(__import__('sys').argv[1])
snapshots = pd.read_csv(root / 'universe_snapshots.csv', dtype={'stock_code': str})
codes = sorted(set(snapshots.stock_code.str.zfill(6)))
master_query = select(Company.company_id, Security.security_id, Company.stock_code, Company.name, Company.industry_name).join(Security, Security.company_id == Company.company_id).where(Company.stock_code.in_(codes))
with engine.connect() as connection:
    master = pd.read_sql(master_query, connection)
    ids = master.security_id.tolist()
    query = select(MarketBar.security_id, MarketBar.trade_date, MarketBar.open, MarketBar.high, MarketBar.low, MarketBar.close, MarketBar.volume, MarketBar.market_cap).where(MarketBar.security_id.in_(ids), MarketBar.trade_date >= '2024-01-01').order_by(MarketBar.security_id, MarketBar.trade_date)
    bars = pd.read_sql(query, connection)
bars = bars.merge(master[['security_id','stock_code']], on='security_id', validate='many_to_one')
master.to_csv(root / 'security_master.csv', index=False)
bars.to_csv(root / 'base_daily_bars.csv.gz', index=False, compression='gzip')
print('exported securities=%s bars=%s' % (len(master), len(bars)))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-python", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    synchronize(args.source_root / "artifacts", root / "artifacts")
    target = root / "artifacts/auction_history_tushare"
    if (target / "security_master.csv").exists() or (target / "base_daily_bars.csv.gz").exists():
        raise RuntimeError("Local export already exists; refusing to overwrite historical handover")
    subprocess.run([str(args.source_python), "-c", EXPORT, str(target)], cwd=args.source_root, check=True, timeout=600)
    print({k: v for k, v in run(root).items() if k != "versions"})


if __name__ == "__main__":
    main()
