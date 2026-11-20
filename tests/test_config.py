import tomllib
from pathlib import Path

import pytest

from meetplan import parse_config
from meetplan.cli import main

CONFIG = Path(__file__).parents[1] / "config" / "2027.toml"


def raw():
    return tomllib.loads(CONFIG.read_text(encoding="utf-8"))


def test_unknown_city_is_rejected():
    r = raw()
    r["fixed_trips"][1]["where"] = "meet:atlantis"
    with pytest.raises(ValueError, match="unknown where"):
        parse_config(r)


def test_reversed_dates_are_rejected():
    r = raw()
    r["fixed_trips"][0]["end"] = ["2027-01-01", "2027-01-01"]
    with pytest.raises(ValueError, match="wrong order"):
        parse_config(r)


@pytest.mark.parametrize("args", [["fares"], ["where", "9"], ["compare"]])
def test_cli_commands_run(args, capsys):
    assert main(["-c", str(CONFIG), *args]) == 0
    assert capsys.readouterr().out.strip()


def test_cli_writes_ics(tmp_path):
    out = tmp_path / "plan.ics"
    assert main(["-c", str(CONFIG), "solve", "-o", str(out)]) == 0
    assert out.read_text().startswith("BEGIN:VCALENDAR")
