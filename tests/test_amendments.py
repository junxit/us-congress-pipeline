"""Reading a bill's amendatory instructions, and executing the few that can be.

Every assumption the obvious design would make about this markup is wrong, and
each one was found by parsing real documents rather than by reading a schema:

* ``<amendment-instruction>`` is not where instructions live. It appears in 1
  document in 400 of the 119th Congress, and holds the instruction attached to
  an engrossed *amendment*. Ordinary amendatory text is prose in ``<text>``.
* The US Code citation is written once, in the chapeau, and every
  sub-instruction beneath it inherits it -- so the target has to be found by
  walking up, and the walk has to stop somewhere or an instruction inherits the
  citation of a different amendment entirely.
* ``<quote>`` is what separates an instruction that can be carried out from one
  that cannot. *Striking "; and"* is executable; *striking subsection (k)* is
  not, because the subsection's words are in the US Code and not in the bill.

The fixtures are trimmed from real documents: ``BILLS-113hres354eh.xml``, whose
30 instructions include both shapes, and ``BILLS-119hr6189ih.xml``, which amends
the Internal Revenue Code and carries no machine-readable reference at all.
"""

from __future__ import annotations

from uscongress.amendments import (
    Instruction,
    Target,
    derived_markdown,
    read_instructions,
)

# --------------------------------------------------------------------------
# Fixtures, trimmed from real documents
# --------------------------------------------------------------------------


def _bill(body: str) -> bytes:
    """Wrap a legislative body in a minimal bill document."""
    return (
        "<?xml version='1.0'?><bill><form><legis-num>H. R. 1</legis-num></form>"
        f"<legis-body>{body}</legis-body></bill>"
    ).encode()


def _xref(cite: str, shown: str) -> str:
    """A machine-readable US Code citation, as GPO writes it."""
    return f'<external-xref legal-doc="usc" parsable-cite="{cite}">{shown}</external-xref>'


#: A literal substitution: the bill states both sides, so the result follows
#: from the bill alone. Trimmed from H.Res. 354 of the 113th.
_REPLACEMENT = _bill(
    "<section><enum>1.</enum><text>Section 102(a) of the Secure Rural Schools "
    f"and Community Self-Determination Act of 2000 ({_xref('usc/16/7112', '16 U.S.C. 7112')})"
    " is amended by striking <quote>2012</quote> and inserting "
    "<quote>2013</quote>.</text></section>"
)

#: A structural amendment: the words removed are in the US Code, not here.
_STRUCTURAL = _bill(
    "<section><enum>2.</enum><text>Section 703 of the Civil Rights Act of 1964 "
    f"({_xref('usc/42/2000e-2', '42 U.S.C. 2000e–2')}) is amended by striking "
    "subsection (k) and inserting the following:</text>"
    "<quoted-block><subsection><enum>(k)</enum><text>New text.</text>"
    "</subsection></quoted-block></section>"
)

#: No machine-readable reference at all -- the Internal Revenue Code named in
#: prose. Trimmed from H.R. 6189 of the 119th.
_UNCITED = _bill(
    "<section><enum>3.</enum><text>Part III of subchapter B of chapter 1 of "
    "subtitle A of the Internal Revenue Code of 1986 is amended by inserting "
    "after section 139I the following new section:</text></section>"
)

#: The citation on the chapeau, the operations in the paragraphs below it.
_INHERITED = _bill(
    "<section><enum>4.</enum>"
    f"<chapeau>Section 205 ({_xref('usc/16/7125', '16 U.S.C. 7125')}) is amended—</chapeau>"
    "<paragraph><enum>(1)</enum><text>by striking <quote>2011</quote> and "
    "inserting <quote>2012</quote>; and</text></paragraph>"
    "<paragraph><enum>(2)</enum><text>by striking <quote>2013</quote> each place "
    "it appears and inserting <quote>2014</quote>.</text></paragraph>"
    "</section>"
)

#: Two amendments to different sections, in one bill. The second must not
#: inherit the first's citation.
_TWO_SECTIONS = _bill(
    f"<section><enum>1.</enum><text>Section A ({_xref('usc/16/7112', '16 U.S.C. 7112')})"
    " is amended by striking <quote>a</quote> and inserting <quote>b</quote>.</text></section>"
    "<section><enum>2.</enum><text>The Internal Revenue Code of 1986 is amended "
    "by striking <quote>c</quote> and inserting <quote>d</quote>.</text></section>"
)


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def test_a_bill_that_amends_nothing_yields_no_instructions() -> None:
    """45% of bill documents amend nothing at all.

    A resolution congratulating a team, or an original Act that adds law
    without touching any. They get no ``derived/`` file rather than one saying
    it has nothing to say.
    """
    plain = _bill("<section><enum>1.</enum><text>This Act may be cited as the "
                  "Example Act.</text></section>")

    assert read_instructions(plain) == ()
    assert derived_markdown("H.R. 1", "119", "Introduced in House", ()) == ""


def test_a_literal_substitution_is_carried_out() -> None:
    """The one shape where the result follows from the bill alone.

    The bill states the text removed and the text inserted, so nothing has to
    be read out of the US Code and the answer can be checked against the bill.
    """
    (instruction,) = read_instructions(_REPLACEMENT)

    assert instruction.applied
    assert instruction.target == Target(title="16", section="7112")
    assert instruction.operation == "replace"
    assert instruction.struck == "2012"
    assert instruction.inserted == "2013"
    assert instruction.reason == ""


def test_a_structural_amendment_is_stated_and_not_guessed() -> None:
    """*Strike subsection (k)* names words that are not in the bill.

    They are in the US Code. Reading them from there would make the output
    depend on a corpus the daily loop does not have, so the instruction is
    recorded with its target and left unapplied.
    """
    (instruction,) = read_instructions(_STRUCTURAL)

    assert not instruction.applied
    assert instruction.target == Target(title="42", section="2000e-2")
    assert "structure" in instruction.reason
    assert instruction.struck == ""


def test_an_instruction_with_no_machine_readable_citation_says_so() -> None:
    """The Internal Revenue Code named in prose resolves to nothing.

    This is the commonest reason after structure, and it is a fact about how
    the bill was drafted rather than a failure of the reading.
    """
    (instruction,) = read_instructions(_UNCITED)

    assert not instruction.applied
    assert instruction.target is None
    assert "no machine-readable US Code section" in instruction.reason


def test_sub_instructions_inherit_the_citation_from_their_chapeau() -> None:
    """Congress names the section once and then lists what to do to it.

    Requiring each instruction to carry its own citation would report almost
    every real amendment as having no target.
    """
    first, second = read_instructions(_INHERITED)

    assert first.target == Target(title="16", section="7125")
    assert second.target == Target(title="16", section="7125")
    assert first.applied and second.applied


def test_a_qualifier_between_the_two_literals_does_not_defeat_the_match() -> None:
    """*striking "2013" each place it appears and inserting "2014"*.

    One of the commonest instructions Congress writes. Requiring "and
    inserting" to follow the quotation immediately dropped every one of them
    into the unapplied pile, with a reason that was untrue of them.
    """
    _, second = read_instructions(_INHERITED)

    assert second.applied
    assert second.struck == "2013"
    assert second.inserted == "2014"


def test_a_citation_does_not_leak_across_sections() -> None:
    """A bill amends many sections of law, each under its own ``<section>``.

    Without a boundary on the walk up the tree, the second amendment here would
    inherit the first's citation and be reported as amending 16 U.S.C. 7112 --
    confidently, and wrongly.
    """
    first, second = read_instructions(_TWO_SECTIONS)

    assert first.target == Target(title="16", section="7112")
    assert second.target is None


def test_quotation_marks_are_restored_in_the_reproduced_instruction() -> None:
    """``<quote>`` is markup, so flattening drops the marks.

    *striking “; and”* becomes *striking ; and*, which reads as gibberish. The
    marks go back the way ``billtext`` puts them back, so an instruction here
    and the same instruction in ``bill.md`` read alike.
    """
    (instruction,) = read_instructions(_REPLACEMENT)

    assert "striking “2012” and inserting “2013”" in instruction.text
    # The literals themselves are bare -- they sit in a table cell already.
    assert instruction.struck == "2012"


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _instruction(**overrides: object) -> Instruction:
    """One instruction, with defaults."""
    base = {
        "text": "by striking “a” and inserting “b”",
        "target": Target(title="16", section="7112"),
        "operation": "replace",
        "struck": "a",
        "inserted": "b",
        "applied": True,
        "reason": "",
    }
    base.update(overrides)
    return Instruction(**base)  # type: ignore[arg-type]


def test_the_rendered_file_says_it_is_derived_before_it_says_anything_else() -> None:
    """This is the whole reason the phase was allowed to ship.

    A file describing what a bill does to federal law, sitting in a repository
    of federal law, is the most mistakable thing this project publishes.
    """
    text = derived_markdown("H.R. 1", "119", "Introduced in House", (_instruction(),))

    assert "derived: true" in text
    assert "**Derived, unofficial, and not law.**" in text
    assert text.index("Derived, unofficial") < text.index("## Executed")


def test_an_unapplied_instruction_is_listed_with_its_reason() -> None:
    """Dropping it would leave the file reading as a complete account.

    It is not one: four instructions in five cannot be carried out, and a
    reader has to be able to see which.
    """
    text = derived_markdown(
        "H.R. 1",
        "119",
        "Introduced in House",
        (
            _instruction(),
            _instruction(
                applied=False,
                struck="",
                inserted="",
                reason="the bill names no machine-readable US Code section",
                target=None,
            ),
        ),
    )

    assert "1 executed, 1 stated and not applied" in text
    assert "## Stated, not applied" in text
    assert "no machine-readable US Code section" in text


def test_a_pipe_in_an_instruction_does_not_break_the_table() -> None:
    """Federal text is not escaped for Markdown, and a table row is delimited."""
    text = derived_markdown(
        "H.R. 1",
        "119",
        "Introduced in House",
        (_instruction(applied=False, text="strike | insert", reason="because"),),
    )

    rows = [line for line in text.splitlines() if line.startswith("| `16 U.S.C.")]
    assert len(rows) == 1
    assert rows[0].count("|") == 5  # four cells, five delimiters


def test_the_document_ends_with_exactly_one_newline() -> None:
    """Every generated document in this project ends the same way."""
    text = derived_markdown("H.R. 1", "119", "Introduced in House", (_instruction(),))

    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_rendering_is_stable_for_the_same_input() -> None:
    """Rendered bytes decide the commit SHA.

    A rebuild of an unchanged bill must produce an unchanged commit, or the
    daily loop force-pushes the corpus every time it runs.
    """
    instructions = read_instructions(_INHERITED)
    first = derived_markdown("H.R. 1", "119", "Introduced in House", instructions)
    second = derived_markdown("H.R. 1", "119", "Introduced in House", instructions)

    assert first == second
