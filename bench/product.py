"""The agent of the product, as the benchmark runs it: the same profile, hooks, prompt and contract.

The benchmark measures the product, so nothing here is special for the benchmark. The one thing that is not
the product's is the budget: it comes from the task. The contract is checked once, when the agent is made,
so a rule that nothing enforces stops the whole benchmark before it spends anything.
"""

import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from bench.runner import AgentRun
from bench.task import Task
from prova.agent.budget import Budget, Pricing
from prova.agent.loop import AgentResult, run_agent
from prova.app import App
from prova.contract import VerifyContext, check_contract, load_contract
from prova.harness.hooks import Hooks, hook_name
from prova.harness.permissions import Profile
from prova.harness.redact import Redactor
from prova.harness.trajectory import TrajectoryWriter
from prova.harness.verifiers import (
    AttacksWrittenBeforeStop,
    FilesListed,
    LintAfterEdit,
    ReproFailsBeforeStop,
    TestsBeforeStop,
)
from prova.mechanisms import builtin_mechanisms
from prova.prompts.render import load_layers, render_system, render_user_context
from prova.sandbox.base import Sandbox
from prova.tools.base import RunContext

MAX_ITERATIONS = 30  # a backstop: the budget of the task is what normally ends a run


@dataclass(frozen=True, slots=True)
class Role:
    """What a run of the product is for: its profile, its contract (contract.yaml and AGENTS.md in one folder)
    and the hooks its contract names. A fix run must make the tests pass; a reproduction run (M13b) must write
    tests in repro/ that fail for the right reason."""

    profile: str
    contract: str  # the folder of the contract, from the root of the project
    hooks: Callable[[], Hooks]


ROLES: dict[str, Role] = {
    "fix": Role(
        "fix", "contract", lambda: Hooks(post=(LintAfterEdit(),), stop=(FilesListed(), TestsBeforeStop()))
    ),
    "repro": Role(
        "repro",
        "contract/repro",
        lambda: Hooks(post=(LintAfterEdit(),), stop=(FilesListed(), ReproFailsBeforeStop())),
    ),
    "adversary": Role(
        "adversary",
        "contract/adversary",
        lambda: Hooks(post=(LintAfterEdit(),), stop=(FilesListed(), AttacksWrittenBeforeStop())),
    ),
}


@dataclass(frozen=True, slots=True)
class ProductAgent:
    """The product's agent for one benchmark. Call it with a task, a workspace and a trajectory path."""

    app: App
    profile: Profile
    hooks: Callable[[], Hooks]  # made fresh for each run
    system: str
    prompt_hash: str
    pricing: Pricing
    open_sandbox: Callable[[Path], AbstractContextManager[Sandbox]]
    clock: Callable[[], datetime]
    config: dict[str, Any]

    def __call__(self, task: Task, workspace: Path, trajectory: Path) -> AgentRun:
        redactor = Redactor.from_environ(os.environ)
        writer = TrajectoryWriter(trajectory, run_id=trajectory.stem, clock=self.clock, redactor=redactor)
        budget = Budget(
            max_tool_calls=task.budget.max_tool_calls,
            max_seconds=task.budget.max_seconds,
            max_cost_usd=task.budget.max_usd,
            pricing=self.pricing,
        )
        with self.open_sandbox(workspace) as sandbox:  # closed whatever the run does
            result = self.attempt(
                render_user_context(task.issue),  # the issue is data from somebody else: it goes in a tag
                sandbox=sandbox,
                workspace=workspace,
                writer=writer,
                budget=budget,
                redactor=redactor,
            )
        return AgentRun(status=result.status, detail=result.detail, usage=result.usage)

    def attempt(  # noqa: PLR0913 - one run of the product, everything named
        self,
        prompt: str,
        *,
        sandbox: Sandbox,
        workspace: Path,
        writer: TrajectoryWriter,
        budget: Budget,
        redactor: Redactor,
    ) -> AgentResult:
        """One run of the product's loop on `prompt`, in a sandbox and a workspace that the caller opened."""
        ctx = RunContext.from_profile(self.profile, workspace, strict=True, sandbox=sandbox)
        return run_agent(
            prompt,
            llm=self.app.llm,
            tools=self.app.tools,
            ctx=ctx,
            settings=self.app.settings,
            system=self.system,
            prompt_hash=self.prompt_hash,
            max_iterations=MAX_ITERATIONS,
            trajectory=writer,
            redactor=redactor,
            hooks=self.hooks(),
            budget=budget,
        )


def make_agent(  # noqa: PLR0913 - everything the product needs, named
    *,
    app: App,
    profiles: dict[str, Profile],
    root: Path,
    pricing: Pricing,
    open_sandbox: Callable[[Path], AbstractContextManager[Sandbox]],
    clock: Callable[[], datetime],
    role: str = "fix",
) -> ProductAgent:
    """Check the contract of the role against this configuration, then fix the prompt. Raises ContractError if
    a rule of the contract has no mechanism that holds: nothing is run in that case."""
    if role not in ROLES:
        raise ValueError(f"no role {role!r}; known: {', '.join(ROLES)}")
    chosen = ROLES[role]
    profile = profiles[chosen.profile]
    hooks = chosen.hooks()
    contract = load_contract(root / chosen.contract / "contract.yaml")
    check_contract(
        contract,
        builtin_mechanisms(),
        VerifyContext(profiles=profiles, hooks=hooks, tools=app.tools, project_root=root),
    )
    agents_text = (root / chosen.contract / "AGENTS.md").read_text(encoding="utf-8")
    prompt = render_system(
        tools=app.tools.to_api_tools(profile.allow), contract_text=agents_text, layers=load_layers()
    )
    config: dict[str, Any] = {
        "model": app.settings.model,
        "profile": chosen.profile,
        "hooks": [hook_name(hook) for hook in (*hooks.post, *hooks.stop)],
        "prompt_hash": prompt.hash,
        "max_iterations": MAX_ITERATIONS,
    }
    return ProductAgent(
        app=app,
        profile=profile,
        hooks=chosen.hooks,
        system=prompt.system,
        prompt_hash=prompt.hash,
        pricing=pricing,
        open_sandbox=open_sandbox,
        clock=clock,
        config=config,
    )
