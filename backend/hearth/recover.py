"""Account recovery CLI — there's no mail server, so a locked-out operator
mints a one-time reset token from a shell on the box:

    docker compose exec hearth python -m hearth.recover you@example.com

It prints a single-use token (valid 1 hour); open `/reset` in the web UI and
paste it with a new password. Requires shell access to the host — which is the
right bar for "I forgot my password" on a self-hosted, single-tenant box.
"""
from __future__ import annotations

import sys

from . import security
from .adapters.app_db import AppDb
from .config import settings


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m hearth.recover <email>")
        return 2
    email = argv[0]
    repo = AppDb(settings.db_path)
    repo.migrate()
    user = repo.user_by_email(email)
    if user is None:
        print(f"No Hearth account found for {email!r}.")
        return 1
    token, sha = security.mint_reset_token()
    repo.create_reset_token(user.id, sha, hours=1)
    base = repo.get_setting("hearth_base_url") or f"http://localhost:{settings.port}"
    print("\n  Recovery token for %s (valid 1 hour, single use):\n" % user.email)
    print(f"      {token}\n")
    print(f"  Open  {base.rstrip('/')}/reset  and paste it with your new password.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
