import shutil

import pytest

from tools import levels, universe


def test_repo_universe_structure():
    u = universe.load()
    ids = [c["id"] for c in u["core"]]
    assert ids == ["BTC/USDT", "BCH/USDT", "XAU/USD", "SI", "SLV", "GLD", "MSFT"]
    assert len(u["drivers"]) == 5 and len(u["pairs"]) == 8 and len(u["ratios"]) == 5
    assert set(u["context"]) == set(ids)
    for c in u["core"]:
        assert c["session"] in levels.SESSIONS


def test_watchlist_comes_from_universe():
    assert list(levels.WATCHLIST) == [c["id"] for c in universe.core()]
    btc = levels.WATCHLIST["BTC/USDT"]
    assert (btc.provider, btc.instrument, btc.backup.instrument) == ("okx", "BTC-USDT", "BTC-USD")
    xau = levels.WATCHLIST["XAU/USD"]
    assert xau.drop_weekend and xau.spot_check == ("XAU", "USD") and xau.session is levels.GOLD
    assert levels.WATCHLIST["SLV"].provider == "yfinance" and levels.WATCHLIST["SI"].session is levels.CME


def test_themes_and_lookup_helpers():
    assert universe.themes()["SLV"] == "precious metals" and universe.themes()["MSFT"] == "equities"
    assert universe.core_symbol_to_id()["BCH-USD"] == "BCH/USDT"
    assert universe.entry("DFII10")[0] == "driver" and universe.entry("XLK")[0] == "pair"
    assert universe.daily_symbol(universe.core_by_id()["BTC/USDT"]) == "BTC-USD"
    assert universe.context("MSFT")["ratios"] == ["msft_qqq"]


def test_set_status_preserves_comments(tmp_path):
    p = tmp_path / "u.yaml"
    shutil.copy(universe.UNIVERSE_PATH, p)
    before = p.read_text(encoding="utf-8")
    assert universe.set_status("^TNX", True, "2026-10-04", 'units: percent; "quoted"', p)
    assert universe.set_status("^TNX", True, "2026-10-05", None, p)        # re-run replaces, doesn't duplicate
    assert universe.set_status("GLD", False, "2026-10-05", "stale", p)
    text = p.read_text(encoding="utf-8")
    assert text.count("# Single source of truth") == 1 and "Yahoo quotes this as yield x10" in text
    u = universe.load(p)
    tnx = next(d for d in u["drivers"] if d["symbol"] == "^TNX")
    assert tnx["verified"] is True and str(tnx["last_verified"]) == "2026-10-05" and "verify_note" not in tnx
    gld = next(c for c in u["core"] if c["symbol"] == "GLD")
    assert gld["verified"] is False and gld["verify_note"] == "stale"
    for sym in ("^TNX", "GLD"):                                              # re-runs never duplicate lines
        block = text.split(f'- symbol: "{sym}"')[1].split("- symbol:")[0]
        assert block.count("verified:") - block.count("last_verified:") == 1 and block.count("last_verified:") == 1
    assert before.count("# Single source of truth") == 1
    assert not universe.set_status("NOPE", True, "2026-10-05", None, p)


def test_source_from_entry_rejects_unknown_venue():
    with pytest.raises(ValueError, match="only OKX"):
        levels.source_from_entry({"id": "X", "session": "crypto", "source": "exchange_public",
                                  "feed": {"venue": "kraken", "instrument": "X"}})


def test_fred_csv_parser():
    from tools import data
    s = data.parse_fred_csv("observation_date,DFII10\n2026-09-30,1.85\n2026-10-01,.\n2026-10-02,1.88\n")
    assert list(s.values) == [1.85, 1.88] and str(s.index[-1].date()) == "2026-10-02"
