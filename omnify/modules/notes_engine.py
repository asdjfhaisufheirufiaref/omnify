from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

@dataclass
class Note:
    id: str = field(default_factory=lambda: uuid4().hex)
    label: str = "Untitled"
    contents: str = ""
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "contents": self.contents,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Note":
        return cls(
            id=str(data.get("id") or uuid4().hex),
            label=str(data.get("label") or "Untitled"),
            contents=str(data.get("contents") or ""),
            updated_at=str(data.get("updated_at") or datetime.now(timezone.utc).isoformat()),
        )    