import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getJson } from "@/lib/api";
import HealthGate from "./HealthGate";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getJson: vi.fn(),
}));

beforeEach(() => vi.clearAllMocks());

const page = () => (
  <HealthGate>
    <p>the page</p>
  </HealthGate>
);

describe("HealthGate", () => {
  it("shows the page when everything is ok", async () => {
    vi.mocked(getJson).mockResolvedValue({ database: "ok", tutor: "ok", timezone: "ok", costs: false });
    render(page());
    expect(await screen.findByText("the page")).toBeInTheDocument();
    expect(getJson).toHaveBeenCalledWith("/health", expect.anything());
  });

  it("shows the tutor's message in place of the page", async () => {
    vi.mocked(getJson).mockResolvedValue({ database: "ok", tutor: "Set GROQ_API_KEY in .env.", timezone: "ok", costs: false });
    render(page());
    expect(await screen.findByText("Set GROQ_API_KEY in .env.")).toBeInTheDocument();
    expect(screen.queryByText("the page")).not.toBeInTheDocument();
  });

  it("shows the database's message", async () => {
    const down = "Can't reach the database. Is it running? Try: docker compose up -d";
    vi.mocked(getJson).mockResolvedValue({ database: down, tutor: "ok", timezone: "ok", costs: false });
    render(page());
    expect(await screen.findByText(down)).toBeInTheDocument();
  });

  it("names the tutor's problem first when both are down", async () => {
    vi.mocked(getJson).mockResolvedValue({ database: "Database down.", tutor: "Tutor not set up.", timezone: "ok", costs: false });
    render(page());
    expect(await screen.findByText("Tutor not set up.")).toBeInTheDocument();
  });

  it("shows a time zone setting it couldn't use", async () => {
    const zone = "EBB_TIMEZONE 'Mars/Olympus' is not a known time zone";
    vi.mocked(getJson).mockResolvedValue({ database: "ok", tutor: "ok", timezone: zone });
    render(page());
    expect(await screen.findByText(zone)).toBeInTheDocument();
    expect(screen.queryByText("the page")).not.toBeInTheDocument();
  });

  it("offers Retry when the API can't be reached", async () => {
    vi.mocked(getJson).mockRejectedValueOnce(new TypeError("Failed to fetch"));
    vi.mocked(getJson).mockResolvedValueOnce({ database: "ok", tutor: "ok", timezone: "ok", costs: false });
    render(page());
    expect(await screen.findByText("Can't reach the API.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("the page")).toBeInTheDocument();
  });
});
