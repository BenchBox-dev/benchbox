from __future__ import annotations

from rich.markup import escape

from benchbox.cli.shared import console
from benchbox.core.results.anonymization import (
    PUBLIC_PSEUDONYM_SALT_ENV,
    ensure_public_pseudonym_salt,
    public_pseudonym_salt_path,
)


def ensure_publication_salt() -> str:
    salt, created = ensure_public_pseudonym_salt()
    if created:
        console.print(
            f"[dim]Created a private pseudonym salt at {escape(str(public_pseudonym_salt_path()))}. "
            "Keep it so your published pseudonyms stay stable; a new HOME or container creates a new one. "
            f"Set {PUBLIC_PSEUDONYM_SALT_ENV} to override.[/dim]"
        )
    return salt
