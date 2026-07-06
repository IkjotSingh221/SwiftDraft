import "@testing-library/jest-dom/vitest";

// jsdom does not implement matchMedia; polyfill it so components that read
// system color-scheme preference (see src/theme.ts) can run under Vitest.
if (!window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }) as unknown as MediaQueryList;
}
