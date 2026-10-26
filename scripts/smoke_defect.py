def is_authorized(token: str, verify) -> bool:
    return bool(verify(token))
