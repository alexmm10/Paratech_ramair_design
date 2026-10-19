"""Portable study starting values. Never write to an existing user configuration."""
from __future__ import annotations
import copy
import json
from pathlib import Path


def catalog(root: Path) -> dict:
    path = Path(root) / 'Application Support/Configurations/studio_defaults.json'
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}


def stage_defaults(root: Path, stage: str, topology: str | None = None, fallback=None):
    root = Path(root).resolve()
    entry = catalog(root).get(stage)
    if isinstance(entry, dict):
        entry = entry.get(topology or 'closed')
    if not entry:
        return copy.deepcopy(fallback)
    path = (root / entry).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f'Preset de estudio ausente o fuera de la aplicación: {entry}')
    result = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(result, dict):
        raise ValueError(f'El preset debe contener un objeto: {entry}')
    return result


def preferred_profile(items: list[dict], root: Path) -> int:
    identifier = catalog(root).get('preferred_profile_id')
    return next((i for i, item in enumerate(items) if item.get('id') == identifier), 0)
