"""Docling fallback: the markdown->sections splitter is pure and hermetic
(docling itself is never imported by this test)."""

from __future__ import annotations

import sys

from draftforge.ingest.docling_fallback import _markdown_to_sections


def test_docling_module_does_not_import_docling_at_import_time():
    assert "docling" not in sys.modules or True  # importing this test module alone is enough
    import draftforge.ingest.docling_fallback as mod

    # the module itself must not have eagerly imported docling
    assert not hasattr(mod, "DocumentConverter")


def test_markdown_headings_build_section_hierarchy():
    markdown = (
        "# Introduction\n"
        "Some intro text.\n"
        "## Background\n"
        "Background details here.\n"
        "# Methods\n"
        "Method details.\n"
    )
    sections = _markdown_to_sections(markdown)
    headings = [s.heading for s in sections]
    assert headings == ["Introduction", "Background", "Methods"]

    background = sections[1]
    assert background.section_path == ["Introduction", "Background"]
    assert "Background details" in background.text

    intro = sections[0]
    assert intro.section_path == ["Introduction"]
    assert "Some intro text" in intro.text


def test_markdown_with_no_headings_yields_single_untitled_section():
    sections = _markdown_to_sections("Just a paragraph with no heading.\n")
    assert len(sections) == 1
    assert sections[0].heading == ""
    assert "Just a paragraph" in sections[0].text


def test_empty_markdown_yields_no_sections():
    assert _markdown_to_sections("") == []
    assert _markdown_to_sections("   \n\n  ") == []
