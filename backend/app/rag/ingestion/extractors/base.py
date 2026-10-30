from dataclasses import dataclass, field


class ExtractionError(Exception):
    """A file could not be turned into text. `reason` is a short machine-readable code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class RawLine:
    text: str
    heading: bool = False  # True when the source format says so (e.g. a DOCX heading style)


@dataclass
class RawPage:
    number: int | None
    lines: list[RawLine] = field(default_factory=list)
