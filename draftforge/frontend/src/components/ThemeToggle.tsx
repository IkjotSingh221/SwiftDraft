import { useEffect, useState } from "react";
import { applyTheme, getInitialTheme, type Theme } from "../theme";

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => getInitialTheme());

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  return (
    <button
      type="button"
      onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
      aria-label="Toggle color theme"
      className="rounded border border-border-subtle px-3 py-1.5 text-sm text-text-secondary hover:text-text-primary hover:border-text-secondary transition-colors"
    >
      {theme === "dark" ? "Dark" : "Light"}
    </button>
  );
}
