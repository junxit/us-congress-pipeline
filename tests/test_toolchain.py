"""Tests that the toolchain the scheduled jobs install can install the lockfile.

On 2026-10-10 all three daily jobs failed at ``uv sync`` without a commit here
to cause it. The workflows pinned ``astral-sh/setup-uv`` but not the uv it
installs, so every runner took uv 0.13.0, released at 19:49 UTC the evening
before, which made Python 3.15 its default. Nothing pinned the interpreter, so
every runner moved to 3.15 overnight; the locked lxml 6.1.1 publishes wheels
for 3.13 and 3.14 only, and uv fell back to compiling it on a runner with no
libxml2 headers.

Three things now hold the toolchain still — ``version`` on every ``setup-uv``
step, ``.python-version``, and ``uv.lock`` — and they can only move together.
These tests fail when one moves alone, here rather than at 01:00 UTC.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from packaging.specifiers import SpecifierSet

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def _pinned_minor() -> int:
    """Read the CPython 3 minor version ``.python-version`` pins.

    Returns:
        The minor version, e.g. 14 for ``3.14``.
    """
    path = ROOT / ".python-version"
    assert path.is_file(), "nothing pins the interpreter, so uv takes the newest it knows"
    pinned = path.read_text(encoding="utf-8").strip()
    match = re.fullmatch(r"3\.(\d+)(?:\.\d+)?", pinned)
    assert match, f".python-version should name a CPython 3 version, not {pinned!r}"
    return int(match[1])


def _installs_on_a_runner(wheel: str, minor: int) -> bool:
    """Say whether a wheel installs on a GitHub-hosted runner as it stands.

    A runner is glibc Linux on x86-64, and uv gives it the standard build of
    CPython rather than the free-threaded one, whose ABI tag ends in ``t``.

    Args:
        wheel: The wheel's filename.
        minor: The CPython 3 minor version the runner uses.

    Returns:
        True if the wheel's tags admit that interpreter on that platform.
    """
    pythons, abis, platforms = (
        tag.split(".") for tag in wheel.removesuffix(".whl").split("-")[-3:]
    )
    if platforms == ["any"]:
        return abis == ["none"] and bool({"py3", f"py3{minor}"} & set(pythons))
    if not any(re.fullmatch(r"manylinux\w*_x86_64", tag) for tag in platforms):
        return False
    if f"cp3{minor}" in abis:
        return True
    return "abi3" in abis and any(
        re.fullmatch(r"cp3\d+", tag) and int(tag[3:]) <= minor for tag in pythons
    )


def _setup_uv(workflow: Path) -> tuple[str, dict[str, str]] | None:
    """Find a workflow's ``setup-uv`` step and the inputs it passes.

    Args:
        workflow: Path to the workflow file.

    Returns:
        The action reference and the step's ``key: value`` lines, or None if
        the workflow does not install uv.
    """
    lines = workflow.read_text(encoding="utf-8").splitlines()
    for at, line in enumerate(lines):
        if uses := re.search(r"uses:\s*astral-sh/setup-uv@(\S+)", line):
            inputs: dict[str, str] = {}
            for following in lines[at + 1 :]:
                if re.match(r"\s*- ", following):
                    break
                pair = re.match(r'\s+([\w-]+):\s*"?([^"\s#]+)"?\s*(?:#.*)?$', following)
                if pair:
                    inputs[pair[1]] = pair[2]
            return uses[1], inputs
    return None


def test_the_interpreter_is_pinned_within_requires_python() -> None:
    """Unpinned, uv takes the newest Python it knows, which moves under every runner."""
    minor = _pinned_minor()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requires = SpecifierSet(project["project"]["requires-python"])
    assert requires.contains(f"3.{minor}.0"), (
        f".python-version pins 3.{minor}, outside requires-python {requires}"
    )


def test_every_locked_package_has_a_wheel_a_runner_can_install() -> None:
    """lxml 6.1.1 has no wheel for 3.15, and on 2026-10-10 that stopped every job."""
    minor = _pinned_minor()
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    stranded = [
        f"{package['name']} {package['version']}"
        for package in lock["package"]
        if "registry" in package["source"]
        and not any(
            _installs_on_a_runner(wheel["url"].rsplit("/", 1)[-1], minor)
            for wheel in package.get("wheels", [])
        )
    ]
    assert not stranded, (
        f"a runner on CPython 3.{minor} would have to compile "
        f"{', '.join(stranded)}, with no headers to do it; move .python-version "
        "and uv.lock together"
    )


def test_every_workflow_installs_the_same_exact_uv() -> None:
    """The action's tag does not pin the uv it installs; only ``version`` does."""
    pins = {workflow.name: step for workflow in WORKFLOWS if (step := _setup_uv(workflow))}
    assert pins, "no workflow installs uv, so the parser has drifted from the files"
    for name, (_, inputs) in pins.items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", inputs.get("version", "")), (
            f"{name} installs whichever uv is newest; pin an exact version"
        )
        assert "python-version" not in inputs, (
            f"{name} overrides .python-version, and nothing run locally would see it"
        )
    assert len({(ref, inputs["version"]) for ref, inputs in pins.values()}) == 1, (
        f"bump every workflow together: {pins}"
    )
