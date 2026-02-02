"""
Claude Agent for Health Advisor.
Handles conversation with Claude API and MCP tool orchestration.
"""

import json
import logging
from typing import Any, Optional

import anthropic

from backend.config import get_config, get_anthropic_api_key
from backend.mcp_client import MCPClient
from backend.debug_logger import DebugLogger

logger = logging.getLogger("claude_agent")


class HealthAdvisorAgent:
    """
    AI agent that uses Claude to answer health-related questions.
    Orchestrates MCP tool calls to retrieve patient information.
    """

    def __init__(self, mcp_client: MCPClient, debug_logger: DebugLogger):
        self.config = get_config()
        self.mcp_client = mcp_client
        self.debug_logger = debug_logger
        self.client = anthropic.Anthropic(api_key=get_anthropic_api_key())

        # Claude configuration
        claude_config = self.config.get("claude", {})
        self.model = claude_config.get("model", "claude-sonnet-4-20250514")
        self.max_tokens = claude_config.get("max_tokens", 4096)

    def _get_system_prompt(self, patient_id: int, patient_name: str) -> str:
        """Generate system prompt with patient context."""
        return f"""You are a helpful healthcare assistant for the Health Advisor application.
You help patients access and understand their health information.

CURRENT PATIENT CONTEXT:
- Patient ID: {patient_id}
- Patient Name: {patient_name}

You have access to tools that retrieve patient health information from the database.
When the patient asks about their health information, use the appropriate tools to look up their data.

IMPORTANT GUIDELINES:
1. Always use the patient_id ({patient_id}) when calling tools - this is the currently logged-in patient.
2. Present health information in a clear, easy-to-understand format.
3. Be empathetic and supportive when discussing health conditions.
4. If you don't have access to certain information, let the patient know.
5. Never make up or guess health information - only report what the tools return.
6. For sensitive information, handle it professionally and respectfully.

Available tools allow you to:
- Look up patient demographics
- View medical records and conditions
- Check current prescriptions
- See upcoming appointments
- Review insurance information
- Access lab results"""

    async def chat(
        self,
        message: str,
        patient_id: int,
        patient_name: str,
        conversation_history: list[dict] = None,
    ) -> dict:
        """
        Process a chat message and return a response.

        Args:
            message: User's message
            patient_id: Current patient ID
            patient_name: Current patient name
            conversation_history: Previous messages in the conversation

        Returns:
            Response with assistant message and debug info
        """
        conversation_history = conversation_history or []

        # Get MCP tools formatted for Claude
        tools = self.mcp_client.get_tools_for_claude()

        # Build messages
        messages = conversation_history + [{"role": "user", "content": message}]

        # Log the request
        self.debug_logger.log_claude_request({
            "model": self.model,
            "system": self._get_system_prompt(patient_id, patient_name)[:200] + "...",
            "messages": messages,
            "tools": [t["name"] for t in tools],
        })

        # Initial Claude API call
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self._get_system_prompt(patient_id, patient_name),
            tools=tools,
            messages=messages,
        )

        self.debug_logger.log_claude_response({
            "stop_reason": response.stop_reason,
            "content_types": [c.type for c in response.content],
        })

        # Process response and handle tool calls
        return await self._process_response(
            response, messages, tools, patient_id, patient_name
        )

    async def _process_response(
        self,
        response,
        messages: list[dict],
        tools: list[dict],
        patient_id: int,
        patient_name: str,
    ) -> dict:
        """Process Claude response and handle any tool calls."""
        tool_calls = []
        final_text = ""

        # Agentic loop - keep processing until we get a final response
        while response.stop_reason == "tool_use":
            # Collect all tool uses from this response
            assistant_content = []
            tool_uses = []

            for block in response.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
                    tool_uses.append(block)

            # Add assistant message with tool uses
            messages.append({"role": "assistant", "content": assistant_content})

            # Execute all tool calls and collect results
            tool_results = []
            for tool_use in tool_uses:
                self.debug_logger.log_mcp_request({
                    "tool": tool_use.name,
                    "arguments": tool_use.input,
                })

                try:
                    result = await self.mcp_client.call_tool(
                        tool_use.name, tool_use.input
                    )

                    # Parse the result content
                    result_text = ""
                    for item in result["content"]:
                        if item.get("type") == "text":
                            result_text = item.get("text", "")
                            break

                    tool_calls.append({
                        "tool": tool_use.name,
                        "arguments": tool_use.input,
                        "result": result_text,
                    })

                    self.debug_logger.log_mcp_response({
                        "tool": tool_use.name,
                        "result": result_text[:500] if result_text else "No result",
                    })

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": result_text,
                    })

                except Exception as e:
                    error_msg = f"Error calling tool {tool_use.name}: {str(e)}"
                    logger.error(error_msg)

                    tool_calls.append({
                        "tool": tool_use.name,
                        "arguments": tool_use.input,
                        "error": str(e),
                    })

                    self.debug_logger.log_error({
                        "tool": tool_use.name,
                        "error": str(e),
                    })

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": error_msg,
                        "is_error": True,
                    })

            # Add tool results to messages
            messages.append({"role": "user", "content": tool_results})

            # Continue the conversation with tool results
            self.debug_logger.log_claude_request({
                "model": self.model,
                "continuation": True,
                "tool_results_count": len(tool_results),
            })

            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self._get_system_prompt(patient_id, patient_name),
                tools=tools,
                messages=messages,
            )

            self.debug_logger.log_claude_response({
                "stop_reason": response.stop_reason,
                "content_types": [c.type for c in response.content],
            })

        # Extract final text response
        for block in response.content:
            if block.type == "text":
                final_text += block.text

        return {
            "response": final_text,
            "tool_calls": tool_calls,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        }
