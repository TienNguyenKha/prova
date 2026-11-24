"""submit_result: the agent hands in its final report. The tool only records it; the stop hook checks it."""

from prova.harness.permissions import Permission
from prova.harness.report import Report
from prova.tools.base import RunContext, ToolResult
from prova.tools.text import count_noun


class SubmitResultTool:
    name = "submit_result"
    description = (
        "Hand in your final report when you are done: every file you changed with the reason, the command "
        "that runs the tests, and what you assumed. A file you changed and did not list, or one you listed "
        "and did not change, will be sent back to you. Calling it again replaces the earlier report."
    )
    args_model = Report
    permission = Permission.READ  # it changes nothing: any run may report

    def run(self, args: Report, ctx: RunContext) -> ToolResult:
        previous = ctx.submission.submit(args)
        listed = count_noun(len(args.files_changed_with_reason), "file", "files")
        replaced = "; it replaces your earlier report" if previous is not None else ""
        return ToolResult.ok(f"report recorded: {listed} listed{replaced}")
