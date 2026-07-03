import os
import asyncio
import logging
from typing import Dict, Any, Tuple
from dotenv import load_dotenv
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.messages import TextMessage

# Setup logger
logger = logging.getLogger("HeadlessRiskCommittee")

load_dotenv()

class RiskCommittee:
    def __init__(self):
        self.api_key = os.getenv("GROQ_API_KEY")
        self.model_name = "llama-3.3-70b-versatile"
        self.client = None
        self.initialized = False

    def _initialize_client(self) -> bool:
        if self.initialized:
            return True
        
        if not self.api_key:
            logger.warning("⚠️ GROQ_API_KEY not found in environment. Risk Committee cannot initialize.")
            return False

        try:
            logger.info("🔌 Initializing Autogen Groq Client...")
            self.client = OpenAIChatCompletionClient(
                model=self.model_name,
                api_key=self.api_key,
                base_url="https://api.groq.com/openai/v1",
                model_info={
                    "vision": False,
                    "function_calling": True,
                    "json_output": False,
                    "family": "unknown"
                }
            )
            self.initialized = True
            return True
        except Exception as e:
            logger.error(f"❌ Failed to initialize Autogen Groq Client: {e}")
            return False

    async def evaluate_trade(self, trade_data: Dict[str, Any]) -> Tuple[str, str]:
        """
        Runs a headless 3-agent Autogen group-chat debate to evaluate a candidate trade.
        Returns:
            Tuple[str, str]: (Verdict: "APPROVED" or "REJECTED", Detailed Reasoning Log)
        """
        ticker = trade_data.get("ticker", "UNKNOWN")
        ml_score = float(trade_data.get("ml_score", 0.5))

        logger.info(f"⚖️ Starting Headless Risk Committee Evaluation for {ticker} (ML: {ml_score:.2f})")

        # 1. Fast-path and auto-veto rules
        if ml_score >= 0.85:
            reasoning = "FAST-TRACK: Elite ML alpha conviction score detected. Automatic Approval."
            logger.info(f"⚡ {ticker} Auto-Approved via Fast-Track (ML Score: {ml_score})")
            return "APPROVED", reasoning
        
        if ml_score < 0.40:
            reasoning = "AUTO-VETO: Low ML score. Capital protection filters active."
            logger.info(f"🛑 {ticker} Auto-Vetoed (ML Score: {ml_score})")
            return "REJECTED", reasoning

        # 2. Check if client can be initialized
        if not self._initialize_client():
            # Fallback to ML threshold rule if Groq API is not configured
            verdict = "APPROVED" if ml_score >= 0.60 else "REJECTED"
            reasoning = (
                f"FALLBACK VERDICT: {verdict}\n"
                f"Reason: Groq API client could not be initialized (missing keys). "
                f"Evaluated via fallback local ML score model rules."
            )
            logger.info(f"⚠️ Missing Groq client. Falling back to local ML threshold rule for {ticker}.")
            return verdict, reasoning

        # 3. Create the multi-agent committee and debate
        try:
            analyst = AssistantAgent(
                name="Quant_Analyst",
                model_client=self.client,
                system_message="You are an aggressive quantitative analyst. Review the provided Options Spread, Order Flow, and structural price action. Argue why this is a highly profitable, mathematically sound trade to execute. Be extremely concise (max 3 sentences)."
            )

            risk_manager = AssistantAgent(
                name="Risk_Manager",
                model_client=self.client,
                system_message="You are a strict risk manager. Review the trade and the Analyst's pitch. Focus on the Risk/Reward ratio, max loss, and potential smart money traps (like PCR divergence). Play devil's advocate and argue why this trade might fail. Be extremely concise (max 3 sentences)."
            )

            head_trader = AssistantAgent(
                name="Head_Trader",
                model_client=self.client,
                system_message="You are the Portfolio Manager. Review the trade data, the Analyst's pitch, and the Risk Manager's warnings. You make the final decision. You MUST conclude your final response with exactly one of these two tags: [EXECUTE] or [HOLD]. Keep your debate review concise."
            )

            team = RoundRobinGroupChat([analyst, risk_manager, head_trader], max_turns=3)

            prompt = f"""
            Proposed Trade Structure:
            - Asset: {ticker}
            - Strategy: {trade_data.get('strategy_type', 'N/A')}
            - Current Spot Price: ₹{trade_data.get('spot_price', 'N/A')}
            - Sell Strike (Leg 1): ₹{trade_data.get('leg_1_sell', 'N/A')}
            - Buy Strike (Leg 2): ₹{trade_data.get('leg_2_buy', 'N/A')}
            - Max Reward (Credit): ₹{trade_data.get('net_credit_per_share', 'N/A')}
            - Max Risk: ₹{trade_data.get('max_risk_per_share', 'N/A')}
            - Risk/Reward Ratio: {trade_data.get('risk_reward_ratio', 'N/A')}
            - Directional Bias: {trade_data.get('bias', 'N/A')}
            
            Market Context & Institutional Metrics:
                * Alpha ML Approval Score: {ml_score:.4f}
                * Volume Surge Multiplier: {trade_data.get('vol_surge_multiplier', 'N/A')}x
                * Smart Money PCR: {trade_data.get('coi_pcr', 'N/A')}
                * Price Action Structure: {trade_data.get('learning_context', {}).get('PA_Status', 'N/A')}

            Please debate the validity of this trade. Note the Alpha ML Score ({ml_score:.2f}) represents a neural network's historical win-probability. Provide a final verdict to either [EXECUTE] or [HOLD].
            """

            reasoning_blob = ""
            final_message = "HOLD"

            async for message in team.run_stream(task=prompt):
                if isinstance(message, TextMessage):
                    logger.info(f"[{message.source}]: {message.content[:60]}...")
                    reasoning_blob += f"[{message.source}]: {message.content}\n\n"
                    final_message = message.content

            # Parse verdict from Head Trader's final response
            final_text = final_message.upper()
            execute_pos = final_text.rfind("EXECUTE")
            hold_pos = final_text.rfind("HOLD")

            if execute_pos > hold_pos:
                verdict = "APPROVED"
            else:
                verdict = "REJECTED"

            logger.info(f"✅ Headless Risk Committee Finished. Verdict: {verdict} for {ticker}")
            return verdict, reasoning_blob

        except Exception as e:
            logger.error(f"❌ Error in Headless Risk Committee group-chat for {ticker}: {e}. Falling back to ML rules.")
            # Fallback on exceptions (e.g., Groq rate limit)
            verdict = "APPROVED" if ml_score >= 0.60 else "REJECTED"
            reasoning = (
                f"FALLBACK VERDICT: {verdict}\n"
                f"Reason: Headless group-chat failed due to API / network exception: {e}. "
                f"Defaulting to local ML threshold rules to protect execution pipeline."
            )
            return verdict, reasoning

# Global singleton instance
risk_committee = RiskCommittee()
