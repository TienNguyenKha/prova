import json
import re


def coder_node(state: AtlasState) -> dict:
    response = client.messages.create(
        model=CODER_MODEL,
        messages=build_coder_messages(state),
        max_tokens=2048,
    )

    text = response.content[0].text
    try:
        json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        changes = json.loads(json_match.group()) if json_match else {}
    except (json.JSONDecodeError, AttributeError):
        changes = {}

    print(f"\nCoder wrote {len(changes)} file(s): {list(changes.keys())}")
    existing = dict(state.get("code_changes") or {})
    existing.update(changes)
    return {"code_changes": existing}
