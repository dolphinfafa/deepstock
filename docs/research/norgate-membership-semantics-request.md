# Norgate membership semantics — support request draft

Status: explicitly authorized by the user on 2026-10-08; **not sent yet**.
Send one email only, with no licensed raw bars, daily member rows, account details
or credentials. Preserve the original approved body below; exclude the internal
references from the email. Official contact address verified from the contact
page: `support@norgatedata.com`.

Suggested subject: Historical S&P 500 indicator on non-quote days — NONE vs ALLMARKETDAYS

Hello Norgate Data support,

We use the Norgate Python package v1.0.77 and the complete S&P 500 Current & Past
watchlist for historical research. We retain native unpadded member indicators
and do not impute unknown membership or executable prices.

For 2024-12-31 through 2026-09-29, these securities have XNYS-session dates
within their quoted lifetimes absent from the NONE indicator response:

| Symbol | Asset ID | Missing security-dates | First / last missing date |
| --- | ---: | ---: | --- |
| BIGGQ | 127503 | 10 | 2025-07-03 / 2026-09-04 |
| SBNY | 143379 | 30 | 2025-07-02 / 2026-09-16 |
| YELLQ-202607 | 130574 | 128 | 2025-01-14 / 2026-06-23 |

Canonical `indexname="S&P 500"` returns the same native dates/values as our
retained capture. ALLMARKETDAYS returns all 168 otherwise missing security-dates
as zero; overlapping native observations agree. These counts alone do not tell
us whether the missing-day values are independently evaluated historical states
or copied prior observations. The public Python documentation describes price
padding as repeating previous closes, without explicitly resolving this member
indicator distinction.

Could you please confirm:

1. On a non-quote day, does `index_constituent_timeseries(...,
   padding_setting=PaddingType.ALLMARKETDAYS)` evaluate historical constituent
   status for the requested date, or forward-fill a previous indicator?
2. If index membership changes while a security has no quote, will that missing
   day's padded indicator reflect the effective index change? Can you provide
   the documented behavior or an independently verifiable example?
3. Is there an API/export of effective historical inclusion/removal intervals
   independent of price records? How are announcement versus effective dates,
   reentries, OTC transitions and terminal aliases represented?
4. For the three asset IDs above, what is the authoritative member status on
   the missing dates, and what evidence/API contract supports it?

Minimal reproduction, run locally without requesting any prices:

```python
import norgatedata as n
import pandas as pd

for symbol in ("BIGGQ", "SBNY", "YELLQ-202607"):
    for padding in (n.PaddingType.NONE, n.PaddingType.ALLMARKETDAYS):
        frame = pd.DataFrame(n.index_constituent_timeseries(
            symbol, "S&P 500", padding_setting=padding,
            start_date="2024-12-31", end_date="2026-09-29"))
        print(symbol, padding.name, len(frame))
```

We will keep our research admission gate blocked until the data semantics are
documented; we are not requesting an assumption that produces a preferred
backtest result. Thank you.

## Internal evidence references

Quantitative-node diagnostic: `membership-gap-diagnostic-20261008-v1`, clean
commit `c8eb883d5e8e58e094967f8278c75a29d428b0ad`. Price-free summary SHA-256
`15d95da8d718fc16c492398810157cdff7624d9e0bed157f3a94af5989f1129b`.
Exact gap-date lists and six provider responses remain restricted/local.
Public documentation: <https://pypi.org/project/norgatedata/>;
official support channel: <https://norgatedata.com/contact.php>.
