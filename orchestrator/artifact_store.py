"""
Artifact storage: inbox, outbox, archive.

Per Spec §7. The archive is IMMUTABLE — once an artifact is written, it is never
modified. Routing operates on outbox→archive→inbox copies, not on shared references.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from models import Artifact, PRIORITY_ORDER
from state_manager import LOCKS, atomic_write


def _read_text(path: str | Path) -> str:
    with open(path) as f:
        return f.read()


class Inbox:
    """Per-agent inbox. Files have status: unprocessed | processed."""

    def __init__(self, agent_dir: str | Path) -> None:
        self.dir = Path(agent_dir) / "inbox"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _list_files(self) -> list[Path]:
        return [p for p in sorted(self.dir.glob("*.md"))]

    def _load(self, path: Path) -> Artifact:
        return Artifact.from_markdown(_read_text(path), filename=path.name)

    def has_unprocessed(self) -> bool:
        for path in self._list_files():
            try:
                if self._load(path).status == "unprocessed":
                    return True
            except Exception:
                continue
        return False

    def peek(self) -> Artifact | None:
        """First unprocessed artifact (no sort), used for cycle lookup."""
        items = self.get_unprocessed()
        return items[0] if items else None

    def get_unprocessed(self) -> list[Artifact]:
        """All unprocessed artifacts, sorted by (priority, timestamp)."""
        artifacts: list[Artifact] = []
        for path in self._list_files():
            try:
                a = self._load(path)
            except Exception:
                continue
            if a.status == "unprocessed":
                artifacts.append(a)
        return sorted(
            artifacts,
            key=lambda a: (PRIORITY_ORDER.get(a.priority or "P2", 2), a.timestamp),
        )

    def mark_processed(self, artifacts: list[Artifact]) -> None:
        """Rewrite each artifact with status=processed."""
        for a in artifacts:
            if not a.filename:
                continue
            a.status = "processed"
            atomic_write(self.dir / a.filename, a.to_markdown())

    def deliver(self, artifact: Artifact) -> Path:
        """Drop an artifact into the inbox. Filename is the artifact id."""
        artifact.status = "unprocessed"
        filename = f"{artifact.id}.md"
        path = self.dir / filename
        atomic_write(path, artifact.to_markdown())
        artifact.filename = filename
        return path


class Outbox:
    """Per-agent outbox. Router drains it each loop iteration."""

    def __init__(self, agent_dir: str | Path) -> None:
        self.dir = Path(agent_dir) / "outbox"
        self.dir.mkdir(parents=True, exist_ok=True)

    def place(self, artifact: Artifact) -> Path:
        if not artifact.id:
            raise ValueError("Cannot place artifact without id in outbox")
        filename = f"{artifact.id}.md"
        path = self.dir / filename
        atomic_write(path, artifact.to_markdown())
        artifact.filename = filename
        return path

    def list_pending(self) -> list[Artifact]:
        out: list[Artifact] = []
        for path in sorted(self.dir.glob("*.md")):
            try:
                out.append(Artifact.from_markdown(_read_text(path), filename=path.name))
            except Exception:
                continue
        return out

    def clear(self, artifact: Artifact) -> None:
        if not artifact.filename:
            return
        p = self.dir / artifact.filename
        if p.exists():
            p.unlink()


class Archive:
    """Immutable artifact archive. Never modified after write (Spec §7.4)."""

    def __init__(self, archive_dir: str | Path) -> None:
        self.dir = Path(archive_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def write(self, artifact: Artifact) -> Path:
        if not artifact.id:
            raise ValueError("Cannot archive artifact without id")
        path = self.dir / f"{artifact.id}.md"
        if path.exists():
            # Already archived (e.g., multi-recipient routing). Don't rewrite.
            return path
        artifact.status = "archived"
        atomic_write(path, artifact.to_markdown())
        return path

    def load(self, artifact_id: str) -> Artifact | None:
        path = self.dir / f"{artifact_id}.md"
        if not path.exists():
            return None
        return Artifact.from_markdown(_read_text(path), filename=path.name)

    def get_recent(self, since: str | None = None) -> list[Artifact]:
        """Return archived artifacts with timestamp > `since` (ISO 8601 string).
        If `since` is None, returns all archived artifacts.

        Spec §5.6 — SYS inbox composition consumes this to audit cross-agent
        artifact flow since its last run.
        """
        out: list[Artifact] = []
        for path in sorted(self.dir.glob("*.md")):
            try:
                a = Artifact.from_markdown(_read_text(path), filename=path.name)
            except Exception:
                continue
            if since is None or a.timestamp > since:
                out.append(a)
        return sorted(out, key=lambda x: x.timestamp)


class ArtifactStore:
    """Facade combining per-agent Inbox/Outbox + the global Archive."""

    def __init__(self, agents_dir: str | Path, archive_dir: str | Path) -> None:
        self.agents_dir = Path(agents_dir)
        self.archive = Archive(archive_dir)
        self._inboxes: dict[str, Inbox] = {}
        self._outboxes: dict[str, Outbox] = {}

    def inbox(self, agent_code: str) -> Inbox:
        if agent_code not in self._inboxes:
            self._inboxes[agent_code] = Inbox(self.agents_dir / agent_code)
        return self._inboxes[agent_code]

    def outbox(self, agent_code: str) -> Outbox:
        if agent_code not in self._outboxes:
            self._outboxes[agent_code] = Outbox(self.agents_dir / agent_code)
        return self._outboxes[agent_code]

    def get_outbox(self, agent_code: str) -> list[Artifact]:
        return self.outbox(agent_code).list_pending()

    def archive_artifact(self, artifact: Artifact) -> Path:
        return self.archive.write(artifact)

    def get_recent_artifacts(self, since: str | None = None) -> list[Artifact]:
        return self.archive.get_recent(since)

    def clear_from_outbox(self, agent_code: str, artifact: Artifact) -> None:
        self.outbox(agent_code).clear(artifact)
