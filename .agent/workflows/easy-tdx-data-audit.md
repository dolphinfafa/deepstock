# Independent easy-tdx A-share data audit

User explicitly chose yanwei99521/easy-tdx 1.20.8, commit
41e56376fafa3abae0f6538f271eafd6af26d27f. Original author URL/PyPI are
unavailable; never silently replace this with an older fork. MIT is a software
license, not proof of market-data redistribution rights.

Audit the pinned source/imports/transport/build and npm lock before execution.
Upstream wheel force-includes absent web-ui/dist: npm ci --ignore-scripts and
the upstream fixed build in ignored artifacts/vendor, then isolated hatchling
wheel build. Install base wheel --no-deps only on server deepstock Python;
retain before/after pip freeze, wheel SHA and pip check. No science/web extras,
SDK service or Windows installation; strategy engines have no SDK dependency.

Six fixed daily symbols: 000001.SZ,000333.SZ,300750.SZ,600519.SH,601398.SH,
510300.SH; <=800 NONE and QFQ bars each. Two minute symbols 000001.SZ/510300.SH,
<=1600 MIN_1 bars each; last five complete calendar sessions. One repeat daily
sample and one quote snapshot. At most three fixed MAC hosts, two attempts per
item, <=70 actual socket sends inclusive of setup/pages/retries, <=1/second.
No auto reconnect, cross-host SDK failover, heartbeat or local QFQ repair.
Decoded page and SDK returns remain immutable evidence, not raw packet claims.

New normalizer easy-tdx-cn-v1 in deepstock.data, identified by the dataset
contract; does not rewrite old manifests. Declare shares/CNY and minute start
timestamps with separate end bounds; validate against independent daily units,
not fit a multiplier to make mismatches disappear. Preserve NaN/invalid rows,
duplicates/conflicts/order evidence and missing sessions. No price/activity
imputation. Registered Tushare calendar includes national holidays; one daily
calendar fetch if retained coverage is insufficient. No restricted Tushare
minute calls, auction quota/scheduler changes, strategy resumptions or orders.

NONE vs already registered Tushare clean bars: stock tick .01, ETF .001;
volume/amount .1% plus one-share/CNY .01 rounding. Retain all exceedances in
CSV, report pagination and repeat stability, minute session/count/aggregate
consistency. Without independent same-window minute data, explicitly do not
claim independently established minute accuracy. QFQ lacks point-in-time action
history and quotes lack independent freshness proof: neither is trade-ready.

CLI: conda run -n deepstock python -m scripts.audit_easy_tdx --output-dir
artifacts/research/easy-tdx/<unique-run>. Raw/clean versions register to DataStore
and authenticated /data; restricted=true, previews disabled. Independent quality
reports use /api/data-audits and /data, not a strategy/report attached to
Granville, ARC, or paused tail momentum. Failures are evidence, not rerouted data.
