import subprocess
import sys
from pathlib import Path

from openwispr import installer, model, shortcut

ROOT = Path(__file__).resolve().parent.parent


def fake_payload(root):
    payload = root / "payload"
    payload.mkdir()
    (payload / "OpenWispr.exe").write_text("fake app", encoding="utf-8")
    models = payload / "models"
    models.mkdir()
    for name in (model.file_name(model.DEFAULT_MODEL), model.VAD_FILE):
        (models / name).write_bytes(model.GGML_MAGIC + b"\0" * 100_000)
    return payload


def paths(root, payload):
    return installer.InstallerPaths(
        install_dir=root / "install",
        start_menu_dir=root / "start menu",
        models_dir=root / "models",
        desktop_dir=root / "desktop",
        startup_dir=root / "startup",
        payload_dir=payload,
    )


def test_install_creates_start_menu_entry_without_desktop_or_startup_shortcuts(tmp_path):
    payload = fake_payload(tmp_path)
    location = paths(tmp_path, payload)

    installer.install(location)

    entry = location.start_menu_dir / "OpenWispr.lnk"
    assert entry.exists()
    assert shortcut.read_shortcut(entry)["TargetPath"] == str(location.install_dir / "OpenWispr.exe")
    assert not (location.desktop_dir / "OpenWispr.lnk").exists()
    assert not (location.startup_dir / "OpenWispr.lnk").exists()


def test_install_bundles_dictation_and_voice_detection_models(tmp_path):
    payload = fake_payload(tmp_path)
    location = paths(tmp_path, payload)

    installer.install(location)

    for name in (model.file_name(model.DEFAULT_MODEL), model.VAD_FILE):
        installed = location.models_dir / name
        assert installed.exists()
        assert model.looks_valid(installed)
        assert installed.read_bytes() == (payload / "models" / name).read_bytes()


def test_uninstall_removes_installed_files_and_start_menu_entry(tmp_path):
    payload = fake_payload(tmp_path)
    location = paths(tmp_path, payload)
    installer.install(location)

    installer.uninstall(location)

    assert not location.install_dir.exists()
    assert not (location.start_menu_dir / "OpenWispr.lnk").exists()
    for name in (model.file_name(model.DEFAULT_MODEL), model.VAD_FILE):
        assert not (location.models_dir / name).exists()


def test_uninstall_removes_legacy_repository_shortcuts(tmp_path):
    payload = fake_payload(tmp_path)
    location = paths(tmp_path, payload)
    installer.install(location)

    repo = tmp_path / "repo"
    pythonw = repo / ".venv" / "Scripts" / "pythonw.exe"
    pythonw.parent.mkdir(parents=True)
    pythonw.write_bytes(b"fake pythonw")
    desktop_legacy = location.desktop_dir / "OpenWispr.lnk"
    startup_legacy = location.startup_dir / "OpenWispr.lnk"
    shortcut.create_shortcut(desktop_legacy, pythonw, "-m openwispr", repo)
    shortcut.create_shortcut(startup_legacy, pythonw, "-m openwispr", repo)
    unrelated = location.desktop_dir / "Other App.lnk"
    shortcut.create_shortcut(unrelated, pythonw, "-m other_app", repo)

    installer.uninstall(location)

    assert not desktop_legacy.exists()
    assert not startup_legacy.exists()
    assert unrelated.exists()
    assert shortcut.read_shortcut(unrelated)["Arguments"] == "-m other_app"


def test_entry_point_installs_and_uninstalls_with_injected_paths(tmp_path):
    payload = fake_payload(tmp_path)
    location = paths(tmp_path, payload)
    common = [
        "--install-dir",
        str(location.install_dir),
        "--start-menu-dir",
        str(location.start_menu_dir),
        "--models-dir",
        str(location.models_dir),
        "--desktop-dir",
        str(location.desktop_dir),
        "--startup-dir",
        str(location.startup_dir),
        "--payload-dir",
        str(location.payload_dir),
    ]

    assert installer.main(["install", *common]) == 0
    assert (location.start_menu_dir / "OpenWispr.lnk").exists()
    assert (location.install_dir / "OpenWispr.exe").exists()

    assert installer.main(["uninstall", *common]) == 0
    assert not (location.start_menu_dir / "OpenWispr.lnk").exists()
    assert not location.install_dir.exists()


def test_shared_shortcut_command_creates_and_reads(tmp_path):
    target = tmp_path / "App.exe"
    target.write_bytes(b"fake app")
    link = tmp_path / "links" / "App.lnk"

    assert shortcut.main(
        [
            "create",
            str(link),
            str(target),
            "--arguments",
            "-m openwispr",
            "--working-directory",
            str(tmp_path),
        ]
    ) == 0

    info = shortcut.read_shortcut(link)
    assert info["TargetPath"] == str(target)
    assert info["Arguments"] == "-m openwispr"
    assert info["WorkingDirectory"] == str(tmp_path)


def test_uninstall_removes_legacy_shortcut_with_empty_arguments(tmp_path):
    payload = fake_payload(tmp_path)
    location = paths(tmp_path, payload)

    repo = tmp_path / "repo"
    pythonw = repo / ".venv" / "Scripts" / "pythonw.exe"
    pythonw.parent.mkdir(parents=True)
    pythonw.write_bytes(b"fake pythonw")
    legacy = location.desktop_dir / "OpenWispr.lnk"
    shortcut.create_shortcut(legacy, pythonw, "", repo)

    installer.uninstall(location)

    assert not legacy.exists()


def test_installer_e2e_check_runs_against_temporary_locations(tmp_path):
    script = ROOT / "tests" / "installer_e2e.ps1"
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Python",
            sys.executable,
            "-Root",
            str(tmp_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "PASS" in result.stdout
