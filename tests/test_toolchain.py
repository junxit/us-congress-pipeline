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

Since 2026-10-23 every other pin is exact as well: each dependency and the build
backend with ``==``, each action by commit SHA with its version beside it, the
runner image by release, and the interpreter to the patch. A range, a tag or a
``-latest`` lets a run take something nobody here has run, which is 2026-10-10
again by another route. Dependabot proposes the upgrades; these refuse anything
that would let one happen by itself.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
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


def _requirements() -> list[str]:
    """Every requirement the project declares, the build backend's included.

    Returns:
        The requirement strings in ``dependencies``, in each dependency group,
        and in ``[build-system] requires``.
    """
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    groups = project.get("dependency-groups", {}).values()
    return [
        *project["project"]["dependencies"],
        *(entry for group in groups for entry in group if isinstance(entry, str)),
        *project["build-system"]["requires"],
    ]


def _workflow_lines(pattern: str) -> list[str]:
    """Every workflow line matching a pattern, named by its file.

    Args:
        pattern: Regular expression searched for in each line.

    Returns:
        ``file: line`` for each match.
    """
    return [
        f"{workflow.name}: {line.strip()}"
        for workflow in WORKFLOWS
        for line in workflow.read_text(encoding="utf-8").splitlines()
        if re.search(pattern, line)
    ]


def test_every_dependency_is_pinned_exactly() -> None:
    """A range lets an install take a release nobody here has run.

    The build backend counts: unpinned, every build fetched the newest hatchling.
    """
    loose = [
        text
        for text in _requirements()
        if [spec.operator for spec in Requirement(text).specifier] != ["=="]
        or "*" in str(Requirement(text).specifier)
    ]
    assert not loose, f"pin these exactly: {loose}"


def test_the_interpreter_is_pinned_to_a_release() -> None:
    """``3.15`` takes whichever 3.15.x the local uv knows, and CI's knows another."""
    pinned = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"3\.\d+\.\d+", pinned), f"pin a full release, not {pinned!r}"


def test_every_action_is_pinned_to_a_commit() -> None:
    """A tag can be moved to other code after it was reviewed; a commit cannot.

    The version stays beside it as a comment, which is what Dependabot rewrites
    when it proposes the next one.
    """
    uses = _workflow_lines(r"^\s*(?:-\s*)?uses:")
    loose = [
        line
        for line in uses
        if not re.search(r"uses:\s*[\w.-]+/[\w./-]+@[0-9a-f]{40}\s+#\s*v\d", line)
    ]
    assert uses, "no workflow uses an action, so the parser has drifted from the files"
    assert not loose, f"pin to a full commit SHA with its version: {loose}"


def test_no_workflow_runs_on_an_image_that_moves() -> None:
    """``ubuntu-latest`` is retargeted to a new image without a commit here."""
    moving = _workflow_lines(r"runs-on:\s*\S*latest")
    assert not moving, f"name an image release: {moving}"


def test_ci_installs_exactly_what_is_locked() -> None:
    """``--locked`` refuses a lockfile that pyproject.toml has moved away from.

    ``--frozen`` installs it regardless, so a pin changed without relocking would
    have run in CI as whatever the stale lockfile said.
    """
    installs = _workflow_lines(r"run:\s*uv sync\b")
    assert installs, "no workflow installs with uv sync, so the parser has drifted"
    unlocked = [line for line in installs if "--locked" not in line]
    assert not unlocked, f"install with --locked: {unlocked}"
