"use strict";

// Theme (light/dark/system). The page sets data-theme; the element's own
// theme is "auto", so it follows the attribute without being told twice.
const THEME_KEY = "ena-browser-ui.theme";
const prefersLight = window.matchMedia("(prefers-color-scheme: light)");

function applyTheme(theme) {
  const effective = theme === "system" ? (prefersLight.matches ? "light" : "dark") : theme;
  document.documentElement.setAttribute("data-theme", effective);
}

(function initTheme() {
  const saved = localStorage.getItem(THEME_KEY) || "system";
  const select = document.getElementById("themeSelect");
  select.value = saved;
  applyTheme(saved);
  select.addEventListener("change", () => {
    localStorage.setItem(THEME_KEY, select.value);
    applyTheme(select.value);
  });
  prefersLight.addEventListener("change", () => {
    if ((localStorage.getItem(THEME_KEY) || "system") === "system") applyTheme("system");
  });
})();
