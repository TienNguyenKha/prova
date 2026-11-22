"""Words for why a model reply ended, safe to show to a person or write to a log."""

from anthropic.types import Message


def describe_refusal(message: Message) -> str:
    """`the model refused`, plus the category and explanation when the API gave them."""
    details = message.stop_details
    parts = [part for part in (details.category, details.explanation) if part] if details else []
    return "the model refused" + (": " + ", ".join(parts) if parts else "")
