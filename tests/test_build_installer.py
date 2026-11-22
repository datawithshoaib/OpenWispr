import sys
from pathlib import Path

import build_installer


def test_pyinstaller_flags_build_one_shared_list():
    app = build_installer.pyinstaller_flags(name="OpenWispr", windowed=True, root=Path("root"))
    installer = build_installer.pyinstaller_flags(
        name="OpenWisprInstaller",
        windowed=False,
        root=Path("root"),
        payload=Path("payload"),
    )

    for flags in (app, installer):
        assert flags[:3] == ["--noconfirm", "--clean", "--onefile"]
        assert "--distpath" in flags
        assert "--workpath" in flags
        assert "--specpath" in flags

    assert "--windowed" in app
    assert "--console" in installer
    assert "--add-data" in installer


def test_both_builds_use_the_shared_flags(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(build_installer, "ROOT", tmp_path)
    monkeypatch.setattr(build_installer, "run_pyinstaller", lambda *args: calls.append(args))
    monkeypatch.setattr(build_installer.shutil, "copy2", lambda *args: None)
    monkeypatch.setattr(build_installer.model, "ensure", lambda name: tmp_path / "model.bin")
    monkeypatch.setattr(build_installer.model, "ensure_vad", lambda: tmp_path / "vad.bin")
    monkeypatch.setattr(sys, "argv", ["build_installer.py"])

    build_installer.main()

    assert len(calls) == 2
    for call in calls:
        assert call[: len(build_installer.COMMON_FLAGS)] == build_installer.COMMON_FLAGS
