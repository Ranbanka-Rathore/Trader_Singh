import streamlit as st
from autogen_agentchat.messages import TextMessage
import asyncio
import os
from dotenv import load_dotenv
from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_agentchat.teams import RoundRobinGroupChat

# --- SECURE GROQ API SETUP ---
# Pull the key from the hidden .env file we created
load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

class RiskCommittee:
    def __init__(self):
        # We leave the init empty. Creating agents here causes the "Event Loop" 
        # crash in Streamlit because the threads get tangled.
        pass

    async def _async_evaluations(self, structured_trades):
        """This function holds the entire agent lifecycle inside one safe async bubble."""
        
        if not GROQ_API_KEY:
            st.error("🚨 GROQ API Key missing! Check your .env file.")
            return []

        # 1. Initialize EVERYTHING inside the async context pointing to GROQ
        model_client = OpenAIChatCompletionClient(
            model="llama-3.1-8b-instant",  
            api_key=GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
            model_info={
                "vision": False,
                "function_calling": True,
                "json_output": False,
                "family": "unknown"
            }
        )

        analyst = AssistantAgent(
            name="Quant_Analyst",
            model_client=model_client,
            system_message="You are an aggressive quantitative analyst. Review the provided Options Spread, Order Flow, and structural price action. Argue why this is a highly profitable, mathematically sound trade to execute. Be concise."
        )

        risk_manager = AssistantAgent(
            name="Risk_Manager",
            model_client=model_client,
            system_message="You are a strict risk manager. Review the trade and the Analyst's pitch. Focus on the Risk/Reward ratio, max loss, and potential smart money traps (like PCR divergence). Play devil's advocate and argue why this trade might fail or why the premium collected isn't worth the risk. Be concise."
        )

        head_trader = AssistantAgent(
            name="Head_Trader",
            model_client=model_client,
            system_message="You are the Portfolio Manager. Review the trade data, the Analyst's pitch, and the Risk Manager's warnings. You make the final decision. You MUST conclude your response with exactly one of these two tags: [EXECUTE] or [HOLD]."
        )

        team = RoundRobinGroupChat([analyst, risk_manager, head_trader], max_turns=3)
        approved_for_execution = []

        for trade in structured_trades:
            ml_score = trade.get('ml_score', 0.5)
            ticker = trade.get('ticker', 'UNKNOWN')

            print(f"\n{'='*60}")
            print(f"⚖️ RISK COMMITTEE EVALUATING: {ticker}")
            print(f"🧠 Alpha ML Score: {ml_score:.4f}")
            print(f"{'='*60}\n")

            # --- 🚀 INSTITUTIONAL FAST-TRACK & FILTER ---
            if ml_score >= 0.85:
                print(f"⚡ FAST TRACK: High Conviction ML Score ({ml_score}). Auto-Approving.")
                approved_for_execution.append(trade)
                with st.chat_message("Assistant", avatar="🧠"):
                    st.success(f"🚀 **FAST TRACK APPROVED**: {ticker} has an Elite Alpha Score of {ml_score:.2f}. Bypassing committee debate.")
                continue
            
            if ml_score <= 0.40:
                print(f"🛑 AUTO-VETO: Low Conviction ML Score ({ml_score}). Rejecting.")
                with st.chat_message("Assistant", avatar="🧠"):
                    st.error(f"🛑 **AUTO-VETO**: {ticker} rejected due to Low Alpha Score ({ml_score:.2f}).")
                continue

            # Updated Prompt with crash-proof .get() methods targeting the v4.0 Order Flow metrics
            prompt = f"""
            Proposed Trade Structure:
            - Asset: {trade.get('ticker', 'N/A')}
            - Strategy: {trade.get('strategy_type', 'N/A')}
            - Current Spot Price: ₹{trade.get('spot_price', 'N/A')}
            - Sell Strike (Leg 1): ₹{trade.get('leg_1_sell', 'N/A')}
            - Buy Strike (Leg 2): ₹{trade.get('leg_2_buy', 'N/A')}
            - Max Reward (Credit): ₹{trade.get('net_credit_per_share', 'N/A')}
            - Max Risk: ₹{trade.get('max_risk_per_share', 'N/A')}
            - Risk/Reward Ratio: {trade.get('risk_reward_ratio', 'N/A')}
            - Directional Bias: {trade.get('bias', 'N/A')}
            
            - Market Context & Institutional Metrics:
                * Alpha ML Approval Score: {ml_score:.4f}
                * Volume Surge Multiplier: {trade.get('vol_surge_multiplier', 'N/A')}x
                * Smart Money PCR: {trade.get('coi_pcr', 'N/A')}
                * Price Action Structure: {trade.get('learning_context', {}).get('PA_Status', 'N/A')}

            Please debate the validity of this trade. Note the Alpha ML Score ({ml_score:.2f}) represents a neural network's historical win-probability for this exact setup. Provide a final verdict to either [EXECUTE] or [HOLD].
            """

            # Manually stream the debate to both the Terminal AND Streamlit
            final_message = "HOLD" # Default fallback
            reasoning_blob = ""
            
            try:
                # We wrap the AI call in a try block to catch rate limits
                async for message in team.run_stream(task=prompt):
                    if isinstance(message, TextMessage):
                        
                        print(f"\n[{message.source}]: {message.content}")
                        reasoning_blob += f"[{message.source}]: {message.content}\n\n"
                        
                        avatar_icon = "🧮" if message.source == "Quant_Analyst" else "🛡️" if message.source == "Risk_Manager" else "👔"
                        
                        with st.chat_message(message.source, avatar=avatar_icon):
                            st.markdown(f"**{message.source}**")
                            st.markdown(message.content)
                            
                        final_message = message.content
            
            except Exception as e:
                # If Groq hits a rate limit or drops connection, we catch it here gracefully
                st.error(f"⚠️ API Rate Limit or Connection Error. Defaulting to HOLD to protect capital.")
                print(f"⚠️ API Error Caught: {e}")
                final_message = "HOLD" # Force a HOLD decision
                reasoning_blob += f"ERROR: {e}"
            
            # --- THE ROBUST DECISION PARSER ---
            final_text = final_message.upper()
            
            # Find the very last index (position) of both words in the Head Trader's response
            execute_pos = final_text.rfind("EXECUTE")
            hold_pos = final_text.rfind("HOLD")
            
            # If EXECUTE was their final conclusion (meaning it appears later in the text than HOLD)
            if execute_pos > hold_pos:
                decision = "EXECUTE"
            else:
                decision = "HOLD" # If HOLD is last, or if neither word is found, protect the capital
            
            print(f"\n{'*'*60}")
            # Attach reasoning to the trade object for logging
            trade['committee_reasoning'] = reasoning_blob
            trade['committee_verdict'] = decision
            
            if decision == "EXECUTE":
                print(f"✅ FINAL VERDICT: [EXECUTE] -> {trade.get('ticker')} Approved for Live Trading!")
                approved_for_execution.append(trade)
            else:
                print(f"🛡️ FINAL VERDICT: [HOLD] -> Trade vetoed for {trade.get('ticker')}. Capital Protected.")
            print(f"{'*'*60}\n")

        return approved_for_execution, structured_trades # Return both for audit logging

    def run_evaluations(self, structured_trades):
        """Safely bridges Streamlit's synchronous threads with Python's async AI."""
        # Create a fresh, dedicated event loop for this specific Streamlit button click
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Execute the async function
            approved_trades, full_audit = loop.run_until_complete(self._async_evaluations(structured_trades))
            return approved_trades, full_audit
        finally:
            # Cleanly destroy the loop when finished so it doesn't leak memory
            loop.close()