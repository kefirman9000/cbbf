"""Bot positions: a practice reset closes the bots' pretend holdings without changing any bot's record, and a
restarted farm still knows its prices."""
import json

import engine as E
from test_feature_tests import market


def holding_farm():
    closes, extra = market(102, 1200)
    t = E.Tournament(list(closes), 1000.0, 0.006, 0.1, 24, opts=E.tournament_opts(E.DEFAULT_SETTINGS), cap=30)
    for i in range(E.WARM, 1200):
        t.step({c: E.View(closes[c], i + 1) for c in closes}, E.slice_extra(extra, i + 1))
        if i > 400 and sum(1 for v in t.vars for q in v.sim.pos.values() if q > 0) >= 3:
            break
    return t


def holdings(t):
    return [(v, c) for v in t.vars for c, q in v.sim.pos.items() if q > 0]


def test_reset_closes_every_holding_without_changing_equity_or_record():
    t = holding_farm()
    held = holdings(t)
    assert held
    before = {id(v): (round(v.sim.equity(t.last_px), 6), v.ntrades, v.wins, v.losses) for v in t.vars}
    assert t.close_positions() == len(held)
    assert not holdings(t)
    for v in t.vars:
        assert (round(v.sim.equity(t.last_px), 6), v.ntrades, v.wins, v.losses) == before[id(v)]


def test_a_restarted_farm_still_knows_its_prices():
    t = holding_farm()
    t2 = E.Tournament(t.coins, 1000.0, 0.006, 0.1, 24, cap=30)
    assert t2.restore(json.loads(json.dumps(t.to_state())))
    assert t2.last_px == t.last_px and t2.last_px
