"""
Wiki manager — read agent + UNIVERSAL wikis, apply updates with diff logging,
track replace_section counters per Spec §8.

Per Framework §6 wiki schema. Read order matters (SCHEMA, index, log, process_rules,
reasoning_corrections, role-specific). UNIVERSAL rules can carry `excluded_for: [BR]`
markers per D-ARCH-031 — filtered out for the excluded agent.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from models import LogEntry, WikiUpdate
from state_manager import (
    LOCKS, atomic_append, atomic_save_json, atomic_write, load_json,
)


READ_ORDER = [
    "SCHEMA.md", "index.md", "log.md",
    "process_rules.md", "reasoning_corrections.md",
]
UNIVERSAL_READ_ORDER = [
    "manifesto.md", "cross_agent_rules.md", "case_index.md",
]


# Entry ID patterns we surface in consultation records
ENTRY_ID_PATTERN = re.compile(
    r"\b("
    r"PR-\d+"          # process rule
    r"|RC-\d+"          # reasoning correction
    r"|U[-_]?RC[-_]?\d+"  # universal reasoning correction
    r"|U-?\d+"          # universal rule (U1, U2, ...)
    r"|U-?RULE-?\d+"    # universal rule explicit
    r"|D-?\d+"          # SG decision
    r"|TD-?\d+"         # TG decision
    r"|GD-?\d+"         # SYS decision
    r")\b"
)


# log.md is append-only and can grow without bound (esp. if transient API errors
# get logged every tick). It's fed into each agent's context via read_all, so an
# unbounded log snowballs the prompt until it blows the model's context window —
# a self-reinforcing failure. Cap how much of the tail we load (~12-17k tokens).
LOG_READ_MAX_CHARS = 40_000


def _read(path: Path) -> str:
    with open(path) as f:
        return f.read()


def _read_log(path: Path, max_chars: int = LOG_READ_MAX_CHARS) -> str:
    """Read an agent log, bounded to the most recent ~max_chars. Trims to the
    first whole entry ('## [' heading) so we never start mid-entry, and prefixes
    a marker noting older entries were elided (the full file is still on disk)."""
    content = _read(path)
    if len(content) <= max_chars:
        return content
    tail = content[-max_chars:]
    cut = tail.find("\n## [")
    if cut != -1:
        tail = tail[cut + 1:]
    return ("[log truncated — older entries elided from context; "
            "full history in this agent's wiki/log.md on disk]\n\n" + tail)


def _extract_entry_ids(content: str) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for match in ENTRY_ID_PATTERN.finditer(content):
        token = match.group(1)
        if token not in seen:
            seen.add(token)
            ids.append(token)
    return ids


def _read_section(filepath: Path, section: str) -> str:
    """Return the contents of `## <section>` (or `### <section>`) up to next heading or EOF."""
    if not filepath.exists():
        return ""
    text = filepath.read_text()
    pattern = re.compile(
        rf"(?ms)^(#{{2,3}})\s+{re.escape(section)}\s*\n(.*?)(?=^#{{2,3}}\s|\Z)"
    )
    m = pattern.search(text)
    return m.group(2).strip() if m else ""


def _replace_section(filepath: Path, section: str, new_content: str) -> None:
    """Replace the body of `## <section>` keeping the heading. Append if missing."""
    text = filepath.read_text() if filepath.exists() else ""
    pattern = re.compile(
        rf"(?ms)(^#{{2,3}}\s+{re.escape(section)}\s*\n)(.*?)(?=^#{{2,3}}\s|\Z)"
    )
    if pattern.search(text):
        new_text = pattern.sub(rf"\1{new_content.rstrip()}\n\n", text)
    else:
        # Append as a new ## section
        sep = "" if text.endswith("\n") else "\n"
        new_text = text + f"{sep}\n## {section}\n\n{new_content.rstrip()}\n"
    atomic_write(filepath, new_text)


class WikiManager:

    def __init__(
        self,
        agents_dir: str | Path,
        universal_dir: str | Path,
        state_dir: str | Path,
        instance_lookup: Callable[[str], str],
        model_lookup: Callable[[str], str],
        replace_threshold_default: int = 10,
        replace_thresholds: dict[str, int] | None = None,
    ) -> None:
        self.agents_dir = Path(agents_dir)
        self.universal_dir = Path(universal_dir)
        self.state_dir = Path(state_dir)
        self.instance_of = instance_lookup
        self.model_of = model_lookup
        self.replace_threshold_default = replace_threshold_default
        self.replace_thresholds = replace_thresholds or {}
        self.counters_file = self.state_dir / "wiki_replace_counters.json"

    # ─── Reads ───────────────────────────────────────────────────────────────

    def read_all(self, agent_code: str) -> tuple[str, list[str]]:
        """Return (concatenated wiki content, list of entry IDs included)."""
        wiki_dir = self.agents_dir / agent_code / "wiki"
        wiki_dir.mkdir(parents=True, exist_ok=True)

        chunks: list[str] = []
        entries: list[str] = []
        seen: set[str] = set()

        # 1. Fixed order
        for filename in READ_ORDER:
            path = wiki_dir / filename
            if path.exists():
                # Bound log.md so an append-only log can't snowball the prompt.
                content = _read_log(path) if filename == "log.md" else _read(path)
                chunks.append(f"## {filename}\n{content}")
                for eid in _extract_entry_ids(content):
                    if eid not in seen:
                        seen.add(eid)
                        entries.append(eid)
            else:
                seen.add(filename)  # avoid re-reading
        # 2. Remaining role-specific files
        for path in sorted(wiki_dir.glob("*.md")):
            if path.name in READ_ORDER:
                continue
            content = _read(path)
            chunks.append(f"## {path.name}\n{content}")
            for eid in _extract_entry_ids(content):
                if eid not in seen:
                    seen.add(eid)
                    entries.append(eid)
        return "\n\n".join(chunks), entries

    def read_universal(self, agent_code: str | None = None) -> str:
        """Read UNIVERSAL files. Filter excluded_for rules if agent_code given."""
        chunks: list[str] = []
        for filename in UNIVERSAL_READ_ORDER:
            path = self.universal_dir / filename
            if not path.exists():
                continue
            content = _read(path)
            if agent_code and filename == "cross_agent_rules.md":
                content = self._filter_exclusions(content, agent_code)
            chunks.append(f"## {filename}\n{content}")
        # Append any additional UNIVERSAL pages (concept_graph.md, log_template.md, etc.)
        for path in sorted(self.universal_dir.glob("*.md")):
            if path.name in UNIVERSAL_READ_ORDER:
                continue
            chunks.append(f"## {path.name}\n{_read(path)}")
        return "\n\n".join(chunks)

    def _filter_exclusions(self, content: str, agent_code: str) -> str:
        """Remove sections with `excluded_for: [..., AGENT, ...]` per D-ARCH-031.

        Section format (per UNIVERSAL_GLOSSARY §3 — metadata order-independent):
            ## U-RULE-XX: Title
            excluded_for: [BR, TE]      ← order
            gov_reference: GOV-SYS-005  ← independent
            ---                          ← separator is optional
            <rule body>

        Implementation: split on ## headings, then parse each section's metadata
        lines individually. Previously a single regex required excluded_for to
        come first, silently passing rules through when authors wrote
        gov_reference first.
        """
        # Split on heading boundaries while keeping headings attached to bodies.
        parts = re.split(r"(?m)(?=^##\s+)", content)
        out: list[str] = []

        for part in parts:
            if not part.strip() or not part.startswith("## "):
                out.append(part)
                continue

            # Header line is the first line; metadata lines follow until we hit
            # a blank line, a `---` separator, or a non-metadata line.
            lines = part.split("\n")
            heading_line = lines[0]
            meta: dict[str, str] = {}
            body_start = 1

            for i, line in enumerate(lines[1:], start=1):
                stripped = line.strip()
                if stripped == "---":
                    body_start = i + 1  # consume the separator
                    break
                if not stripped:
                    body_start = i + 1
                    break
                m = re.match(r"^(\w+):\s*(.*)$", stripped)
                if not m:
                    body_start = i  # first non-metadata line → start of body
                    break
                meta[m.group(1)] = m.group(2)
                body_start = i + 1

            exclusions_raw = meta.get("excluded_for", "")
            # Strip [ ] if list-style.
            exclusions_raw = exclusions_raw.strip().lstrip("[").rstrip("]")
            excluded = [s.strip() for s in exclusions_raw.split(",") if s.strip()]
            gov_ref = meta.get("gov_reference", "").strip()

            if agent_code in excluded:
                out.append(
                    f"{heading_line}\n"
                    f"[Rule excluded for {agent_code}"
                    f"{f' — pending {gov_ref} resolution' if gov_ref else ''}]\n\n"
                )
            else:
                out.append(part)

        return "".join(out)

    # ─── Writes ──────────────────────────────────────────────────────────────

    async def apply_updates(self, agent_code: str, updates: list[WikiUpdate]) -> bool:
        """Apply WIKI_UPDATE blocks. Returns True if any replace threshold tripped.

        Async because Spec §16.1 requires asyncio.Lock around wiki_replace_counters.json
        writes (see _bump_replace_counter).
        """
        wiki_dir = self.agents_dir / agent_code / "wiki"
        wiki_dir.mkdir(parents=True, exist_ok=True)
        threshold_tripped = False

        for update in updates:
            if update.target == "universal":
                # SYS-only path — handled by apply_universal_update()
                self.apply_universal_update(update)
                continue

            filepath = wiki_dir / update.file

            if update.action == "replace_section":
                if not update.section:
                    self.append_log(agent_code, LogEntry(
                        "WARNING | replace_section without section — skipped"
                    ))
                    continue
                before = _read_section(filepath, update.section)
                _replace_section(filepath, update.section, update.content)
                self.append_log(agent_code, LogEntry(
                    f"WIKI_REPLACE | {update.file} § {update.section} | "
                    f"Justification: {update.justification or 'NONE PROVIDED'} | "
                    f"Before: {before[:200]}... | After: {update.content[:200]}..."
                ))
                if not update.justification:
                    self.append_log(agent_code, LogEntry(
                        "WARNING | replace_section without justification — flagged for SYS review"
                    ))
                tripped = await self._bump_replace_counter(agent_code)
                threshold_tripped = tripped or threshold_tripped

            elif update.action == "append":
                atomic_append(filepath, _ensure_trailing_blank(update.content))
                self.append_log(agent_code, LogEntry(
                    f"WIKI_APPEND | {update.file} | {update.content[:200]}..."
                ))

            elif update.action == "new_entry":
                atomic_append(filepath, "\n\n" + _ensure_trailing_blank(update.content))
                self.append_log(agent_code, LogEntry(
                    f"WIKI_NEW_ENTRY | {update.file} | {update.content[:100]}..."
                ))
            else:
                self.append_log(agent_code, LogEntry(
                    f"WARNING | unknown wiki update action: {update.action}"
                ))

        return threshold_tripped

    def apply_universal_update(self, update: WikiUpdate) -> None:
        """SYS writes to UNIVERSAL (autonomous per D-ARCH-031).

        Spec v5 §8.2 / audit P1-3: UNIVERSAL writes get the SAME diff logging as
        own-wiki writes. UNIVERSAL is read by all 9 agents on every execution, so
        silent SYS edits have systemic impact with no audit trail. We log to
        SYS/wiki/log.md (where SYS sees its own write history) and mirror to
        universal/log.md so the change is discoverable from the UNIVERSAL side too.
        """
        path = self.universal_dir / update.file
        path.parent.mkdir(parents=True, exist_ok=True)
        if update.action == "replace_section" and update.section:
            before = _read_section(path, update.section)
            _replace_section(path, update.section, update.content)
            self._log_universal(
                f"UNIVERSAL_REPLACE | {update.file} § {update.section} | "
                f"Justification: {update.justification or 'NONE PROVIDED'} | "
                f"Before: {before[:200]}... | After: {update.content[:200]}..."
            )
        elif update.action == "append":
            atomic_append(path, _ensure_trailing_blank(update.content))
            self._log_universal(
                f"UNIVERSAL_APPEND | {update.file} | {update.content[:200]}..."
            )
        else:
            atomic_append(path, "\n\n" + _ensure_trailing_blank(update.content))
            self._log_universal(
                f"UNIVERSAL_NEW_ENTRY | {update.file} | {update.content[:200]}..."
            )

    def _log_universal(self, message: str) -> None:
        """Record a UNIVERSAL write to SYS's own log and mirror to the UNIVERSAL
        log so the change is visible from both sides (audit P1-3)."""
        self.append_log("SYS", LogEntry(message))
        universal_log = self.universal_dir / "log.md"
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        atomic_append(universal_log, f"## [{ts}] SYS | {message}\n")

    def append_log(self, agent_code: str, entry: LogEntry) -> None:
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        instance = self.instance_of(agent_code)
        model = self.model_of(agent_code)
        line = f"## [{ts}] {instance} ({model}) | {entry.content.rstrip()}\n"
        atomic_append(self.agents_dir / agent_code / "wiki" / "log.md", line)

    def read_log(self, agent_code: str, n: int = 10) -> str:
        """Return the last `n` log entries from an agent's wiki/log.md (NEW-3 —
        shared by Telegram /log and MCP vega_log so both stay in sync). An entry
        starts at a `## [YYYY-MM-DD ...]` header; if no headers parse (legacy
        free-form logs), fall back to the trailing slice."""
        import re
        path = self.agents_dir / agent_code.upper() / "wiki" / "log.md"
        if not path.exists():
            return ""
        text = path.read_text()
        entry_re = re.compile(r"(?m)^## \[\d{4}-\d{2}-\d{2}")
        matches = list(entry_re.finditer(text))
        if not matches:
            return text[-3500:]
        offsets = [m.start() for m in matches] + [len(text)]
        entries = [text[offsets[i]:offsets[i + 1]] for i in range(len(matches))]
        return "".join(entries[-n:])

    # ─── Replace threshold tracking ──────────────────────────────────────────

    async def _bump_replace_counter(self, agent_code: str) -> bool:
        """Spec §16.1 — wiki_replace_counters.json requires asyncio.Lock to avoid
        undercount under concurrent agent executions (asyncio.gather).

        The threshold comparison AND the THRESHOLD log emission must also live
        inside the lock: otherwise, two concurrent calls can both observe
        `count >= threshold` (because the post-bump value crosses the boundary
        for both readers) and emit the THRESHOLD log twice for the same trip.
        """
        async with LOCKS.get("wiki_replace_counters"):
            counters = load_json(self.counters_file, default={})
            count = counters.get(agent_code, 0) + 1
            counters[agent_code] = count
            atomic_save_json(self.counters_file, counters)
            threshold = self.replace_thresholds.get(
                agent_code, self.replace_threshold_default
            )
            if count >= threshold:
                self.append_log(agent_code, LogEntry(
                    f"THRESHOLD | {count} replace_sections since last SYS audit — "
                    f"flagged for immediate SYS review"
                ))
                return True
        return False

    def reset_replace_counters(self) -> None:
        """Called after each SYS run."""
        atomic_save_json(self.counters_file, {})

    def read_all_agents(self, agent_codes: list[str]) -> dict[str, str]:
        return {code: self.read_all(code)[0] for code in agent_codes}

    def read_all_logs(self, agent_codes: list[str]) -> dict[str, str]:
        # Bounded per-log so a SYS audit (which reads every agent's log) can't be
        # blown out by one runaway log either.
        result = {}
        for code in agent_codes:
            path = self.agents_dir / code / "wiki" / "log.md"
            result[code] = _read_log(path) if path.exists() else ""
        return result


def _ensure_trailing_blank(s: str) -> str:
    s = s.rstrip()
    return s + "\n"
