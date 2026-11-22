from openwispr import autostart


def test_install_and_uninstall(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))

    path = autostart.install()
    assert path.suffix == ".lnk"
    assert path.exists()

    assert autostart.uninstall() is True
    assert not path.exists()
    assert autostart.uninstall() is False


def test_desktop_shortcut_install_and_uninstall(monkeypatch, tmp_path):
    monkeypatch.setattr(autostart, "desktop_dir", lambda: tmp_path)

    path = autostart.install_desktop()
    assert path.suffix == ".lnk"
    assert path.exists()

    assert autostart.uninstall_desktop() is True
    assert not path.exists()
    assert autostart.uninstall_desktop() is False


def test_shortcut_script_targets_pythonw(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))

    script = autostart.shortcut_script(tmp_path / "OpenWispr.lnk")
    assert "CreateShortcut" in script
    assert "pythonw.exe" in script
    assert "-m openwispr" in script
    assert str(autostart.project_dir()) in script
