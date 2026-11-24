"""Hooks: rules that run around the agent loop. A hook can only make a run stricter, never looser.

Where a hook runs decides what it may do:
- before a tool (`pre_tool_use`): say no. The first `deny` wins and the hooks after it are not called.
- after a tool (`post_tool_use`): add information for the model. The tool has already run, so it cannot block.
- when the model wants to stop (`stop`): say "not yet" and keep the run going.

A hook that fails, or answers with something that is not a HookDecision, counts as a no where a no is
possible: a rule that cannot be checked is not a rule that passed. Permissions and path checks are NOT hooks:
they are enforced in ToolRegistry.dispatch, and nothing here can lift them.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, Self

from prova.tools.base import RunContext, ToolResult

HookKind = Literal["pre_tool_use", "post_tool_use", "stop"]
Action = Literal["allow", "deny", "continue"]
_ACTIONS = ("allow", "deny", "continue")


@dataclass(frozen=True, slots=True)
class HookEvent:
    kind: HookKind
    ctx: RunContext
    tool: str | None = None
    args: Mapping[str, Any] | None = None
    result: ToolResult | None = None  # only after a tool ran


@dataclass(frozen=True, slots=True)
class HookDecision:
    action: Action
    message: str = ""  # what the model is told; required when the answer is deny or continue

    def __post_init__(self) -> None:
        if self.action not in _ACTIONS:
            raise ValueError(f"unknown hook action {self.action!r}")
        if self.action != "allow" and not self.message.strip():
            raise ValueError(f"a {self.action!r} decision needs a message: the model reads it")

    @classmethod
    def allow(cls, message: str = "") -> Self:
        return cls("allow", message)

    @classmethod
    def deny(cls, message: str) -> Self:
        return cls("deny", message)

    @classmethod
    def continue_run(cls, message: str) -> Self:
        return cls("continue", message)


class Hook(Protocol):
    def __call__(self, ev: HookEvent) -> HookDecision: ...


@dataclass(frozen=True, slots=True)
class Hooks:
    """The hooks of a run, in the order they are called. Fixed when the run starts."""

    pre: tuple[Hook, ...] = ()
    post: tuple[Hook, ...] = ()
    stop: tuple[Hook, ...] = ()
    max_stop_denials: int = 2  # how often `stop` may keep the run going; after that it ends unverified

    def __post_init__(self) -> None:
        if self.max_stop_denials < 0:
            raise ValueError("max_stop_denials cannot be negative")


@dataclass(frozen=True, slots=True)
class HookOutcome:
    """What one hook did, for the trajectory."""

    hook: str
    action: Action  # the effective action, after the rules above (a failed pre hook is a deny)
    message: str
    error: str | None  # why the hook failed or misbehaved; only the log sees this, never the model


@dataclass(frozen=True, slots=True)
class Verdict:
    blocked: bool  # before a tool: it must not run. At stop: the run must go on
    message: str  # for the model, when blocked
    outcomes: tuple[HookOutcome, ...]  # one per hook that was called, in order
    notes: tuple[str, ...]  # after a tool: texts to show the model next to the result


def hook_name(hook: object) -> str:
    """A `name` attribute, else the function's name, else the class's name."""
    name = getattr(hook, "name", None)
    if isinstance(name, str) and name:
        return name
    function_name = getattr(hook, "__name__", None)
    if isinstance(function_name, str) and function_name:
        return function_name
    return type(hook).__name__


def run_hooks(hooks: Sequence[Hook], ev: HookEvent) -> Verdict:
    """Call `hooks` in order for `ev` and apply the rules of its kind."""
    if ev.kind == "pre_tool_use":
        return _run_pre(hooks, ev)
    if ev.kind == "post_tool_use":
        return _run_post(hooks, ev)
    if ev.kind == "stop":
        return _run_stop(hooks, ev)
    raise ValueError(f"unknown hook kind {ev.kind!r}")


def _call(hook: Hook, ev: HookEvent) -> tuple[HookDecision | None, str | None]:
    """The hook's decision, or why there is none. An exception is caught here and nowhere else: it is the
    hook's failure, and what to do about it depends on the kind."""
    try:
        decision = hook(ev)
    except Exception as e:  # noqa: BLE001 - any failure of a hook is handled as a failure, never let through
        return None, f"{type(e).__name__}: {e}"
    if not isinstance(decision, HookDecision):  # pyright: ignore[reportUnnecessaryIsInstance]
        return None, f"returned {type(decision).__name__}, not a HookDecision"
    return decision, None


def _run_pre(hooks: Sequence[Hook], ev: HookEvent) -> Verdict:
    outcomes: list[HookOutcome] = []
    for hook in hooks:
        name = hook_name(hook)
        decision, error = _call(hook, ev)
        if decision is None:
            message = f"the hook {name!r} failed, so the action is not allowed"
            outcomes.append(HookOutcome(name, "deny", message, error))
            return Verdict(True, message, tuple(outcomes), ())
        if decision.action == "deny":
            outcomes.append(HookOutcome(name, "deny", decision.message, None))
            return Verdict(True, decision.message, tuple(outcomes), ())
        if decision.action == "continue":
            message = f"the hook {name!r} gave an answer with no meaning before a tool: not allowed"
            outcomes.append(HookOutcome(name, "deny", message, "answered 'continue' before a tool"))
            return Verdict(True, message, tuple(outcomes), ())
        outcomes.append(HookOutcome(name, "allow", decision.message, None))
    return Verdict(False, "", tuple(outcomes), ())


def _run_post(hooks: Sequence[Hook], ev: HookEvent) -> Verdict:
    outcomes: list[HookOutcome] = []
    notes: list[str] = []
    for hook in hooks:  # every hook runs: a failure of one must not hide the others
        name = hook_name(hook)
        decision, error = _call(hook, ev)
        if decision is None:
            notes.append(f"[{name}] this check did not run: the hook failed")
            outcomes.append(HookOutcome(name, "allow", "", error))
        elif decision.action != "allow":
            notes.append(f"[{name}] ignored: a hook that runs after the tool can only add information")
            misuse = f"answered {decision.action!r} after a tool"
            outcomes.append(HookOutcome(name, "allow", decision.message, misuse))
        else:
            if decision.message:
                notes.append(f"[{name}] {decision.message}")
            outcomes.append(HookOutcome(name, "allow", decision.message, None))
    return Verdict(False, "", tuple(outcomes), tuple(notes))


def _run_stop(hooks: Sequence[Hook], ev: HookEvent) -> Verdict:
    outcomes: list[HookOutcome] = []
    for hook in hooks:
        name = hook_name(hook)
        decision, error = _call(hook, ev)
        if decision is None:
            message = f"the hook {name!r} failed, so the run could not be checked and may not end yet"
            outcomes.append(HookOutcome(name, "continue", message, error))
            return Verdict(True, message, tuple(outcomes), ())
        if decision.action in ("continue", "deny"):  # a deny at stop can only mean "not yet"
            outcomes.append(HookOutcome(name, "continue", decision.message, None))
            return Verdict(True, decision.message, tuple(outcomes), ())
        outcomes.append(HookOutcome(name, "allow", decision.message, None))
    return Verdict(False, "", tuple(outcomes), ())
