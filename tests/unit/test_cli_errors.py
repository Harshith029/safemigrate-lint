"""CLI operational failures must exit 2, never 1.

The exit contract is 0 clean / 1 findings / 2 the tool couldn't run. The Action
treats 1 as a normal result, so an uncaught OSError or TOML error — which exits
1 with a traceback — reported broken input as a clean lint. Every path that can
fail on bad input is pinned here.
"""

from __future__ import annotations

import pathlib

import pytest

from safemigrate_lint.cli import main


def _write(tmp_path: pathlib.Path, name: str, text: str) -> pathlib.Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_clean_file_exits_zero(tmp_path: pathlib.Path) -> None:
    f = _write(tmp_path, "m.sql", "CREATE INDEX CONCURRENTLY i ON t (c);\n")
    assert main([str(f), "--severity=critical"]) == 0


def test_findings_exit_one(tmp_path: pathlib.Path) -> None:
    f = _write(tmp_path, "m.sql", "ALTER TABLE t DROP COLUMN c;\n")
    assert main([str(f)]) == 1


def test_missing_file_exits_two(tmp_path: pathlib.Path, capsys) -> None:
    assert main([str(tmp_path / "nope.sql")]) == 2
    assert "file not found" in capsys.readouterr().err


def test_directory_argument_exits_two(tmp_path: pathlib.Path, capsys) -> None:
    (tmp_path / "sub").mkdir()
    assert main([str(tmp_path / "sub")]) == 2
    assert "is a directory" in capsys.readouterr().err


def test_invalid_utf8_exits_two(tmp_path: pathlib.Path, capsys) -> None:
    bad = tmp_path / "m.sql"
    bad.write_bytes(b"CREATE TABLE t (c text);\n\xff\xfe invalid")
    assert main([str(bad)]) == 2
    assert "not valid UTF-8" in capsys.readouterr().err


def test_malformed_toml_exits_two(tmp_path: pathlib.Path, capsys) -> None:
    _write(tmp_path, ".safemigrate.toml", "[rules\ndisabled = ")
    f = _write(tmp_path, "m.sql", "SELECT 1;\n")
    assert main([str(f)]) == 2
    assert "invalid TOML" in capsys.readouterr().err


def test_wrong_toml_shape_exits_two(tmp_path: pathlib.Path, capsys) -> None:
    _write(tmp_path, ".safemigrate.toml", '[rules]\ndisabled = "not-a-list"\n')
    f = _write(tmp_path, "m.sql", "SELECT 1;\n")
    assert main([str(f)]) == 2
    assert "must be a list of strings" in capsys.readouterr().err


def test_unknown_rule_id_in_config_is_rejected(tmp_path: pathlib.Path, capsys) -> None:
    """A typo'd rule id silently disables nothing, so it must not pass quietly."""
    _write(tmp_path, ".safemigrate.toml", '[rules]\ndisabled = ["drop-colum-restricted"]\n')
    f = _write(tmp_path, "m.sql", "ALTER TABLE t DROP COLUMN c;\n")
    assert main([str(f)]) == 2
    err = capsys.readouterr().err
    assert "unknown rule" in err
    assert "drop-colum-restricted" in err


def test_valid_config_still_works(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, ".safemigrate.toml", '[rules]\ndisabled = ["drop-column-restricted"]\n')
    f = _write(tmp_path, "m.sql", "ALTER TABLE t DROP COLUMN c;\n")
    assert main([str(f), "--severity=critical"]) == 0


def test_promoted_style_finding_keeps_its_lock_impact(tmp_path: pathlib.Path, capsys) -> None:
    """Promotion rebuilt the Finding by hand and dropped fields added later."""
    _write(
        tmp_path,
        ".safemigrate.toml",
        '[rules.style]\nenabled = ["timestamptz-over-timestamp-preferred"]\n',
    )
    f = _write(tmp_path, "m.sql", "ALTER TABLE t ADD COLUMN c timestamp;\n")
    assert main([str(f)]) == 1
    import json

    findings = json.loads(capsys.readouterr().out)
    promoted = [x for x in findings if x["rule_id"] == "timestamptz-over-timestamp-preferred"]
    assert promoted and promoted[0]["severity"] == "warning"
    # A rule that carries a lock impact must keep it through promotion.
    fk = _write(tmp_path, "fk.sql", "ALTER TABLE o ADD CONSTRAINT f CHECK (n > 0);\n")
    assert main([str(fk)]) == 1
    out = json.loads(capsys.readouterr().out)
    assert any("lock_impact" in x for x in out)


@pytest.mark.parametrize("sev", ["nonsense", "critical,bogus"])
def test_invalid_severity_is_rejected(tmp_path: pathlib.Path, sev: str) -> None:
    f = _write(tmp_path, "m.sql", "SELECT 1;\n")
    with pytest.raises(SystemExit) as exc:  # argparse exits 2 for a bad argument
        main([str(f), f"--severity={sev}"])
    assert exc.value.code == 2
