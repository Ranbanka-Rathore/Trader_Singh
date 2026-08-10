"""Does Amendment B3's era break exist in STOCK FUTURES, or only in NIFTY options?

B3 draws three liquidity eras and requires every result to be reported per era,
on the evidence that the share of NIFTY **option** legs that actually trade rises
~6x across the archive. That evidence is entirely about the option book. Nothing
in B3 measured single-stock futures, so applying its boundaries to them is an
extension by analogy rather than by evidence.

The question matters because arena 3 is now underpowered: T2b showed the smallest
detectable IC on 2023-2026 (0.0551) is the same size as the IC required to be
worth trading (~0.04-0.05). Roughly 35 rebalances would detect 0.05 and 55 would
detect 0.04, against 29 available. The ramp era supplies them -- if pooling is
legitimate.

So: measure the instrument properties of stock futures per year, the same CLASS
of measurement B3 itself rests on. If the properties that matter for a
cross-sectional book are continuous across 2022->2023, then the options-derived
boundary does not describe this instrument and B3's rationale does not reach it.
If they break, pooling is exactly the sin B3 names.

Deliberately NOT measured here: any signal's IC by era. That is the quantity the
pooling decision would be used to estimate, and choosing the window by looking at
it first is how a result gets manufactured. Instrument properties only.
"""
import datetime as dt
import sys

import numpy as np

sys.path.insert(0, r"D:\Projects\Agentic_Trader")

from backtest import futures

GATE = "strict_legacy"
KIND = "stock"
YEARS = range(2016, 2027)
MIN_BARS = 120
MAX_PAIRS = 4000        # sampled pairs for rho_bar, so the sweep stays cheap
SEED = 20260810


def rho_bar(by_sym, rng):
    syms = sorted(by_sym)
    if len(syms) < 5:
        return float("nan")
    pairs = []
    for _ in range(MAX_PAIRS):
        a, b = rng.choice(len(syms), 2, replace=False)
        da, db = by_sym[syms[a]], by_sym[syms[b]]
        common = sorted(set(da) & set(db))
        if len(common) < 60:
            continue
        x = np.array([da[d] for d in common])
        y = np.array([db[d] for d in common])
        if x.std() > 0 and y.std() > 0:
            pairs.append(np.corrcoef(x, y)[0, 1])
    return float(np.mean(pairs)) if pairs else float("nan")


def main():
    rng = np.random.default_rng(SEED)
    print(f"stock futures, gate={GATE} — instrument properties per year")
    print(f"{'year':>5} {'era':>7} {'names':>6} {'pass%':>7} {'rho_bar':>8} "
          f"{'N_eff@8':>8} {'disp21%':>8} {'medvol%':>8} {'medlot':>7}")
    print("-" * 78)
    for y in YEARS:
        start = dt.date(y, 1, 1)
        end = dt.date(y, 12, 31)
        dates = futures.trading_dates(start, end)
        if not dates:
            continue
        panel = futures.build_panel(dates, kind=KIND, gate=GATE)
        by_sym, lots, vols = {}, [], []
        for s, ser in panel.series.items():
            d = {b.date: r for b, r in zip(ser.bars, ser.rets) if r is not None}
            if len(d) >= MIN_BARS:
                by_sym[s] = d
                lots.extend(b.lot for b in ser.bars if b.lot > 0)
                arr = np.array(list(d.values()))
                vols.append(arr.std() * np.sqrt(252) * 100)
        if len(by_sym) < 5:
            continue

        # cross-sectional dispersion of 21-day returns: how much do names differ?
        # This is what a cross-sectional book harvests; if it collapses, the same
        # IC buys less Sharpe, which would be a genuine regime change.
        all_dates = sorted({d for v in by_sym.values() for d in v})
        disps = []
        for i in range(0, len(all_dates) - 21, 21):
            blk = all_dates[i:i + 21]
            rets = []
            for s, dd in by_sym.items():
                vals = [dd[d] for d in blk if d in dd]
                if len(vals) >= 17:
                    rets.append(np.prod(1.0 + np.array(vals)) - 1.0)
            if len(rets) >= 30:
                disps.append(np.std(rets) * 100)

        r = rho_bar(by_sym, rng)
        n_eff8 = 8.0 / (1.0 + 7.0 * r) if np.isfinite(r) else float("nan")
        era = ("early" if y <= 2019 else "ramp" if y <= 2022 else "modern")
        print(f"{y:>5} {era:>7} {len(by_sym):6d} {panel.pass_rate:6.2f}% "
              f"{r:8.3f} {n_eff8:8.2f} {np.mean(disps):7.2f}% "
              f"{np.median(vols):7.2f}% {int(np.median(lots)):7d}")

    print("\ndisp21% = cross-sectional sd of 21-day name returns (what a "
          "cross-sectional book harvests)")
    print("medvol% = median annualised realised vol across names")
    print("N_eff@8 = independent bets in an 8-name book at that year's rho_bar")


if __name__ == "__main__":
    main()
