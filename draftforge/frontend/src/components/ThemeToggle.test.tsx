import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import ThemeToggle from "./ThemeToggle";

describe("ThemeToggle", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("dark");
  });

  it("toggles the `dark` class on <html> and persists the choice", () => {
    render(<ThemeToggle />);
    const button = screen.getByRole("button", { name: /toggle color theme/i });

    // starting state depends on system preference (mocked as light by jsdom default)
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    fireEvent.click(button);
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(localStorage.getItem("draftforge-theme")).toBe("dark");

    fireEvent.click(button);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(localStorage.getItem("draftforge-theme")).toBe("light");
  });
});
