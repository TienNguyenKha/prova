"""The budget of a run: how many tool calls, how many seconds, how much money.

A budget is a fence, not a scale. Tool calls are checked before they run, so a reply that would go over the
limit is not run at all. Seconds and money are checked between turns: the turn that crosses the line has
already happened, so a run can end over the limit by the cost of one turn.
"""

from dataclasses import dataclass

from prova.llm.usage import UsageLog


@dataclass(frozen=True, slots=True)
class Pricing:
    """What the model costs, in dollars per million tokens. The numbers come from the person who runs the
    benchmark: they change, and a wrong table gives a wrong cost, so the table is data and not code."""

    input_per_mtok: float
    output_per_mtok: float
    max_prompt_tokens: int | None = None  # the price holds only for prompts up to this size

    def __post_init__(self) -> None:
        for name in ("input_per_mtok", "output_per_mtok"):
            if getattr(self, name) <= 0:
                raise ValueError(f"the price {name} must be above 0")

    def cost(self, usage: UsageLog) -> float:
        """Dollars spent so far. Tokens this price does not cover (cache tokens, or a prompt over
        `max_prompt_tokens`) are an error, not a guess."""
        for record in usage.records:
            if record.cache_creation_input_tokens or record.cache_read_input_tokens:
                raise ValueError(
                    f"turn {record.turn} used cache tokens and Pricing has no price for cache tokens"
                )
            if self.max_prompt_tokens is not None and record.input_tokens > self.max_prompt_tokens:
                raise ValueError(
                    f"turn {record.turn} sent {record.input_tokens} prompt tokens: "
                    f"the price holds only up to {self.max_prompt_tokens}"
                )
        spent = (
            usage.total_input_tokens * self.input_per_mtok + usage.total_output_tokens * self.output_per_mtok
        )
        return spent / 1_000_000


@dataclass(frozen=True, slots=True)
class Budget:
    max_tool_calls: int | None = None
    max_seconds: float | None = None
    max_cost_usd: float | None = None
    pricing: Pricing | None = None

    def __post_init__(self) -> None:
        for name in ("max_tool_calls", "max_seconds", "max_cost_usd"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be above 0 (or left out), got {value}")
        if self.max_cost_usd is not None and self.pricing is None:
            raise ValueError("max_cost_usd needs pricing: without a price the cost cannot be known")

    def check_tool_calls(self, *, done: int, asking: int) -> str | None:
        """Why the `asking` calls of the next reply may not run after `done` calls, or None if they may."""
        if self.max_tool_calls is not None and done + asking > self.max_tool_calls:
            total = done + asking
            return f"the run would make {total} tool calls, over the limit of {self.max_tool_calls}"
        return None

    def check_time_and_cost(self, *, seconds: float, usage: UsageLog) -> str | None:
        """Why the run may not make another model call, or None if it may."""
        if self.max_seconds is not None and seconds > self.max_seconds:
            return f"the run took {seconds:.1f} seconds, over the limit of {self.max_seconds:g} seconds"
        if self.max_cost_usd is not None and self.pricing is not None:
            spent = self.pricing.cost(usage)
            if spent > self.max_cost_usd:
                return f"the run cost ${spent:.4f}, over the limit of ${self.max_cost_usd:.4f}"
        return None
