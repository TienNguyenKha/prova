"""Small helpers for the sentences tools write for the model."""


def count_noun(n: int, singular: str, plural: str) -> str:
    """`1 file`, `2 files`: the right form for the number."""
    return f"{n} {singular if n == 1 else plural}"
