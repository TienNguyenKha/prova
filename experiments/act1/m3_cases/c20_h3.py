def unique_emails(users: list[dict]) -> list[str]:
    result: list[str] = []
    for user in users:
        if user["email"].lower() not in [e.lower() for e in result]:
            result.append(user["email"])
    return result
