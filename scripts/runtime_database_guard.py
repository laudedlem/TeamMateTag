"""Hard database-size guard for compact live-season publishers."""
from __future__ import annotations

import os


DEFAULT_MAX_DATABASE_MIB = 350


def enforce_runtime_database_limit(cur) -> str:
    """Return the database size or raise before the caller commits its update."""
    raw_limit = os.environ.get("TEAMMATETAG_MAX_DATABASE_MIB", str(DEFAULT_MAX_DATABASE_MIB))
    try:
        max_mib = int(raw_limit)
    except ValueError as exc:
        raise RuntimeError("TEAMMATETAG_MAX_DATABASE_MIB must be a positive integer") from exc
    if max_mib <= 0:
        raise RuntimeError("TEAMMATETAG_MAX_DATABASE_MIB must be a positive integer")

    pretty, size_bytes = cur.execute(
        "SELECT pg_size_pretty(pg_database_size(current_database())), pg_database_size(current_database())"
    ).fetchone()
    limit_bytes = max_mib * 1024 * 1024
    if int(size_bytes) > limit_bytes:
        raise RuntimeError(
            f"runtime database is {pretty}, above the {max_mib} MiB safety ceiling; "
            "rolling back this live-season update"
        )
    return str(pretty)
