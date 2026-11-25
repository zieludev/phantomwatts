"""The README shows real output. These tests keep it that way."""

import doctest
from pathlib import Path

from phantomwatts.bumble_adapter.demo import run_demo

README = Path(__file__).resolve().parents[1] / "README.md"


def code_block_after(marker: str) -> list[str]:
    text = README.read_text()
    block = text[text.index(marker) :].split("```")[1]
    return block.splitlines()[1:]


async def test_the_demo_output_in_the_readme_is_what_the_demo_prints() -> None:
    printed: list[str] = []

    await run_demo(pace_s=0, say=printed.append)

    assert printed == code_block_after("takes about three seconds:")


def test_the_interpreter_session_in_the_readme_runs_as_shown() -> None:
    session = "\n".join(code_block_after("without any Bluetooth at all:"))
    examples = doctest.DocTestParser().get_doctest(session, {}, "README", str(README), 0)
    runner = doctest.DocTestRunner()

    runner.run(examples)

    assert runner.summarize(verbose=False) == doctest.TestResults(failed=0, attempted=7)
