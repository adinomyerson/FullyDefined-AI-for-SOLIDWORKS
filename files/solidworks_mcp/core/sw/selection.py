"""Selection: the calls that select edges and count the selection.
The tools do the inputs, messages and measuring."""

from ..com import null_object


def sw_select_edge(edge) -> bool:
    """Add one edge to the selection."""
    return bool(edge.Select4(True, null_object()))


def sw_selected_count(doc) -> int:
    return int(doc.SelectionManager.GetSelectedObjectCount2(-1))
