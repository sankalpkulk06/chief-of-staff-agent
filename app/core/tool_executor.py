"""Executor for handling model tool calls and responses."""
import json
import re
from typing import Any, Dict, Optional, Tuple

from app.core.tools import ToolRegistry


class ToolCallResult:
    """Result of executing a tool call."""

    def __init__(self, tool_name: str, parameters: Dict[str, Any], result: Dict[str, Any]):
        self.tool_name = tool_name
        self.parameters = parameters
        self.result = result
        self.success = result.get("success", False)
        self.output = result.get("result") or result.get("error", "Unknown error")


class ToolExecutor:
    """Executes tool calls from model output and manages the loop."""

    def __init__(self, registry: ToolRegistry, max_calls: int = 5):
        self.registry = registry
        self.max_calls = max_calls
        self.call_count = 0

    def parse_tool_call(self, text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Parse a tool call from model output.

        Model should output JSON like:
        {"tool": "fetch_news", "parameters": {"query": "SpaceX"}}

        Returns:
            Tuple of (tool_name, parameters) or None if no tool call found
        """
        # Try to extract JSON from the text
        # Look for pattern: {...}
        json_pattern = r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}"
        matches = re.findall(json_pattern, text)

        for match in matches:
            try:
                data = json.loads(match)
                if "tool" in data and "parameters" in data:
                    return data["tool"], data["parameters"]
            except json.JSONDecodeError:
                continue

        return None

    def extract_response_text(self, text: str) -> str:
        """Extract the text response, removing any JSON tool calls."""
        # Remove JSON objects that look like tool calls
        json_pattern = r"\{[^{}]*\"tool\"[^{}]*\}"
        cleaned = re.sub(json_pattern, "", text)
        return cleaned.strip()

    def execute_tool_call(self, tool_name: str, parameters: Dict[str, Any]) -> ToolCallResult:
        """Execute a tool call and return the result."""
        self.call_count += 1
        result = self.registry.execute_tool(tool_name, parameters)
        return ToolCallResult(tool_name, parameters, result)

    def process_model_output(self, output: str) -> Tuple[Optional[ToolCallResult], str]:
        """Process model output and check for tool calls.

        Returns:
            Tuple of (ToolCallResult or None, cleaned_response_text)
        """
        # Check if we've exceeded max calls
        if self.call_count >= self.max_calls:
            text = self.extract_response_text(output)
            return None, text

        # Try to parse a tool call
        tool_call = self.parse_tool_call(output)

        if tool_call:
            tool_name, parameters = tool_call
            result = self.execute_tool_call(tool_name, parameters)
            return result, ""
        else:
            # No tool call, extract regular response
            text = self.extract_response_text(output)
            return None, text

    def reset(self) -> None:
        """Reset the executor for a new conversation."""
        self.call_count = 0
