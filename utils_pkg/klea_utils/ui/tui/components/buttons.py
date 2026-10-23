"""Reusable TUI component factories for the klea TUI."""

from __future__ import annotations

from textual.widgets import Button


def action_button(
    label: str,
    *,
    variant: str = "default",
    id: str | None = None,
    compact: bool = False,
) -> Button:
    """Create a flat action button with a consistent look.

    Args:
        label: Text displayed on the button.
        variant: Textual button variant (e.g. ``"default"``, ``"primary"``).
        id: Optional widget id.
        compact: When ``True`` the button collapses to its minimum width.
    """
    return Button(
        label,
        variant=variant,
        id=id,
        flat=True,
        min_width=0 if compact else 16,
    )
