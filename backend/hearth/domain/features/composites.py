"""Composite features as DATA, not code — the cross-home portability core.

A composite is a boolean AST over already-computed feature columns, evaluated
generically. Composites are proposed by heuristics or the LLM advisor and
stored in settings ("composites"); NO entity names or home-specific logic
ever appears in Python.

AST grammar (same one the labeling rule engine uses):
  {"all": [<node>...]}            logical AND
  {"any": [<node>...]}            logical OR
  {"not": <node>}                 negation
  {"feat": str, "op": ">"|<"|">="|"<="|"=="|"!=", "value": number}

Composite definition: {"name": "lights_off_in_bed", "ast": <node>}
Result column = float 0/1.
"""
from __future__ import annotations

import pandas as pd

_OPS = {
    ">": lambda s, v: s > v, "<": lambda s, v: s < v,
    ">=": lambda s, v: s >= v, "<=": lambda s, v: s <= v,
    "==": lambda s, v: s == v, "!=": lambda s, v: s != v,
}


def evaluate_ast(node: dict, df: pd.DataFrame) -> pd.Series:
    """AST -> boolean Series aligned to df.index. Unknown feature -> all-False
    (a proposed composite referencing a disabled binding degrades gracefully)."""
    if "all" in node:
        out = pd.Series(True, index=df.index)
        for child in node["all"]:
            out &= evaluate_ast(child, df)
        return out
    if "any" in node:
        out = pd.Series(False, index=df.index)
        for child in node["any"]:
            out |= evaluate_ast(child, df)
        return out
    if "not" in node:
        return ~evaluate_ast(node["not"], df)
    feat, op, value = node["feat"], node["op"], node["value"]
    if feat not in df.columns:
        return pd.Series(False, index=df.index)
    return _OPS[op](df[feat].fillna(0), value)


def apply_composites(df: pd.DataFrame, composites: list[dict]) -> pd.DataFrame:
    for comp in composites:
        try:
            df[comp["name"]] = evaluate_ast(comp["ast"], df).astype(float)
        except Exception:
            df[comp["name"]] = 0.0
    return df
