"""Cross-reference engine — build tag/symbol usage index from L5X projects."""

from __future__ import annotations

import re
from typing import Any

from studio5000_mcp_server.l5x_parser import L5XProject

_xref_cache: dict[int, dict[str, list[dict[str, Any]]]] = {}


def build_xref(project: L5XProject) -> dict[str, list[dict[str, Any]]]:
    """Build a symbol→usage index by scanning all NeutralText and ST code.

    Returns a dict mapping symbol names to lists of references:
        {"Motor_1": [{"program": "MainProgram", "routine": "MainRoutine", "rung": 2, "context": "..."}]}
    """
    key = id(project)
    if key in _xref_cache:
        return _xref_cache[key]

    index: dict[str, list[dict[str, Any]]] = {}
    aoi_names = frozenset(
        aoi.get("Name", "")
        for aoi in project.controller.findall("AddOnInstructionDefinitions/AddOnInstructionDefinition")
    )

    # Scan program routines
    for prog in project.controller.findall("Programs/Program"):
        prog_name = prog.get("Name", "")
        for routine in prog.findall("Routines/Routine"):
            rname = routine.get("Name", "")
            rtype = routine.get("Type", "")
            if rtype == "RLL":
                for rung in routine.findall("RLLContent/Rung"):
                    text_el = rung.find("Text")
                    text = text_el.text.strip() if text_el is not None and text_el.text else ""
                    if not text:
                        continue
                    rung_num = int(rung.get("Number", "0"))
                    for sym in _extract_symbols(text, aoi_names):
                        index.setdefault(sym, []).append({
                            "program": prog_name, "routine": rname,
                            "rung": rung_num, "context": text,
                        })
            elif rtype == "ST":
                lines = []
                for line_el in routine.findall("STContent/Line"):
                    lines.append((int(line_el.get("Number", "0")), line_el.text or ""))
                for line_num, line_text in lines:
                    if not line_text.strip() or line_text.strip().startswith("//"):
                        continue
                    for sym in _extract_symbols_st(line_text):
                        index.setdefault(sym, []).append({
                            "program": prog_name, "routine": rname,
                            "line": line_num, "context": line_text.strip(),
                        })

    # Scan AOI routines
    for aoi in project.controller.findall("AddOnInstructionDefinitions/AddOnInstructionDefinition"):
        aoi_name = aoi.get("Name", "")
        for routine in aoi.findall("Routines/Routine"):
            rname = routine.get("Name", "")
            rtype = routine.get("Type", "")
            if rtype == "RLL":
                for rung in routine.findall("RLLContent/Rung"):
                    text_el = rung.find("Text")
                    text = text_el.text.strip() if text_el is not None and text_el.text else ""
                    if not text:
                        continue
                    rung_num = int(rung.get("Number", "0"))
                    for sym in _extract_symbols(text, aoi_names):
                        index.setdefault(sym, []).append({
                            "aoi": aoi_name, "routine": rname,
                            "rung": rung_num, "context": text,
                        })

    _xref_cache[key] = index
    return index


def search_xref(project: L5XProject, pattern: str) -> list[dict[str, Any]]:
    """Search the cross-reference index for symbols matching a pattern (regex)."""
    index = build_xref(project)
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error:
        # Fall back to literal match
        regex = re.compile(re.escape(pattern), re.IGNORECASE)

    results: list[dict[str, Any]] = []
    for sym, refs in index.items():
        if regex.search(sym):
            for ref in refs:
                results.append({"symbol": sym, **ref})
    return results


# NeutralText operand extraction.
# A tag operand is a name, optionally qualified by module path segments
# (Local:1:I), followed by any number of member (.Member, .0 for bits) and
# array index ([3], [Idx], [1,2]) parts, e.g. Local:1:I.Data.0 or Recipe[Step].Speed.
_NT_OPERAND_RE = re.compile(
    r'[A-Za-z_][A-Za-z0-9_]*'      # tag name
    r'(?::[A-Za-z0-9_]+)*'         # module path segments
    r'(?:\.[A-Za-z0-9_]+|\[[^\[\]]*\])*'  # members, bits and array indexes
)
_NT_BASE_RE = re.compile(r'[^.\[]+')


def _extract_symbols(neutral_text: str, aoi_names: frozenset[str] = frozenset()) -> set[str]:
    """Extract tag/symbol references from NeutralText.

    Any name directly followed by "(" is an instruction call (XIC, ONS, CPT, JMP,
    an AOI, ...). Built-in instructions are skipped; calls to AOIs named in
    ``aoi_names`` are indexed so AOI usages can be searched.
    """
    symbols: set[str] = set()
    for match in _NT_OPERAND_RE.finditer(neutral_text):
        token = match.group()
        rest = neutral_text[match.end():].lstrip()
        if rest.startswith("("):
            if token in aoi_names:
                symbols.add(token)
            continue
        symbols.add(token)
        # Also index the base tag of a member, bit, or array reference
        base = _NT_BASE_RE.match(token).group()
        if base != token:
            symbols.add(base)
        # Index tags used as indirect array indexes, e.g. Idx in Recipe[Idx]
        for index_expr in re.findall(r'\[([^\[\]]*)\]', token):
            symbols |= _extract_symbols(index_expr, aoi_names)
    return symbols


_ST_IDENT_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*')
_ST_KEYWORDS = {"IF", "THEN", "ELSE", "ELSIF", "END_IF", "FOR", "TO", "DO", "END_FOR",
                "WHILE", "END_WHILE", "REPEAT", "UNTIL", "END_REPEAT", "CASE", "OF",
                "END_CASE", "RETURN", "EXIT", "NOT", "AND", "OR", "XOR", "MOD", "TRUE", "FALSE"}


def _extract_symbols_st(line: str) -> set[str]:
    """Extract tag/symbol references from a Structured Text line."""
    symbols: set[str] = set()
    for match in _ST_IDENT_RE.finditer(line):
        token = match.group()
        base = token.split(".")[0]
        if base.upper() in _ST_KEYWORDS:
            continue
        # Skip pure numeric-looking things and assignment operator parts
        symbols.add(token)
        if "." in token:
            symbols.add(base)
    return symbols
