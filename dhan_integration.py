import pandas as pd
import requests
import os
import logging
import asyncio
import json
import time
import uuid
from dhanhq import dhanhq
from dotenv import load_dotenv

# Load the keys from the hidden .env file safely
load_dotenv()

# Central PAPER/LIVE gate — the single choke point for real orders.
from trading_mode import is_live, TRADING_MODE

logger = logging.getLogger("DhanBroker")

class DhanBroker:
    def __init__(self, client_id, access_token, redis_client=None):
        self.client_id = client_id
        self.access_token = access_token
        self.dhan = dhanhq(client_id, access_token)
        self.instrument_master_path = "api-scrip-master.csv"
        self.df_master = None
        self.redis = redis_client
        self._oc_cache = {} # Local fallback cache

        # Auto-load the master list when the broker boots up
        self._load_instrument_master()

    def _load_instrument_master(self):
        """Downloads today's live Security ID list directly from Dhan's servers."""
        logger.info("Connecting to Exchange Data...")
        url = "https://images.dhan.co/api-data/api-scrip-master.csv"
        
        try:
            # Check if file exists and is from today
            import time
            file_exists = os.path.exists(self.instrument_master_path)
            if file_exists:
                file_age = time.time() - os.path.getmtime(self.instrument_master_path)
                if file_age > 86400: # Older than 24 hours
                    file_exists = False
            
            if not file_exists:
                self._download_instrument_master(url)

            # Load into a lightning-fast Pandas DataFrame
            self.df_master = pd.read_csv(self.instrument_master_path, low_memory=False)
            logger.info("Instrument Master Loaded Successfully!")
            
        except Exception as e:
            logger.error(f"CRITICAL ERROR: Could not load Instrument Master. {e}")

    # All six microservices boot within ~2s of each other and every one of them
    # races for this 25MB file. A bare requests.get() with no timeout means a
    # stalled transfer wedges the service forever, at import time, before its
    # logging is configured -- so it hangs silently with no trace anywhere.
    _MASTER_TIMEOUT = (10, 90)  # (connect, read) seconds
    _MASTER_RETRIES = 3

    def _download_instrument_master(self, url):
        """Fetches the scrip master with timeouts, retries and an atomic swap."""
        last_error = None

        for attempt in range(1, self._MASTER_RETRIES + 1):
            tmp_path = f"{self.instrument_master_path}.{os.getpid()}.tmp"
            try:
                logger.info(f"Downloading live Instrument Master CSV "
                            f"(attempt {attempt}/{self._MASTER_RETRIES})...")
                response = requests.get(url, timeout=self._MASTER_TIMEOUT)
                response.raise_for_status()
                if not response.content:
                    raise ValueError("empty response body")

                # Write to a per-process temp file and swap it in, so a sibling
                # service never reads a half-written CSV out from under us.
                with open(tmp_path, 'wb') as f:
                    f.write(response.content)
                for _ in range(5):
                    try:
                        os.replace(tmp_path, self.instrument_master_path)
                        break
                    except PermissionError:
                        # A sibling is mid-read of the target; give it a moment.
                        time.sleep(0.5)
                else:
                    raise PermissionError(
                        f"could not swap in {self.instrument_master_path}")

                logger.info("Instrument Master downloaded.")
                return

            except Exception as e:
                last_error = e
                logger.warning(f"Instrument Master download failed "
                               f"(attempt {attempt}/{self._MASTER_RETRIES}): {e}")
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                if attempt < self._MASTER_RETRIES:
                    time.sleep(2 * attempt)

        # A stale scrip master beats no scrip master: yesterday's contracts are
        # mostly still valid, whereas df_master=None disables the whole service.
        if os.path.exists(self.instrument_master_path):
            logger.warning(f"Falling back to the stale Instrument Master on disk "
                           f"after {self._MASTER_RETRIES} failed downloads "
                           f"(last error: {last_error})")
            return

        raise RuntimeError(f"Could not download Instrument Master: {last_error}")

    def get_equity_security_id(self, trading_symbol):
        """Translates a human ticker into the NSE/BSE Security ID."""
        if self.df_master is None:
            return None
            
        # Strip trailing Yahoo Finance extensions
        clean_symbol = trading_symbol.replace(".NS", "").replace(".BO", "")
        
        # Dynamic Exchange Routing
        instrument_type = 'INDEX' if clean_symbol in ["NIFTY", "BANKNIFTY", "SENSEX"] else 'EQUITY'
        exchange_id = 'BSE' if clean_symbol == "SENSEX" else 'NSE'
        
        # Search the CSV for the exact match
        result = self.df_master[
            (self.df_master['SEM_TRADING_SYMBOL'] == clean_symbol) & 
            (self.df_master['SEM_EXM_EXCH_ID'] == exchange_id) &
            (self.df_master['SEM_INSTRUMENT_NAME'] == instrument_type)
        ]
        
        if not result.empty:
            return str(result.iloc[0]['SEM_SMST_SECURITY_ID'])
        else:
            logger.warning(f"Could not find Security ID for {clean_symbol} on {exchange_id}")
            return None

    @staticmethod
    def _underlying_ticker(underlying_security_id) -> str:
        """Maps a Dhan underlying security id to our canonical ticker."""
        return {"13": "NIFTY", "25": "BANKNIFTY", "1": "SENSEX"}.get(
            str(underlying_security_id), str(underlying_security_id)
        )

    @staticmethod
    def _extract_leg_premium(leg_data: dict) -> dict:
        """Normalizes a Dhan option-chain ce/pe payload into premium + greeks.

        Dhan v2 option_chain returns per leg: last_price, top_bid_price,
        top_ask_price, implied_volatility, oi, volume, greeks{delta,theta,gamma,vega}.
        All fields are read defensively — missing values become 0.0.
        """
        leg_data = leg_data or {}
        greeks = leg_data.get("greeks", {}) or {}

        def _f(*keys):
            for k in keys:
                v = leg_data.get(k)
                if v is not None:
                    try:
                        return float(v)
                    except (ValueError, TypeError):
                        continue
            return 0.0

        ltp = _f("last_price", "ltp")
        bid = _f("top_bid_price", "bid")
        ask = _f("top_ask_price", "ask")
        return {
            "ltp": ltp,
            "bid": bid,
            "ask": ask,
            # mid falls back to ltp if either side of the book is empty (illiquid strike)
            "mid": round((bid + ask) / 2.0, 2) if (bid > 0 and ask > 0) else ltp,
            "iv": _f("implied_volatility", "iv"),
            "oi": _f("oi"),
            "volume": _f("volume"),
            "delta": float(greeks.get("delta", 0.0) or 0.0),
            "theta": float(greeks.get("theta", 0.0) or 0.0),
            "gamma": float(greeks.get("gamma", 0.0) or 0.0),
            "vega": float(greeks.get("vega", 0.0) or 0.0),
        }

    async def _select_trading_expiry(self, underlying_security_id, dhan_segment):
        """Fetch Dhan's expiry list and pick the expiry we ENTER on.

        Ladder mode trades the 30-45 DTE expiry, not the nearest weekly — the
        whole chain (premiums, strikes, IV) must come from the expiry the ladder
        actually sells. Returns the expiry string, or None on API failure.
        """
        try:
            exp_response = await asyncio.to_thread(
                self.dhan.expiry_list,
                under_security_id=int(underlying_security_id),
                under_exchange_segment=dhan_segment
            )

            if not (isinstance(exp_response, dict) and 'data' in exp_response):
                logger.error(f"Dhan API Rejected Request. Raw Server Response: {exp_response}")
                return None

            inner_payload = exp_response['data']

            # Check for Rate Limiting in Expiry List
            if isinstance(inner_payload, dict) and '805' in str(inner_payload):
                logger.error(f"🛑 DHAN RATE LIMIT (Expiry): Too many requests for {underlying_security_id}")
                return None

            # Unpack the double 'data' folder
            if isinstance(inner_payload, dict) and 'data' in inner_payload:
                exp_list = inner_payload['data']
            else:
                exp_list = inner_payload

            if not exp_list or not isinstance(exp_list, list) or len(exp_list) == 0:
                logger.warning(f"No active expiries found for {underlying_security_id}. Dhan returned: {exp_response}")
                return None

            logger.info(f"   ↳ Found {len(exp_list)} expiries: {exp_list[:3]}...")
            # Ensure expiries are sorted by date
            try:
                exp_list.sort()
            except Exception:
                pass

            nearest_expiry = exp_list[0]
            from trading_mode import ladder_enabled, select_ladder_expiry
            if ladder_enabled():
                _ticker = self._underlying_ticker(underlying_security_id)
                _q = await self._chain_quality_scores(_ticker)
                _sel, _dte, _why = select_ladder_expiry(exp_list, quality=_q)
                if _sel is not None:
                    nearest_expiry = _sel
                _bad = sorted(k for k, v in _q.items()
                              if v < self.MIN_CHAIN_QUALITY_PCT)
                if _bad:
                    logger.info(f"   ↳ LADDER skipping unpriceable expiries: {_bad}")
                if _why == "in_window":
                    logger.info(f"   ↳ LADDER expiry selected: {nearest_expiry} "
                                f"({_dte} DTE, {_q.get(str(nearest_expiry)[:10], '?')}% priceable)")
                elif str(_why).endswith("_degraded"):
                    logger.error(f"   ↳ LADDER: NO tradeable expiry — every candidate "
                                 f"is below {self.MIN_CHAIN_QUALITY_PCT}% priceable. "
                                 f"Using {nearest_expiry} ({_dte} DTE); entries will "
                                 f"be refused by the live-pricing guard until a "
                                 f"quotable expiry appears or the DTE window moves.")
                elif _why == "closest_holdable":
                    logger.warning(f"   ↳ LADDER: no 30-45 DTE expiry available; using closest "
                                   f"holdable {nearest_expiry} ({_dte} DTE)")
                else:
                    logger.warning(f"   ↳ LADDER: no holdable expiry; keeping nearest {nearest_expiry}")
            else:
                logger.info(f"   ↳ Locked onto nearest expiry: {nearest_expiry}")
            return nearest_expiry

        except Exception as e:
            logger.error(f"Expiry API Exception: {e}")
            return None

    # Near-spot priceable share below which an expiry is treated as untradeable
    # by the entry selector. A liquid monthly sits at ~100%; the 2026-09-08
    # weekly that blocked every trade on 2026-08-07 ran 50-57%.
    MIN_CHAIN_QUALITY_PCT = 80.0

    async def _log_chain_quality(self, ticker, expiry, payload, spot):
        """Log how much of a published chain is actually priceable, and record it.

        Near-spot coverage is the number that matters — a liquid monthly quotes
        100% of the strikes we trade, while a freshly-listed weekly can drop a
        third of them behind stub markets like bid 71.90 / ask 189.10.

        The score is written to `chain_quality:{ticker}` so expiry selection can
        avoid an expiry the feed has already shown it cannot price, instead of
        re-picking it every cycle and refusing every trade downstream.
        """
        try:
            from backend.app.services.options_pricing_service import (
                arbitrage_violations, spread_is_tradeable)
            strikes = payload.get("strikes") or {}
            viol = arbitrage_violations(payload)
            near_ok = near_tot = 0
            for k, node in strikes.items():
                try:
                    if abs(float(k) - float(spot or 0)) > 1000:
                        continue
                except (TypeError, ValueError):
                    continue
                for typ in ("ce", "pe"):
                    q = (node or {}).get(typ) or {}
                    if float(q.get("bid") or 0) <= 0 and float(q.get("ask") or 0) <= 0:
                        continue
                    near_tot += 1
                    near_ok += bool(spread_is_tradeable(q.get("bid"), q.get("ask")))
            n_viol = len(viol["ce"]) + len(viol["pe"])
            pct = (100.0 * near_ok / near_tot) if near_tot else 0.0
            msg = (f"   ↳ chain quality {ticker} {expiry}: near-spot priceable "
                   f"{near_ok}/{near_tot} ({pct:.0f}%), {n_viol} arb-flagged strikes")
            if pct < self.MIN_CHAIN_QUALITY_PCT or n_viol > 0:
                logger.warning(msg)
            else:
                logger.info(msg)

            # Only a chain with enough near-spot strikes to judge is worth
            # recording — a handful of quotes says nothing about liquidity.
            if self.redis and near_tot >= 10:
                try:
                    raw = await self.redis.get(f"chain_quality:{ticker}")
                    scores = json.loads(raw) if raw else {}
                except (ValueError, TypeError, AttributeError):
                    scores = {}
                scores[str(expiry)[:10]] = {"pct": round(pct, 1),
                                            "arb": n_viol,
                                            "ts": time.time()}
                # Bound the map and let it lapse, so a stale reading can never
                # keep an expiry blacklisted after its book fills out.
                fresh = {k: v for k, v in scores.items()
                         if time.time() - float(v.get("ts", 0)) < 86400}
                await self.redis.set(f"chain_quality:{ticker}",
                                     json.dumps(fresh), ex=86400)
        except Exception as e:
            logger.debug(f"chain quality check failed: {e}")

    async def _chain_quality_scores(self, ticker):
        """Recent near-spot priceable percent per expiry, for entry selection.

        Failures here are non-fatal: an empty map just means selection falls
        back to its DTE ordering and measures as it goes.
        """
        try:
            raw = await self.redis.get(f"chain_quality:{ticker}")
            scores = json.loads(raw) if raw else {}
            return {k: float(v.get("pct", 100.0)) for k, v in scores.items()}
        except Exception as e:
            logger.debug(f"chain quality read failed for {ticker}: {e}")
            return {}

    async def _publish_held_expiry_chains(self, underlying_security_id, segment, primary_expiry):
        """Top up per-expiry premium maps for expiries we still hold.

        The entry selector moves as positions age (Aug-25 -> Sep-01 on
        2026-07-28), so the primary chain stops covering older open positions.
        `held_expiries:{ticker}` is published by the execution service; anything
        in it other than the primary gets its own chain fetched here so
        mark-to-market always has the real contract to price against.
        """
        ticker = self._underlying_ticker(underlying_security_id)
        try:
            raw = await self.redis.get(f"held_expiries:{ticker}")
            held = json.loads(raw) if raw else []
        except Exception as e:
            logger.debug(f"held expiry read failed for {ticker}: {e}")
            return

        wanted = {str(e)[:10] for e in (held or []) if e} - {str(primary_expiry)[:10]}
        for exp in sorted(wanted):
            try:
                spot, df = await self.get_clean_option_chain(
                    underlying_security_id, segment, expiry=exp)
                if df is None or df.empty:
                    logger.warning(f"   ↳ HELD expiry {exp}: no chain returned — "
                                   f"positions on it cannot be marked this cycle")
                else:
                    logger.info(f"   ↳ HELD expiry {exp}: chain published ({len(df)} strikes)")
            except Exception as e:
                logger.warning(f"   ↳ HELD expiry {exp} fetch failed: {e}")

    async def get_clean_option_chain(self, underlying_security_id, segment, expiry=None):
        """
        Uses Dhan's Expiry List API to find the exact target date,
        then fetches the Option Chain for live OI data.

        expiry=None (default) selects the trading expiry (ladder window or
        nearest), publishes it as the PRIMARY `option_premiums:{ticker}` map and
        then tops up chains for any expiry we still hold. Passing an explicit
        expiry fetches exactly that one and publishes only its per-expiry map —
        held positions must be marked against their OWN contracts, never against
        whatever expiry the entry selector happens to be pointing at today.
        """
        explicit_expiry = expiry is not None

        # --- CACHE CHECK (REDIS or LOCAL) ---
        cache_key = f"oc_cache:{underlying_security_id}"
        if explicit_expiry:
            pass  # oc_cache is keyed by underlying only, so it cannot answer
                  # "give me THIS expiry" — go straight to the API.
        elif self.redis:
            try:
                cached_data = await self.redis.get(cache_key)
                if cached_data:
                    data = json.loads(cached_data)
                    # Convert list of dicts back to DataFrame
                    df = pd.DataFrame(data['chain'])
                    return data['spot'], df
            except Exception as re:
                logger.warning(f"Redis cache read error: {re}")
        else:
            now = time.time()
            if underlying_security_id in self._oc_cache:
                ts, spot, df = self._oc_cache[underlying_security_id]
                if now - ts < 60:
                    return spot, df

        logger.info(f"Requesting Option Chain (Underlying ID: {underlying_security_id})...")
        
        # SENSEX (ID 1) is on the BSE exchange. NIFTY/BANKNIFTY are on NSE (IDX_I).
        if str(underlying_security_id) == "1":
            dhan_segment = "BSE_IDX"
        elif segment == "OPTIDX":
            dhan_segment = "IDX_I"
        else:
            dhan_segment = "NSE_EQ"
            
        # 1. Which expiry? An explicit one skips the selector entirely (and its
        #    expiry-list API call) — the caller is asking for a specific contract.
        if explicit_expiry:
            nearest_expiry = str(expiry)[:10]
        else:
            nearest_expiry = await self._select_trading_expiry(
                underlying_security_id, dhan_segment)
            if nearest_expiry is None:
                return None, None
            
        # 2. Fetch the actual option chain using the exact date
        try:
            response = await asyncio.to_thread(
                self.dhan.option_chain,
                under_security_id=int(underlying_security_id), 
                under_exchange_segment=dhan_segment, 
                expiry=nearest_expiry
            )

            # Check for Rate Limiting or API Errors
            if isinstance(response, dict) and (response.get('status') == 'failure' or response.get('status') == 'failed'):
                remarks = response.get('remarks', {})
                error_msg = ""
                if isinstance(remarks, dict):
                    error_msg = remarks.get('error_message', '')

                # Double nest check for rate limit
                data_part = response.get('data', {})
                if isinstance(data_part, dict) and '805' in str(data_part):
                    logger.error(f"🛑 DHAN RATE LIMIT: Too many requests for {underlying_security_id}")
                    return None, None

                logger.warning(f"Dhan API Error for {underlying_security_id}: {error_msg or response}")
                return None, None

            # Recurse and extract strike prices
            def find_strikes(data_dict):
                if not isinstance(data_dict, dict): return None
                
                # Check if this layer contains strike prices (keys that are numbers)
                for k, v in data_dict.items():
                    try:
                        float(k) # Can the key be a number?
                        if isinstance(v, dict) and (('ce' in v and v['ce']) or ('pe' in v and v['pe'])):
                            return data_dict 
                    except ValueError:
                        pass
                
                # If not found, dig one layer deeper into every folder
                for v in data_dict.values():
                    if isinstance(v, dict):
                        found = find_strikes(v)
                        if found: return found
                return None
                
            raw_oc = find_strikes(response)
            
            if raw_oc and len(raw_oc) > 0:
                chain_data = []
                premium_strikes = {}  # {strike: {"ce": {...}, "pe": {...}}} — real premiums
                for strike_str, data in raw_oc.items():
                    try:
                        strike = float(strike_str)
                    except ValueError:
                        continue

                    ce_data = data.get('ce', {}) or {}
                    pe_data = data.get('pe', {}) or {}

                    call_coi = ce_data.get('oi', 0) - ce_data.get('previous_oi', 0)
                    put_coi = pe_data.get('oi', 0) - pe_data.get('previous_oi', 0)

                    ce_leg = self._extract_leg_premium(ce_data)
                    pe_leg = self._extract_leg_premium(pe_data)
                    premium_strikes[f"{strike:.2f}"] = {"ce": ce_leg, "pe": pe_leg}

                    chain_data.append({
                        'Strike': strike,
                        'Call_COI': call_coi,
                        'Put_COI': put_coi,
                        # Raw open interest — PCR must use OI, not change-in-OI (COI
                        # ratios explode when one side's OI change ≈ 0). Matches the
                        # validated backtest's chain_pcr (Put OI / Call OI).
                        'Call_OI': ce_data.get('oi', 0) or 0,
                        'Put_OI': pe_data.get('oi', 0) or 0,
                        # Real premiums preserved alongside OI (additive — existing
                        # consumers that read only Strike/Call_COI/Put_COI are unaffected)
                        'Call_LTP': ce_leg['ltp'], 'Call_Bid': ce_leg['bid'], 'Call_Ask': ce_leg['ask'], 'Call_IV': ce_leg['iv'],
                        'Put_LTP': pe_leg['ltp'], 'Put_Bid': pe_leg['bid'], 'Put_Ask': pe_leg['ask'], 'Put_IV': pe_leg['iv'],
                    })

                if chain_data:
                    df_chain = pd.DataFrame(chain_data)
                    df_chain = df_chain.sort_values(by='Strike').reset_index(drop=True)
                    logger.info(f"   ↳ Option Chain parsing successful! ({len(df_chain)} strikes parsed)")
                    
                    # Try to extract the actual spot price from the response payload
                    spot_price = 0.0
                    try:
                        # 1. Try to find in the 'data' header (Dhan v2 double nest)
                        if isinstance(response, dict) and 'data' in response:
                            inner = response['data']
                            if isinstance(inner, dict) and 'data' in inner:
                                spot_price = float(inner['data'].get('last_price', 0))
                            else:
                                spot_price = float(inner.get('last_price', 0))
                        
                        # 2. Fallback: If still 0, try to fetch it via a separate quote call
                        if spot_price == 0:
                            quote = await asyncio.to_thread(
                                self.dhan.get_quote_data,
                                str(underlying_security_id), 
                                "IDX_I" if str(underlying_security_id) in ["13", "25"] else "NSE_EQ"
                            )
                            if quote and 'data' in quote:
                                spot_price = float(quote['data'].get('last_price', 0))
                    except Exception as se:
                        logger.warning(f"Could not extract spot price: {se}")
                        
                    # --- UPDATE CACHE (REDIS or LOCAL) ---
                    if self.redis:
                        try:
                            # Publish the real per-strike premium map for the pricing service.
                            ticker = self._underlying_ticker(underlying_security_id)
                            premium_payload = {
                                "underlying": ticker,
                                "underlying_security_id": str(underlying_security_id),
                                "spot": spot_price,
                                "expiry": nearest_expiry,
                                "segment": dhan_segment,
                                "timestamp": time.time(),
                                "source": "DHAN_LIVE",
                                "strikes": premium_strikes,
                            }
                            # Chain health, logged so a junk feed is visible
                            # rather than silently priced off. Enforcement is at
                            # point of use (options_pricing_service refuses the
                            # bad strikes) — publishing the whole chain still
                            # matters because the OI/GEX/PCR analytics need it.
                            await self._log_chain_quality(ticker, nearest_expiry,
                                                          premium_payload, spot_price)

                            # Per-expiry map: the ONLY safe thing to mark a held
                            # position against, since it is addressed by the
                            # contract's own expiry rather than by "whatever is
                            # current". Always written, for every expiry fetched.
                            await self.redis.set(
                                f"option_premiums:{ticker}:{str(nearest_expiry)[:10]}",
                                json.dumps(premium_payload), ex=90
                            )

                            # The primary key + the expiry-agnostic oc_cache stay
                            # the ENTRY/analytics view and are only refreshed by
                            # the primary call, never by a held-expiry top-up.
                            if not explicit_expiry:
                                cache_payload = {
                                    "spot": spot_price,
                                    "chain": df_chain.to_dict(orient='records')
                                }
                                await self.redis.set(cache_key, json.dumps(cache_payload), ex=60)
                                await self.redis.set(
                                    f"option_premiums:{ticker}", json.dumps(premium_payload), ex=90
                                )
                        except Exception as re:
                            logger.warning(f"Redis cache write error: {re}")
                    elif not explicit_expiry:
                        self._oc_cache[underlying_security_id] = (time.time(), spot_price, df_chain)

                    # Top up chains for expiries we hold but no longer enter on.
                    # Guarded by explicit_expiry so a top-up never recurses.
                    if self.redis and not explicit_expiry:
                        await self._publish_held_expiry_chains(
                            underlying_security_id, segment, nearest_expiry)

                    return spot_price, df_chain
                    
            logger.warning(f"Option chain format unrecognizable or market data empty for {underlying_security_id}. Segment: {dhan_segment}, Expiry: {nearest_expiry}")
            return None, None
                    
        except Exception as e:
            logger.error(f"Error parsing option chain payload: {e}")
            
        return None, None

    async def ping_dhan_servers(self):
        """Pings the Dhan servers to ensure your API keys are valid."""
        try:
            funds = await asyncio.to_thread(self.dhan.get_fund_limits)
            if 'data' in funds:
                available_margin = funds['data'].get('availabelBalance', 0)
                logger.info(f"API Connection SUCCESSFUL! Available Margin: INR {available_margin}")
                return True
            else:
                logger.error(f"API Connection FAILED. Check your Client ID and Access Token.")
                return False
        except Exception as e:
            logger.error(f"API CRASH: {e}")
            return False

    async def get_historical_intraday(self, symbol, security_id, exchange_segment, from_date, to_date):
        """
        Fetches 1-minute historical data from Dhan.
        from_date/to_date format: YYYY-MM-DD
        """
        logger.info(f"DEBUG: get_historical_intraday called for {symbol} (SecID: {security_id})")
        logger.info(f"🚜 Harvesting Historical Data for {symbol} ({from_date} to {to_date})...")
        try:
            # Standardize index names for Dhan API
            is_index = symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "NSEI", "NSEBANK"]
            inst_type = 'INDEX' if is_index else 'EQUITY'
            
            data = await asyncio.wait_for(
                asyncio.to_thread(
                    self.dhan.intraday_minute_data,
                    security_id=str(security_id),
                    exchange_segment=exchange_segment,
                    instrument_type=inst_type,
                    from_date=from_date,
                    to_date=to_date
                ),
                timeout=30.0
            )
            
            if data.get('status') == 'success':
                res = data.get('data', [])
                count = 0
                if isinstance(res, dict) and "open" in res:
                    count = len(res['open'])
                elif isinstance(res, list):
                    count = len(res)
                logger.info(f"   ↳ Successfully retrieved {count} historical records.")
                return res
            else:
                logger.error(f"❌ Dhan Historical API Error: {data.get('remarks')}")
                return []
        except Exception as e:
            import traceback
            logger.error(f"❌ Historical Harvesting Failed for {symbol}: {e}")
            logger.error(traceback.format_exc())
            return []

    # --- ORDER MANAGEMENT METHODS ---

    async def place_order(self, security_id, exchange_segment, side, order_type, quantity, price=0.0):
        """Places a live order on the exchange.

        PAPER/LIVE gate: unless TRADING_MODE=LIVE, the order is intercepted here
        (the single point where an order can leave for Dhan) and a synthetic
        paper order id is returned. This protects against the delta hedger /
        RL-OMS firing real futures/options orders against simulated positions.
        """
        if not is_live():
            paper_id = f"PAPER-{str(side).upper()}-{security_id}-{uuid.uuid4().hex[:8]}"
            logger.warning(
                f"🧻 [PAPER MODE] Order NOT sent to broker: {order_type} {side} "
                f"{quantity} units (SecID: {security_id}, seg: {exchange_segment}, "
                f"px: {price}). Simulated id: {paper_id}"
            )
            return paper_id

        logger.info(f"📤 [LIVE] Placing {order_type} {side} order for {quantity} units (SecID: {security_id})")
        try:
            # Side mapping: BUY -> self.dhan.BUY, SELL -> self.dhan.SELL
            dhan_side = self.dhan.BUY if side.upper() == "BUY" else self.dhan.SELL
            
            # Order type mapping
            dhan_type = self.dhan.LIMIT if order_type.upper() == "LIMIT" else self.dhan.MARKET
            
            response = await asyncio.to_thread(
                self.dhan.place_order,
                security_id=str(security_id),
                exchange_segment=exchange_segment,
                transaction_type=dhan_side,
                order_type=dhan_type,
                quantity=int(quantity),
                price=float(price),
                product_type=self.dhan.INTRA, # Default to intraday
                validity='DAY'
            )
            
            if response.get('status') == 'success':
                order_id = response.get('data', {}).get('orderId')
                logger.info(f"   ✅ Order Placed! ID: {order_id}")
                return order_id
            else:
                logger.error(f"   ❌ Order Failed: {response.get('remarks')}")
                return None
        except Exception as e:
            logger.error(f"   ❌ API Exception during order placement: {e}")
            return None

    async def modify_order(self, order_id, quantity, price):
        """Modifies an existing pending order."""
        logger.info(f"🔄 Modifying Order {order_id} -> Price: {price}")
        try:
            response = await asyncio.to_thread(
                self.dhan.modify_order,
                order_id=str(order_id),
                order_type=self.dhan.LIMIT,
                leg_name='ENTRY_LEG',
                quantity=int(quantity),
                price=float(price),
                disclosed_quantity=0,
                trigger_price=0,
                validity='DAY'
            )
            return response.get('status') == 'success'
        except Exception as e:
            logger.error(f"   ❌ API Exception during order modification: {e}")
            return False

    async def cancel_order(self, order_id):
        """Cancels a pending order."""
        logger.info(f"🚫 Cancelling Order {order_id}")
        try:
            response = await asyncio.to_thread(self.dhan.cancel_order, order_id=str(order_id))
            return response.get('status') == 'success'
        except Exception as e:
            logger.error(f"   ❌ API Exception during order cancellation: {e}")
            return False

    async def get_order_status(self, order_id):
        """Checks the current status of an order."""
        try:
            response = await asyncio.to_thread(self.dhan.get_order_by_id, order_id=str(order_id))
            if response.get('status') == 'success':
                data = response.get('data', {})
                return data.get('orderStatus') # e.g., 'TRADED', 'PENDING', 'CANCELLED'
            return 'UNKNOWN'
        except Exception as e:
            logger.error(f"   ❌ API Exception checking order status: {e}")
            return 'ERROR'

    async def get_order_details(self, order_id):
        """Returns {'status': ..., 'avg_price': float, 'filled_qty': int} for an
        order, or None on API failure. Used by the order router to capture real
        fill prices for the audit trail."""
        try:
            response = await asyncio.to_thread(self.dhan.get_order_by_id, order_id=str(order_id))
            if isinstance(response, dict) and response.get('status') == 'success':
                data = response.get('data', {}) or {}
                # Dhan sometimes nests order data in a list
                if isinstance(data, list):
                    data = data[0] if data else {}
                return {
                    "status": data.get('orderStatus', 'UNKNOWN'),
                    "avg_price": float(data.get('averageTradedPrice', 0) or 0),
                    "filled_qty": int(data.get('filledQty', 0) or 0),
                    "reason": data.get('omsErrorDescription') or data.get('remarks') or "",
                }
            return None
        except Exception as e:
            logger.error(f"   ❌ API Exception fetching order details: {e}")
            return None

    async def get_positions(self):
        """Fetches current broker positions (LIVE truth for reconciliation).

        Returns a list of position dicts (Dhan schema: securityId, netQty,
        exchangeSegment, tradingSymbol, ...), or None on API failure — callers
        must distinguish 'no positions' ([]) from 'could not fetch' (None).
        """
        try:
            response = await asyncio.to_thread(self.dhan.get_positions)
            if isinstance(response, dict) and response.get('status') == 'success':
                data = response.get('data') or []
                return data if isinstance(data, list) else []
            logger.error(f"   ❌ get_positions failed: {response.get('remarks') if isinstance(response, dict) else response}")
            return None
        except Exception as e:
            logger.error(f"   ❌ API Exception fetching positions: {e}")
            return None

    async def get_market_depth(self, security_id, exchange_segment):
        """Fetches Best Bid and Best Ask using Level 2 depth."""
        try:
            seg = str(exchange_segment).upper()
            if "FNO" in seg or seg == "NFO":
                api_segment = "NSE_FNO"
            elif "EQ" in seg or seg == "NSE":
                api_segment = "NSE_EQ"
            elif "IDX" in seg:
                api_segment = "IDX_I"
            else:
                api_segment = exchange_segment
                
            quote = await asyncio.to_thread(
                self.dhan.quote_data,
                {api_segment: [int(security_id)]}
            )
            
            if quote and (quote.get('status') == 'success' or quote.get('remarks') == 'Success'):
                data = quote.get('data', {}).get('data', {}).get(api_segment, {}).get(str(security_id), {})
                if data:
                    buy_list = data.get('depth', {}).get('buy', []) or []
                    sell_list = data.get('depth', {}).get('sell', []) or []
                    best_bid = float(buy_list[0].get('price', 0)) if buy_list else 0.0
                    best_ask = float(sell_list[0].get('price', 0)) if sell_list else 0.0
                    
                    return {
                        "bid": best_bid or float(data.get('bestBidPrice', 0)),
                        "ask": best_ask or float(data.get('bestAskPrice', 0)),
                        "ltp": float(data.get('last_price', 0))
                    }
            return None
        except Exception as e:
            logger.error(f"   ❌ API Exception fetching depth: {e}")
            return None

    def get_futures_security_id(self, underlying_symbol: str) -> str:
        """Finds the nearest expiring Futures contract Security ID for the underlying index."""
        if self.df_master is None:
            return None
        
        clean_symbol = underlying_symbol.replace(".NS", "").replace(".BO", "").replace("^", "")
        # Standardize NIFTY/BANKNIFTY names
        if clean_symbol == "NSEI": clean_symbol = "NIFTY"
        elif clean_symbol == "NSEBANK": clean_symbol = "BANKNIFTY"
        
        instrument_type = 'FUTIDX' if clean_symbol in ["NIFTY", "BANKNIFTY", "SENSEX"] else 'FUTSTK'
        
        try:
            results = self.df_master[
                (self.df_master['SEM_INSTRUMENT_NAME'] == instrument_type) &
                (self.df_master['SEM_EXM_EXCH_ID'] == 'NSE') &
                (self.df_master['SEM_TRADING_SYMBOL'].str.startswith(clean_symbol))
            ]
            
            if not results.empty:
                # Sort by expiry to get the nearest month contract
                sorted_results = results.sort_values(by='SEM_EXPIRY_DATE')
                return str(sorted_results.iloc[0]['SEM_SMST_SECURITY_ID'])
        except Exception as e:
            logger.error(f"Error resolving futures security ID: {e}")
        return None

# ==========================================
# DIAGNOSTIC TEST
# ==========================================
if __name__ == "__main__":
    TEST_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
    TEST_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN")
    
    if not TEST_CLIENT_ID or not TEST_ACCESS_TOKEN:
        print("CRITICAL: Missing Dhan API keys in .env file!")
        exit()
        
    broker = DhanBroker(TEST_CLIENT_ID, TEST_ACCESS_TOKEN)
    print("\n--- Running System Diagnostics ---")
    broker.ping_dhan_servers()
    
    test_ticker = "SENSEX"
    sec_id = broker.get_equity_security_id(test_ticker)
    print(f"The Exchange ID for {test_ticker} is: {sec_id}")
    print("----------------------------------\n")

    def get_true_futures_vwap(futures_security_id):
        """
        Fetches the true volume-weighted average price (VWAP) 
        from the current month's futures contract via Dhanhq.
        """
        try:
            quote = dhan.get_market_quote(
                security_id=str(futures_security_id),
                exchange_segment=dhan.FNO,
                instrument_type="FUTIDX" 
            )
            
            if quote.get('status') == 'success' or quote.get('remarks') == 'Success':
                data = quote.get('data', {})
                true_vwap = data.get('vwap') or data.get('averagePrice')
                if true_vwap:
                    return float(true_vwap)
                    
            return None
            
        except Exception as e:
            print(f"Dhan API Error fetching Futures VWAP: {e}")
            return None