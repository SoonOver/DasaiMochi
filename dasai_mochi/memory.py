"""Cross-platform persistent companion memory (`facts` + recent conversation)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

_REMEMBER_TAG_RE = re.compile(r"\[\s*(?:remember|ingat)\s*:\s*([^\]]+)\]", re.IGNORECASE)


def default_memory_path() -> Path:
    """Return cross-platform user data path for persistent companion memory."""
    override = os.environ.get("DASAI_MOCHI_DATA_DIR")
    if override:
        return Path(override) / "memory.json"
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        root = Path(base) if base else (Path.home() / "AppData" / "Local")
        return root / "DasaiMochi" / "memory.json"
    xdg_data = os.environ.get("XDG_DATA_HOME")
    root = Path(xdg_data) if xdg_data else (Path.home() / ".local" / "share")
    return root / "dasai-mochi" / "memory.json"


def extract_memory_tags(raw_text: str) -> tuple[list[str], str]:
    """Extract `[remember: <fact>]` tags and strip them from `raw_text`."""
    facts: list[str] = []
    for match in _REMEMBER_TAG_RE.finditer(raw_text):
        fact = re.sub(r"\s+", " ", match.group(1)).strip()
        if fact:
            facts.append(fact[:180])
    cleaned = _REMEMBER_TAG_RE.sub("", raw_text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return facts, cleaned


@dataclass
class MemoryStore:
    """JSON-backed companion memory store."""

    path: Path = field(default_factory=default_memory_path)
    facts: list[str] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)
    max_facts: int = 30
    max_history: int = 40

    def load(self) -> None:
        if not self.path.exists():
            self.facts = []
            self.history = []
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.facts = []
            self.history = []
            return
        if not isinstance(data, dict):
            return
        raw_facts = data.get("facts") or []
        raw_hist = data.get("history") or []
        self.facts = [str(f).strip() for f in raw_facts if isinstance(f, str) and f.strip()][-self.max_facts :]
        cleaned_hist: list[dict[str, str]] = []
        for item in raw_hist:
            if (
                isinstance(item, dict)
                and item.get("role") in ("user", "assistant")
                and isinstance(item.get("content"), str)
                and item["content"].strip()
            ):
                cleaned_hist.append({"role": item["role"], "content": item["content"].strip()})
        self.history = cleaned_hist[-self.max_history :]

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "facts": self.facts[-self.max_facts :],
                "history": self.history[-self.max_history :],
            }
            self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def add_facts(self, new_facts: list[str]) -> bool:
        added = False
        existing_low = {f.lower() for f in self.facts}
        for raw in new_facts:
            fact = raw.strip()
            if not fact or fact.lower() in existing_low:
                continue
            self.facts.append(fact)
            existing_low.add(fact.lower())
            added = True
        if len(self.facts) > self.max_facts:
            self.facts = self.facts[-self.max_facts :]
        if added:
            self.save()
        return added

    def clear(self) -> None:
        self.facts.clear()
        self.history.clear()
        if self.path.exists():
            try:
                self.path.unlink()
            except OSError:
                self.save()
