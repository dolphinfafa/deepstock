"""US-only sizing experiment contract, separate from unchanged Both history."""
import numpy as np


def validate_sizing_config(cfg):
    expected = {"strategy_id": "granville_stock_portfolio", "experiment_id": "granville-us-sizing-v3-20261007",
                "market": "US", "variant": "trend_pullback", "exit_policy": "time_7", "entry_policy": "signal_level",
                "sizing_policies": ["fixed_16pct", "volatility_shrink_only"], "principal_sizing_policy": "fixed_16pct",
                "cost_cases": ["base", "stress"], "volatility_sessions": 20, "volatility_ddof": 0,
                "formula": "0.16 * min(1, eligible_pool_median_volatility / stock_volatility)",
                "paper_authorized": False, "live_authorized": False}
    if any(cfg.get(k) != v for k, v in expected.items()):
        raise ValueError("Fixed US sizing experiment boundary changed")
    return cfg


def validate_sizing(value):
    cfg = validate_sizing_config(value["experiment"])
    if set(value["market_results"]) != {"US", "CN"}:
        raise ValueError("Both historical markets must remain visible")
    us, cn = value["market_results"]["US"], value["market_results"]["CN"]
    if "sizing_experiment" in cn or cn.get("current_experiment_status") != "not_rerun_historical_evidence":
        raise ValueError("CN must not be relabelled as a new experiment")
    opt = us["sizing_experiment"]
    if opt["experiment"] != cfg:
        raise ValueError("Sizing contracts differ")
    cases = {(c["sizing_policy"], c["cost_case"]): c for c in opt["cases"]}
    if len(opt["cases"]) != 4 or set(cases) != {(p, c) for p in cfg["sizing_policies"] for c in cfg["cost_cases"]}:
        raise ValueError("All four fixed US sizing cases required")
    for (policy, cost), case in cases.items():
        if case["status"] == "blocked":
            if not case.get("blocking_reason") or case["periods"]:
                raise ValueError("Blocked sizing case cannot fabricate performance")
        elif case["status"] != "completed":
            raise ValueError("Invalid sizing case status")
        if policy == "fixed_16pct":
            if case["status"] != "completed" or not case.get("baseline_verified") or not case.get("verified_baseline_artifact_hashes"):
                raise ValueError("Both baseline ledgers must be independently verified")
            original = next(c for c in us["cases"] if (c["variant"], c["exit_policy"], c["cost_case"]) == (cfg["variant"], cfg["exit_policy"], cost))
            for name, old in original["periods"]["full"].items():
                new = case["periods"]["full"].get(name)
                if isinstance(old, (int, float)):
                    if new is None or not np.isclose(new, old, rtol=1e-10, atol=1e-10):
                        raise ValueError("Baseline metrics differ")
                elif new != old:
                    raise ValueError("Baseline scope differs")
    if us["metrics"] != cases[("fixed_16pct", "base")]["periods"]["full"]:
        raise ValueError("Do not promote a sizing winner into the headline")
