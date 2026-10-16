"""In-memory ``FeatureFlagReaderPort`` for tests (NM-680 kill switches)."""

from src.domain.ports.feature_flag_reader_port import FeatureFlagReaderPort


class StaticFeatureFlagReader(FeatureFlagReaderPort):
    """Answers from ``flags``; a missing name is ``default`` (off), like the DB reader."""

    def __init__(self, flags: dict[str, bool] | None = None) -> None:
        self.flags = dict(flags or {})
        self.reads: list[str] = []

    async def is_enabled(self, name: str, default: bool = False) -> bool:
        self.reads.append(name)
        return self.flags.get(name, default)

    def set(self, name: str, enabled: bool = True) -> None:
        self.flags[name] = enabled
