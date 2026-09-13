# -*- coding: utf-8 -*-
"""Answer a specific natural-language question against uploaded tabular data.

Flow: describe the schema (never the full data) → LLM writes one pandas
snippet → sandbox-execute it against the real dataframe(s) → a second LLM
call turns the computed result into a plain-language answer. If the snippet
never executes cleanly after a retry, the caller should fall back to a
generic profile-based answer rather than presenting a guess as fact.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from services.graph_generation.dataset import load_all_sheets, resolve_tabular_paths
from services.spreadsheet_query.sandbox import run_sandboxed

_MAX_RETRIES = 3
_PREVIEW_ROWS = 5
_MAX_RESULT_CHARS = 4000
_MAX_SCHEMA_ROWS_CHARS = 2500   # per-dataset budget for sample rows (whole rows only)
_MAX_GUESS_CHARS = 6000         # serialized-data cap for the best-effort fallback
FAIL_MARKER = "__DS_FAIL__"     # prefix of the structured failure payload (explain_failure)


def _norm_col(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).replace("\n", " ")).strip().lower()


def _find_column(df, *hints: str) -> str | None:
    for col in df.columns:
        nc = _norm_col(col)
        if all(h in nc for h in hints):
            return str(col)
    return None


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _col_tokens(name: str) -> set[str]:
    return _tokens(_norm_col(name).replace("_", " "))


def _best_column(df, question: str, *, numeric: bool = False, min_overlap: int = 1) -> str | None:
    q = _tokens(question)
    best_score, best = 0, None
    for col in df.columns:
        if numeric and df[col].dtype.kind not in "if":
            continue
        ct = _col_tokens(col)
        score = len(ct & q)
        if score > best_score:
            best_score, best = score, col
    return str(best) if best_score >= min_overlap else None


_NAME_HINTS = (
    ("full", "name"),
    ("display", "name"),
    ("customer", "name"),
    ("product", "name"),
    ("employee", "name"),
    ("first", "name"),
    ("last", "name"),
)
_ID_HINTS = (
    ("employee", "id"),
    ("customer", "id"),
    ("product", "id"),
    ("user", "id"),
    ("account", "id"),
    ("order", "id"),
)
_DATE_SEMANTIC = {
    "hire": ("hire", "hired", "employ", "employed", "employment", "join", "joined", "start", "started", "onboard"),
    "birth": ("birth", "born", "dob", "birthday"),
    "order": ("order", "purchase", "sale", "sold", "transaction", "invoice"),
    "pay": ("pay", "payroll", "period", "payment"),
    "create": ("created", "create", "opened", "registered"),
}
_MONEY_TOKENS = frozenset(
    {"salary", "salaries", "wage", "wages", "pay", "paid", "earn", "earns", "earning",
     "money", "income", "revenue", "sales", "price", "cost", "amount", "fee", "compensation"}
)
_RATING_TOKENS = frozenset(
    {"rating", "ratings", "score", "scores", "performance", "perform", "grade", "rank", "ranking"}
)
_CATEGORY_TOKENS = frozenset(
    {"department", "dept", "region", "team", "category", "group", "division", "branch",
     "status", "type", "class", "segment", "channel", "platform", "country", "city", "state"}
)
_ENTITY_COUNT_TOKENS = frozenset(
    {"employee", "employees", "staff", "people", "worker", "workers", "customer", "customers",
     "user", "users", "member", "members", "record", "records", "row", "rows", "item", "items"}
)


def _name_column(df) -> str | None:
    for hints in _NAME_HINTS:
        col = _find_column(df, *hints)
        if col:
            return col
    return _find_column(df, "name")


def _id_column(df) -> str | None:
    for hints in _ID_HINTS:
        col = _find_column(df, *hints)
        if col:
            return col
    for col in df.columns:
        nc = _norm_col(col)
        if nc.endswith(" id") or nc.endswith("_id") or nc == "id":
            return str(col)
    return None


def _row_label(df, row_idx: int, dfs: dict[str, Any]) -> str:
    """Prefer a human-readable name; fall back to an id looked up across frames."""
    name_col = _name_column(df)
    if name_col:
        return str(df.loc[row_idx, name_col])
    id_col = _id_column(df)
    if not id_col:
        return str(row_idx)
    eid = str(df.loc[row_idx, id_col])
    for other in dfs.values():
        if other is df:
            continue
        oid = _id_column(other)
        oname = _name_column(other)
        if oid and oname:
            hit = other[other[oid].astype(str) == eid]
            if not hit.empty:
                return str(hit[oname].iloc[0])
    return eid


def _label_for_id(eid: str, id_col: str, dfs: dict[str, Any], fallback_df=None) -> str:
    for other in dfs.values():
        oid = _id_column(other) or (id_col if id_col in other.columns else None)
        oname = _name_column(other)
        if oid and oname:
            hit = other[other[oid].astype(str) == str(eid)]
            if not hit.empty:
                return str(hit[oname].iloc[0])
    if fallback_df is not None and id_col in fallback_df.columns:
        name_col = _name_column(fallback_df)
        if name_col:
            hit = fallback_df[fallback_df[id_col].astype(str) == str(eid)]
            if not hit.empty:
                return str(hit[name_col].iloc[0])
    return str(eid)


def _parse_question_date(raw: str):
    import pandas as pd

    raw = (raw or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            return pd.to_datetime(raw, format="%Y-%m-%d", errors="raise").normalize()
        except Exception:
            pass
    for dayfirst in (True, False):
        try:
            ts = pd.to_datetime(raw, dayfirst=dayfirst, errors="raise")
            return ts.normalize()
        except Exception:
            continue
    return None


def _is_date_like(series) -> bool:
    import pandas as pd
    import warnings

    if str(series.dtype).startswith("datetime"):
        return True
    sample = series.dropna().head(12)
    if sample.empty:
        return False
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        parsed = pd.to_datetime(sample, errors="coerce", dayfirst=True)
    return float(parsed.notna().mean()) >= 0.7


def _date_columns(df, question: str) -> list[str]:
    """Rank date-like columns by how well they match the question semantics."""
    q = (question or "").lower()
    q_tokens = _tokens(question)
    scored: list[tuple[float, str]] = []
    for col in df.columns:
        if not _is_date_like(df[col]):
            continue
        nc = _norm_col(col)
        ct = _col_tokens(col)
        score = float(len(ct & q_tokens))
        for _bucket, words in _DATE_SEMANTIC.items():
            if any(w in nc for w in words) and any(w in q for w in words):
                score += 3.0
            elif any(w in nc for w in words):
                score += 0.5
        if "date" in nc or "time" in nc or "period" in nc:
            score += 0.25
        scored.append((score, str(col)))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [c for s, c in scored if s > 0] or [c for _, c in scored]


def _metric_column(df, question: str) -> str | None:
    """Pick the numeric column that best matches the question (money, rating, etc.)."""
    q = (question or "").lower()
    q_tokens = _tokens(question)
    num_cols = [str(c) for c in df.select_dtypes(include="number").columns]
    if not num_cols:
        return None
    from pipeline.query_intent_i18n import matches as _qi_matches

    wants_money = bool(q_tokens & _MONEY_TOKENS) or _qi_matches(q, "spreadsheet_money")
    wants_rating = bool(q_tokens & _RATING_TOKENS) or _qi_matches(q, "spreadsheet_rating") or bool(
        re.search(r"\b(perform|performance)\b", q)
    ) or (
        bool(re.search(r"\b(best|worst)\b", q))
        and not wants_money
        and any(_col_tokens(c) & _RATING_TOKENS for c in num_cols)
    )

    def score(col: str) -> float:
        ct = _col_tokens(col)
        s = float(len(ct & q_tokens))
        if wants_money and ct & _MONEY_TOKENS:
            s += 4.0
        if wants_rating and ct & _RATING_TOKENS:
            s += 4.0
        if wants_money and ct & _RATING_TOKENS:
            s -= 2.0
        if wants_rating and ct & _MONEY_TOKENS:
            s -= 2.0
        return s

    ranked = sorted(num_cols, key=score, reverse=True)
    if score(ranked[0]) >= 1:
        return ranked[0]
    return _best_column(df, question, numeric=True, min_overlap=1) or ranked[0]


def _is_id_like(col: str) -> bool:
    nc = _norm_col(col)
    if nc == "id":
        return True
    # "employee id" / "employee_id" — but not "department id" (category).
    if _col_tokens(col) & _CATEGORY_TOKENS:
        return False
    return nc.endswith(" id") or nc.endswith("_id") or bool(re.search(r"(^| )id$", nc))


def _category_column(df, question: str) -> str | None:
    q_tokens = _tokens(question)
    best, best_score = None, 0.0
    for col in df.columns:
        if _is_id_like(str(col)):
            continue
        if _is_date_like(df[col]):
            continue
        if df[col].dtype.kind in "if":
            nunique = int(df[col].nunique(dropna=True))
            if nunique > max(40, int(0.5 * max(1, len(df)))):
                continue
            if not (_col_tokens(col) & _CATEGORY_TOKENS):
                continue
        nunique = int(df[col].nunique(dropna=True))
        if nunique < 2 or nunique > max(40, int(0.5 * max(1, len(df)))):
            continue
        ct = _col_tokens(col)
        score = float(len(ct & q_tokens))
        if ct & _CATEGORY_TOKENS and (q_tokens & _CATEGORY_TOKENS):
            score += 4.0
        elif ct & _CATEGORY_TOKENS:
            score += 0.5
        if score > best_score:
            best_score, best = score, str(col)
    return best if best_score >= 1 else None


def _entity_key_column(df) -> str | None:
    """Column that identifies entities for aggregation (id preferred over name)."""
    return _id_column(df) or _name_column(df)


def _superlative_answer(question: str, dfs: dict[str, Any]) -> str | None:
    """Who/what has the highest|lowest <metric> — works across any tabular schema."""
    q = question.lower()
    if not re.search(
        r"\b(highest|max|maximum|top|largest|biggest|best|most|lowest|min|minimum|smallest|worst|least)\b",
        q,
    ):
        return None
    want_max = bool(re.search(
        r"\b(highest|max|maximum|top|largest|biggest|best|most)\b", q
    ))

    # Prefer frames that actually contain a matching metric.
    candidates: list[tuple[float, str, Any, str]] = []
    for key, df in dfs.items():
        if key == "joined":
            continue
        metric = _metric_column(df, question)
        if not metric:
            continue
        score = float(len(_col_tokens(metric) & _tokens(question)))
        if _col_tokens(metric) & _MONEY_TOKENS and (_tokens(question) & _MONEY_TOKENS):
            score += 3
        if _col_tokens(metric) & _RATING_TOKENS and (_tokens(question) & _RATING_TOKENS):
            score += 3
        candidates.append((score, key, df, metric))
    # Joined frame last as a fallback when it has both metric + labels.
    if "joined" in dfs:
        df = dfs["joined"]
        metric = _metric_column(df, question)
        if metric:
            candidates.append((0.5, "joined", df, metric))
    candidates.sort(key=lambda t: t[0], reverse=True)

    for _score, _key, df, metric in candidates:
        series = df[metric]
        if series.dropna().empty:
            continue
        entity = _entity_key_column(df)
        use_mean = bool(_col_tokens(metric) & _MONEY_TOKENS)
        if entity and entity != metric and use_mean:
            grouped = df.groupby(entity, as_index=False)[metric].mean()
            row = grouped.loc[grouped[metric].idxmax() if want_max else grouped[metric].idxmin()]
            label = _label_for_id(row[entity], entity, dfs, fallback_df=df)
            val = row[metric]
        else:
            idx = series.idxmax() if want_max else series.idxmin()
            label = _row_label(df, idx, dfs)
            val = series.loc[idx]
        return f"{label} ({metric}: {val})"
    return None


def _year_filter_answer(question: str, dfs: dict[str, Any]) -> str | None:
    """Rows whose date column is after|before|since|in|from a year — schema-agnostic."""
    q = question.lower()
    m = re.search(
        r"\b(?:hired|hire|joined|join|employed|employ|employment|started|start|"
        r"born|birth|created|create|opened|registered|ordered|purchased|"
        r"paid|pay|dated?)\b.*?\b(after|before|since|in|from|during)\s+(\d{4})\b"
        r"|\b(after|before|since|in|from|during)\s+(\d{4})\b.*?\b"
        r"(?:hired|hire|joined|join|employed|employ|started|start|born|birth|"
        r"created|ordered|purchased)\b",
        q,
    )
    if not m:
        # Broader: any "after|before YYYY" when a date column exists.
        m = re.search(r"\b(after|before|since|in|from|during)\s+(\d{4})\b", q)
        if not m:
            return None
        # Require a date-ish verb/noun so we don't steal unrelated year questions.
        if not re.search(
            r"\b(hire|hired|employ|employed|join|joined|start|started|born|birth|"
            r"create|created|order|ordered|date|dated|year|before|after)\b",
            q,
        ):
            return None
        op, year_s = m.group(1), m.group(2)
    else:
        op = m.group(1) or m.group(3)
        year_s = m.group(2) or m.group(4)
    year = int(year_s)
    op = (op or "after").lower()

    import pandas as pd

    # Prefer frames whose date columns match question semantics (hire vs pay period).
    ranked: list[tuple[float, str, Any, str]] = []
    for key, df in dfs.items():
        if key == "joined":
            continue
        for col in _date_columns(df, question):
            score = 1.0
            nc = _norm_col(col)
            for words in _DATE_SEMANTIC.values():
                if any(w in nc for w in words) and any(w in q for w in words):
                    score += 4.0
            ranked.append((score, key, df, col))
    ranked.sort(key=lambda t: t[0], reverse=True)

    for _score, _key, df, date_col in ranked:
        parsed = pd.to_datetime(df[date_col], errors="coerce")
        years = parsed.dt.year
        if op in ("after", "since"):
            mask = years > year
            phrase = f"after {year}"
        elif op == "before":
            mask = years < year
            phrase = f"before {year}"
        else:  # in / from / during
            mask = years == year
            phrase = f"in {year}"
        hits = df[mask]
        if hits.empty:
            return f"No matching rows {phrase} ({date_col})."
        labels = [_row_label(df, idx, dfs) for idx in hits.index[:25]]
        # Deduplicate while preserving order.
        seen: set[str] = set()
        uniq = []
        for lb in labels:
            if lb not in seen:
                seen.add(lb)
                uniq.append(lb)
        return ", ".join(uniq)
    return None


def _date_lookup_answer(question: str, dfs: dict[str, Any]) -> str | None:
    q = question.lower()
    m = re.search(
        r"\bon\s+(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{2}-\d{2})\b",
        q,
    )
    if not m:
        return None
    target = _parse_question_date(m.group(1))
    if target is None:
        return None

    import pandas as pd

    for _key, df in dfs.items():
        for date_col in _date_columns(df, question):
            parsed = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True).dt.normalize()
            hits = df[parsed == target]
            if hits.empty:
                continue
            labels = [_row_label(df, idx, dfs) for idx in hits.index[:10]]
            return ", ".join(labels)
    return None


def _group_metric_answer(question: str, dfs: dict[str, Any]) -> str | None:
    """Which <category> has the best/highest <metric>?"""
    from pipeline.query_intent_i18n import matches as _qi_matches

    q = question.lower()
    if not (re.search(r"\b(which|what)\b", q) or _qi_matches(q, "which_what_words")):
        return None
    if not (
        (_tokens(question) & _CATEGORY_TOKENS) or _qi_matches(q, "spreadsheet_category")
    ):
        return None
    if not (
        re.search(
            r"\b(best|top|highest|lowest|worst|most|least|perform|performance|rating|score)\b",
            q,
        )
        or _qi_matches(q, "superlative_max_words")
        or _qi_matches(q, "superlative_min_words")
        or _qi_matches(q, "spreadsheet_rating")
    ):
        return None
    want_max = not bool(
        re.search(r"\b(lowest|worst|least)\b", q) or _qi_matches(q, "superlative_min_words")
    )

    frames: list[tuple[str, Any]] = []
    if "joined" in dfs:
        frames.append(("joined", dfs["joined"]))
    frames.extend((k, d) for k, d in dfs.items() if k != "joined")

    for _key, df in frames:
        cat = _category_column(df, question)
        metric = _metric_column(df, question)
        if not cat or not metric or cat == metric:
            continue
        means = df.groupby(cat)[metric].mean()
        if means.dropna().empty:
            continue
        winner = means.idxmax() if want_max else means.idxmin()
        return f"{winner} (mean {metric}: {means.loc[winner]:.2f})"
    return None


def _group_count_answer(question: str, dfs: dict[str, Any]) -> str | None:
    """Which <category> has the most <entities/rows>?"""
    from pipeline.query_intent_i18n import matches as _qi_matches

    q = question.lower()
    if not (re.search(r"\b(which|what)\b", q) or _qi_matches(q, "which_what_words")):
        return None
    if not (
        (_tokens(question) & _CATEGORY_TOKENS) or _qi_matches(q, "spreadsheet_category")
    ):
        return None
    if not (
        re.search(r"\b(most|highest|max|maximum|largest|number|count|many|fewest|least)\b", q)
        or _qi_matches(q, "superlative_max_words")
        or _qi_matches(q, "superlative_min_words")
    ):
        return None
    if (
        not (_tokens(question) & _ENTITY_COUNT_TOKENS)
        and "number" not in q
        and "count" not in q
        and not _qi_matches(q, "spreadsheet_entity_count")
    ):
        return None
    want_max = not bool(
        re.search(r"\b(fewest|least)\b", q) or _qi_matches(q, "superlative_min_words")
    )

    best = None
    for _key, df in dfs.items():
        if _key == "joined":
            continue
        cat = _category_column(df, question)
        if not cat:
            continue
        entity = _entity_key_column(df)
        if entity and entity != cat:
            counts = df.groupby(cat)[entity].nunique()
        else:
            counts = df.groupby(cat).size()
        if counts.empty:
            continue
        # Prefer frames whose category column matches the question (e.g. Department).
        cat_score = float(len(_col_tokens(cat) & _tokens(question)))
        if _col_tokens(cat) & _CATEGORY_TOKENS:
            cat_score += 5.0
        score = cat_score * 100 + float(len(df)) + (10.0 if entity else 0.0)
        winner = counts.idxmax() if want_max else counts.idxmin()
        val = int(counts.loc[winner])
        leaders = counts[counts == counts.loc[winner]].index.tolist()
        if best is None or score > best[0]:
            best = (score, leaders, val, cat)

    if best is None:
        return None
    _score, leaders, val, cat = best
    unit = "rows"
    if any(t in question.lower() for t in ("employee", "staff", "people", "worker")):
        unit = "employees"
    elif any(t in question.lower() for t in ("customer", "user", "member")):
        unit = "entities"
    if len(leaders) == 1:
        return f"{leaders[0]} ({val} {unit})"
    return ", ".join(f"{d} ({val} {unit})" for d in leaders)


def _heuristic_answer(question: str, dfs: dict[str, Any]) -> str | None:
    """Fast path for common filter/lookup questions — no LLM codegen."""
    q = (question or "").strip().lower()
    if not q or not dfs:
        return None

    for fn in (
        _year_filter_answer,
        _group_count_answer,
        _group_metric_answer,
        _superlative_answer,
        _date_lookup_answer,
    ):
        hit = fn(question, dfs)
        if hit:
            return hit

    num_m = re.search(r"(?:=\s*|=\s*to\s*|has\s+|have\s+)(\d+)", q)
    num = int(num_m.group(1)) if num_m else None
    if num is None:
        num_m = re.search(r"\b(\d+)\s+(?:item|unit)", q)
        num = int(num_m.group(1)) if num_m else None

    product_term = ""
    m = re.search(r"(?:for (?:the )?|named )([a-z][a-z0-9 _-]+)", q)
    if m:
        product_term = m.group(1).strip()

    for _key, df in dfs.items():
        name_col = _find_column(df, "product", "name") or _find_column(df, "name")
        sold_col = _find_column(df, "units", "sold") or _find_column(df, "sold")
        cost_col = (
            _find_column(df, "cost", "unit")
            or _find_column(df, "cost", "price")
            or _find_column(df, "price", "unit")
        )

        if num is not None and sold_col and name_col and ("sold" in q or "units" in q):
            hits = df[df[sold_col] == num]
            if not hits.empty:
                names = hits[name_col].astype(str).tolist()
                return ", ".join(names)

        if cost_col and name_col and ("cost" in q or "price" in q):
            subset = df
            if product_term:
                subset = df[df[name_col].astype(str).str.lower().str.contains(product_term, na=False)]
            if subset.empty:
                continue
            val = subset[cost_col].iloc[0]
            pname = subset[name_col].iloc[0]
            return f"{pname}: {val}"

        if name_col and product_term and ("what" in q or "which" in q):
            subset = df[df[name_col].astype(str).str.lower().str.contains(product_term, na=False)]
            if not subset.empty and len(subset.columns) > 1:
                row = subset.iloc[0]
                bits = [f"{name_col}: {row[name_col]}"]
                for c in subset.columns:
                    if c != name_col and _norm_col(c) in q:
                        bits.append(f"{c}: {row[c]}")
                if len(bits) > 1:
                    return "; ".join(bits)
    return None


def _schema_text(dfs: dict[str, Any]) -> str:
    """Per-column dtype + sample values + nulls, plus whole sample rows (never cut mid-JSON)."""
    parts: list[str] = []
    for name, df in dfs.items():
        col_lines: list[str] = []
        for c in df.columns:
            s = df[c]
            samples = ", ".join(str(v)[:40] for v in s.dropna().unique()[:4]) or "—"
            nulls = int(s.isna().sum())
            line = f"  {str(c)!r} ({s.dtype}) — sample: {samples}"
            if nulls:
                line += f"; nulls: {nulls}"
            col_lines.append(line)
        row_lines: list[str] = []
        used = 0
        for rec in df.head(_PREVIEW_ROWS).to_dict(orient="records"):
            row = json.dumps(rec, default=str, ensure_ascii=False)
            if used + len(row) > _MAX_SCHEMA_ROWS_CHARS:
                break
            row_lines.append(row)
            used += len(row)
        parts.append(
            f"### {name}\nRows: {len(df)}\nColumns:\n" + "\n".join(col_lines)
            + "\nSample rows:\n" + "\n".join(row_lines)
        )
    return "\n\n".join(parts)


def _history_block(history: list[tuple[str, str]] | None) -> str:
    if not history:
        return ""
    lines = [f"Q: {q}\nA: {a[:300]}" for q, a in history[-3:]]
    return "Previous conversation (for context):\n" + "\n".join(lines) + "\n\n"


def _codegen_messages(
    question: str,
    dfs: dict[str, Any],
    *,
    error: str = "",
    prior_code: str = "",
    history: list[tuple[str, str]] | None = None,
) -> list[dict[str, str]]:
    schema = _schema_text(dfs)
    single = len(dfs) == 1
    ref = "df" if single or "joined" in dfs else "dfs[<name>]"
    system = (
        "You write ONE short pandas snippet that computes the answer to a question "
        "about tabular data already loaded in memory.\n"
        f"Available: {'a DataFrame named df' if single else 'a dict of DataFrames named dfs, keyed by exact string'} "
        "and the module pd (pandas).\n"
        "Allowed: normal indexing/boolean masks, .dt and .str accessors, groupby with "
        "built-in aggregations (.mean()/.sum()/... or .agg('mean') with string names), "
        "sorting, f-strings, ternaries.\n"
        "NOT allowed: imports, file/network access, lambdas or def, "
        ".apply/.transform/.pipe/.query, plotting.\n"
        "Never loop or use a comprehension over a DataFrame — filter with a boolean mask, "
        "select the column, and finish with .tolist() when a list is wanted.\n"
        "Use exact column names as shown (including spaces). Access columns with df['col'] or "
        "dfs['dataset_key']['col'] — never invent column names. Datetime columns are already "
        "parsed — filter with .dt (e.g. df[df['Hire_Date'].dt.year == 2022]).\n"
        "Assign the final answer to a variable named `result` — a number, string, list, "
        "or small Series/DataFrame. Keep it to a few lines.\n"
        "Do NOT call print() — only assign to `result`.\n"
        "Examples:\n```python\nhits = df[df['Hire_Date'].dt.year == 2022]\n"
        "result = hits['Full_Name'].tolist()\n```\n"
        "```python\nresult = df.groupby('Department_ID')['Monthly_Salary'].mean()\n```\n"
        "Output ONLY the code in a single ```python fenced block. No explanation."
    )
    if "joined" in dfs:
        system += (
            "\ndf is all uploaded files merged on their join keys — it has every column."
            if single
            else (
                "\ndf is the pre-joined table (all files merged on their join keys). PREFER df "
                "for any question that spans files. Never loop over dfs.values() — each dataset "
                "has different columns; use a column only on the dataset that has it."
            )
        )
    keys = ""
    if not single:
        keys = "Dataset keys: " + ", ".join(repr(k) for k in dfs.keys()) + "\n"
    user = (
        f"{_history_block(history)}Data schema:\n\n{keys}{schema}\n\n"
        f"Question: {question}\n\nWrite the snippet using {ref}."
    )
    if error:
        hint = ""
        if error.startswith("rejected:"):
            hint = (
                "Common fixes: no lambdas/.apply — use built-in aggregations; "
                "no .query — use boolean masks.\n"
            )
        user = (
            f"Your previous snippet failed:\n```python\n{prior_code}\n```\nError: {error}\n"
            f"{hint}Fix it and try again.\n\n{user}"
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _extract_code(raw: str) -> str:
    m = re.search(r"```(?:python)?\s*\n(.*?)```", raw or "", re.S)
    return (m.group(1) if m else (raw or "")).strip()


def _answer_messages(
    question: str, result: Any, history: list[tuple[str, str]] | None = None
) -> list[dict[str, str]]:
    result_text = repr(result)
    if len(result_text) > _MAX_RESULT_CHARS:
        result_text = result_text[:_MAX_RESULT_CHARS] + "…"
    system = (
        "Answer the user's question using ONLY the computed result given below — "
        "it is the exact, correct answer. Do not recompute, guess, or state a "
        "different number. One to three sentences, plain language."
    )
    user = f"{_history_block(history)}Question: {question}\n\nComputed result:\n{result_text}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _best_effort_guess(
    question: str,
    dfs: dict[str, Any],
    generate_text: Callable[[list[dict[str, str]]], str],
    history: list[tuple[str, str]] | None = None,
) -> str:
    """Direct LLM answer over the serialized data — only when it fits comfortably."""
    blobs: list[str] = []
    for name, df in dfs.items():
        if name == "joined":
            continue  # raw sheets are enough; the joined frame just repeats them
        blobs.append(f"### {name}\n{df.to_csv(index=False)}")
    body = "\n\n".join(blobs)
    if not body or len(body) > _MAX_GUESS_CHARS:
        return ""
    system = (
        "Answer the user's question directly from the small dataset below. Be precise "
        "and mention the relevant values. If the answer cannot be determined from the "
        "data, say exactly that."
    )
    user = f"{_history_block(history)}Data:\n{body}\n\nQuestion: {question}"
    try:
        return (generate_text([
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]) or "").strip()
    except Exception:  # noqa: BLE001 — the guess is optional
        return ""


def _load_named_dataframes(tabular: list[tuple[str, str]], *, log_fn=None) -> dict[str, Any]:
    dfs: dict[str, Any] = {}
    for name, path in tabular:
        try:
            sheets = load_all_sheets(path)
        except Exception as ex:
            if log_fn:
                log_fn(f"Could not load {name}: {ex}")
            continue
        multi_sheet = len(sheets) > 1
        for sheet_name, df in sheets.items():
            key = f"{name}::{sheet_name}" if multi_sheet else name
            dfs[key] = df
    return dfs


def answer_spreadsheet_question(
    question: str,
    *,
    parsed_sources: list | None = None,
    dfs: dict[str, Any] | None = None,
    generate_text: Callable[[list[dict[str, str]]], str],
    log_fn: Callable[[str], None] | None = None,
    history: list[tuple[str, str]] | None = None,
    explain_failure: bool = False,
) -> str:
    """Compute a precise answer, or return "" so the caller can fall back.

    Callers may pass ready-made in-memory DataFrames via ``dfs`` (e.g. Data Studio's
    already-cleaned workspace frames) to skip re-reading from disk; otherwise frames are
    loaded from ``parsed_sources``.

    With ``explain_failure=True`` a total failure returns FAIL_MARKER + JSON
    {"error", "code", "guess"} instead of "" — the code that was attempted, the last
    error, and an optional best-effort LLM answer from the raw data (unverified).
    """
    question = (question or "").strip()
    if not question:
        return ""

    if dfs is None:
        tabular = resolve_tabular_paths(parsed_sources)
        if not tabular:
            return ""
        dfs = _load_named_dataframes(tabular, log_fn=log_fn)
    if not dfs:
        return ""

    heuristic = _heuristic_answer(question, dfs)
    if heuristic:
        return heuristic

    namespace: dict[str, Any] = {"pd": __import__("pandas"), "dfs": dfs}
    codegen_dfs = dfs
    if len(dfs) == 1:
        namespace["df"] = next(iter(dfs.values()))
    elif "joined" in dfs:
        # Weak models handle the single-frame idiom best — the merged table has
        # every column, so present codegen with just `df` (dfs stays available).
        namespace["df"] = dfs["joined"]
        codegen_dfs = {"joined": dfs["joined"]}

    error = ""
    code = ""
    result = None
    for attempt in range(_MAX_RETRIES + 1):
        messages = _codegen_messages(
            question, codegen_dfs, error=error, prior_code=code, history=history
        )
        raw = generate_text(messages)
        code = _extract_code(raw)
        if log_fn:
            log_fn(f"Spreadsheet query attempt {attempt + 1}: {code[:200]!r}")
        result, error = run_sandboxed(code, dict(namespace))
        if not error:
            break
        if error.startswith("KeyError"):
            cols = "; ".join(
                f"{name} has columns {[str(c) for c in df.columns]}"
                for name, df in codegen_dfs.items()
            )
            error += f" — that column does not exist. {cols}"
        elif error.startswith("TypeError"):
            error += (
                " — do not loop or comprehend over a DataFrame; use a boolean mask, "
                "select the column, then .tolist()"
            )
        if log_fn:
            log_fn(f"Spreadsheet query attempt {attempt + 1} failed: {error}")

    if error:
        if log_fn:
            log_fn(f"Spreadsheet query failed: {error}")
        retry = _heuristic_answer(question, dfs)
        if retry:
            return retry
        if explain_failure:
            guess = _best_effort_guess(question, dfs, generate_text, history)
            return FAIL_MARKER + json.dumps(
                {"error": error, "code": code, "guess": guess}, ensure_ascii=False
            )
        return ""

    if result is None:
        return ""

    return generate_text(_answer_messages(question, result, history))
