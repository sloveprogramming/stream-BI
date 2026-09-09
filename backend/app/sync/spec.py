import re
from dataclasses import dataclass

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote_ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"非法标识符: {name!r}")
    return name


def quote_qualified(name: str) -> str:
    parts = name.split(".")
    if len(parts) == 2:
        return f"{quote_ident(parts[0])}.{quote_ident(parts[1])}"
    return quote_ident(name)


@dataclass(frozen=True)
class SyncSpec:
    source_table: str
    columns: list[str]
    pk_columns: list[str]
    incremental_field: str | None


def spec_from_task(task) -> SyncSpec:
    return SyncSpec(
        source_table=task.source_table,
        columns=list(task.columns),
        pk_columns=list(task.pk_columns),
        incremental_field=task.incremental_field,
    )
