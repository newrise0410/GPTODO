"""사용자 프로필(§23) — 이름/역할/선호를 data/profile.json에 보관.

추출 프롬프트에 주입해 호칭·선호 카테고리·정리 방식을 맞춤화한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROFILE_PATH = Path(__file__).resolve().parent.parent / "data" / "profile.json"

_FIELDS = {"name", "role", "categories", "preference"}


def load() -> dict[str, Any]:
    try:
        return json.loads(PROFILE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def update(fields: dict[str, Any]) -> dict[str, Any]:
    """알려진 키만 병합 저장. categories는 리스트로 정규화."""
    prof = load()
    for k, v in fields.items():
        if k not in _FIELDS or v in (None, "", []):
            continue
        if k == "categories" and isinstance(v, str):
            v = [c.strip() for c in v.replace("/", ",").split(",") if c.strip()]
        prof[k] = v
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(prof, ensure_ascii=False, indent=2))
    return prof


def as_context() -> str:
    prof = load()
    return f"[사용자 프로필] {json.dumps(prof, ensure_ascii=False)}" if prof else ""
