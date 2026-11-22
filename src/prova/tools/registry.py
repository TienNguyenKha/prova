"""ToolRegistry: list tools for the API and dispatch the model's calls. The model's mistakes never raise."""

import difflib
import logging
from collections.abc import Collection

from anthropic.types import ToolParam
from pydantic import ValidationError

from prova.errors import ErrorCode, RegistryError, ToolError
from prova.registry import Registry
from prova.tools.base import Permission, RunContext, Tool, ToolResult
from prova.validation import describe_errors

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self, tools: Registry[Tool]) -> None:
        if not tools.frozen:
            # Otherwise a tool could still be added after a run started, and the tool list would change.
            message = "the tool registry must be frozen before use: register every tool, then call freeze()"
            raise RegistryError(message)
        self._tools = tools

    def to_api_tools(self, allowed: Collection[Permission]) -> list[ToolParam]:
        """The `tools` list for the API: allowed permissions only, in name order, schema from `args_model`."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.args_model.model_json_schema(),
            }
            for tool in self._tools  # Registry iterates in name order, so the list is the same on every call
            if tool.permission in allowed
        ]

    def dispatch(self, name: str, raw_input: object, ctx: RunContext) -> ToolResult:
        """Run the tool the model asked for. Mistakes of the model come back as data, never as exceptions."""
        tool = self._tools.find(name)
        if tool is None:
            return ToolResult.error(ErrorCode.NOT_FOUND, self._unknown_tool_message(name))
        # The model may ask for a tool it was never offered (a hallucination, or an injected instruction).
        # Checked before the arguments, so a denied tool reveals nothing about what it expects.
        if tool.permission not in ctx.permissions:
            return ToolResult.error(ErrorCode.PERMISSION_DENIED, f"tool {name!r} is not allowed in this run")

        try:
            args = tool.args_model.model_validate(raw_input)
        except ValidationError as e:
            message = f"invalid arguments for {name!r}: {describe_errors(e)}"
            return ToolResult.error(ErrorCode.INVALID_ARGS, message)

        try:
            return tool.run(args, ctx)
        except ToolError as e:  # a failure the tool itself classified: pass its code on
            return ToolResult.error(e.code, str(e))
        except Exception:
            # A bug in our own tool. Keep the traceback in the log; the model gets no exception text.
            logger.exception("tool %r raised an unexpected exception", name)
            if ctx.strict:
                raise
            return ToolResult.error(
                ErrorCode.INTERNAL, f"tool {name!r} failed unexpectedly; this is not caused by your arguments"
            )

    def _unknown_tool_message(self, name: str) -> str:
        names = [tool.name for tool in self._tools]
        message = f"unknown tool {name!r}. Available tools: {', '.join(names) or '(none)'}"
        close = difflib.get_close_matches(name, names, n=3)
        return message + (f". Did you mean: {', '.join(close)}?" if close else "")
