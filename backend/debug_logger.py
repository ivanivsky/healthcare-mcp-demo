"""
Debug Logger for Health Advisor.
Captures MCP messages, Claude API calls, and other debug information
for display in the debug panel.
"""

import json
import logging
from collections import deque
from datetime import datetime
from typing import Any, Optional

from backend.config import get_config

logger = logging.getLogger("debug_logger")


class DebugLogger:
    """
    Captures and stores debug events for the debug panel.
    Maintains a rolling buffer of recent events.
    """

    def __init__(self, max_events: int = 500):
        self.config = get_config()
        self.max_events = max_events
        self.events: deque = deque(maxlen=max_events)
        self.enabled = self.config.get("logging", {}).get("debug_panel_enabled", True)

    def _should_log(self, event_type: str) -> bool:
        """Check if this event type should be logged."""
        if not self.enabled:
            return False

        logging_config = self.config.get("logging", {})

        if event_type == "mcp" and not logging_config.get("log_mcp_messages", True):
            return False
        if event_type == "claude" and not logging_config.get("log_claude_requests", True):
            return False
        if event_type == "sql" and not logging_config.get("log_sql_queries", True):
            return False

        return True

    def _add_event(self, event_type: str, category: str, data: Any):
        """Add an event to the log."""
        if not self._should_log(event_type):
            return

        event = {
            "id": len(self.events),
            "timestamp": datetime.now().isoformat(),
            "type": event_type,
            "category": category,
            "data": data,
        }
        self.events.append(event)

        # Also log to standard logger at debug level
        logger.debug(f"[{event_type}:{category}] {json.dumps(data, default=str)[:500]}")

    def log_mcp_request(self, data: dict):
        """Log an outgoing MCP tool call request."""
        self._add_event("mcp", "request", data)

    def log_mcp_response(self, data: dict):
        """Log an MCP tool call response."""
        self._add_event("mcp", "response", data)

    def log_claude_request(self, data: dict):
        """Log an outgoing Claude API request."""
        self._add_event("claude", "request", data)

    def log_claude_response(self, data: dict):
        """Log a Claude API response."""
        self._add_event("claude", "response", data)

    def log_sql_query(self, query: str, params: Optional[tuple] = None):
        """Log a SQL query execution."""
        self._add_event("sql", "query", {"query": query, "params": params})

    def log_error(self, data: dict):
        """Log an error event."""
        self._add_event("error", "error", data)

    def log_agent_reasoning(self, data: dict):
        """Log agent decision/reasoning flow."""
        self._add_event("agent", "reasoning", data)

    def log_security_event(self, data: dict):
        """Log security-related events (for vulnerability demos)."""
        self._add_event("security", "event", data)

    def get_events(
        self,
        event_type: Optional[str] = None,
        since_id: Optional[int] = None,
        limit: int = 100,
    ) -> list[dict]:
        """
        Get logged events, optionally filtered.

        Args:
            event_type: Filter by event type (mcp, claude, sql, error, etc.)
            since_id: Only return events after this ID
            limit: Maximum number of events to return

        Returns:
            List of event dictionaries
        """
        events = list(self.events)

        if since_id is not None:
            events = [e for e in events if e["id"] > since_id]

        if event_type:
            events = [e for e in events if e["type"] == event_type]

        # Return most recent events first
        events = sorted(events, key=lambda e: e["id"], reverse=True)

        return events[:limit]

    def clear_events(self):
        """Clear all logged events."""
        self.events.clear()


# Global debug logger instance
_debug_logger: Optional[DebugLogger] = None


def get_debug_logger() -> DebugLogger:
    """Get or create the debug logger instance."""
    global _debug_logger
    if _debug_logger is None:
        _debug_logger = DebugLogger()
    return _debug_logger
