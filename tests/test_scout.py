"""Bot picks: busy and moving, a huge move can't outweigh everything, and one place for BTC or ETH."""
import engine as E

SNAP = {"BTC": (95000, 30, 0.8), "ETH": (3400, 15, 1.1), "SOL": (180, 4, 5.5), "DOGE": (0.2, 2, 7.0), "XRP": (2.3, 3, 4.0),
        "PEPE": (0.00001, 0.3, 40.0), "LINK": (20, 0.8, 6.0), "AVAX": (35, 0.6, 5.0)}


def products(snap=SNAP):
    return [dict(coin=c, price=p, volume_usd=v * 1e9, change_24h=m) for c, (p, v, m) in snap.items()]


def test_btc_or_eth_always_gets_a_place():
    picks = E.scout_coins(products(), 4)
    assert len(picks) == 4 and any(c in E.ANCHOR_COINS for c in picks)


def test_a_huge_move_on_thin_volume_does_not_win():
    assert E.scout_coins(products(), 2)[0] != "PEPE"


def test_reasons_are_given():
    why = {}
    picks = E.scout_coins(products(), 3, why=why)
    assert all(c in why for c in picks)
