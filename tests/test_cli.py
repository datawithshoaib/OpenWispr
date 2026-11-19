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


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        cli.build_parser().parse_args(["--version"])
    assert e.value.code == 0
    assert "openwispr" in capsys.readouterr().out
