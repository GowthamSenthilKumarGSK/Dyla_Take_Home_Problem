from __future__ import annotations
import json
from pathlib import Path
from models import EntityRecord, Fact


class EntityMemory:
    """Simple JSON-backed entity memory store."""

    def __init__(self, path: str = "knowledge.json"):
        self.path = Path(path)
        self.entities: dict[str, EntityRecord] = {}
        self.sources: dict[str, dict] = {}
        self._load()

    def _load(self):
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for key, val in data.get("entities", {}).items():
                self.entities[key] = EntityRecord(**val)
            self.sources = data.get("sources", {})

    def save(self):
        data = {
            "entities": {k: v.model_dump() for k, v in self.entities.items()},
            "sources": self.sources,
        }
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _normalize(self, name: str) -> str:
        return name.strip().lower().replace(" ", "_")

    def get(self, entity_name: str) -> EntityRecord | None:
        return self.entities.get(self._normalize(entity_name))

    def search(self, query: str) -> list[EntityRecord]:
        """Find entities whose name contains the query substring."""
        q = query.strip().lower()
        return [
            e for key, e in self.entities.items()
            if q in key or q in e.name.lower()
        ]

    def add_facts(self, entity_name: str, facts: list[Fact],
                  entity_type: str = "unknown",
                  related: list[str] | None = None):
        key = self._normalize(entity_name)
        if key in self.entities:
            record = self.entities[key]
            existing_texts = {f.text.lower() for f in record.facts}
            for f in facts:
                if f.text.lower() not in existing_texts:
                    record.facts.append(f)
                    existing_texts.add(f.text.lower())
            if related:
                for r in related:
                    nr = self._normalize(r)
                    if nr not in record.related_entities:
                        record.related_entities.append(nr)
        else:
            self.entities[key] = EntityRecord(
                name=entity_name,
                entity_type=entity_type,
                facts=facts,
                related_entities=[self._normalize(r) for r in (related or [])],
            )
        self.save()

    def add_source(self, url: str, title: str, summary: str):
        self.sources[url] = {
            "title": title,
            "summary": summary[:500],
        }
        self.save()

    def get_context_for_entities(self, entity_names: list[str]) -> str:
        """Build a context string for injecting into the LLM prompt."""
        parts = []
        for name in entity_names:
            record = self.get(name)
            if not record:
                results = self.search(name)
                record = results[0] if results else None
            if record:
                facts_str = "\n".join(f"  - {f.text} [source: {f.source}]" for f in record.facts)
                related_str = ", ".join(record.related_entities) if record.related_entities else "none"
                parts.append(
                    f"Entity: {record.name} (type: {record.entity_type})\n"
                    f"Known facts:\n{facts_str}\n"
                    f"Related entities: {related_str}"
                )
        return "\n\n".join(parts) if parts else ""
