import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

spec=importlib.util.spec_from_file_location('gifts',Path(__file__).resolve().parents[1]/'scripts/dragon_supporter_gifts.py')
gifts=importlib.util.module_from_spec(spec);spec.loader.exec_module(gifts)


def test_only_repeated_public_support_over_time_creates_review_candidate():
    now=datetime(2026,10,28,tzinfo=timezone(timedelta(hours=8)))
    rows=[{'mid':'12','username':'A','time':f'2026-10-{d} 12:00','content':'支持，加油','channel':'comment'} for d in (12,18,27)]
    assert gifts.candidates(rows,now)[0]['active_days']==3
    for row in rows:row['channel']='dm'
    assert gifts.candidates(rows,now)==[]


def test_one_days_spam_or_short_history_is_not_long_term_support():
    now=datetime(2026,10,28,tzinfo=gifts.TZ)
    row={'mid':'12','time':'2026-10-27 12:00','content':'加油','channel':'comment'}
    assert gifts.candidates([row]*200,now)==[]
