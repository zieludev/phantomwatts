"""The dependency rule of docs/architecture.md, checked against the source."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "phantomwatts"
BLUETOOTH_STACKS = {"bumble", "winvhci"}
PURE_STANDARD_LIBRARY = {"collections", "dataclasses", "enum", "math", "random", "re", "typing"}

# What each pure part may import from this project.
PURE_PARTS = {
    "protocol": {"phantomwatts.protocol"},
    "port.py": set[str](),
    "core": {"phantomwatts.core", "phantomwatts.port"},
    "frontend.py": {"phantomwatts.port", "phantomwatts.protocol"},
}


def imports_of(source: Path) -> set[str]:
    """Every module a file imports, wherever in the file the import sits."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"{source}: relative imports hide what is imported"
            assert node.module is not None
            found.add(node.module)
    return found


def files_of(part: str) -> list[Path]:
    path = PACKAGE / part
    return sorted(path.rglob("*.py")) if path.is_dir() else [path]


def within(module: str, packages: set[str]) -> bool:
    return any(module == package or module.startswith(package + ".") for package in packages)


def test_only_the_adapter_package_imports_a_bluetooth_stack() -> None:
    offenders = [
        str(source.relative_to(PACKAGE))
        for source in sorted(PACKAGE.rglob("*.py"))
        if "bumble_adapter" not in source.relative_to(PACKAGE).parts
        and any(module.split(".")[0] in BLUETOOTH_STACKS for module in imports_of(source))
    ]

    assert offenders == []


def test_the_adapter_package_is_where_the_bluetooth_stack_is_used() -> None:
    users = [
        source.name
        for source in files_of("bumble_adapter")
        if any(module.split(".")[0] == "bumble" for module in imports_of(source))
    ]

    assert {"central.py", "peripheral.py", "radio.py", "service.py"} <= set(users)


def test_the_adapter_package_does_not_reach_up_to_the_entry_points() -> None:
    entry_points = {"phantomwatts.cli", "phantomwatts.pytest_plugin", "phantomwatts.testing"}

    for source in files_of("bumble_adapter"):
        assert not entry_points & imports_of(source), source.name


@pytest.mark.parametrize("part", PURE_PARTS)
def test_pure_parts_import_only_what_the_rule_allows(part: str) -> None:
    for source in files_of(part):
        for module in imports_of(source):
            if module.split(".")[0] == "phantomwatts":
                assert within(module, PURE_PARTS[part]), f"{source.name} imports {module}"
            else:
                assert module.split(".")[0] in PURE_STANDARD_LIBRARY, (
                    f"{source.name} imports {module}"
                )


def test_nothing_loads_a_bluetooth_stack_until_a_trainer_is_served() -> None:
    probe = """
import sys
import phantomwatts, phantomwatts.cli, phantomwatts.pytest_plugin, phantomwatts.testing
import phantomwatts.frontend, phantomwatts.core.simulation, phantomwatts.protocol.ftms

loaded = sorted(name for name in sys.modules if name.split(".")[0] in {"bumble", "winvhci"})
print(loaded)
sys.exit(bool(loaded))
"""

    finished = subprocess.run(
        [sys.executable, "-c", probe], check=False, capture_output=True, text=True
    )

    assert finished.returncode == 0, finished.stdout + finished.stderr


def test_the_public_names_that_need_bumble_resolve_on_first_use() -> None:
    import phantomwatts
    from phantomwatts.bumble_adapter import radio, service

    assert phantomwatts.serve is service.serve
    assert phantomwatts.RunningTrainer is service.RunningTrainer
    assert phantomwatts.VirtualRadio is radio.VirtualRadio
    assert phantomwatts.HciTransport is radio.HciTransport
    assert set(phantomwatts.__all__) <= set(dir(phantomwatts)) | {
        "serve",
        "RunningTrainer",
        "VirtualRadio",
        "HciTransport",
    }
    with pytest.raises(AttributeError, match="no attribute 'nonsense'"):
        phantomwatts.nonsense  # noqa: B018
