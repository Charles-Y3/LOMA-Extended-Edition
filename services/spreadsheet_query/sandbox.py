# -*- coding: utf-8 -*-
"""Restricted execution for LLM-generated pandas snippets.

The LLM only ever sees a schema + row preview (never the full data — see
query.py), then writes a short snippet to compute the actual answer. That
snippet is untrusted input and is validated + sandboxed before it ever touches
the real dataframe:

- AST allowlist (not a blocklist): only a fixed set of node types are permitted;
  anything else (imports, lambdas, dunder access, exec/eval) is rejected outright.
- No builtins except a small, side-effect-free subset (round/len/min/max/...).
- A handful of DataFrame/Series methods are blocked even though their node types
  are otherwise allowed, because they accept arbitrary callables (.apply, .agg,
  .transform, .pipe) or write to disk (.to_csv, .to_excel, ...).
- No `for`/`while`/`def`/`lambda` node types are in the allowlist at all, so an
  unbounded loop can't be expressed in the first place — the caller always gets
  a fast rejection or a fast result, never a hang on that front.
- A timeout still wraps execution for the remaining risk (a single slow-but-legal
  operation, e.g. a huge comprehension or an expensive pandas op). It runs the
  snippet on a worker thread and returns "execution timed out" to the caller
  after the deadline; note this does NOT forcibly kill the thread (CPython can't),
  so a truly pathological snippet keeps consuming CPU in the background after
  the caller has already moved on. Accepted for now given the allowlist already
  excludes unbounded constructs — revisit with process-based isolation
  (multiprocessing, unused elsewhere in this codebase) if that residual gap
  ever matters in practice.
"""
from __future__ import annotations

import ast
import concurrent.futures

_ALLOWED_NODES = (
    ast.Module,
    ast.Expr,
    ast.Assign,
    ast.AugAssign,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Attribute,
    ast.Subscript,
    ast.Index,
    ast.Slice,
    ast.Call,
    ast.keyword,
    ast.Constant,
    ast.Tuple,
    ast.List,
    ast.Dict,
    ast.Set,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.IfExp,
    ast.JoinedStr,
    ast.FormattedValue,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Not,
    ast.Invert,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
    ast.comprehension,
    ast.ListComp,
    ast.DictComp,
    ast.SetComp,
    ast.GeneratorExp,
    ast.Starred,
)

_FORBIDDEN_NAMES = frozenset(
    {
        "__import__",
        "exec",
        "eval",
        "compile",
        "open",
        "input",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "__builtins__",
    }
)

# Methods that accept an arbitrary callable (could smuggle in unwanted behavior even
# though ast.Lambda/ast.FunctionDef are already blocked) or write to disk/network.
_DISALLOWED_METHODS = frozenset(
    {
        "apply",
        "applymap",
        "pipe",
        "transform",
        "eval",
        "query",
        "to_csv",
        "to_excel",
        "to_pickle",
        "to_sql",
        "to_json",
        "to_hdf",
        "to_parquet",
        "to_clipboard",
        "to_feather",
        # more pandas writers that accept a file path/buffer, and file-reading helpers (pd is in the
        # namespace, so pd.read_pickle(...) would execute pickle code and pd.read_csv(path) would read
        # any file) -- see also the "read_" prefix rule in validate_code().
        "to_markdown",
        "to_html",
        "to_latex",
        "to_xml",
        "to_stata",
        "to_orc",
        "to_gbq",
        "to_string",
        "to_xarray",
        "io",
        "read",
        "load",
        "loads",
    }
)

def _noop_print(*_args, **_kwargs) -> None:
    """Allow LLM snippets that call print(result) without crashing execution."""


_SAFE_BUILTINS = {
    "len": len,
    "round": round,
    "abs": abs,
    "min": min,
    "max": max,
    "sum": sum,
    "sorted": sorted,
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "isinstance": isinstance,
    "print": _noop_print,
}


def _is_const_str_spec(node) -> bool:
    """True when node is a constant string, or a list/tuple/set/dict built only of them."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_const_str_spec(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(k is not None and _is_const_str_spec(k) for k in node.keys) and all(
            _is_const_str_spec(v) for v in node.values
        )
    return False


def validate_code(code: str) -> tuple[bool, str]:
    """AST-allowlist check. Returns (ok, reason)."""
    try:
        tree = ast.parse(code or "", mode="exec")
    except SyntaxError as ex:
        return False, f"syntax error: {ex}"

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            return False, f"disallowed syntax: {type(node).__name__}"
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            return False, f"disallowed name: {node.id}"
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                return False, f"disallowed attribute: {node.attr}"
            if node.attr in _DISALLOWED_METHODS or node.attr.startswith("read_"):
                return False, f"disallowed method: {node.attr}"
        # .agg()/.aggregate() may only name aggregations as constant strings —
        # callables can't sneak in (Lambda/FunctionDef aren't allowlisted anyway).
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("agg", "aggregate")
        ):
            specs = list(node.args) + [kw.value for kw in node.keywords]
            if not specs or not all(_is_const_str_spec(s) for s in specs):
                return False, "agg accepts only constant string aggregation names"
    return True, ""


def run_sandboxed(code: str, namespace: dict, *, timeout: float = 5.0) -> tuple[object, str]:
    """Execute validated code in a restricted namespace with a hard timeout.

    `code` must assign its answer to a variable named `result`. Returns
    (result, error) — error is empty on success, result is None on failure.
    """
    ok, reason = validate_code(code)
    if not ok:
        return None, f"rejected: {reason}"

    restricted_globals: dict = {"__builtins__": _SAFE_BUILTINS}
    restricted_globals.update(namespace)

    def _run():
        exec(compile(code, "<spreadsheet_query>", "exec"), restricted_globals)
        return restricted_globals.get("result")

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run)
        try:
            return future.result(timeout=timeout), ""
        except concurrent.futures.TimeoutError:
            return None, "execution timed out"
        except Exception as ex:  # noqa: BLE001 - surfacing to the LLM for a repair attempt
            return None, f"{type(ex).__name__}: {ex}"
