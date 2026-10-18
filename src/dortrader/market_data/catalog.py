"""Instrument catalog lifecycle: what was listed, delisted or changed between two loads."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from dortrader.domain import Instrument


@dataclass(frozen=True)
class StateChange:
    name: str
    old: str
    new: str


@dataclass(frozen=True)
class CatalogDiff:
    added: tuple[Instrument, ...]
    removed: tuple[str, ...]
    state_changes: tuple[StateChange, ...]
    changed: tuple[str, ...]  # any attribute other than state

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.state_changes or self.changed)


def diff_catalog(previous: Mapping[str, Instrument], current: Sequence[Instrument]) -> CatalogDiff:
    now = {i.name: i for i in current}
    added = tuple(i for name, i in now.items() if name not in previous)
    removed = tuple(sorted(name for name in previous if name not in now))
    state_changes: list[StateChange] = []
    changed: list[str] = []
    for name, new in now.items():
        old = previous.get(name)
        if old is None:
            continue
        if old.state != new.state:
            state_changes.append(StateChange(name, old.state, new.state))
        if old.model_copy(update={"state": new.state}) != new:
            changed.append(name)
    return CatalogDiff(added, removed, tuple(state_changes), tuple(changed))
