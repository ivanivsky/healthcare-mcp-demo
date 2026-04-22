"""
Router Agent — intent classifier for the multi-agent architecture.

Receives every user message, makes a single lightweight Gemini call
to classify intent, and returns a routing decision: "health" or
"appointments". Does not connect to any MCP server and calls no tools.

Architecture:
    User message
         ↓
    RouterAgent.route()   ← single Gemini call, no tools
         ↓
    "health" | "appointments"
         ↓
    HealthAdvisorAgent  OR  AppointmentsAgent
         ↓
    Response returned to user
"""

import asyncio
import json
import logging
import os

logger = logging.getLogger("router_agent")


class RouterAgent:
    """
    Lightweight intent classifier that routes user messages to the
    appropriate specialist agent.

    Does NOT use ADK LlmAgent — makes a direct Vertex AI Gemini call
    for classification. No tools. No MCP connection.
    Returns a routing decision in under 1 second.
    """

    SYSTEM_PROMPT = """
You are a routing agent for a healthcare assistant application.
Your only job is to classify user messages and return a JSON routing decision.

You must respond with ONLY valid JSON in this exact format:
{"agent": "health", "confidence": 0.95, "reason": "brief reason"}

OR:
{"agent": "appointments", "confidence": 0.95, "reason": "brief reason"}

Classification rules:
- "appointments": scheduling, booking, cancelling, rescheduling,
  available slots, providers, upcoming appointments, visit times,
  "when is my next", "book a", "schedule a", "cancel my appointment",
  "what appointments do I have", "available times"
- "health": medical records, prescriptions, medications, lab results,
  diagnoses, insurance, demographics, health conditions, test results,
  "what are my", "show me my", any clinical or medical query

When ambiguous, default to "health".
Never respond with anything other than the JSON object.
"""

    def __init__(self):
        self._client = None
        logger.info("ROUTER_AGENT_INITIALIZED")

    def _get_client(self):
        """Lazy-initialize Vertex AI Gemini client."""
        if self._client is None:
            import vertexai
            from vertexai.generative_models import GenerativeModel

            project = os.environ.get("GOOGLE_CLOUD_PROJECT", "healthcare-demo-app")
            location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
            vertexai.init(project=project, location=location)
            self._client = GenerativeModel(
                "gemini-2.5-flash",
                system_instruction=self.SYSTEM_PROMPT,
            )
            logger.info(
                f"ROUTER_GEMINI_CLIENT initialized "
                f"project={project} location={location}"
            )
        return self._client

    async def route(
        self,
        message: str,
        conversation_history: list | None = None,
    ) -> dict:
        """
        Classify the user message and return a routing decision.

        Args:
            message: The user's message to classify.
            conversation_history: Recent conversation turns for context
                (only last 4 entries are used).

        Returns:
            {
                "agent": "health" | "appointments",
                "confidence": float,
                "reason": str,
            }

        Falls back to "health" on any error so the health agent always
        handles ambiguous or unclassifiable messages.
        """
        try:
            model = self._get_client()

            # Include last 2 turns of history for context
            if conversation_history:
                recent = conversation_history[-4:]
                context = "\n".join(
                    f"{m['role']}: {m['content']}" for m in recent
                )
                message_with_context = (
                    f"Recent conversation:\n{context}\n\n"
                    f"New message: {message}"
                )
            else:
                message_with_context = message

            response = await asyncio.to_thread(
                model.generate_content,
                message_with_context,
                generation_config={"temperature": 0.0, "max_output_tokens": 100},
            )

            text = response.text.strip()

            # Strip markdown code fences if present
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0]

            decision = json.loads(text)
            agent = decision.get("agent", "health")
            if agent not in ("health", "appointments"):
                agent = "health"

            logger.info(
                f"ROUTER_DECISION agent={agent} "
                f"confidence={decision.get('confidence', '?')} "
                f"reason={str(decision.get('reason', '?'))[:80]}"
            )
            return {
                "agent": agent,
                "confidence": decision.get("confidence", 1.0),
                "reason": decision.get("reason", ""),
            }

        except Exception as e:
            logger.warning(f"ROUTER_ERROR falling back to health: {e}")
            return {
                "agent": "health",
                "confidence": 0.5,
                "reason": f"routing error: {e}",
            }
