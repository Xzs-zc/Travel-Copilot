from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SKILL_ROOT = Path(__file__).resolve().parent / "skills"
MAX_SKILL_BYTES = 16_000


@dataclass(frozen=True)
class SupportSkill:
    name: str
    route: str
    description: str
    version: str
    priority: int
    intents: tuple[str, ...]
    content: str
    path: str

    def metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "route": self.route,
            "description": self.description,
            "version": self.version,
            "priority": self.priority,
            "intents": list(self.intents),
            "path": self.path,
        }


class SupportSkillRegistry:
    def __init__(self, root: Path | str = SKILL_ROOT):
        self.root = Path(root)
        self._skills: list[SupportSkill] = []
        self._errors: list[dict[str, str]] = []
        self._snapshot: tuple[tuple[str, int, int], ...] = ()
        self._revision = 0
        self._lock = threading.Lock()

    def _current_snapshot(self) -> tuple[tuple[str, int, int], ...]:
        if not self.root.exists():
            return ()
        files: list[tuple[str, int, int]] = []
        for path in sorted(self.root.glob("*/SKILL.md")):
            try:
                stat = path.stat()
                files.append((str(path), stat.st_mtime_ns, stat.st_size))
            except OSError:
                continue
        return tuple(files)

    def ensure_loaded(self) -> None:
        snapshot = self._current_snapshot()
        if snapshot != self._snapshot:
            self.reload()

    def reload(self) -> dict[str, Any]:
        with self._lock:
            skills: list[SupportSkill] = []
            errors: list[dict[str, str]] = []
            for path in sorted(self.root.glob("*/SKILL.md")):
                try:
                    stat = path.stat()
                    if stat.st_size > MAX_SKILL_BYTES:
                        raise ValueError(f"Skill 超过 {MAX_SKILL_BYTES} 字节上限")
                    skill = self._parse(path)
                    skills.append(skill)
                except Exception as exc:
                    errors.append({"path": str(path), "error": str(exc)[:300]})
            self._skills = skills
            self._errors = errors
            self._snapshot = self._current_snapshot()
            self._revision += 1
            return {
                "revision": self._revision,
                "count": len(self._skills),
                "skills": [skill.metadata() for skill in self._skills],
                "errors": list(self._errors),
            }

    def describe(self) -> dict[str, Any]:
        self.ensure_loaded()
        return {
            "revision": self._revision,
            "count": len(self._skills),
            "skills": [skill.metadata() for skill in self._skills],
            "errors": list(self._errors),
        }

    def select(
        self,
        *,
        primary_agent: str,
        supporting_agents: Iterable[str],
        intent: str,
    ) -> list[SupportSkill]:
        self.ensure_loaded()
        selected: list[SupportSkill] = []
        for route in [primary_agent, *supporting_agents][:2]:
            candidates = [
                skill
                for skill in self._skills
                if skill.route == route and (not skill.intents or intent in skill.intents)
            ]
            if not candidates:
                candidates = [skill for skill in self._skills if skill.route == route]
            if not candidates:
                continue
            candidates.sort(key=lambda skill: (-skill.priority, skill.name))
            candidate = candidates[0]
            if candidate not in selected:
                selected.append(candidate)
        return selected

    def _parse(self, path: Path) -> SupportSkill:
        text = path.read_text(encoding="utf-8").strip()
        if not text.startswith("---"):
            raise ValueError("缺少 frontmatter")
        header, body = self._split_frontmatter(text)
        metadata = self._parse_header(header)
        required = ("name", "route", "description", "version")
        missing = [key for key in required if not metadata.get(key)]
        if missing:
            raise ValueError(f"缺少字段：{', '.join(missing)}")
        if not body.strip():
            raise ValueError("Skill 正文为空")
        try:
            priority = int(metadata.get("priority") or 0)
        except ValueError as exc:
            raise ValueError("priority 必须是整数") from exc
        intents = tuple(
            item.strip()
            for item in re.split(r"[,，]", metadata.get("intents") or "")
            if item.strip()
        )
        return SupportSkill(
            name=metadata["name"],
            route=metadata["route"],
            description=metadata["description"],
            version=metadata["version"],
            priority=priority,
            intents=intents,
            content=body.strip(),
            path=str(path.relative_to(self.root)),
        )

    @staticmethod
    def _split_frontmatter(text: str) -> tuple[str, str]:
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
        if not match:
            raise ValueError("frontmatter 格式不正确")
        return match.group(1), match.group(2)

    @staticmethod
    def _parse_header(header: str) -> dict[str, str]:
        values: dict[str, str] = {}
        for raw_line in header.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                raise ValueError(f"无法解析 frontmatter：{line}")
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
        return values


skill_registry = SupportSkillRegistry()
