import pytest

from openwispr import cli, model


def parse(argv):
    return cli.build_parser().parse_args(cli.normalize_argv(argv))


def test_run_options_without_subcommand():
    args = parse(["--hotkey", "f9", "--model", "tiny.en"])
    assert args.command == "run"
    assert args.hotkey == "f9"
    assert args.model == "tiny.en"


def test_model_option_after_subcommand():
    assert parse(["download", "-m", "small.en"]).model == "small.en"


def test_model_option_before_subcommand_is_kept():
    args = parse(["-m", "base", "-l", "de", "transcribe", "x.wav"])
    assert (args.model, args.language) == ("base", "de")


def test_defaults():
    args = parse([])
    assert args.model == model.DEFAULT_MODEL
    assert args.language is None and args.threads is None


def test_default_model_is_small_en():
    assert model.DEFAULT_MODEL == "small.en"


def test_autostart_commands():
    assert parse(["install-autostart"]).command == "install-autostart"
    assert parse(["uninstall-autostart"]).command == "uninstall-autostart"
    assert parse(["install-desktop-shortcut"]).command == "install-desktop-shortcut"
    assert parse(["uninstall-desktop-shortcut"]).command == "uninstall-desktop-shortcut"


def test_ensure_output_streams_without_console(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(cli.sys, "stderr", None)

    cli.ensure_output_streams()
    assert cli.sys.stderr is not None
    cli.sys.stderr.write("hello\n")
    cli.sys.stderr.flush()

    log = tmp_path / "OpenWispr" / "openwispr.log"
    assert "hello" in log.read_text(encoding="utf-8")


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        cli.build_parser().parse_args(["--version"])
    assert e.value.code == 0
    assert "openwispr" in capsys.readouterr().out
