import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RoleModelsResponse } from "../api/client";
import Settings from "./Settings";

const mockResponse: RoleModelsResponse = {
  models: [
    {
      name: "claude-sonnet-4-6",
      provider: "anthropic",
      model: "claude-sonnet-4-6",
      local: false,
      cost_per_mtok_in: 3,
      cost_per_mtok_out: 15,
      note: null,
      api_key_present: true,
    },
    {
      name: "ollama-llama3.1-8b",
      provider: "ollama",
      model: "llama3.1:8b",
      local: true,
      cost_per_mtok_in: 0,
      cost_per_mtok_out: 0,
      note: "Fits in 8GB VRAM.",
      api_key_present: true,
    },
  ],
  role_models: {
    planner: "claude-sonnet-4-6",
    drafter: "ollama-llama3.1-8b",
    critic: "ollama-llama3.1-8b",
    citation_verifier: "claude-sonnet-4-6",
    continuity_editor: "ollama-llama3.1-8b",
  },
  ollama_reachable: true,
};

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>
  );
}

describe("Settings page", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => mockResponse,
      })
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders a model picker per agent role, populated from the registry", async () => {
    renderWithClient(<Settings />);

    await waitFor(() => expect(screen.getByLabelText("Planner")).toBeInTheDocument());

    expect(screen.getByLabelText("Planner")).toHaveValue("claude-sonnet-4-6");
    expect(screen.getByLabelText("Drafter")).toHaveValue("ollama-llama3.1-8b");
    expect(screen.getByLabelText("Critic")).toBeInTheDocument();
    expect(screen.getByLabelText("Citation Verifier")).toBeInTheDocument();
    expect(screen.getByLabelText("Continuity Editor")).toBeInTheDocument();

    // available models table lists both registry entries
    expect(screen.getAllByText("claude-sonnet-4-6").length).toBeGreaterThan(0);
    expect(screen.getAllByText("ollama-llama3.1-8b").length).toBeGreaterThan(0);
  });
});
