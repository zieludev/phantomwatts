"""House rules for the repository as a whole."""

import functools
import os
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKIPPED_DIRECTORIES = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "dist"}
TEXT_SUFFIXES = {".py", ".md", ".toml", ".yml", ".yaml", ".lock", ".txt", ".typed", ""}
EN_DASH, EM_DASH = "\u2013", "\u2014"


@functools.cache
def text_files() -> tuple[Path, ...]:
    found: list[Path] = []
    for directory, subdirectories, names in os.walk(ROOT):
        subdirectories[:] = [name for name in subdirectories if name not in SKIPPED_DIRECTORIES]
        found += [Path(directory, name) for name in names if Path(name).suffix in TEXT_SUFFIXES]
    return tuple(sorted(found))


def test_there_are_files_to_check() -> None:
    names = {path.relative_to(ROOT).as_posix() for path in text_files()}

    assert {"README.md", "LICENSE", "pyproject.toml", "uv.lock", ".gitignore"} <= names
    assert ".github/workflows/ci.yml" in names
    assert "src/phantomwatts/cli.py" in names


def test_plain_hyphens_only() -> None:
    offenders = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in text_files()
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if EN_DASH in line or EM_DASH in line
    ]

    assert offenders == []


def test_nothing_depends_on_where_the_checkout_lives() -> None:
    # The checkout is free to move. Its path would also give away an account name.
    places = {str(ROOT), str(Path.home())}
    offenders = [
        str(path.relative_to(ROOT))
        for path in text_files()
        if any(place in path.read_text() for place in places)
    ]

    assert offenders == []


def third_party_table() -> str:
    lines = (ROOT / "3rdparty_licenses.md").read_text().splitlines()
    return "\n".join(line for line in lines if line.startswith("| http"))


def python_dependencies() -> set[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    requirements = list(project["project"]["dependencies"]) + project["build-system"]["requires"]
    for group in project["dependency-groups"].values():
        requirements += [entry for entry in group if isinstance(entry, str)]
    return {re.match(r"[A-Za-z0-9_.-]+", requirement).group() for requirement in requirements}  # pyright: ignore[reportOptionalMemberAccess]


def github_actions() -> set[str]:
    used: set[str] = set()
    for workflow in (ROOT / ".github" / "workflows").glob("*.yml"):
        for owner, repository in re.findall(r"uses: ([\w.-]+)/([\w.-]+)", workflow.read_text()):
            used.add(f"{owner}/{repository}")
    return used


def test_the_manifest_has_the_dependencies_this_test_expects() -> None:
    assert python_dependencies() == {
        "bumble",
        "pytest",
        "pycycling",
        "bleak",
        "winvhci",
        "ruff",
        "basedpyright",
        "uv_build",
    }
    assert github_actions() == {
        "actions/checkout",
        "astral-sh/setup-uv",
        "dlech/windows-vhci-driver",
    }


@pytest.mark.parametrize("component", sorted(python_dependencies() | github_actions()))
def test_every_third_party_component_is_in_the_licence_table(component: str) -> None:
    assert re.search(rf"(?<![\w-]){re.escape(component)}(?![\w-])", third_party_table())


def test_the_licence_table_has_one_row_per_source_and_four_columns() -> None:
    rows = [row.strip("|").split("|") for row in third_party_table().splitlines()]
    sources = [row[0].strip() for row in rows]

    assert all(len(row) == 4 for row in rows)
    assert len(sources) == len(set(sources))
    assert all(row[3].strip().startswith("https://") for row in rows)


def test_no_copyleft_package_is_locked() -> None:
    locked = {
        package["name"] for package in tomllib.loads((ROOT / "uv.lock").read_text())["package"]
    }

    assert "bumble" in locked
    assert locked.isdisjoint({"libusb1", "libusb-package", "pathspec"})


def test_workflows_follow_the_house_conventions() -> None:
    for workflow in (ROOT / ".github" / "workflows").glob("*.yml"):
        text = workflow.read_text()
        assert "permissions:\n  contents: read\n" in text, workflow.name

    experimental = (ROOT / ".github" / "workflows" / "windows-e2e.yml").read_text()
    assert "on:\n  workflow_dispatch:\n\npermissions:" in experimental
