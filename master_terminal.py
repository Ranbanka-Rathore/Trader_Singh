import time
import json
import os
from datetime import datetime, timedelta
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from database_manager import db_manager, MarketIndicator, OpenPosition, Trade, SignalAudit

# --- NEON CYBERPUNK THEME COLORS ---
THEME = {
    "bg": "#050505",
    "card": "#0a0a0a",
    "border": "#1a1a1a",
    "text": "#e0e0e0",
    "accent": "#00f3ff", # Neon Cyan
    "success": "#39ff14", # Neon Green
    "warning": "#ffe014", # Neon Gold
    "danger": "#ff007f",  # Neon Pink
    "delta": "#00ff9f",
    "gamma": "#bc8cff",
    "theta": "#e3b341"
}

def load_v2_autotrender_data(timeframe):
    """Reads the institutional metrics from the Database."""
    try:
        query = (MarketIndicator
                 .select()
                 .where(MarketIndicator.ticker == 'NIFTY')
                 .order_by(MarketIndicator.timestamp.desc())
                 .limit(100))
        
        data = []
        for row in query:
            data.append({
                "Time": row.timestamp.strftime("%H:%M"),
                "Price": float(row.price) if row.price else 0.0,
                "VWAP": float(row.vwap) if row.vwap else 0.0,
                "PCR": float(row.pcr) if row.pcr else 0.0,
                "GEX (Mn)": float(row.total_gex or 0.0) / 1000000,
                "POC": float(row.poc or 0.0)
            })
        
        return pd.DataFrame(data)
    except Exception:
        return pd.DataFrame()

def get_pcr_signal(pcr):
    if pcr <= 0.85: return "SELL"
    elif pcr >= 1.15: return "BUY"
    return "WAIT"
    
def get_vwap_signal(p, v):
    if p < v: return "SELL"
    elif p > v: return "BUY"
    return "WAIT"

# --- 1. IMPORT YOUR CUSTOM MODULES ---
from quant_engine import QuantEngine
from options_desk import OptionsDesk
from risk_committee import RiskCommittee
from ai_market_analyst import AIMarketAnalyst
from paper_broker import PaperBroker

st.set_page_config(page_title="AGENTIC TRADER | NEON TERMINAL", layout="wide")

# --- FUTURISTIC CUSTOM CSS ---
st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&display=swap');
    
    html, body, [class*="css"] {{
        font-family: 'JetBrains+Mono', monospace;
        background-color: {THEME["bg"]};
        color: {THEME["text"]};
    }}

    .stApp {{ background-color: {THEME["bg"]}; }}
    
    /* Neon Glow Containers */
    div[data-testid="metric-container"] {{
        background-color: {THEME["card"]};
        border: 1px solid {THEME["border"]};
        padding: 20px;
        border-radius: 4px;
        box-shadow: 0 0 10px rgba(0, 243, 255, 0.05);
        transition: all 0.3s ease;
    }}
    div[data-testid="metric-container"]:hover {{
        border-color: {THEME["accent"]};
        box-shadow: 0 0 15px rgba(0, 243, 255, 0.2);
    }}

    /* Tabs Neon Styling */
    .stTabs [data-baseweb="tab-list"] {{
        background-color: transparent;
        gap: 15px;
    }}
    .stTabs [data-baseweb="tab"] {{
        background-color: transparent;
        border-bottom: 2px solid {THEME["border"]};
        padding: 10px 5px;
        color: #666;
        text-transform: uppercase;
        letter-spacing: 2px;
        font-size: 13px;
    }}
    .stTabs [aria-selected="true"] {{
        border-bottom: 2px solid {THEME["accent"]} !important;
        color: {THEME["accent"]} !important;
        text-shadow: 0 0 8px {THEME["accent"]};
    }}

    /* Futuristic Headlines */
    h1, h2, h3 {{
        text-transform: uppercase;
        letter-spacing: 4px;
        color: {THEME["accent"]};
        text-shadow: 0 0 10px rgba(0, 243, 255, 0.5);
    }}

    /* Dataframe Styling */
    .stDataFrame {{
        border: 1px solid {THEME["border"]};
        border-radius: 4px;
    }}

    /* Sidebar Styling */
    section[data-testid="stSidebar"] {{
        background-color: #0a0a0a;
        border-right: 1px solid #1a1a1a;
    }}

    /* Sidebar Radio Styling */
    .stRadio > div {{
        gap: 8px;
    }}

    .stRadio label {{
        background-color: transparent !important;
        border: 1px solid #1a1a1a !important;
        padding: 12px 15px !important;
        border-radius: 4px !important;
        color: #888 !important;
        transition: all 0.3s ease !important;
        cursor: pointer !important;
        display: block !important;
        width: 100% !important;
        text-transform: uppercase !important;
        letter-spacing: 1px !important;
        font-size: 11px !important;
    }}

    .stRadio label:hover {{
        border-color: #00f3ff !important;
        color: #00f3ff !important;
        box-shadow: 0 0 10px rgba(0, 243, 255, 0.1);
    }}

    /* Target the selected radio label */
    .stRadio div[data-testid="stMarkdownContainer"] p {{
        margin-bottom: 0;
    }}

    #MainMenu {{visibility: hidden;}}
    footer {{visibility: hidden;}}
    </style>
    """, unsafe_allow_html=True)


# --- 2. INITIALIZE MEMORY ---
db_manager.connect()

engine = QuantEngine()
desk = OptionsDesk()
risk_committee = RiskCommittee()
analyst_agent = MarketAnalyst()
paper_broker = PaperBroker()

universe = {
    "backup_stocks": ["^NSEI", "^NSEBANK", "^BSESN", "RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS"]
}

# --- 3. TOP NAVIGATION / STATUS ---
with st.container():
    col_nav1, col_nav2, col_nav3 = st.columns([3, 1, 1])
    with col_nav1:
        st.markdown(f"<h1>⚡ NEON TERMINAL v6.0</h1>", unsafe_allow_html=True)
        st.markdown(f"<p style='color: {THEME['success']}; font-size: 10px;'>● CORE SYSTEM ONLINE | MULTI-AGENT SWARM ACTIVE</p>", unsafe_allow_html=True)
    
    with col_nav2:
        trading_mode = st.radio("NETWORK", ["📝 PAPER", "🔴 LIVE"], horizontal=True, label_visibility="collapsed")
    
    with col_nav3:
        autopilot = st.toggle("🤖 AUTOPILOT", value=False)
        status_color = THEME["success"] if autopilot else THEME["danger"]
        st.markdown(f"<div style='text-align: right; color: {status_color}; font-weight: bold;'>[{'ARMED' if autopilot else 'STANDBY'}]</div>", unsafe_allow_html=True)

st.markdown(f"<hr style='border: 0.5px solid {THEME['border']};'>", unsafe_allow_html=True)

# --- 4. MAIN INTERFACE ---
with st.sidebar:
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("### 🛠️ NAVIGATION")
    nav_selection = st.radio(
        "GO TO",
        ["📊 OPS CENTER", "📜 LEDGER", "🕵️ SIGNAL INTEL", "👁️ RADAR", "🧠 AI AUDIT"],
        label_visibility="collapsed"
    )
    
    st.markdown("<br><hr style='border: 0.5px solid #1a1a1a;'><br>", unsafe_allow_html=True)
    
    st.markdown("### 🛡️ RISK SHIELD")
    from risk_shield import RiskShield
    shield = RiskShield()
    st.caption(f"Max Daily Loss: ₹{shield.max_daily_loss:,.0f}")
    st.caption(f"Max Positions: {shield.max_open_positions}")
    st.caption(f"Entry Cooldown: {shield.min_trade_spacing_mins}m")
    
    st.markdown("<br><hr style='border: 0.5px solid #1a1a1a;'><br>", unsafe_allow_html=True)
    st.markdown("### ⚡ SYSTEM STATUS")
    st.markdown(f"**DB:** <span style='color:{THEME['success']}'>CONNECTED</span>", unsafe_allow_html=True)
    st.markdown(f"**WS:** <span style='color:{THEME['success']}'>STREAMING</span>", unsafe_allow_html=True)
    st.markdown(f"**AI:** <span style='color:{THEME['accent']}'>LLAMA 3.3 READY</span>", unsafe_allow_html=True)

if nav_selection == "📊 OPS CENTER":
    try:
        open_trades = list(OpenPosition.select().dicts())
        closed_trades = list(Trade.select().where(Trade.entry_date >= datetime.now().replace(hour=0, minute=0, second=0)).dicts())
        from risk_shield import RiskShield
        shield = RiskShield()
        portfolio_var = shield.calculate_portfolio_var(open_trades)
    except Exception as e:
        open_trades, closed_trades, portfolio_var = [], [], 0.0
        st.error(f"DB Error: {e}")

    u_pnl = sum([float(t.get('net_credit_per_share') or 0.0) * (int(t.get('lots_sized') or 1) * 25) for t in open_trades])
    r_pnl = sum([float(t.get('realized_pnl') or 0.0) * (int(t.get('lots_sized') or 1) * 25) for t in closed_trades])

    # NEON METRICS
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("REALIZED P&L", f"₹{r_pnl:,.0f}")
    m2.metric("FLOATING P&L", f"₹{u_pnl:,.0f}", delta=f"{u_pnl:,.0f}")
    m3.metric("VAR (95%)", f"₹{portfolio_var:,.0f}")
    m4.metric("VOL SESSION", len(open_trades) + len(closed_trades))
    m5.metric("ACTIVE NODES", len(open_trades))

    st.markdown("<br>", unsafe_allow_html=True)

    c1, c2 = st.columns([2, 1])
    
    with c1:
        st.markdown("### EQUITY CURVE")
        if closed_trades:
            equity = []
            curr = 0
            for i, t in enumerate(closed_trades):
                curr += float(t.get('realized_pnl') or 0.0) * (int(t.get('lots_sized') or 1) * 25)
                equity.append({"T": i+1, "P": curr})
            fig = px.area(pd.DataFrame(equity), x="T", y="P", template="plotly_dark", color_discrete_sequence=[THEME["accent"]])
            fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", margin=dict(l=0,r=0,t=20,b=0),
                              xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor=THEME["border"]))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Awaiting execution data...")

    with c2:
        st.markdown("### RISK GREEKS")
        if open_trades:
            g_vals = [sum([float(t.get(f'net_{g.lower()}') or 0.0) for t in open_trades]) for g in ["Delta", "Gamma", "Theta"]]
            fig_g = go.Figure(data=[go.Bar(x=["DELTA", "GAMMA", "THETA"], y=g_vals, marker_color=[THEME["delta"], THEME["gamma"], THEME["theta"]])])
            fig_g.update_layout(template="plotly_dark", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", margin=dict(l=0,r=0,t=20,b=0))
            st.plotly_chart(fig_g, use_container_width=True)
        else:
            st.caption("Zero delta exposure.")

    # Auto-Pilot Logic
    if autopilot:
        with open("heartbeat.txt", "w") as f: f.write(str(time.time()))
        try:
            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            today_trades_list = list(Trade.select().where(Trade.entry_date >= today_start).order_by(Trade.exit_date.desc()).dicts())
            daily_pnl = sum([float(t['realized_pnl'] or 0.0) * (int(t['lots_sized'] or 1) * 25) for t in today_trades_list])
            
            cons_losses = 0
            for t in today_trades_list:
                if float(t['realized_pnl'] or 0) < 0: cons_losses += 1
                else: break
            
            last_t = Trade.select().order_by(Trade.entry_date.desc()).first()
            radar_d = load_v2_autotrender_data(5)
            curr_gex = float(radar_d.iloc[0]['GEX (Mn)'] * 1000000) if not radar_d.empty else 0.0
            
            kill, reason = shield.check_kill_switches(daily_pnl, len(open_trades), curr_gex, last_t.entry_date if last_t else None, cons_losses)
            
            if kill:
                st.error(f"🚨 GATEKEEPER: {reason}")
                autopilot = False 
            else:
                now = datetime.now()
                if "PAPER" in trading_mode.upper(): paper_broker.evaluate_open_positions()
                
                if not (now.hour == 15 and now.minute >= 25) and now.weekday() < 5:
                    with st.spinner("PULSING MATRIX..."):
                        passed = engine.analyze_universe(universe)
                        structured = desk.process_approved_assets(passed)
                        if structured:
                            for t in structured:
                                for a in passed:
                                    if t['ticker'] == a['ticker']:
                                        t.update({'vol_surge_multiplier': a.get('vol_surge_multiplier'), 'coi_pcr': a.get('coi_pcr'), 'bias': a.get('bias'), 'ml_score': a.get('ml_score'), 'pa_status': a.get('learning_context', {}).get('PA_Status', 'N/A')})
                            
                            # RUN COMMITTEE
                            approved, full_audit = risk_committee.run_evaluations(structured)
                            
                            # 🕵️ SIGNAL AUDIT LOGGING
                            for s in full_audit:
                                db_manager.add_signal_audit({
                                    "ticker": s['ticker'],
                                    "pa_status": s.get('pa_status', 'N/A'),
                                    "pcr": s.get('coi_pcr', 0.0),
                                    "gex_mn": curr_gex / 1000000,
                                    "ml_score": s.get('ml_score', 0.5),
                                    "committee_verdict": s.get('committee_verdict', 'HOLD'),
                                    "committee_reasoning": s.get('committee_reasoning', ''),
                                    "backtester_rule_match": f"Structural {s.get('pa_status')} + PCR/GEX Alignment"
                                })

                            if approved:
                                for t in approved:
                                    t.update({'execution_time': now.time(), 'mode': trading_mode, 'lots_sized': 1})
                                    paper_broker.execute_trade(t)
                time.sleep(60)
        except Exception as e: st.error(f"Logic Error: {e}")
        st.rerun()

elif nav_selection == "📜 LEDGER":
    try:
        open_trades = list(OpenPosition.select().dicts())
        closed_trades = list(Trade.select().where(Trade.entry_date >= datetime.now().replace(hour=0, minute=0, second=0)).dicts())
    except Exception as e:
        open_trades, closed_trades = [], []
        st.error(f"DB Error: {e}")

    st.markdown("### TRANSACTION LEDGER")
    full_log = []
    for t in open_trades + closed_trades:
        lots = int(t.get('lots_sized') or 1)
        pnl = float(t.get('realized_pnl') or 0.0) * (lots * 25) if 'realized_pnl' in t else float(t.get('net_credit_per_share') or 0.0) * (lots * 25)
        status = "🟢 OPEN" if 'realized_pnl' not in t or t['realized_pnl'] is None else ("🔵 WIN" if pnl > 0 else "🔴 LOSS")
        full_log.append({
            "TIME": t.get("entry_date") or t.get("execution_time"),
            "ASSET": t.get("ticker"),
            "STRATEGY": t.get("strategy_type"),
            "DELTA": f"{float(t.get('net_delta') or 0.0):.3f}",
            "STATUS": status,
            "PNL": f"₹{pnl:,.0f}"
        })
    if full_log:
        st.dataframe(pd.DataFrame(full_log).sort_values(by="TIME", ascending=False), use_container_width=True, hide_index=True)

elif nav_selection == "🕵️ SIGNAL INTEL":
    st.markdown("### 🕵️ SIGNAL INTELLIGENCE (TRANS-FLOW)")
    audits = db_manager.get_signal_audits(50)
    if audits:
        for a in audits:
            with st.expander(f"🔍 {a['timestamp'].strftime('%H:%M:%S')} | {a['ticker']} | {a['committee_verdict']}", expanded=False):
                col_a1, col_nav2 = st.columns([1, 2])
                with col_a1:
                    st.metric("ML ALPHA", f"{float(a['ml_score']):.2f}")
                    st.metric("GEX (Mn)", f"{float(a['gex_mn']):.2f}")
                    st.write(f"**PA Status:** {a['pa_status']}")
                    st.write(f"**Rule Match:** {a['backtester_rule_match']}")
                with col_nav2:
                    st.markdown("**COMMITTEE REASONING:**")
                    st.caption(a['committee_reasoning'])
    else:
        st.caption("System hasn't generated signals yet.")

elif nav_selection == "👁️ RADAR":
    st.markdown("### 👁️ INSTITUTIONAL ORDER FLOW (GEX/VOC)")
    tf = st.select_slider("Buffer Window", options=[3, 5, 15], value=5)
    df_radar = load_v2_autotrender_data(tf)
    if not df_radar.empty:
        df_radar['OPT SIGNAL'] = df_radar['PCR'].apply(get_pcr_signal)
        df_radar['VWAP SIGNAL'] = df_radar.apply(lambda x: get_vwap_signal(x['Price'], x['VWAP']), axis=1)
        
        display_cols = ["Time", "Price", "VWAP", "POC", "PCR", "GEX (Mn)", "OPT SIGNAL", "VWAP SIGNAL"]
        
        def style_neon(val):
            color = THEME["success"] if val == "BUY" else THEME["danger"] if val == "SELL" else "#666"
            return f'color: {color}; font-weight: bold;'

        styled_radar = df_radar[display_cols].style.map(style_neon, subset=['OPT SIGNAL', 'VWAP SIGNAL']) \
                                .format({"Price": "{:.2f}", "VWAP": "{:.2f}", "PCR": "{:.2f}", "GEX (Mn)": "{:.2f}", "POC": "{:.2f}"})

        st.dataframe(styled_radar, use_container_width=True, hide_index=True)
    else:
        st.caption("Connecting to exchange pulse...")

elif nav_selection == "🧠 AI AUDIT":
    if st.button("EXECUTE DEEP FORENSIC AUDIT"):
        with st.spinner("AI ANALYZING MARKET FRICTION..."):
            report = analyst_agent.generate_learning_report()
            st.markdown(report)
