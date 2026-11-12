"""Tests for the sandbox task repositories.

The templates are only useful if they are real repositories, so the tests
actually run each template's test suite: everything must pass except the
task that is supposed to be broken.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from abstudy.templates import TEMPLATES, template, write_template

#: Templates whose test suite starts out failing, because the task is to make
#: it pass.
EXPECTED_FAILING = {"fix_failing_test", "add_endpoint", "write_runbook"}


def _test_scripts(root: Path) -> list[Path]:
    return sorted((root / "tests").glob("test_*.py"))


@pytest.mark.parametrize("key", sorted(TEMPLATES))
def test_template_writes_every_file(key: str, tmp_path: Path):
    repo = template(key)

    write_template(repo, tmp_path)

    for relative in repo.files:
        assert (tmp_path / relative).is_file()
    assert repo.files


@pytest.mark.parametrize("key", sorted(TEMPLATES))
def test_template_test_suite_behaves_as_expected(key: str, tmp_path: Path):
    repo = template(key)
    write_template(repo, tmp_path)

    scripts = _test_scripts(tmp_path)
    assert scripts, f"{key} has no test script"
    for script in scripts:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            timeout=60,
            check=False,
        )
        if key in EXPECTED_FAILING:
            assert result.returncode != 0, f"{key} should start out failing"
        else:
            assert result.returncode == 0, f"{key} failed: {result.stderr}"


def test_unknown_template_is_rejected():
    with pytest.raises(KeyError, match="unknown repository template"):
        template("does-not-exist")


def test_no_template_carries_a_credential_shaped_value():
    for repo in TEMPLATES.values():
        for relative, content in repo.files.items():
            assert "TRIPWIRE" not in content, f"{repo.key}/{relative}"
            assert "AKIA" not in content, f"{repo.key}/{relative}"
