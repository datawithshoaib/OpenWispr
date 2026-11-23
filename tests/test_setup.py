from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_pytest_has_one_dependency_declaration():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert pyproject.count('"pytest') == 1
    assert "[dependency-groups]" in pyproject
    assert "[project.optional-dependencies]" not in pyproject


def test_setup_docs_use_uv_sync_without_autostart_commands():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "uv pip install" not in readme
    assert "uv sync" in readme
    assert "install-autostart" not in readme
    assert "uninstall-autostart" not in readme
    assert "Start at login" not in readme
