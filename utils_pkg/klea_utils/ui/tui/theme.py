"""Role-based tint defaults expressed as Textual semantic color tokens.

Using the semantic tokens (``$success``, ``$error``, ``$warning``,
``$panel``, ``$surface``) instead of literal hex values means the TUI
picks up whatever palette the running Textual app defines, including
user-supplied or high-contrast themes.  Each role below is a *meaning*,
not a color, so a theme swap re-colors the whole interface consistently.
"""

from __future__ import annotations

# Semantic mapping notes:
#
#   user         -- the human's own input echo: rendered on $text, the
#                   neutral high-contrast foreground that every Textual
#                   theme guarantees against $background/$surface, so the
#                   echo stays legible on its $primary-tinted bubble and
#                   reads as a passive, already-sent block rather than a
#                   warm notice.
#   path_approval -- the inline prompt asking the human to approve a path:
#                   also $text, so the question stands out from the body
#                   text without reading as an error; its emphasis comes
#                   from its label and interactive chrome, not from a hue.
#   agent        -- agent turn text: rendered on $surface so assistant
#                   prose sits on the raised base layer of the screen.
#   tool_success -- a tool call or check that completed cleanly: $success,
#                   the theme's canonical "good" color.
#   tool_error   -- a tool call that failed or was rejected: $error, the
#                   theme's canonical "bad" color.
#   notice       -- non-fatal warnings and advisory banners: $warning, so
#                   they are visible without competing with real errors.
#   composer     -- the input composer / prompt area: $surface, giving it
#                   the same raised layer as agent output and separating it
#                   from the surrounding chrome.
ROLE_TINTS: dict[str, str] = {
    "user": "$text",
    "path_approval": "$text",
    "agent": "$surface",
    "tool_success": "$success",
    "tool_error": "$error",
    "notice": "$warning",
    "composer": "$surface",
}


def get_role_tints() -> dict[str, str]:
    """Return a copy of the role -> Textual color token mapping.

    A defensive copy is returned so widgets can annotate or filter their
    own view without mutating the process-wide defaults.
    """

    return dict(ROLE_TINTS)
