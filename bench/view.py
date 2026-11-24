"""Show a trajectory in a terminal: what a run did, turn by turn.

  uv run python -m bench.view bench/results/trajectories/<run>.jsonl

It only reads the log. The times come from the timestamps of the events (the log has no other clock) and the
money from the tokens and a price table; where either cannot be known it says "n/a" and does not guess. The
text it shows comes from the model and from tools, so a control character is replaced before it can reach the
terminal: an escape sequence could hide or rewrite the very line that is being read.
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from bench.pricing import PriceTable, PricingError, load_price_table
from prova.llm.usage import UsageLog, UsageRecord

Event = dict[str, Any]
CUT = 80  # how much of an argument, a message or a reply is shown
NOT_KNOWN = "n/a"
_TAGS = ("<untrusted_issue>", "</untrusted_issue>")


class ViewError(Exception):
    """The trajectory cannot be read, so there is nothing true to show."""


def load_events(path: Path) -> list[Event]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ViewError(f"cannot read {path}: {e.strerror or e}") from e
    events: list[Event] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event: object = json.loads(line)
        except ValueError as e:
            raise ViewError(f"line {number} of {path} is not JSON: {e}") from e
        if not isinstance(event, dict):
            raise ViewError(f"line {number} of {path} is not an event (a JSON object)")
        events.append(event)  # pyright: ignore[reportUnknownArgumentType]
    if not events:
        raise ViewError(f"{path} has no event")
    return events


def render(events: list[Event], table: PriceTable | None) -> str:
    results = {e.get("tool_use_id"): e for e in events if e.get("type") == "tool_result"}
    requests: dict[Any, Event] = {}
    lines = _header(events)
    tokens_in = tokens_out = turns = 0
    costs: list[float | None] = []
    for event in events:
        kind = event.get("type")
        if kind == "model_request":
            requests[event.get("turn")] = event
        elif kind == "model_response":
            turns += 1
            tokens_in += _count(event.get("input_tokens"))
            tokens_out += _count(event.get("output_tokens"))
            request = requests.get(event.get("turn"))
            cost = _cost(table, request, event)
            costs.append(cost)
            lines += _turn(event, request, cost)
        elif kind == "tool_call":
            lines.append(_tool(event, results.get(event.get("tool_use_id"))))
        elif kind == "hook_decision":
            lines.append(_hook(event))
        elif kind not in ("task", "model_request", "tool_result", "outcome"):
            lines.append(f"  ? unknown event {_clean(kind)!r}")
    lines.append(_summary(events, turns, tokens_in, tokens_out, costs))
    return "\n".join(lines)


# -- the parts ----------------------------------------------------------------------------------------


def _header(events: list[Event]) -> list[str]:
    first = events[0]
    found = next((e for e in events if e.get("type") == "task"), None)
    task: Event = found if found is not None else {}
    prompt = task.get("prompt_hash")
    shown = _clean(prompt[:8]) if isinstance(prompt, str) else NOT_KNOWN
    return [f"run {_clean(first.get('run_id'))}  prompt {shown}", f"task: {_first_line(task.get('task'))}"]


def _first_line(text: object) -> str:
    if not isinstance(text, str):
        return NOT_KNOWN
    for line in text.splitlines():
        if line.strip() and line.strip() not in _TAGS:
            return _cut(line.strip())
    return NOT_KNOWN


def _turn(response: Event, request: Event | None, cost: float | None) -> list[str]:
    took = _seconds(request, response)
    money = NOT_KNOWN if cost is None else f"${cost:.4f}"
    line = (
        f"turn {_clean(response.get('turn'))}  model {took}  in {_count(response.get('input_tokens'))} "
        f"out {_count(response.get('output_tokens'))}  {money}  {_clean(response.get('stop_reason'))}"
    )
    text = response.get("text")
    return [line, f"    says: {_cut(text)}"] if isinstance(text, str) and text.strip() else [line]


def _tool(call: Event, result: Event | None) -> str:
    arguments = _cut(json.dumps(call.get("input"), ensure_ascii=False))
    head = f"  → {_clean(call.get('tool'))} {arguments}"
    if call.get("decision") == "deny":
        return f"{head}  denied"
    if result is None:
        return f"{head}  no result in the log"
    ended = "ok" if result.get("success") is True else f"error {_clean(result.get('error_code'))}"
    return f"{head}  {ended} {_seconds(call, result)}  {_cut(result.get('message'))}"


def _hook(event: Event) -> str:
    action = event.get("action")
    flag = " " if action == "allow" else "!"
    message = event.get("message")
    said = f": {_cut(message)}" if isinstance(message, str) and message else ""
    return f"    {flag} hook {_clean(event.get('hook'))} {_clean(action)}{said}"


def _summary(
    events: list[Event], turns: int, tokens_in: int, tokens_out: int, costs: list[float | None]
) -> str:
    outcome = next((e for e in reversed(events) if e.get("type") == "outcome"), None)
    status = f"{_clean(outcome.get('status'))} " if outcome else "did not finish (no outcome in the log) "
    total = NOT_KNOWN if any(c is None for c in costs) else f"${sum(c for c in costs if c is not None):.4f}"
    return (
        f"{status}after {turns} turn(s) | tokens in {tokens_in} out {tokens_out} | cost {total} "
        f"| {_seconds(events[0], events[-1])}"
    )


# -- numbers and text ---------------------------------------------------------------------------------


def _cost(table: PriceTable | None, request: Event | None, response: Event) -> float | None:
    model = request.get("model") if request else None
    if table is None or not isinstance(model, str):
        return None
    try:
        pricing = table.pricing_for(model)
        log = UsageLog()
        log.add(UsageRecord(turn=1, input_tokens=_count(response.get("input_tokens")),
                            output_tokens=_count(response.get("output_tokens"))))  # fmt: skip
        return pricing.cost(log)
    except (PricingError, ValueError):
        return None  # a model the table does not know, or a prompt over the size the price holds for


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _seconds(start: Event | None, end: Event | None) -> str:
    try:
        began = datetime.fromisoformat(str((start or {})["ts"]))
        ended = datetime.fromisoformat(str((end or {})["ts"]))
        return f"{(ended - began).total_seconds():.1f}s"
    except (KeyError, ValueError, TypeError):
        return NOT_KNOWN


def _clean(value: object) -> str:
    """The text with every character that a terminal would act on (escape, bell, carriage return, newline)
    replaced by a space."""
    return "".join(ch if ch.isprintable() else " " for ch in str(value))


def _cut(value: object) -> str:
    text = _clean(value)
    return text if len(text) <= CUT else f"{text[:CUT]}…"


# -- the command line ---------------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m bench.view <trajectory.jsonl>")  # noqa: T201
        return 2
    try:
        table: PriceTable | None = load_price_table(Path(__file__).resolve().parent / "pricing.yaml")
    except PricingError as e:
        print(f"note: no costs, the price table cannot be read ({e})", file=sys.stderr)  # noqa: T201
        table = None
    try:
        print(render(load_events(Path(argv[0])), table))  # noqa: T201
    except ViewError as e:
        print(f"error: {e}")  # noqa: T201
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
