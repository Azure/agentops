"""Canonical design tokens and theme controls for the local AgentOps Cockpit.

Theme model
-----------
Themes are **explicit**, never inferred from ``prefers-color-scheme``. The
default theme is dark. A light
theme is a complete, accessible equivalent applied via an explicit
``[data-theme="light"]`` attribute on the document root -- there is no bare
``prefers-color-scheme`` override that could make product surfaces diverge.

The toggle stores only the non-sensitive ``theme=light|dark`` preference in the
page URL, without cookies or browser storage.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Canonical design tokens
# ---------------------------------------------------------------------------
#
# DARK_TOKENS are copied from cockpit.py's dashboard ``:root`` block.
# LIGHT_TOKENS provide an accessible light equivalent with the same token names.

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
    block -- theme selection is explicit and predictable.
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


def render_theme_toggle(
    *, control_id: str = "aos-theme-toggle", extra_class: str = ""
) -> str:
    """Return the shared accessible light/dark theme control."""
    classes = "aos-btn aos-theme-toggle"
    if extra_class:
        classes += f" {extra_class}"
    return (
        f'<button id="{control_id}" class="{classes}" data-aos-theme-toggle '
        'type="button" aria-label="Switch to light theme" '
        'aria-pressed="false">'
        '<span class="aos-theme-icon" aria-hidden="true">&#9790;</span>'
        '<span class="aos-theme-label">Dark</span>'
        "</button>"
    )


THEME_TOGGLE_SCRIPT: str = r"""
function setupAgentOpsThemeToggle() {
  var toggle = document.querySelector("[data-aos-theme-toggle]");
  if (!toggle) { return; }
  var root = document.documentElement;
  var params = new URLSearchParams(window.location.search);
  var requested = params.get("theme");
  var initialTheme = requested === "light" || requested === "dark" ? requested : "dark";

  function applyTheme(theme, updateUrl) {
    var isLight = theme === "light";
    root.setAttribute("data-theme", theme);
    toggle.setAttribute("aria-pressed", isLight ? "true" : "false");
    toggle.setAttribute("aria-label", isLight ? "Switch to dark theme" : "Switch to light theme");
    var icon = toggle.querySelector(".aos-theme-icon");
    var label = toggle.querySelector(".aos-theme-label");
    if (icon) { icon.textContent = isLight ? "\u2600" : "\u263e"; }
    if (label) { label.textContent = isLight ? "Light" : "Dark"; }
    if (updateUrl) {
      var next = new URLSearchParams(window.location.search);
      next.set("theme", theme);
      history.replaceState(null, "", window.location.pathname + "?" + next.toString() + window.location.hash);
    }
  }

  applyTheme(initialTheme, requested !== initialTheme);
  toggle.addEventListener("click", function () {
    applyTheme(root.getAttribute("data-theme") === "light" ? "dark" : "light", true);
  });
  window.addEventListener("popstate", function () {
    var restored = new URLSearchParams(window.location.search).get("theme");
    applyTheme(restored === "light" || restored === "dark" ? restored : "dark", false);
  });
}
""".strip()


__all__ = [
    "DARK_TOKENS",
    "LIGHT_TOKENS",
    "render_theme_variables",
    "render_theme_toggle",
    "THEME_TOGGLE_SCRIPT",
]
