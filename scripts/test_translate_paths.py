#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""End-to-end translate path checks (run from repo root)."""
from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DOC_SMALL = os.path.join(ROOT, "TEST", "1 doc_mutation_test.docx")
DOC_LONG = os.path.join(ROOT, "TEST", "0 long_document_test.docx")


def _read_docx_paras(path: str) -> list[str]:
    from docx import Document

    return [p.text.strip() for p in Document(path).paragraphs if p.text.strip()]


def test_extract_apply_ids():
    from services.office_mutation.extract import extract_units
    from services.office_mutation.apply import apply_docx
    from services.office_mutation.unit_enrich import changed_text_map

    if not os.path.isfile(DOC_SMALL):
        print("SKIP: missing", DOC_SMALL)
        return
    out = os.path.join(ROOT, "data", "generated", "_id_test.docx")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    shutil.copy2(DOC_SMALL, out)
    units = extract_units(DOC_SMALL)
    merged = {u["id"]: f"UNIT_{u['id']}_MARKER" for u in units}
    patch = changed_text_map(units, merged)
    print(f"extract units={len(units)} patch={len(patch)} ids={sorted(patch)}")
    apply_docx(out, units, merged)
    paras = _read_docx_paras(out)
    markers = sum(1 for p in paras if "UNIT_p" in p or "UNIT_t" in p)
    print(f"applied markers in file: {markers}/{len(units)}")
    for p in paras[:6]:
        print(" ", p[:100])


def _meta(q: str, ot: str, *, name: str, ftype: str):
    from pipeline.schemas.task_schema import InputMetadata

    return InputMetadata(
        query=q,
        preferred_output_format=ot,
        file_count=1,
        link_count=0,
        links=[],
        files=[{"name": name, "type": ftype}],
        has_docs=True,
        has_image=False,
        profile_id="none",
        message_count=0,
        has_excerpt=False,
        has_preview_selection=False,
        has_valid_preview_selection=False,
        query_source="user",
    )


def test_mode_routing():
    from pipeline.direct.mode_resolver import resolve_direct_mode

    print(
        "chat translate:",
        resolve_direct_mode(
            "chat",
            _meta("translate to english", "chat", name="1 doc_mutation_test.docx", ftype="document"),
            "translate to english",
        ),
    )
    print(
        "doc chinese:",
        resolve_direct_mode(
            "document",
            _meta(
                "translate chinese to english",
                "document",
                name="1 doc_mutation_test.docx",
                ftype="document",
            ),
            "translate chinese to english",
        ),
    )
    print(
        "pdf doc translate:",
        resolve_direct_mode(
            "document",
            _meta(
                "translate to english",
                "document",
                name="1 doc_mutation_test.pdf",
                ftype="document",
            ),
            "translate to english",
        ),
    )
    print(
        "pptx doc translate:",
        resolve_direct_mode(
            "document",
            _meta(
                "translate to english",
                "document",
                name="2 testppt_mutation.pptx",
                ftype="presentation",
            ),
            "translate to english",
        ),
    )
    print(
        "pptx presentation translate:",
        resolve_direct_mode(
            "presentation",
            _meta(
                "translate to english",
                "presentation",
                name="2 testppt_mutation.pptx",
                ftype="presentation",
            ),
            "translate to english",
        ),
    )


def test_batch_strip():
    from pipeline.context.digest_store import join_digest_plain_text, build_source_digests
    from pipeline.direct.batch_processor import strip_context_source_headers, _hard_split_chars
    from pipeline.direct.batch_budget import single_pass_char_limit, resolve_batch_budget, batch_chunk_chars
    from services.source_parser.parse import parse_file

    if not os.path.isfile(DOC_LONG):
        print("SKIP long doc:", DOC_LONG)
        return
    ps = parse_file(DOC_LONG)
    digests = build_source_digests([ps])
    plain = strip_context_source_headers(join_digest_plain_text(digests))
    if not plain:
        units = ps.mutation_units or []
        plain = "\n\n".join((u.get("text") or "") for u in units)
        plain = strip_context_source_headers(plain)
    budget = resolve_batch_budget({}, "qwen3.5-instruct:2b")
    limit = single_pass_char_limit(budget)
    chunks = _hard_split_chars(plain, batch_chunk_chars(budget))
    print(
        f"long doc chars={len(plain)} single_pass_limit={limit} "
        f"hard_chunks={len(chunks)} batches_needed={len(plain) > limit}"
    )
    assert len(plain) > 1000
    assert len(chunks) >= 2 or len(plain) <= limit
    assert "Source:" not in plain[:800]


def test_pdf_pptx_extract():
    from services.office_mutation.extract import extract_units

    pdf = os.path.join(ROOT, "TEST", "1 doc_mutation_test.pdf")
    pptx = os.path.join(ROOT, "TEST", "2 testppt_mutation.pptx")
    if os.path.isfile(pdf):
        units = extract_units(pdf)
        print(f"pdf units={len(units)} sample={units[0]['id'] if units else 'none'}")
        assert len(units) >= 1
    if os.path.isfile(pptx):
        units = extract_units(pptx)
        print(f"pptx units={len(units)} sample={units[0]['id'] if units else 'none'}")
        assert len(units) >= 1


if __name__ == "__main__":
    test_mode_routing()
    test_extract_apply_ids()
    test_pdf_pptx_extract()
    test_batch_strip()
    print("DONE")
