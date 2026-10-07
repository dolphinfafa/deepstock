# Norgate membership semantics — support inquiry record

Status: explicitly authorized by the user on 2026-10-08; **one email submitted**
at 2026-10-08 01:28:11 Asia/Shanghai (2026-10-07 17:28:11 UTC). SMTP accepted it;
delivery and a provider response are not confirmed. No attachments, licensed raw
bars, daily member rows, account details or credentials were sent. The exact
approved body below was sent, excluding internal references. Official contact
address verified from the contact page: `support@norgatedata.com`.

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

## Delivery evidence and follow-up

Single server-side attempt at clean commit `3951e95`. Private sender headers,
local `.eml` and transport receipt stay under the ignored
`artifacts/research/support/norgate-membership-20261008-attempt-1/` directory;
they are never committed or published to the data catalog.

Request-body SHA-256:
`add3725b30e9abe2d19e91611a8432fe507453ca58df67303d494c180f5f2c3a`.
Message SHA-256:
`2253285719748e5a1d11e7a817f64e87cf150d94a4eca3a5e7be702a68113d6b`.
Receipt SHA-256:
`04c656e0c861bd1cfd3ea419e248d083256da635b478b0fff3aa21954b1e1c8f`.
Official contact-page retained response version:
`3be3e5aa28b7da42fc2a80e572bb58b163739b248fa370fa5638b1b5244c45b0`;
SHA-256 `f942bb319e1303c0fb76df1f765544f486f93c5a984bb0c19375c4e858696999`.

The request is waiting for a reply. No mailbox-reading integration is configured;
the user must share any relevant response. No automated resends or reminders
were scheduled. Preserve the original reply and its source/date before evaluating
the semantics. Provider acknowledgement alone is not missing-day truth: require
a concrete explanation of how indicators behave across non-quote index changes,
then separately assess whether a new versioned contract is defensible. Existing
membership, WBA and trading gates remain blocked.
