"""Only proven bots decide the account's trades; everything else keeps training on its own pretend money."""
import json

import engine as E
from test_feature_tests import market


def farm(opts=None, n=900):
    closes, extra = market(102, n)
    t = E.Tournament(list(closes), 10000.0, 0.006, 0.1, 24, opts=E.tournament_opts(dict(E.DEFAULT_SETTINGS, **(opts or {}))),
                     cap=30)
    for i in range(E.WARM, n):
        t.step({c: E.View(closes[c], i + 1) for c in closes}, E.slice_extra(extra, i + 1))
    return t


def make_proven(t, v):
    v.wins, v.losses, v.age = 8, 4, t.window_for(v.tf) + 1
    v.sim.cash = v.sim.start * 3
    v.eq.append(v.sim.equity(t.last_px))
    v.exams = [dict(k="self-test", edge=5.0 + i, n=4, at=0, when=0, g=60) for i in range(5)]
    t.serial = 2


def test_a_new_bot_is_not_proven():
    t = farm()
    assert not any(t.proven(v) for v in t.vars if v.wins + v.losses < 8)


def test_a_record_and_passed_exams_make_a_bot_proven():
    t = farm()
    v = t.vars[0]
    make_proven(t, v)
    assert t.proven(v) and t.proven_gaps(v) == []
    v.exams = []
    assert not t.proven(v) and any("exams" in g for g in t.proven_gaps(v))
    t.o["proven_exam"] = False
    assert t.proven(v)


def test_only_proven_winners_vote():
    t = farm()
    for v in t.vars:
        v.sig = {c: "buy" for c in t.coins}
    t.active = t.vars[:3]
    for v in t.active:
        t.info[id(v)].update(status="active", weight=1.0, stake=1.0, cons=1.0)
    c = t.coins[0]
    assert t.vote(c)[0] is None
    make_proven(t, t.active[0])
    assert t.vote(c)[0] == "buy"
    t.o["proven_only"] = False
    t.active[0].exams = []
    assert t.vote(c)[0] == "buy"


def test_your_team_is_not_held_back():
    t = farm()
    v = t.vars[0]
    v.origin = "team"
    assert t.proven(v)


def test_exams_are_saved_with_the_farm():
    t = farm()
    E.add_exam(t.vars[0], "self-test", 4.2, 5, 0, 60)
    t2 = E.Tournament(t.coins, 10000.0, 0.006, 0.1, 24, cap=30)
    assert t2.restore(json.loads(json.dumps(t.to_state())))
    v2 = next(v for v in t2.vars if v.key() == t.vars[0].key())
    assert v2.exams and v2.exams[-1]["edge"] == 4.2


def test_exam_verdicts():
    t = farm()
    v = t.vars[0]
    assert E.exam_verdict(v, 100)[0] == "untested"
    v.exams = [dict(k="self-test", edge=-3.0, n=4) for _ in range(4)]
    assert E.exam_verdict(v, 100)[0] == "failed"
    v.exams = [dict(k="self-test", edge=8.0 + i * 0.1, n=4) for i in range(5)]
    assert E.exam_verdict(v, 100)[0] == "passed"
