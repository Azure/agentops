"""Canonical AgentOps UI design tokens and shared shell primitives.

This module is the single source of truth for the visual language shared by
the AgentOps **Cockpit** and the hosted **Observe** dashboard. It exists so
that both surfaces render as one product: the same background, surfaces,
borders, typography, accent, and status colors.

Why a separate module
----------------------
Historically the Cockpit (:mod:`agentops.agent.cockpit`) carried its design
tokens inline in a large CSS f-string, and Observe
(:mod:`agentops.agent.observe.ui`) defined its *own*, unrelated ``--observe-*``
custom properties plus a bare ``@media (prefers-color-scheme: dark)`` block.
That meant Observe followed the operating-system color scheme independently
and could render a white page while the Cockpit was dark -- the two drifted
apart. The token values below are copied verbatim out of Cockpit's dashboard
stylesheet so the two surfaces are guaranteed to match.

Theme model
-----------
Themes are **explicit**, never inferred from ``prefers-color-scheme``. The
default theme is dark (matching the Cockpit shell, which is dark-only). A light
theme is a complete, accessible equivalent applied via an explicit
``[data-theme="light"]`` attribute on the document root -- there is no bare
``prefers-color-scheme`` override that could diverge from the Cockpit.

Purity
------
This module performs no I/O, reads no environment variables, and imports no
Azure SDKs. It only assembles CSS strings from constants.

.. note::
   :mod:`agentops.agent.cockpit` consumes :func:`render_theme_variables` for its
   design tokens, so the Cockpit and Observe token values can no longer drift.
   Cockpit still styles its page with bare element selectors rather than the
   ``aos-*`` shell primitives below; adopting :data:`SHARED_SHELL_CSS` there
   would require reworking the Cockpit markup and is deliberately out of scope.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Font stacks (shared by Cockpit + Observe)
# ---------------------------------------------------------------------------

#: Primary UI font stack. Matches the Cockpit dashboard ``body`` rule.
FONT_STACK: str = (
    '-apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", system-ui, sans-serif'
)

#: Monospace font stack for provenance/identifier chips and code.
MONO_FONT_STACK: str = '"SF Mono", "Cascadia Code", Consolas, monospace'


# ---------------------------------------------------------------------------
# Canonical design tokens
# ---------------------------------------------------------------------------
#
# DARK_TOKENS are copied verbatim from cockpit.py's dashboard ``:root`` block so
# Observe and Cockpit are pixel-for-pixel consistent. LIGHT_TOKENS are a
# deliberate, accessible light equivalent (WCAG AA text contrast on the light
# background) that keeps the same token *names* so every downstream rule works
# unchanged in either theme.

#: Ordered names of the core design tokens both themes define. Used by the
#: theme-parity test to prove Observe emits exactly these tokens.
TOKEN_NAMES: tuple[str, ...] = (
    "--bg",
    "--bg-grad",
    "--card",
    "--card-hi",
    "--border",
    "--border-strong",
    "--text",
    "--text-dim",
    "--text-faint",
    "--ok",
    "--info",
    "--warn",
    "--crit",
    "--muted",
    "--accent",
    "--fg",
)

#: Canonical dark theme (verbatim from cockpit.py). ``--accent``/``--fg`` are
#: convenience aliases used by a handful of Cockpit rules; they are defined
#: explicitly here so every reference resolves in either surface.
DARK_TOKENS: dict[str, str] = {
    "--bg": "#08090b",
    "--bg-grad": (
        "radial-gradient(1200px 600px at 80% -10%, "
        "rgba(56, 189, 248, 0.06), transparent 60%)"
    ),
    "--card": "#161618",
    "--card-hi": "#1c1c1f",
    "--border": "rgba(255, 255, 255, 0.06)",
    "--border-strong": "rgba(255, 255, 255, 0.12)",
    "--text": "#fafafa",
    "--text-dim": "#a1a1aa",
    "--text-faint": "#71717a",
    "--ok": "#4ade80",
    "--info": "#38bdf8",
    "--warn": "#fbbf24",
    "--crit": "#f87171",
    "--muted": "#71717a",
    "--accent": "#38bdf8",
    "--fg": "#fafafa",
}

#: Complete, accessible light equivalent. Same token names as ``DARK_TOKENS``.
LIGHT_TOKENS: dict[str, str] = {
    "--bg": "#ffffff",
    "--bg-grad": (
        "radial-gradient(1200px 600px at 80% -10%, "
        "rgba(9, 105, 218, 0.05), transparent 60%)"
    ),
    "--card": "#f6f8fa",
    "--card-hi": "#ffffff",
    "--border": "rgba(15, 23, 42, 0.10)",
    "--border-strong": "rgba(15, 23, 42, 0.20)",
    "--text": "#0d1117",
    "--text-dim": "#57606a",
    "--text-faint": "#6e7781",
    "--ok": "#1a7f37",
    "--info": "#0969da",
    "--warn": "#9a6700",
    "--crit": "#cf222e",
    "--muted": "#6e7781",
    "--accent": "#0969da",
    "--fg": "#0d1117",
}


def _emit_block(selector: str, tokens: dict[str, str], *, color_scheme: str) -> str:
    lines = [f"  color-scheme: {color_scheme};"]
    lines += [f"  {name}: {value};" for name, value in tokens.items()]
    body = "\n".join(lines)
    return f"{selector} {{\n{body}\n}}"


def render_theme_variables(*, default_theme: str = "dark") -> str:
    """Return the explicit light/dark theme CSS custom-property blocks.

    The default theme (dark unless overridden) is emitted on ``:root`` so a
    document with no ``data-theme`` attribute still matches the Cockpit shell.
    The opposite theme is emitted under an explicit ``[data-theme="..."]``
    selector. There is intentionally **no** ``@media (prefers-color-scheme)``
    block -- theme selection is explicit so Observe cannot diverge from the
    Cockpit's deliberate dark presentation.
    """
    if default_theme not in ("dark", "light"):
        raise ValueError("default_theme must be 'dark' or 'light'")

    if default_theme == "dark":
        base_tokens, base_scheme = DARK_TOKENS, "dark"
        alt_tokens, alt_scheme, alt_name = LIGHT_TOKENS, "light", "light"
    else:
        base_tokens, base_scheme = LIGHT_TOKENS, "light"
        alt_tokens, alt_scheme, alt_name = DARK_TOKENS, "dark", "dark"

    root_block = _emit_block(":root", base_tokens, color_scheme=base_scheme)
    alt_selector = f':root[data-theme="{alt_name}"], [data-theme="{alt_name}"]'
    alt_block = _emit_block(alt_selector, alt_tokens, color_scheme=alt_scheme)
    return f"{root_block}\n\n{alt_block}"


# ---------------------------------------------------------------------------
# Shared shell primitives
# ---------------------------------------------------------------------------
#
# These class-based primitives (``aos-*``) express the shared page shell:
# app frame, header/brand, section titles, card, grid, buttons, links, and the
# theme toggle. They are consumed by Observe today and are intended to be
# consumed by Cockpit in the planned follow-up. Every rule references the
# canonical tokens above, so a single theme switch restyles the whole shell.

SHARED_SHELL_CSS: str = """
* { box-sizing: border-box; }

.aos-app {
  max-width: 1400px;
  margin: 0 auto;
  padding: 28px 32px 48px;
  background: var(--bg) var(--bg-grad) no-repeat;
  color: var(--text);
  font-family: __AOS_FONT__;
  -webkit-font-smoothing: antialiased;
}

.aos-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  flex-wrap: wrap;
  margin-bottom: 24px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--border);
}
.aos-brand { display: flex; align-items: center; gap: 14px; }
.aos-brand h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--text);
}
.aos-subtitle {
  color: var(--text-dim);
  font-size: 12px;
  font-weight: 500;
  font-family: __AOS_MONO__;
  margin-top: 2px;
}
.aos-header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.aos-section-title {
  margin: 30px 0 12px;
  font-size: 11px;
  font-weight: 700;
  color: var(--text-faint);
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.aos-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 16px 18px;
  color: var(--text);
  transition: border-color 0.15s ease, transform 0.15s ease;
}
.aos-card:hover { border-color: var(--border-strong); }

.aos-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 14px;
}

.aos-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 13px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--card);
  color: var(--text);
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}
.aos-btn:hover {
  border-color: var(--border-strong);
  background: var(--card-hi);
}
.aos-btn:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.aos-btn-primary {
  background: color-mix(in srgb, var(--accent) 16%, transparent);
  border-color: color-mix(in srgb, var(--accent) 42%, transparent);
  color: var(--accent);
}
.aos-btn-primary:hover {
  background: color-mix(in srgb, var(--accent) 26%, transparent);
  border-color: color-mix(in srgb, var(--accent) 60%, transparent);
}

.aos-link {
  color: var(--info);
  text-decoration: none;
  font-weight: 600;
  font-size: 13px;
}
.aos-link:hover { text-decoration: underline; }

.aos-theme-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.aos-theme-toggle .aos-theme-icon { font-size: 14px; line-height: 1; }
""".replace(
    "__AOS_FONT__", FONT_STACK
).replace(
    "__AOS_MONO__", MONO_FONT_STACK
)


__all__ = [
    "FONT_STACK",
    "MONO_FONT_STACK",
    "TOKEN_NAMES",
    "DARK_TOKENS",
    "LIGHT_TOKENS",
    "render_theme_variables",
    "SHARED_SHELL_CSS",
]
