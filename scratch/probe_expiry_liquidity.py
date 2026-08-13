"""One-off: measure near-spot priceability of candidate NIFTY expiries."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv; load_dotenv()
from dhan_integration import DhanBroker
from backend.app.services.options_pricing_service import spread_is_tradeable

NIFTY_ID, SEG = "13", "IDX_I"

def score(payload, spot):
    st = payload.get("strikes") or {}
    ok = tot = oi = 0
    for k, node in st.items():
        try:
            if abs(float(k) - float(spot)) > 1000: continue
        except (TypeError, ValueError): continue
        for t in ("ce", "pe"):
            q = (node or {}).get(t) or {}
            if float(q.get("bid") or 0) <= 0 and float(q.get("ask") or 0) <= 0: continue
            tot += 1
            ok += bool(spread_is_tradeable(q.get("bid"), q.get("ask")))
            oi += float(q.get("oi") or 0) > 0
    return ok, tot, oi

async def main():
    import redis.asyncio as aioredis
    r = aioredis.Redis(host=os.getenv("REDIS_HOST"), port=int(os.getenv("REDIS_PORT")),
                       decode_responses=True)
    b = DhanBroker(os.getenv("DHAN_CLIENT_ID","").strip('"'),
                   os.getenv("DHAN_ACCESS_TOKEN","").strip('"'), redis_client=r)
    for exp in sys.argv[1:]:
        try:
            spot, df = await b.get_clean_option_chain(NIFTY_ID, SEG, expiry=exp)
            raw = await r.get(f"option_premiums:NIFTY:{exp}")
            if not raw:
                print(f"{exp}: no chain published"); continue
            import json; p = json.loads(raw)
            ok, tot, oi = score(p, p.get("spot") or spot)
            pct = 100.0*ok/tot if tot else 0.0
            print(f"{exp}: near-spot priceable {ok:3d}/{tot:3d} ({pct:5.1f}%)  legs_with_OI={oi:3d}")
        except Exception as e:
            print(f"{exp}: FAILED {type(e).__name__}: {e}")
        await asyncio.sleep(2)
    await r.aclose()

asyncio.run(main())
