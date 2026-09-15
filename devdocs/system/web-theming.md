# Web UI theming: tokens, cascade layers, and the override contract

Status: implemented convention.  The token scheme and the
`@layer overrides` contract are in place; extend them rather than adding
ad-hoc colour CSS.

Last updated: 2026-09-15 (token refactor, commits `943033a3`, `b3a1c493`).

## Scope

The NiceGUI web UI shipped from `klea_utils.ui.web.nicegui` and composed by
apps (ADR-0031).  App-specific controls (e.g. the agent's operating-mode and
tool-access selectors in `agent_pkg/.../ui/web/`) use the same hooks.

## Why we do not replace Quasar's CSS

NiceGUI is a thin Python layer over Quasar (a Vue component framework) plus
Tailwind and `nicegui.css`.  Quasar's CSS is the *contract* for its
components (layout, drawers, tabs, dialogs, button internals): replacing it
breaks them.  Customisation therefore goes through, in order of preference:

1. Quasar CSS variables (brand tokens) -- highest leverage.
2. Tailwind utilities and `.classes()` / `.style()`.
3. A curated override sheet in the `overrides` cascade layer (below).

Quasar's SCSS variables (`$primary`) are not exposed because NiceGUI ships
precompiled Quasar, so the CSS-variable route is the practical equivalent.

## The cascade-layer rule (the non-obvious part)

NiceGUI declares a fixed layer order in its `index.html`:

```
@layer theme, base, quasar, nicegui, components, utilities, overrides, quasar_importants;
```

- Quasar's utilities (e.g. `.text-primary`) are `!important` inside the
  **last** layer, `quasar_importants`.
- Per the cascade spec, for `!important` declarations *earlier* layers win,
  and **unlayered** rules are the weakest.
- `ui.add_css()` injects an **unlayered** `<style>`, so a colour override
  like `.icon-btn { color: #424242 !important; }` is silently ignored: the
  widget keeps Quasar's fixed blue/grey.

**Rule:** colour/brand overrides must be wrapped in `@layer overrides { ... }`
so they sit before `quasar_importants`.  For normal (non-`!important`)
declarations the reverse holds - unlayered wins over layers - so
layout/structure/padding overrides work fine as plain `ui.add_css`.

## Token contract

`klea_utils/ui/web/nicegui/components/theme.py` (`_add_css_overrides`) installs
the tokens.  Light values live under `:root`; dark values override the *same*
custom properties under `body.body--dark`, so components reference one
variable and never need paired light/dark rules.

| Token | Light | Dark | Use |
|-------|-------|------|-----|
| `--klea-control` | `#424242` | `#e0e0e0` | icon buttons, inactive segmented option |
| `--klea-muted` | `#757575` | `#cfcfcf` | labels and secondary text |
| `--klea-hint` | `#9e9e9e` | `#bdbdbd` | faintest text (Quasar `text-grey-5`) |
| `--q-primary` | `#1976D2` (Quasar default) | `#64b5f6` | primary brand colour |

Notes:

- Redefining `--q-primary` under `body.body--dark` themes **every**
  primary-tinted widget at once (send button, active tab, Save, links) with no
  per-widget rules.  Quasar defines it on `:root` in the `quasar` layer, and
  we do not call `ui.colors()`, so our override wins.
- Quasar's grey **text** classes use fixed literals
  (`.text-grey-7 { color: #757575 !important; }`, not `var(--q-grey-7)`), so
  they cannot be tokenised via `--q-grey-*`; the ones we use are mapped onto
  the tokens in dark mode instead.
- Dark mode is the `body.body--dark` class (Quasar/NiceGUI).  Tailwind's
  `dark:` variant is configured as
  `&:where(body.body--dark, body.body--dark *)`, so `dark:` classes also work.

## Semantic classes

Prefer a semantic class over inline colours so the token applies in both
themes:

- `.icon-btn` -- neutral icon-only flat buttons (chat kebab, message
  copy/expand, model settings).
- `.choice-row`, `.choice-label`, `.choice-btn`, `.choice-btn--active` -- the
  segmented single-choice control (`choice.py`); the inactive option is a
  Quasar `outline` button whose border follows the text colour
  (`currentColor`).

All of these are styled in `theme.py`; call sites only add the class.

## Adding a themed control (checklist)

1. Use a token (`--klea-control` / `--klea-muted` / `--klea-hint`) or, for
   brand colour, Quasar's `color=` prop (themed by `--q-primary`).
2. Put any colour rule that must beat Quasar in `@layer overrides`;
   layout/structure rules can stay unlayered.
3. Keep the rule in `theme.py` (one override sheet) and key it to a semantic
   class applied via `.classes(...)`.
4. Verify both themes by toggling dark mode.

## Pointers

- Implementation: `theme.py`, `choice.py`, `chat_bubble.py`,
  `status_pane.py`, `input_area.py`, `chat_list.py`.
- Layer order source: NiceGUI `templates/index.html` (the
  `quasar_importants` layer is deliberate).
- ADR-0031 (apps own API contracts and UI composition).
