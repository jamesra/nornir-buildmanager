"""Characterization tests for how reporting renders one value into an HTML table cell."""

from __future__ import annotations

from typing import Any
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

from nornir_buildmanager.operations import reporting
from nornir_buildmanager.operations.reporting import (
    ColumnList,
    HTMLBuilder,
    RowList,
    UnorderedItemList,
)

# Module-level dunder names are not mangled at module scope, but would be inside a
# class body, so look them up by string.
value_to_cell = getattr(reporting, "__ValueToTableCell")
list_to_columns = getattr(reporting, "__ListToTableColumns")
list_to_rows = getattr(reporting, "__ListToTableRows")
list_to_unordered = getattr(reporting, "__ListToUnorderedList")


def render(value: Any, indent: int = 0) -> str:
    """Render ``value`` as one table cell and return the HTML text."""
    return str(value_to_cell(value, indent))


def reference_value_to_cell(value: Any, IndentLevel: int) -> HTMLBuilder:
    """The isinstance ladder as it was before the dispatch table, used as an oracle."""
    HTML = HTMLBuilder(IndentLevel)
    if hasattr(value, 'bgColor'):
        HTML.Add('<td bgcolor="%s" valign="top">\n' % value.bgColor)  # noqa: UP031 - mirrors old code
    else:
        HTML.Add('<td valign="top">')

    if isinstance(value, str):
        HTML.Add(value)
    elif isinstance(value, dict):
        HTML.Indent()
        HTML.Add(reporting.DictToTable(value, HTML.IndentLevel))
        HTML.Dedent()
    elif isinstance(value, UnorderedItemList):
        HTML.Indent()
        HTML.Add(list_to_unordered(value, HTML.IndentLevel))
        HTML.Dedent()
    elif isinstance(value, RowList):
        HTML.Indent()
        HTML.Add(list_to_rows(value, HTML.IndentLevel))
        HTML.Dedent()
    elif isinstance(value, ColumnList):  # noqa: SIM114 - kept as the old ladder had it
        HTML.Indent()
        HTML.Add(list_to_columns(value, HTML.IndentLevel))
        HTML.Dedent()
    elif isinstance(value, list):
        HTML.Indent()
        HTML.Add(list_to_columns(value, HTML.IndentLevel))
        HTML.Dedent()
    else:
        HTML.Add(f"Unknown type passed to __ValueToHTML: {value}")

    HTML.Add("</td>\n")
    return HTML


def render_reference(value: Any, indent: int = 0) -> str:
    """Render with the reference ladder, including every nested cell."""
    with mock.patch.object(reporting, "__ValueToTableCell", reference_value_to_cell):
        return str(reference_value_to_cell(value, indent))


class _Colored:
    """An unknown value type that carries a background colour."""

    def __init__(self, color: str) -> None:
        self.bgColor = color

    def __str__(self) -> str:
        return "colored"


def test_str_is_inlined_after_plain_cell_open() -> None:
    assert render("hi", 2) == '  <td valign="top">  hi  </td>\n'


def test_dict_renders_nested_table_one_level_deeper() -> None:
    assert render({"a": "x"}, 0) == (
        '<td valign="top"> <table>\n  <tr>   <td valign="top">   x   </td>\n  </tr>\n </table>\n</td>\n'
    )


def test_unordered_item_list_renders_ul_not_columns() -> None:
    assert render(UnorderedItemList(["a", 1]), 0) == (
        '<td valign="top"> <ul>\n    <li>a</li>\n    <li>1</li>\n </ul>\n</td>\n'
    )


def test_row_list_renders_one_row_per_entry_not_columns() -> None:
    out = render(RowList(["a", "b"]), 0)
    assert out == (
        '<td valign="top"> <table>\n  <tr>\n   <td valign="top">   a   </td>\n  </tr>\n'
        '  <tr>\n   <td valign="top">   b   </td>\n  </tr>\n </table>\n</td>\n'
    )
    assert out != render(["a", "b"], 0)


def test_column_list_renders_like_plain_list_plus_caption_and_color() -> None:
    columns = ColumnList("a", "b")
    columns.caption = "cap"
    columns.bgColor = "red"
    assert render(columns, 0) == (
        '<td bgcolor="red" valign="top">\n <table>\n  <tr>\n   <td valign="top">   a   </td>\n'
        '   <td valign="top">   b   </td>\n  </tr>\n  cap </table>\n</td>\n'
    )


def test_plain_list_renders_columns() -> None:
    assert render(["a", "b"], 1) == (
        ' <td valign="top">  <table>\n   <tr>\n    <td valign="top">    a    </td>\n'
        '    <td valign="top">    b    </td>\n   </tr>\n  </table>\n </td>\n'
    )


def test_unknown_type_reports_value_and_keeps_bgcolor_prelude() -> None:
    assert render(_Colored("blue"), 0) == (
        '<td bgcolor="blue" valign="top">\nUnknown type passed to __ValueToHTML: colored</td>\n'
    )
    assert render(42, 0) == '<td valign="top">Unknown type passed to __ValueToHTML: 42</td>\n'


_text = st.text(alphabet="abc<>& ", max_size=4)


def _cell_values(children: st.SearchStrategy[Any]) -> st.SearchStrategy[Any]:
    return st.one_of(
        st.dictionaries(_text, children, max_size=3),
        st.lists(_text, max_size=3).map(UnorderedItemList),
        st.lists(children, max_size=3).map(RowList),
        st.lists(children, max_size=3).map(lambda xs: ColumnList(*xs)),
        st.lists(children, max_size=3),
    )


_values = st.recursive(
    st.one_of(_text, st.integers(), st.builds(_Colored, _text)),
    _cell_values,
    max_leaves=12,
)


@settings(max_examples=300, deadline=None)
@given(value=_values, indent=st.integers(min_value=0, max_value=6))
def test_matches_reference_ladder(value: Any, indent: int) -> None:
    assert render(value, indent) == render_reference(value, indent)
