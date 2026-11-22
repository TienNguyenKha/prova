"""Turn a Pydantic ValidationError into a short message that is safe to show to a model or write to a log."""

from pydantic import ValidationError


def describe_errors(error: ValidationError) -> str:
    """Name each offending field and the rule it broke, without echoing the value that was sent."""
    problems = [f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}" for e in error.errors()]
    return "; ".join(problems)
