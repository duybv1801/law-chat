"""robots.txt matching per RFC 9309: the longest matching rule wins, `Allow` on a tie.

Not `urllib.robotparser`: it applies the first matching rule, so vbpl.vn's file
(`Allow: /` before `Disallow: /api/`) would allow `/api/`.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow, pattern)


def _pattern(path: str) -> re.Pattern[str]:
    """`*` matches any run of characters, a trailing `$` anchors the end."""
    anchored = path.endswith("$")
    body = re.escape(path.rstrip("$")).replace(r"\*", ".*")
    return re.compile(body + ("$" if anchored else ""))


class RobotsRules:
    def __init__(
        self, rules: list[tuple[bool, str]] | None = None, *, disallow_all: bool = False
    ) -> None:
        self._rules = [(allow, path, _pattern(path)) for allow, path in rules or []]
        self._disallow_all = disallow_all

    @classmethod
    def parse(cls, text: str, user_agent: str) -> "RobotsRules":
        """Rules of the groups naming our product token, else of the `*` group."""
        groups: list[_Group] = []
        current: _Group | None = None
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if current is None or current.rules:
                    current = _Group()
                    groups.append(current)
                current.agents.append(value.lower())
            elif key in ("allow", "disallow") and current is not None:
                if value:  # an empty Disallow allows everything: no rule
                    current.rules.append((key == "allow", value))

        token = user_agent.split("/", 1)[0].strip().lower()
        mine = [g for g in groups if token in g.agents]
        chosen = mine or [g for g in groups if "*" in g.agents]
        return cls([rule for g in chosen for rule in g.rules])

    def can_fetch(self, url: str) -> bool:
        if self._disallow_all:
            return False
        parts = urlsplit(url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best: tuple[int, bool] | None = None  # (pattern length, allow)
        for allow, path, pattern in self._rules:
            if pattern.match(target):
                candidate = (len(path), allow)
                if best is None or candidate > best:  # longer wins; equal length: allow
                    best = candidate
        return True if best is None else best[1]
