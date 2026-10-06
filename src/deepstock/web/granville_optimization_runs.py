"""Keep single-change comparisons alongside the immutable original evidence."""
from deepstock.web.granville_stock_runs import ingest_granville_stock_runs


def ingest_granville_optimization_runs(session, root):
    return ingest_granville_stock_runs(session, root, subdirectory="granville-stock-optimizations", optimization=True)
