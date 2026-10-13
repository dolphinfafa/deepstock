import pandas as pd
import pytest
from scripts.diagnose_granville_sources import classify_gap


@pytest.mark.parametrize('raw,adjusted,listing,expected',[
    (True,True,1,'new_quote_requires_separate_inventory'),
    (True,False,1,'raw_adjusted_mismatch'),
    (False,False,0,'no_eligible_trade_not_major_exchange'),
    (False,False,1,'no_eligible_trade_major_exchange'),
    (False,False,None,'listing_unknown'),
])
def test_no_trade_or_listing_is_not_a_fabricated_price(raw,adjusted,listing,expected):
    day=pd.Timestamp('2024-01-02')
    assert classify_gap(day,[day] if raw else [],[day] if adjusted else [],{day:listing})==expected
