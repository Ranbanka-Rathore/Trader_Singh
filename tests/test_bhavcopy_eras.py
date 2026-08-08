"""Tests for the two-era bhavcopy loader and the data quality report.

The archive spans two incompatible NSE schemas. These tests pin the places where
the eras disagree, because every one of them is a route to a silently wrong
backtest rather than a loud failure.

Run with:  PYTHONUTF8=1 python tests/test_bhavcopy_eras.py
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtest import bhavcopy as B
from backtest.data_quality import Report, check_days
from backtest.liquidity_gate import LiquidityGate

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")


def _have(d):
    p = B.zip_path(d)
    return os.path.exists(p) and os.path.getsize(p) > 0


LEGACY_DAY = datetime.date(2020, 2, 11)
UDIFF_DAY = datetime.date(2024, 2, 6)
TRANSITION_DAY = datetime.date(2025, 1, 15)   # two lot sizes live at once


def test_era_routing():
    print("\n[1] era detection and URL routing")
    check("2023-12-29 is legacy", B.is_udiff(datetime.date(2023, 12, 29)) is False)
    check("2024-01-01 is UDiFF", B.is_udiff(datetime.date(2024, 1, 1)) is True)
    check("legacy URL is the historical archive",
          "historical/DERIVATIVES" in B._source_url(LEGACY_DAY))
    check("UDiFF URL is the fo feed",
          "content/fo/BhavCopy" in B._source_url(UDIFF_DAY))
    check("legacy URL uses DDMMMYYYY",
          "fo11FEB2020bhav" in B._source_url(LEGACY_DAY))
    # one cache naming scheme, so callers never branch on era
    check("cache path is era-independent",
          B.zip_path(LEGACY_DAY).endswith("BhavCopy_NSE_FO_20200211.csv.zip"))


def test_opt_float():
    print("\n[2] _opt_float keeps 'unknown' distinct from 'zero'")
    check("None -> None", B._opt_float(None) is None)
    check("NaN -> None", B._opt_float(float("nan")) is None)
    check("'' -> None", B._opt_float("") is None)
    check("0 -> 0.0 (not None)", B._opt_float(0) == 0.0)
    check("'75' -> 75.0", B._opt_float("75") == 75.0)


def test_legacy_schema_normalised():
    print("\n[3] legacy file is normalised to UDiFF column names")
    if not _have(LEGACY_DAY):
        print("  ⏭  legacy sample not cached, skipping")
        return
    df = B._read_df(LEGACY_DAY)
    for col in ("TckrSymb", "FinInstrmTp", "XpryDt", "StrkPric", "OptnTp",
                "ClsPric", "SttlmPric", "TtlTradgVol", "OpnIntrst"):
        check(f"has {col}", col in df.columns)
    check("instrument types mapped to UDiFF codes",
          set(df["FinInstrmTp"].dropna().unique()) <= {"IDO", "STO", "IDF", "STF"})
    check("expiry normalised to ISO",
          str(df["XpryDt"].dropna().iloc[0])[:4].isdigit())
    # fields the legacy era genuinely lacks must be NaN, never 0
    for absent in ("TtlNbOfTxsExctd", "UndrlygPric", "NewBrdLotQty"):
        check(f"{absent} is NaN not 0", B._opt_float(df[absent].iloc[0]) is None)


def test_traded_is_volume_based():
    print("\n[4] `traded` means volume>0 in BOTH eras (the critical fix)")
    if not (_have(LEGACY_DAY) and _have(UDIFF_DAY)):
        print("  ⏭  samples not cached, skipping")
        return
    for d in (LEGACY_DAY, UDIFF_DAY):
        ch = B.load_chain(d, "NIFTY")
        bad = [(k, v) for k, v in ch["options"].items()
               if v["traded"] != (float(v["volume"] or 0) > 0)]
        check(f"{d} [{ch['era']}]: traded == (volume>0) for every leg", not bad)

    # The legacy archive prints a CLOSE for contracts that never traded, so a
    # close-based flag would wave them through. Prove such legs exist and that
    # the gate refuses them.
    ch = B.load_chain(LEGACY_DAY, "NIFTY")
    ghosts = [v for v in ch["options"].values()
              if float(v["close"] or 0) > 0 and not v["traded"]]
    check(f"legacy has priced-but-untraded legs ({len(ghosts)} of "
          f"{len(ch['options'])}) — close>0 would be a false signal",
          len(ghosts) > 0)
    gate = LiquidityGate(LiquidityGate.TRADED)
    check("gate refuses every one of them",
          all(gate.leg_ok(g)[0] is False for g in ghosts[:200]))


def test_lot_resolution():
    print("\n[5] market lot: derived in legacy, per-expiry in both")
    if _have(LEGACY_DAY):
        ch = B.load_chain(LEGACY_DAY, "NIFTY")
        check(f"legacy lot derived from futures turnover ({ch['lot']})",
              ch["lot"] == 75)
        check("legacy lot is a positive int", isinstance(ch["lot"], int) and ch["lot"] > 0)
    if _have(UDIFF_DAY):
        ch = B.load_chain(UDIFF_DAY, "NIFTY")
        check(f"UDiFF lot read from the file ({ch['lot']})", ch["lot"] == 50)
    if _have(TRANSITION_DAY):
        ch = B.load_chain(TRANSITION_DAY, "NIFTY")
        lots = set(ch["lot_by_expiry"].values())
        check(f"transition day carries two lots {sorted(lots)}", len(lots) == 2)
        check(f"modal lot is the common one ({ch['lot']})", ch["lot"] == 75)
        odd = [e for e, l in ch["lot_by_expiry"].items() if l != ch["lot"]]
        check(f"the odd expiry is isolated ({odd})", len(odd) == 1)
        # every leg must carry ITS expiry's lot, not the day's modal one
        mismatch = [k for k, v in ch["options"].items()
                    if v["lot"] != ch["lot_by_expiry"].get(k[0], ch["lot"])]
        check("each leg carries its own expiry's lot", not mismatch)
        odd_legs = [v["lot"] for k, v in ch["options"].items() if k[0] in odd]
        check(f"legs on the odd expiry use lot {set(odd_legs)}, not {ch['lot']}",
              odd_legs and set(odd_legs) != {ch["lot"]})


def test_spot_source():
    print("\n[6] spot provenance is recorded, not guessed")
    if _have(UDIFF_DAY):
        ch = B.load_chain(UDIFF_DAY, "NIFTY")
        check("UDiFF spot comes from the file",
              ch["spot_source"] == "bhavcopy_underlying" and ch["spot"] > 0)
    if _have(LEGACY_DAY):
        ch = B.load_chain(LEGACY_DAY, "NIFTY")
        check(f"legacy spot comes from the index archive ({ch['spot']:,.2f})",
              ch["spot_source"] == "index_close_archive" and ch["spot"] > 0)
        # sanity: NIFTY was ~12,100 on 2020-02-11
        check("legacy spot is the index level, not a futures price",
              11_500 < ch["spot"] < 12_600)
    check("unknown index -> no name mapping", B.INDEX_NAMES.get("NOTANINDEX") is None)


def _fixture_chain(**over):
    """A minimal well-formed chain, mutated by kwargs for fault injection."""
    d = over.get("date", datetime.date(2024, 6, 3))
    exp = datetime.date(2024, 6, 27)
    leg = {"close": 100.0, "traded": True, "oi": 1000.0, "chg_oi": 0.0,
           "volume": 50.0, "txns": 20.0, "lot": 50}
    ch = {"date": d, "underlying": "NIFTY", "spot": 23000.0,
          "spot_source": "bhavcopy_underlying", "lot": 50,
          "lot_by_expiry": {exp: 50}, "era": "udiff", "expiries": [exp],
          "options": {(exp, 23000.0, "CE"): dict(leg),
                      (exp, 22900.0, "PE"): dict(leg)},
          "futures": {}}
    ch.update({k: v for k, v in over.items() if k != "date"})
    return ch


def test_quality_catches_faults():
    print("\n[7] quality report catches injected faults")
    orig = B.load_chain
    try:
        def run_with(chain):
            B.load_chain = lambda d, underlying="NIFTY": chain
            rep = Report()
            check_days(rep, [datetime.date(2024, 6, 3)], "NIFTY")
            return rep

        check("clean chain passes", run_with(_fixture_chain()).ok)

        check("missing spot fails", not run_with(_fixture_chain(spot=0.0)).ok)
        check("missing lot fails", not run_with(_fixture_chain(lot=0)).ok)

        exp = datetime.date(2024, 6, 27)
        neg = _fixture_chain()
        neg["options"][(exp, 23000.0, "CE")]["oi"] = -5.0
        check("negative OI fails", not run_with(neg).ok)

        past = _fixture_chain()
        past["options"] = {(datetime.date(2024, 1, 1), 23000.0, "CE"):
                           {"close": 1.0, "traded": True, "oi": 1.0,
                            "chg_oi": 0.0, "volume": 1.0, "txns": 1.0, "lot": 50}}
        check("expiry in the past fails", not run_with(past).ok)

        short = _fixture_chain()
        short["options"] = {(exp, 23000.0, "CE"): {"close": 1.0}}
        check("leg missing required fields fails", not run_with(short).ok)
    finally:
        B.load_chain = orig


def test_quality_catches_spot_break():
    print("\n[8] quality report catches a broken spot series")
    rep = Report()
    orig = B.load_chain
    try:
        d1, d2 = datetime.date(2024, 6, 3), datetime.date(2024, 6, 4)
        chains = {d1: _fixture_chain(date=d1, spot=23000.0),
                  d2: _fixture_chain(date=d2, spot=11000.0)}  # -52% overnight
        for k, c in chains.items():
            c["date"] = k
        B.load_chain = lambda d, underlying="NIFTY": chains.get(d)
        check_days(rep, [d1, d2], "NIFTY")
        check(f"52% overnight spot break is a HARD failure ({len(rep.hard)})",
              any("spot jumps" in m for m in rep.hard))
    finally:
        B.load_chain = orig


def test_turnover_units_normalised():
    print("\n[9] turnover is rupees in BOTH eras after normalisation")
    # Legacy VAL_INLAKH is in lakhs, UDiFF TtlTrfVal in rupees. Both land in the
    # same column; if the conversion is dropped, derived lots are 1e5 out.
    for d, want_lot in ((LEGACY_DAY, 75), (UDIFF_DAY, 50)):
        if not _have(d):
            continue
        df = B._read_df(d)
        f = df[(df["TckrSymb"] == "NIFTY") & (df["FinInstrmTp"] == "IDF")]
        r = f.iloc[0]
        c = B._opt_float(r["TtlTradgVol"]) or 0
        v = B._opt_float(r["TtlTrfVal"]) or 0
        p = B._opt_float(r["ClsPric"]) or 0
        implied = v / (c * p) if c and p else 0
        check(f"{d} [{'udiff' if B.is_udiff(d) else 'legacy'}]: turnover/(contracts*close) "
              f"= {implied:.2f}, near the true lot {want_lot}",
              abs(implied - want_lot) / want_lot < 0.05)


def test_lot_table_accuracy():
    print("\n[10] lot table: exact on indices, bounded error elsewhere")
    t = B.lot_table()
    if not t:
        print("  ⏭  lot table not built, skipping")
        return
    check(f"table is populated ({len(t)} entries)", len(t) > 1000)
    check("all entries are positive ints",
          all(isinstance(v, int) and v > 0 for v in t.values()))

    # Real NSE lots are arbitrary (NSE targets a rupee contract value), so the
    # table must NOT have been snapped to round numbers.
    odd = [v for v in t.values() if v % 25 and v < 1000]
    check(f"non-round lots preserved, not snapped ({len(odd)} e.g. "
          f"{sorted(set(odd))[:6]})", len(odd) > 0)

    # index lots must be exact — they carry the liquidity we actually trade
    for d, sym, want in ((datetime.date(2016, 2, 16), "NIFTY", 75),
                         (datetime.date(2020, 2, 11), "NIFTY", 75),
                         (datetime.date(2020, 2, 11), "BANKNIFTY", 20)):
        if not _have(d):
            continue
        ch = B.load_chain(d, sym)
        check(f"{d} {sym} lot == {want} (exact)", ch and ch["lot"] == want)

    # pooling fixes what a single day gets wrong: RELIANCE 2016 derives 501.6
    # from one session and rounds to a wrong 502
    if _have(datetime.date(2016, 2, 16)):
        ch = B.load_chain(datetime.date(2016, 2, 16), "RELIANCE")
        check(f"RELIANCE 2016 lot == 500 via pooling (not 502) [{ch['lot']}]",
              ch["lot"] == 500)


if __name__ == "__main__":
    test_era_routing()
    test_opt_float()
    test_legacy_schema_normalised()
    test_traded_is_volume_based()
    test_lot_resolution()
    test_spot_source()
    test_quality_catches_faults()
    test_quality_catches_spot_break()
    test_turnover_units_normalised()
    test_lot_table_accuracy()
    print(f"\n{'=' * 50}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
