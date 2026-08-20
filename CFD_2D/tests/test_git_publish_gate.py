from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "Application Support/Tools/project_git.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("project_git_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_push_requires_preview_bound_to_current_head(tmp_path: Path, monkeypatch) -> None:
    module = _load_module()
    root = tmp_path / "project"
    remote = tmp_path / "remote.git"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Test User")
    _git(root, "config", "user.email", "test@example.invalid")
    (root / "input.txt").write_text("one\n", encoding="utf-8")
    _git(root, "add", "input.txt")
    _git(root, "commit", "-m", "baseline")
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    _git(root, "remote", "add", "origin", str(remote))
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "PUSH_PREVIEW", root / ".git/ramair_push_preview.json")

    module.preview_push()
    preview = json.loads(module.PUSH_PREVIEW.read_text(encoding="utf-8"))
    assert preview == module.repository_identity()

    (root / "input.txt").write_text("two\n", encoding="utf-8")
    _git(root, "add", "input.txt")
    _git(root, "commit", "-m", "change")
    try:
        module.require_current_push_preview()
    except SystemExit as exc:
        assert "changed after the preview" in str(exc)
    else:
        raise AssertionError("A stale push preview must be rejected")
    assert not module.PUSH_PREVIEW.exists()
