# -*- coding: utf-8 -*-
"""DOCX formatting engine for Formslator (ported from reference/format_utils_v2.py)."""
from __future__ import annotations

import os
import shutil
from zipfile import ZipFile
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.style import WD_STYLE_TYPE
from lxml import etree
import re
import pandas as pd
from docx.oxml import OxmlElement
from docx.opc.constants import RELATIONSHIP_TYPE
from copy import deepcopy
from docx.shared import Inches, Pt, RGBColor
from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.table import Table
from docx.enum.text import WD_UNDERLINE


def column_indices(original_column: str = "left") -> tuple[int, int]:
    """Return (original_idx, translation_idx) for a two-column table."""
    if (original_column or "left").strip().lower() == "right":
        return 1, 0
    return 0, 1


def _iter_original_column_paragraphs(doc, original_column: str = "left"):
    orig_idx, _ = column_indices(original_column)
    for table in doc.tables:
        for row in table.rows:
            if len(row.cells) > orig_idx:
                for p in row.cells[orig_idx].paragraphs:
                    yield p


def _iter_all_paragraphs(doc):
    for p in doc.paragraphs:
        yield p
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    yield p


def list_styles_in_template(template_path: str) -> list[str]:
    if not template_path or not os.path.isfile(template_path):
        return []
    try:
        doc = Document(template_path)
        names = []
        for st in doc.styles:
            if st.type == WD_STYLE_TYPE.PARAGRAPH and st.name:
                names.append(st.name)
        return sorted(set(names))
    except Exception:
        return []


def is_single_column_template(template_path: str) -> bool:
    """True if the template's own table has exactly one column. The Format tab uses
    this to auto-detect single- vs double-column layout from whichever template file
    is actually selected, instead of always assuming double-column (which built a
    fresh two-column table with default widths even when a single-column template
    was selected, ignoring its real structure)."""
    if not template_path or not os.path.isfile(template_path):
        return False
    try:
        tmpl = Document(template_path)
        for tbl in tmpl.tables:
            return len(tbl.columns) == 1
    except Exception:
        pass
    return False


def preview_style_samples(input_path: str, signature: str, limit: int = 8) -> list[str]:
    """Return sample paragraph texts matching a style signature."""
    if not input_path or not os.path.isfile(input_path):
        return []
    from services.formslator.style_service import signature_for_run

    try:
        doc = Document(input_path)
        items = extract_content(doc, input_path)
        styles_data = parse_styles_xml(input_path)
        doc_default_east = styles_data.get("document_defaults", {}).get("eastAsia")
    except Exception:
        return []

    samples: list[str] = []
    for item in items:
        runs = item.get("runs") or []
        para_style = item.get("style") or ""
        for r in runs:
            sig = signature_for_run(r, para_style, doc_default_east)
            if sig == signature:
                full = "".join(x.get("text", "") for x in runs).strip()
                if full and full not in samples:
                    samples.append(full)
                break
        if len(samples) >= limit:
            break
    return samples


def copy_single_run_formatting(dst_run, src_run):
    if not dst_run or not src_run:
        return

    dst_run.bold = src_run.get("bold")
    dst_run.italic = src_run.get("italic")

    # If underline is explicitly False, we must set it to False to
    # override any paragraph style defaults.
    u_val = src_run.get("underline")
    if u_val is not None:
        dst_run.underline = u_val

    if "strike" in src_run:
        dst_run.font.strike = src_run.get("strike")

    if "color" in src_run and src_run["color"]:
        try:
            dst_run.font.color.rgb = RGBColor(*src_run["color"])
        except:
            pass


def _iter_left_column_paragraphs(doc):
    for table in doc.tables:
        for row in table.rows:
            if len(row.cells) > 0:
                for p in row.cells[0].paragraphs:
                    yield p


def normalize_size(v):
    if v is None or v == "":
        return "?"
    try:
        f = float(v)
        return str(int(f)) if abs(f - round(f)) < 1e-6 else str(f)
    except Exception:
        return str(v)


def enrich_runs_from_styles(
    runs: list,
    para_style_id: str,
    styles_info: dict,
) -> None:
    """Fill missing run font/size from styles.xml (same logic as format loop)."""
    style_defaults = styles_info.get("styles", {}) if styles_info else {}
    doc_defaults = styles_info.get("document_defaults", {}) if styles_info else {}
    style_info = style_defaults.get(para_style_id, {})
    normal_info = style_defaults.get("Normal", {})
    style_font = (
        style_info.get("eastAsia")
        or normal_info.get("eastAsia")
        or doc_defaults.get("eastAsia")
    )
    style_size = style_info.get("font_size")
    for r in runs:
        if not r.get("font_name") and style_font:
            r["font_name"] = style_font
        if r.get("font_size") is None and style_size is not None:
            r["font_size"] = style_size


def run_signature(run: dict, para_style_id: str) -> str:
    """Canonical style signature: run_style|font|size (reference format_utils_v2)."""
    text = (run.get("text") or "").strip()
    if not text or text.strip().rstrip(".").isdigit():
        return ""
    run_style = (run.get("run_style") or para_style_id or "").strip()
    font_name = (run.get("font_name") or "?").strip()
    size_str = normalize_size(run.get("font_size"))
    return f"{run_style}|{font_name}|{size_str}"


def legacy_detect_signature(
    run: dict,
    para_style_id: str,
    doc_default_east: str | None,
) -> str:
    """Pre-enrich signature (matches older saved mapping files using ? for font)."""
    text = (run.get("text") or "").strip()
    if not text or text.strip().rstrip(".").isdigit():
        return ""
    run_style = (run.get("run_style") or para_style_id or "").strip()
    f_name = (run.get("font_name") or doc_default_east or "?").strip()
    size_str = normalize_size(run.get("font_size"))
    return f"{run_style}|{f_name}|{size_str}"


def paragraph_lookup_signatures(
    runs: list,
    para_style_id: str,
    styles_info: dict,
) -> list[str]:
    """Ordered signature candidates for mapping lookup (enriched + legacy)."""
    doc_default_east = (styles_info.get("document_defaults") or {}).get("eastAsia")
    raw_runs = [dict(r) for r in runs]
    enrich_runs_from_styles(runs, para_style_id, styles_info)

    ordered: list[str] = []
    seen: set[str] = set()

    def _add(sig: str) -> None:
        if sig and sig not in seen:
            ordered.append(sig)
            seen.add(sig)

    for r in runs:
        _add(run_signature(r, para_style_id))
    for r in raw_runs:
        _add(legacy_detect_signature(r, para_style_id, doc_default_east))
        rs = (r.get("run_style") or para_style_id or "").strip()
        sz = normalize_size(r.get("font_size"))
        if rs:
            _add(f"{rs}|?|{sz}")
    return ordered


def lookup_style_mapping(
    runs: list,
    para_style_id: str,
    style_mapping: dict,
    styles_info: dict,
) -> tuple[str | None, str | None, bool]:
    """Resolve original/translation template styles for a paragraph (reference rules)."""
    if not style_mapping:
        return None, None, False

    for sig in paragraph_lookup_signatures(runs, para_style_id, styles_info):
        if sig in style_mapping:
            cn, en = style_mapping[sig]
            return cn, en, True

    candidate_styles: set[str] = set()
    for r in runs:
        rs = (r.get("run_style") or para_style_id or "").strip()
        if rs:
            candidate_styles.add(rs)

    for key, (cn, en) in style_mapping.items():
        key_run = key.split("|", 1)[0].strip()
        if key_run and key_run in candidate_styles:
            return cn, en, True

    return None, None, False


def _set_paragraph_style(para, style_name: str) -> None:
    if not style_name or not str(style_name).strip():
        return
    try:
        para.style = style_name
    except Exception:
        try:
            para.style = para.part.document.styles[style_name]
        except Exception:
            pass


def build_two_column_table(out_doc: Document, template_path: str):
    tmpl = Document(template_path)
    source_table = None
    for tbl in tmpl.tables:
        if len(tbl.columns) == 2:
            source_table = tbl
            break

    table = out_doc.add_table(rows=0, cols=2)
    table.autofit = False

    if source_table:
        for i, col in enumerate(source_table.columns):
            try:
                table.columns[i].width = col.width
            except Exception:
                pass
    else:
        table.columns[0].width = Inches(3.5)
        table.columns[1].width = Inches(3.5)

    return table


def build_single_column_table(out_doc: Document, template_path: str):
    """Single-column counterpart to build_two_column_table — one row per paragraph/
    section, matching the template's own 1-column table instead of discarding it."""
    tmpl = Document(template_path)
    source_table = None
    for tbl in tmpl.tables:
        if len(tbl.columns) == 1:
            source_table = tbl
            break

    table = out_doc.add_table(rows=0, cols=1)
    table.autofit = False

    if source_table:
        try:
            table.columns[0].width = source_table.columns[0].width
        except Exception:
            pass
    else:
        table.columns[0].width = Inches(7.0)

    return table


def parse_styles_xml(docx_path):
    styles_data = {"document_defaults": {}, "styles": {}}
    try:
        with ZipFile(docx_path, "r") as docx_zip:
            if "word/styles.xml" not in docx_zip.namelist():
                return styles_data

            xml_bytes = docx_zip.read("word/styles.xml")
            from xml.etree import ElementTree as ET

            root = ET.fromstring(xml_bytes)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

            doc_defaults = root.find("w:docDefaults", ns)
            if doc_defaults is not None:
                rpr_default = doc_defaults.find("w:rPrDefault/w:rPr", ns)
                if rpr_default is not None:
                    fonts_elem = rpr_default.find("w:rFonts", ns)
                    if fonts_elem is not None:
                        ea = fonts_elem.get(f"{{{ns['w']}}}eastAsia")
                        if ea:
                            styles_data["document_defaults"]["eastAsia"] = ea

            for st in root.findall("w:style", ns):
                style_id = st.get(f"{{{ns['w']}}}styleId")
                if not style_id:
                    continue
                info = {}
                rpr = st.find("w:rPr", ns)
                if rpr is not None:
                    fonts_elem = rpr.find("w:rFonts", ns)
                    if fonts_elem is not None:
                        ea = fonts_elem.get(f"{{{ns['w']}}}eastAsia")
                        if ea:
                            info["eastAsia"] = ea
                    sz_elem = rpr.find("w:sz", ns)
                    if sz_elem is not None:
                        val = sz_elem.get(f"{{{ns['w']}}}val")
                        try:
                            info["font_size"] = int(val) / 2
                        except:
                            pass
                if info:
                    styles_data["styles"][style_id] = info
    except:
        return styles_data
    return styles_data


# ======================================================================
#  Content extraction (FIXED for Bold/Italic/Underline Detection)
# ======================================================================

def extract_content(doc: Document, filename: str):
    items = []
    NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

    def extract_from_xml(xml_bytes):
        out = []
        root = etree.fromstring(xml_bytes)

        for p in root.xpath(".//w:p", namespaces=NS):
            p_style = ""
            try:
                st = p.xpath("./w:pPr/w:pStyle/@w:val", namespaces=NS)
                if st:
                    p_style = st[0]
            except:
                pass

            runs = []
            para_text = ""

            for r in p.xpath(".//w:r", namespaces=NS):

                # Detect manual line breaks (Shift+Enter)
                has_br = True if r.xpath(".//w:br", namespaces=NS) else False

                txt_list = r.xpath(".//w:t/text()", namespaces=NS)
                txt = "".join(txt_list) if txt_list else ""

                # If a break is present, append a newline character to the text
                if has_br:
                    txt += "\n"

                para_text += txt

                run_style = ""
                try:
                    rs = r.xpath("./w:rPr/w:rStyle/@w:val", namespaces=NS)
                    if rs:
                        run_style = rs[0]
                except:
                    pass

                font_name = None
                try:
                    ea = r.xpath(".//w:rPr/w:rFonts/@w:eastAsia", namespaces=NS)
                    if ea:
                        font_name = ea[0]
                    else:
                        ac = r.xpath(".//w:rPr/w:rFonts/@w:ascii", namespaces=NS)
                        if ac:
                            font_name = ac[0]
                except:
                    pass

                font_size = None
                try:
                    sz = r.xpath(".//w:rPr/w:sz/@w:val", namespaces=NS)
                    if sz:
                        font_size = int(sz[0]) / 2
                except:
                    pass

                rpr = r.xpath("./w:rPr", namespaces=NS)

                # Inside extract_content() function in format_utils_zero_1_4.py
                def get_format_val(rpr_list, tag, ns):
                    if not rpr_list:
                        return None
                    el = rpr_list[0].find(tag, namespaces=ns)
                    if el is None:
                        return None

                    val = el.get(f"{{{ns['w']}}}val")

                    if tag == "w:u":
                        # Mapping Word XML strings to python-docx WD_UNDERLINE members
                        if val in ('none', '0', 'false'):
                            return False
                        if val is None or val in ('single', '1', 'true', 'on'):
                            return True  # python-docx treats True as WD_UNDERLINE.SINGLE

                        # For other styles, try to match the enumeration
                        # Word XML 'double' -> WD_UNDERLINE.DOUBLE
                        try:
                            return getattr(WD_UNDERLINE, val.upper())
                        except (AttributeError, KeyError):
                            return True  # Fallback to single underline if style is unknown

                    # Bold/Italic logic (stays the same)
                    if val is None or val in ('1', 'true', 'on'):
                        return True
                    return False

                # Update the calls to use this new function:
                is_bold = get_format_val(rpr, "w:b", NS)
                is_italic = get_format_val(rpr, "w:i", NS)
                is_underline = get_format_val(rpr, "w:u", NS)  # Now returns style string if applicable
                is_strike = get_format_val(rpr, "w:strike", NS)

                # Color extraction
                color_val = None
                try:
                    if rpr:
                        c_el = rpr[0].find("w:color", namespaces=NS)
                        if c_el is not None:
                            hex_val = c_el.get(f"{{{NS['w']}}}val")
                            if hex_val and len(hex_val) == 6 and hex_val.lower() != "auto":
                                color_val = tuple(int(hex_val[i:i + 2], 16) for i in (0, 2, 4))
                except:
                    pass

                runs.append({
                    "text": txt,
                    "run_style": run_style,
                    "font_name": font_name,
                    "font_size": font_size,
                    "bold": is_bold,
                    "italic": is_italic,
                    "underline": is_underline,
                    "strike": is_strike,
                    "color": color_val
                })

            out.append({
                "text": para_text,
                "style": p_style,
                "runs": runs,
                "_p": p,
            })
        return out

    with ZipFile(filename) as z:
        if "word/document.xml" in z.namelist():
            items.extend(extract_from_xml(z.read("word/document.xml")))
    return items


# ======================================================================
#  Default style helpers
# ======================================================================

def _find_default_styles(doc: Document):
    cn_default = None
    en_default = None
    normal_name = "Normal"

    for st in doc.styles:
        if st.type != WD_STYLE_TYPE.PARAGRAPH:
            continue
        name = st.name or ""
        if name == "Normal":
            normal_name = name
        if not cn_default and name.startswith("C3"):
            cn_default = name
        if not cn_default and name.startswith("C1"):
            cn_default = name
        if not en_default and name.startswith("E3"):
            en_default = name
        if not en_default and name.startswith("E1"):
            en_default = name

    if not cn_default:
        cn_default = normal_name
    if not en_default:
        en_default = normal_name
    return cn_default, en_default


def _format_preserve_original_column(
    input_path,
    output_path,
    template_path,
    original_column: str = "left",
):
    orig_idx, trans_idx = column_indices(original_column)
    src_doc = Document(input_path)
    out_doc = Document(output_path)

    for t in list(out_doc.tables):
        tbl = t._tbl
        tbl.getparent().remove(tbl)

    table = build_two_column_table(out_doc, template_path)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    from docx.oxml.text.paragraph import CT_P
    from docx.oxml.table import CT_Tbl

    def iter_all_paragraphs(doc):
        body = doc.element.body
        for child in body.iterchildren():
            if isinstance(child, CT_P):
                yield Paragraph(child, doc)
                try:
                    txbx_paras = child.xpath(".//w:txbxContent//w:p")
                    for tx_p in txbx_paras:
                        yield Paragraph(tx_p, doc)
                except Exception:
                    pass
            elif isinstance(child, CT_Tbl):
                tbl = Table(child, doc)
                for row in tbl.rows:
                    for cell in row.cells:
                        for p in cell.paragraphs:
                            yield p

    for p in iter_all_paragraphs(src_doc):
        row = table.add_row()
        orig_cell = row.cells[orig_idx]
        trans_cell = row.cells[trans_idx]
        for cell in (orig_cell, trans_cell):
            for q in list(cell.paragraphs):
                q._element.getparent().remove(q._element)

        orig_cell._element.append(deepcopy(p._p))
        trans_cell.add_paragraph("")

    out_doc.save(output_path)


def _format_preserve_left_column(input_path, output_path, template_path):
    _format_preserve_original_column(input_path, output_path, template_path, "left")


# ======================================================================
#  Main formatting entry point
# ======================================================================

def convert_vertical_to_horizontal(text):
    vertical_to_horizontal = {
        '\uFE35': '(', '\uFE36': ')', '\uFE37': '{', '\uFE38': '}',
        '\uFE39': '〔', '\uFE3A': '〕', '\uFE3B': '【', '\uFE3C': '】',
        '\uFE3D': '《', '\uFE3E': '》', '\uFE3F': '〈', '\uFE40': '〉',
        '\uFE41': '「', '\uFE42': '」', '\uFE43': '『', '\uFE44': '』',
        '\uFE47': '〖', '\uFE48': '〗',
    }
    for vertical, horizontal in vertical_to_horizontal.items():
        text = text.replace(vertical, horizontal)
    return text


def format_document(
        input_path,
        output_path=None,
        suffix="-formatted",
        output_dir=None,
        template_path=None,
        style_mapping=None,
        style_mapping_path=None,
        original_column: str = "left",
        single_column: bool = False,
):
    """single_column=True builds sequential (original, translation) paragraph pairs
    in the document body instead of a two-column table — same style-mapping and
    post-cleanup rules (colon line-breaks, vertical-to-horizontal text, spacer
    insertion), just laid out one below the other rather than side by side. Requires
    an effective style_mapping; callers should not use this mode without one."""
    orig_idx, trans_idx = column_indices(original_column)

    if template_path is None:
        raise FileNotFoundError("Style template path is required.")

    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Base template not found: {template_path}")

    if output_path is None:
        base = os.path.splitext(os.path.basename(input_path))[0]
        filename_with_suffix = f"{base}{suffix}.docx"
        output_path = os.path.join(output_dir if output_dir else os.path.dirname(input_path), filename_with_suffix)

    from services.formslator.paths import resolve_writable_output_path

    output_path = resolve_writable_output_path(output_path)
    print(f"Output will be saved to: {output_path}")
    shutil.copy2(template_path, output_path)

    effective_mapping = {}
    if style_mapping:
        for sig, pair in style_mapping.items():
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            cn_style, en_style = pair
            if (cn_style and str(cn_style).strip()) or (en_style and str(en_style).strip()):
                effective_mapping[sig] = (cn_style, en_style)

    if not effective_mapping:
        _format_preserve_original_column(input_path, output_path, template_path, original_column)
        return output_path

    style_mapping = effective_mapping
    src_doc = Document(input_path)
    content_items = extract_content(src_doc, input_path)
    print(f"Found {len(content_items)} extracted items")

    styles_info = parse_styles_xml(input_path)
    style_defaults = styles_info.get("styles", {})
    doc_defaults = styles_info.get("document_defaults", {})

    out_doc = Document(output_path)
    for t in list(out_doc.tables):
        tbl = t._tbl
        tbl.getparent().remove(tbl)

    pairs: list = []
    if single_column:
        table = build_single_column_table(out_doc, template_path)
    else:
        table = build_two_column_table(out_doc, template_path)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    default_cn_style, default_en_style = _find_default_styles(out_doc)

    # ----------------- Main Formatting Loop -----------------
    for item in content_items:
        runs = item.get("runs") or []
        para_style_id = item.get("style") or ""
        runs_raw = [dict(r) for r in runs]

        style_info = styles_info.get("styles", {}).get(para_style_id, {})
        normal_info = styles_info.get("styles", {}).get("Normal", {})
        doc_defaults = styles_info.get("document_defaults", {})
        style_font = (
            style_info.get("eastAsia")
            or normal_info.get("eastAsia")
            or doc_defaults.get("eastAsia")
        )
        style_size = style_info.get("font_size")
        doc_default_east = doc_defaults.get("eastAsia")

        for r in runs:
            if not r.get("font_name") and style_font:
                r["font_name"] = style_font
            if r.get("font_size") is None and style_size is not None:
                r["font_size"] = style_size

        cn_text = "".join(r.get("text", "") for r in runs).strip()
        if cn_text == "":
            if len(table.rows) == 0:
                continue  # Skip leading empty
            else:
                pass  # preserve internal empty paragraphs might be desired, or skip

        candidate_sigs: list[str] = []
        seen_sigs: set[str] = set()

        def _add_sig(sig: str) -> None:
            if sig and sig not in seen_sigs:
                candidate_sigs.append(sig)
                seen_sigs.add(sig)

        for r, raw in zip(runs, runs_raw):
            text = (r.get("text") or "").strip()
            if not text or text.strip().rstrip(".").isdigit():
                continue
            run_style = r.get("run_style") or para_style_id
            font_name = r.get("font_name") or "?"
            size_str = normalize_size(r.get("font_size"))
            _add_sig(f"{run_style}|{font_name}|{size_str}")
            raw_style = raw.get("run_style") or para_style_id
            raw_font = raw.get("font_name") or doc_default_east or "?"
            raw_size = normalize_size(raw.get("font_size"))
            _add_sig(f"{raw_style}|{raw_font}|{raw_size}")
            _add_sig(f"{raw_style}|?|{raw_size}")

        mapped_cn = None
        mapped_en = None
        found_mapping = False

        for sig in candidate_sigs:
            if sig in effective_mapping:
                mapped_cn, mapped_en = style_mapping[sig]
                found_mapping = True
                break

        if not found_mapping and style_mapping:
            candidate_styles: set[str] = set()
            for r in runs:
                rs = r.get("run_style") or para_style_id
                if rs:
                    candidate_styles.add(rs)
            for key, (cn, en) in style_mapping.items():
                if key.split("|", 1)[0] in candidate_styles:
                    mapped_cn, mapped_en = cn, en
                    found_mapping = True
                    break

        style_name_cn = mapped_cn if mapped_cn else default_cn_style
        style_name_en = mapped_en if mapped_en else default_en_style

        # Create Output Row — single_column: one cell per row, original and
        # translation stacked as two paragraphs in that cell. double_column:
        # original and translation in separate cells of the same row.
        if single_column:
            row = table.add_row()
            cell = row.cells[0]

            for p in list(cell.paragraphs):
                p._element.getparent().remove(p._element)

            cn_para = cell.add_paragraph()
            en_para = cell.add_paragraph()
            pairs.append((cn_para, en_para))
        else:
            row = table.add_row()
            orig_cell = row.cells[orig_idx]
            trans_cell = row.cells[trans_idx]

            # Clean cells
            for cell in (orig_cell, trans_cell):
                for p in list(cell.paragraphs):
                    p._element.getparent().remove(p._element)

            cn_para = orig_cell.add_paragraph()
            en_para = trans_cell.add_paragraph()

            # Remove extra paragraphs
            for cell in (orig_cell, trans_cell):
                while len(cell.paragraphs) > 1:
                    p = cell.paragraphs[0]
                    p._element.getparent().remove(p._element)

        # ---------------------------------------------------------
        # RULE 1 — MAPPING APPLIED (Single Para, Mixed Fonts/Bold/Italic)
        # ---------------------------------------------------------
        if found_mapping:
            cn_para.style = style_name_cn
            en_para.style = style_name_en

            target_fonts = [
                "華康仿宋體W4", "華康魏碑體", "華康楷書體W5", "華康正顏楷體W5"
            ]

            c4_brackets_re = None
            if style_name_cn and style_name_cn.strip().startswith("C4"):
                c4_brackets_re = re.compile(r"[（(︵︽《].*?[）》)︶︾]")

            for r_info in runs:
                txt = r_info.get("text", "")
                if not txt:
                    continue

                # NEW: Remove specific quotes if it's C4
                is_c4 = style_name_cn and style_name_cn.strip().startswith("C4")
                if is_c4:
                    txt = txt.replace("「", "").replace("」", "")

                segments = []
                if c4_brackets_re:
                    last_idx = 0
                    for m in c4_brackets_re.finditer(txt):
                        if m.start() > last_idx:
                            segments.append((txt[last_idx:m.start()], False))
                        segments.append((m.group(0), True))
                        last_idx = m.end()
                    if last_idx < len(txt):
                        segments.append((txt[last_idx:], False))
                else:
                    segments = [(txt, False)]

                for seg_text, is_bracket in segments:
                    if not seg_text: continue

                    # Split by our internal newline marker to handle Shift-Enter
                    sub_parts = seg_text.split("\n")

                    for i, part in enumerate(sub_parts):
                        if part:
                            new_r = cn_para.add_run(part)
                            # Apply Formatting (Bold/Italic/Underline)
                            copy_single_run_formatting(new_r, r_info)

                            # Font Override
                            font_name = r_info.get("font_name")
                            if font_name in target_fonts:
                                new_r.font.name = font_name
                                new_r._element.rPr.rFonts.set(qn('w:eastAsia'), font_name)

                            # C4 Bracket Size Override
                            if is_bracket:
                                new_r.font.size = Pt(12)

                        # If there are more parts, it means a '\n' was here
                        # Add a physical <w:br/> (Shift-Enter) to the document
                        if i < len(sub_parts) - 1:
                            cn_para.add_run().add_break()

            if not en_para.runs:
                en_para.add_run("")

            # Clean empty runs to avoid leading whitespace issues
            for p in (cn_para, en_para):
                for r in list(p.runs):
                    # Check if the run has visible text
                    has_text = r.text and r.text.strip() != ""
                    # Check for the manual line break tag <w:br>
                    has_break = r._element.find(qn("w:br")) is not None

                    # Only remove if it has NEITHER text NOR a break
                    if not has_text and not has_break:
                        try:
                            r._element.getparent().remove(r._element)
                        except:
                            pass
            continue

        # ---------------------------------------------------------
        # RULE 2 — NO MAPPING
        # ---------------------------------------------------------
        else:
            cn_para._element.getparent().replace(cn_para._element, deepcopy(item["_p"]))
            en_para.add_run("")

    # ------------------------------------------------------------
    # POST-CLEANUPS
    # ------------------------------------------------------------

    # Post-Cleanup 2: Linebreak after colon for C4
    def insert_linebreak_after(p, target_chars):
        for run in list(p.runs):
            txt = run.text or ""
            if not txt: continue
            split_pos = -1
            for ch in target_chars:
                idx = txt.find(ch)
                if idx != -1:
                    split_pos = idx + 1
                    break
            if split_pos == -1: continue

            before = txt[:split_pos]
            after = txt[split_pos:]

            r_el = run._r
            orig_rPr = r_el.find(qn("w:rPr"))
            if orig_rPr is not None:
                r_el.remove(orig_rPr)
                r_el.append(deepcopy(orig_rPr))

            run.text = before
            br = OxmlElement("w:br")
            r_el.addnext(br)

            if after:
                new_r_el = OxmlElement("w:r")
                if orig_rPr is not None:
                    new_r_el.append(deepcopy(orig_rPr))
                t_el = OxmlElement("w:t")
                t_el.text = after
                t_el.set(qn("xml:space"), "preserve")
                new_r_el.append(t_el)
                br.addnext(new_r_el)
            return

    def _cn_paras() -> list:
        if single_column:
            return [p[0] for p in pairs]
        return [row.cells[orig_idx].paragraphs[0] for row in table.rows]

    for cn_para in _cn_paras():
        try:
            cn_style = cn_para.style.name
        except Exception:
            cn_style = None
        if cn_style and cn_style.startswith("C4"):
            insert_linebreak_after(cn_para, target_chars={":", "："})

    # Post-Cleanup 3: Vertical to Horizontal Brackets
    for cn_para in _cn_paras():
        for run in cn_para.runs:
            if run.text:
                new_text = convert_vertical_to_horizontal(run.text)
                if new_text != run.text:
                    run.text = new_text

    # Post-Cleanup 4: Spacer Rows (single_column: spacer paragraph pairs instead)
    def insert_spacer_row(table, index):
        new_row = table.add_row()
        new_tr = new_row._tr
        target_tr = table.rows[index]._tr
        target_tr.addprevious(new_tr)
        row = table.rows[index]
        for cell in row.cells:
            for p in list(cell.paragraphs):
                p._element.getparent().remove(p._element)
            cell.add_paragraph("")
        p = row.cells[trans_idx].paragraphs[0]
        run = p.add_run("\u200B")
        run.font.name = "Book Antiqua"
        run.font.size = Pt(9)
        fmt = p.paragraph_format
        fmt.space_before = Pt(0)
        fmt.space_after = Pt(0)
        fmt.line_spacing = 1

    def _set_paragraph_mark_size(paragraph, points):
        # Word computes an empty (or emptied-out) line's rendered height from the
        # paragraph MARK's own run properties (w:pPr/w:rPr) — not from an individual
        # run's font size, especially once that run has no visible characters. Setting
        # only run.font.size (as this used to do) looked right in python-docx's data
        # model but didn't reliably shrink the line in Word, and didn't survive at all
        # once write_translations_single_column_styled (translate_engine.py) deletes
        # and rebuilds this paragraph's runs during the translation write-back pass —
        # that step never touches pPr, so setting it here is what actually persists.
        half_points = str(int(round(points * 2)))
        pPr = paragraph._p.get_or_add_pPr()
        rPr = pPr.find(qn("w:rPr"))
        if rPr is None:
            rPr = OxmlElement("w:rPr")
            pPr.append(rPr)
        for tag in ("w:sz", "w:szCs"):
            el = rPr.find(qn(tag))
            if el is None:
                el = OxmlElement(tag)
                rPr.append(el)
            el.set(qn("w:val"), half_points)

    def insert_spacer_row_single(table, index):
        # Two blank paragraphs, matching every content row's (original, translation)
        # shape — extraction/write-back always read paragraph 0 / paragraph 1 of the
        # cell, so the marker run must sit on paragraph 1, leaving paragraph 0 (read
        # as this row's "original" text) genuinely empty rather than a stray ZWS.
        new_row = table.add_row()
        new_tr = new_row._tr
        target_tr = table.rows[index]._tr
        target_tr.addprevious(new_tr)
        row = table.rows[index]
        cell = row.cells[0]
        for p in list(cell.paragraphs):
            p._element.getparent().remove(p._element)
        blank_para = cell.add_paragraph("")
        marker_para = cell.add_paragraph("")
        run = marker_para.add_run("​")
        run.font.name = "Book Antiqua"
        run.font.size = Pt(9)
        # blank_para must stay a real, separate paragraph (extraction/write-back reads
        # paragraph 0/1 of every row uniformly), but it's never given any content, so
        # with no run of its own it fell back to the template's Normal style default —
        # 14pt here — stacking with marker_para's 9pt line into a ~23pt gap where
        # double-column's equivalent spacer is a single ~9pt line. A run alone wasn't
        # enough (Word renders an empty/emptied-out line's height from the paragraph
        # MARK's own run properties, not an individual run's), so set both: the mark
        # (via _set_paragraph_mark_size, which also survives write_translations_
        # single_column_styled replacing marker_para's run during translation write-
        # back — that step never touches pPr) and a matching run for belt-and-suspenders.
        blank_run = blank_para.add_run("")
        blank_run.font.name = "Book Antiqua"
        blank_run.font.size = Pt(1)
        _set_paragraph_mark_size(blank_para, 1)
        _set_paragraph_mark_size(marker_para, 9)
        for p in (blank_para, marker_para):
            fmt = p.paragraph_format
            fmt.space_before = Pt(0)
            fmt.space_after = Pt(0)
            fmt.line_spacing = 1

    spacer_positions = []
    prev_nonempty_style = None
    cn_list = _cn_paras()
    for i, cn_para in enumerate(cn_list):
        text = "".join(r.text for r in cn_para.runs).strip()
        try:
            style_name = cn_para.style.name
        except:
            style_name = None
        if not text: continue

        if style_name and style_name.startswith("C4"):
            if prev_nonempty_style not in ("C1 班名", "C2a 標題"):
                spacer_positions.append(i)
        elif style_name == "C2a 標題":
            if prev_nonempty_style != "C1 班名":
                spacer_positions.append(i)
        prev_nonempty_style = style_name

    for pos in reversed(spacer_positions):
        if single_column:
            insert_spacer_row_single(table, pos)
        else:
            insert_spacer_row(table, pos)

    # Post-Cleanup 5: Remove empty leading paragraph before the table, if any —
    # single_column now uses a table too, same check as double_column.
    body_paras = out_doc.paragraphs
    if len(body_paras) > 0 and len(out_doc.tables) > 0:
        if body_paras[0]._p.getnext() == out_doc.tables[0]._tbl:
            if not body_paras[0].text.strip():
                body_paras[0]._element.getparent().remove(body_paras[0]._element)

    out_doc.save(output_path)
    print(f"Formatted document saved to: {output_path}")
    return output_path


def insert_glossary_hyperlinks(
    docx_path: str,
    term_info: dict[str, str] | None = None,
    url_template: str | None = None,
    original_column: str = "left",
    log_fn=None,
):
    if not term_info:
        return

    terms = sorted(term_info.keys(), key=len, reverse=True)
    if not terms:
        return

    url_tpl = url_template or (
        "https://www.mdbg.net/chinese/dictionary?page=worddict&wdrst=0&wdqb={term}"
    )
    term_counts = {t: 0 for t in terms}

    quick_re = re.compile("|".join([re.escape(t) for t in terms]))
    doc = Document(docx_path)

    def wrap_run_with_hyperlink(paragraph, run_el, term, tooltip):
        part = paragraph.part
        url = url_tpl.format(term=term)
        r_id = part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
        parent = run_el.getparent()
        idx = parent.index(run_el)
        hl = OxmlElement("w:hyperlink")
        hl.set(qn("r:id"), r_id)
        if tooltip: hl.set(qn("w:tooltip"), tooltip)
        rPr = run_el.find(qn("w:rPr"))
        if rPr is None: rPr = OxmlElement("w:rPr"); run_el.insert(0, rPr)
        c = rPr.find(qn("w:color"));
        if c is None: c = OxmlElement("w:color"); rPr.append(c)
        c.set(qn("w:val"), "0000FF")
        u = rPr.find(qn("w:u"));
        if u is None: u = OxmlElement("w:u"); rPr.append(u)
        u.set(qn("w:val"), "single")
        parent.remove(run_el);
        hl.append(run_el);
        parent.insert(idx, hl)

    def split_run(run, rs, re):
        txt = run.text
        parent = run._element.getparent()
        orig_idx = parent.index(run._element)
        rPr = run._element.find(qn("w:rPr"))

        def make_r(t):
            new_r = OxmlElement("w:r")
            if rPr is not None: new_r.append(deepcopy(rPr))
            te = OxmlElement("w:t");
            te.text = t;
            te.set(qn("xml:space"), "preserve")
            new_r.append(te)
            return new_r

        rb, rm, ra = make_r(txt[:rs]), make_r(txt[rs:re]), make_r(txt[re:])
        parent.insert(orig_idx, rb);
        parent.insert(orig_idx + 1, rm);
        parent.insert(orig_idx + 2, ra)
        parent.remove(run._element)
        return rb, rm, ra

    paragraphs = list(_iter_original_column_paragraphs(doc, original_column))
    if not paragraphs:
        paragraphs = list(_iter_all_paragraphs(doc))

    for para in paragraphs:
        text = para.text or ""
        if not text.strip() or not quick_re.search(text): continue
        matches = []
        taken = set()
        for t in terms:
            start = 0
            while True:
                idx = text.find(t, start)
                if idx == -1: break
                rng = set(range(idx, idx + len(t)))
                if taken.isdisjoint(rng):
                    matches.append((idx, idx + len(t), t))
                    taken |= rng
                start = idx + len(t)
        matches.sort(key=lambda x: x[0], reverse=True)

        for s, e, t in matches:
            char_pos = 0
            for run in list(para.runs):
                rt = run.text or ""
                rlen = len(rt)
                if s < char_pos + rlen and e > char_pos:
                    _, mid, _ = split_run(run, max(0, s - char_pos), min(rlen, e - char_pos))
                    term_counts[t] += 1
                    wrap_run_with_hyperlink(para, mid, t, f"{term_info[t]}\nOccurrences: {term_counts[t]}")
                    break
                char_pos += rlen
    doc.save(docx_path)


def clean_all_hyperlinks(docx_path: str):
    doc = Document(docx_path)

    # Define a helper to process paragraphs
    def process_paragraphs(paragraphs):
        for p in paragraphs:
            p_xml = p._p
            # Look for all hyperlink tags in the paragraph XML
            for h in p_xml.findall(".//w:hyperlink", namespaces=p_xml.nsmap):
                # 1. Remove the 'blue/underlined' formatting from the runs inside the hyperlink
                for run in h.findall(".//w:r", namespaces=p_xml.nsmap):
                    rPr = run.find("w:rPr", namespaces=p_xml.nsmap)
                    if rPr is not None:
                        for tag in ("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}u",
                                    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}color"):
                            el = rPr.find(tag)
                            if el is not None:
                                rPr.remove(el)

                # 2. Unwrap the hyperlink tag: move children to the parent, then delete the hyperlink tag
                parent = h.getparent()
                idx = list(parent).index(h)
                for child in list(h):
                    parent.insert(idx, child)
                    idx += 1
                parent.remove(h)

    # Clean the main body text
    process_paragraphs(doc.paragraphs)

    # Clean text inside tables
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                process_paragraphs(cell.paragraphs)

    doc.save(docx_path)