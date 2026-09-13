#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Smoke-test office mutation paths and routing (run from repo root)."""
from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DOC_SRC = r"c:\Users\charl\OneDrive\Desktop\TEST\doc_mutation_test.docx"
PPT_SRC = r"c:\Users\charl\OneDrive\Desktop\TEST\testppt.pptx"
UPLOADS = os.path.join(ROOT, "data", "uploads")


def _stage(name: str, src: str) -> str:
    os.makedirs(UPLOADS, exist_ok=True)
    dest = os.path.join(UPLOADS, name)
    if os.path.isfile(src):
        shutil.copy2(src, dest)
        return dest
    if os.path.isfile(dest):
        return dest
    return ""


def test_extract():
    from services.office_mutation import extract_units, resolve_upload_path

    for fname in ("doc_mutation_test.docx", "testppt.pptx"):
        staged = _stage(fname, DOC_SRC if "doc" in fname else PPT_SRC)
        if not staged:
            print(f"SKIP extract {fname} (file not found)")
            continue
        path = resolve_upload_path(fname)
        units = extract_units(path)
        print(f"OK extract {fname}: {len(units)} units from {path}")
        if units:
            print(f"   sample: {units[0]['text'][:80]!r}")


def test_routing():
    from pipeline.input_metadata import build_input_metadata
    from pipeline.input_router import InputRouter
    from services.session import state

    state.active_context_files = []
    state.active_web_links = []
    state.current_settings = state.current_settings or {}
    state.current_settings["preferred_output_format"] = "sound"

    router = InputRouter()
    from pipeline.schemas.task_schema import InputMetadata

    meta = InputMetadata(
        query="write a quote on kindness and output as sound in .mp3",
        preferred_output_format="sound",
        file_count=0,
        link_count=0,
        message_count=0,
        links=[],
        files=[],
        has_docs=False,
        has_image=False,
        has_excerpt=False,
        has_preview_selection=False,
        profile_id="none",
        needs_context_retrieval=False,
        total_source_chars=0,
        has_valid_preview_selection=False,
        query_source="workspace",
    )
    d = router.route(meta)
    print(f"OK sound routing: output={d.output_type} mode={d.mode} services={d.required_services}")


def test_json3():
    from services.media_fetch import _json3_to_text

    sample = '{"events":[{"segs":[{"utf8":"Hi"},{"utf8":" world"}]}]}'
    text = _json3_to_text(sample)
    assert "Hi" in text and "world" in text
    print(f"OK json3 parse: {text!r}")


if __name__ == "__main__":
    test_json3()
    test_routing()
    test_extract()
    print("Done.")
