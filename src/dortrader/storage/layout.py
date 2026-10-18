"""Where each dataset lives under the data directory. The recorder writes and replay reads."""

from pathlib import Path

SOURCE = "source=deribit"


def raw_dir(data_dir: Path) -> Path:
    return data_dir / "raw" / SOURCE / "channel=book_summary"


def quotes_dir(data_dir: Path) -> Path:
    return data_dir / "normalized" / SOURCE / "channel=book_summary"


def catalog_dir(data_dir: Path) -> Path:
    return data_dir / "catalog" / SOURCE


def events_dir(data_dir: Path) -> Path:
    return data_dir / "events"
