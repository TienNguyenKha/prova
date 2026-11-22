"""Turn the model's tool_use blocks into the single user message that answers them."""

from collections.abc import Callable, Mapping, Sequence
from typing import cast

from anthropic.types import ContentBlock, MessageParam, ToolResultBlockParam

from prova.errors import ErrorCode, ToolError

# Runs one tool: (name, input) -> text for the model. Raises ToolError for failures the model can fix.
ToolRunner = Callable[[str, Mapping[str, object]], str]


def build_tool_results(content: Sequence[ContentBlock], run: ToolRunner) -> MessageParam | None:
    """Answer every tool_use block of an assistant turn.

    Returns ONE user message holding one tool_result per tool_use, same ids, same order.
    Returns None when the turn has no tool_use (the model is done).
    """
    results = [
        _run_one(block.id, block.name, block.input, run) for block in content if block.type == "tool_use"
    ]
    if not results:
        return None
    return {"role": "user", "content": results}


def _run_one(tool_use_id: str, name: str, tool_input: object, run: ToolRunner) -> ToolResultBlockParam:
    if not isinstance(tool_input, Mapping):
        message = f"input for {name!r} must be a JSON object"
        return _error_result(tool_use_id, ErrorCode.INVALID_ARGS, message)
    try:
        text = run(name, cast(Mapping[str, object], tool_input))
    except ToolError as e:  # caused by the model's call: report it as data so the model can correct itself
        return _error_result(tool_use_id, e.code, str(e))
    # Any other exception is a bug in our code: let it propagate.
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}


def _error_result(tool_use_id: str, code: ErrorCode, message: str) -> ToolResultBlockParam:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": f"{code}: {message}",
        "is_error": True,
    }
