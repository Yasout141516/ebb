import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getJson } from "@/lib/api";
import type { CostSummaryPage } from "@/lib/types";
import CostsView from "./CostsView";
import { calls, emptyPage, prices, summaryPage, TENANT } from "./fixtures";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getJson: vi.fn(),
}));

function serve(page: CostSummaryPage = summaryPage) {
  vi.mocked(getJson).mockImplementation(async (path: string) => {
    if (path.startsWith("/costs/summary")) {
      // A narrowed query lists only what it narrowed to, as the API does.
      const endpoint = new URLSearchParams(path.split("?")[1]).get("endpoint");
      return endpoint ? { ...page, by_endpoint: page.by_endpoint.filter((e) => e.key === endpoint) } : page;
    }
    if (path.startsWith("/costs/calls")) return page === emptyPage ? [] : calls;
    if (path.startsWith("/costs/prices")) return prices;
    throw new Error(`unexpected ${path}`);
  });
}

beforeEach(() => vi.clearAllMocks());

describe("CostsView", () => {
  it("renders every section from a fixture", async () => {
    serve();
    render(<CostsView />);
    expect(await screen.findByRole("heading", { name: "Costs and gateway" })).toBeInTheDocument();
    const names = [
      "Filters",
      "Anomalies",
      "Summary",
      "Estimated cost by feature",
      "Tokens per day",
      "Gateway: who handles what",
      "Speed and reliability by model",
      "By endpoint",
      "By tenant",
      "Recent calls",
      "Where the prices come from",
    ];
    await screen.findByRole("region", { name: "Where the prices come from" });
    const regions = names.map((n) => screen.getByRole("region", { name: n }));
    // In the spec's order.
    regions.slice(1).forEach((r, i) => {
      expect(regions[i].compareDocumentPosition(r) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    expect(screen.getByRole("heading", { name: "2 anomalies in this range" })).toBeInTheDocument();
    const banner = within(screen.getByRole("region", { name: "Anomalies" }));
    expect(banner.getByText(/Grade answer at 14:02 used 4.1 times the input tokens/)).toBeInTheDocument();

    const summary = within(screen.getByRole("region", { name: "Summary" }));
    expect(summary.getByText("$0.00042")).toBeInTheDocument();
    expect(summary.getByText("Real spend: $0.00 on free tiers")).toBeInTheDocument();
    expect(summary.getByText("1,310 in · 530 out")).toBeInTheDocument();
    expect(summary.getByText("2 retried · 1 fell back")).toBeInTheDocument();
    expect(summary.getByText("700 ms / 2.9 s")).toBeInTheDocument();
    expect(summary.getByText("3.4 s / 14.8 s")).toBeInTheDocument();
    expect(summary.getByText("8.3%")).toBeInTheDocument();
    expect(summary.getByText("16.7%")).toBeInTheDocument();
    expect(summary.getByText("Daily call limit used")).toBeInTheDocument();
    expect(summary.getByText("84 / 300")).toBeInTheDocument();
    expect(summary.getByText("Resets at midnight, your time zone")).toBeInTheDocument();

    const feature = within(screen.getByRole("region", { name: "Estimated cost by feature" }));
    expect(feature.getByText("Lessons and re-teaching")).toBeInTheDocument();
    expect(feature.getByText("$0.0003")).toBeInTheDocument();

    expect(
      screen.getByRole("img", { name: /Tokens per day: .*28 Oct 600 \(flagged as an anomaly\)/ }),
    ).toBeInTheDocument();

    const gateway = within(screen.getByRole("region", { name: "Gateway: who handles what" }));
    expect(gateway.getByRole("columnheader", { name: "Backup" })).toBeInTheDocument();
    expect(gateway.queryByRole("columnheader", { name: /switches when/i })).not.toBeInTheDocument();
    expect(gateway.getAllByText("Groq, gpt-oss-120b")).toHaveLength(2);
    expect(gateway.getByText("11.2 s")).toBeInTheDocument();
    expect(gateway.getByText("10.0% failed")).toBeInTheDocument();

    const priced = within(screen.getByRole("region", { name: "Where the prices come from" }));
    expect(await priced.findByText("https://groq.com/pricing")).toBeInTheDocument();
    expect(priced.getByText("2026-10-27")).toBeInTheDocument();
  });

  it('shows "not reported" and "price unknown" for nulls', async () => {
    serve();
    render(<CostsView />);
    const byEndpoint = within(await screen.findByRole("region", { name: "By endpoint" }));
    const grade = within(byEndpoint.getByRole("row", { name: /Grade answer/ }));
    expect(grade.getAllByText("not reported")).toHaveLength(2);
    expect(grade.getAllByText("price unknown")).toHaveLength(2); // est. cost and per call
    const models = within(screen.getByRole("region", { name: "Speed and reliability by model" }));
    expect(within(models.getByRole("row", { name: /Mistral/ })).getAllByText("—").length).toBeGreaterThan(0);
  });

  it("never shows $0.00 when the provider reported no tokens", async () => {
    const page = {
      ...summaryPage,
      summary: {
        ...summaryPage.summary,
        cost_usd: null,
        unreported_calls: 2,
        input_tokens: null,
        output_tokens: null,
        all_free_tier: false,
      },
    };
    serve(page);
    render(<CostsView />);
    const summary = within(await screen.findByRole("region", { name: "Summary" }));
    expect(summary.getByText("price unknown")).toBeInTheDocument();
    expect(summary.getByText("2 calls reported no token usage")).toBeInTheDocument();
    expect(summary.queryByText("$0.00")).not.toBeInTheDocument();
  });

  it("empty range shows the empty copy", async () => {
    serve(emptyPage);
    render(<CostsView />);
    expect(await screen.findByText("No model calls yet in this range.")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Range" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Recent calls" })).not.toBeInTheDocument();
  });

  it("refetches the summary and the calls when a filter changes", async () => {
    serve();
    render(<CostsView />);
    await screen.findByRole("region", { name: "By tenant" });
    expect(getJson).toHaveBeenCalledWith("/costs/summary?range=7d", expect.anything());
    expect(getJson).toHaveBeenCalledWith("/costs/calls?range=7d&limit=50", expect.anything());
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Range" }), "24h");
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Tenant" }), TENANT);
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Endpoint" }), "grade");
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Model" }), "mistral-small");
    const q = `range=24h&tenant=${TENANT}&endpoint=grade&model=mistral-small`;
    expect(getJson).toHaveBeenCalledWith(`/costs/summary?${q}`, expect.anything());
    expect(getJson).toHaveBeenCalledWith(`/costs/calls?${q}&limit=50`, expect.anything());
  });

  it("lists every endpoint of the range while one is chosen, and switching refetches", async () => {
    serve();
    render(<CostsView />);
    const endpoint = await screen.findByRole("combobox", { name: "Endpoint" });
    await userEvent.selectOptions(endpoint, "grade");
    await screen.findByRole("region", { name: "By endpoint" });
    expect(within(endpoint).getByRole("option", { name: "Lesson" })).toBeInTheDocument();
    await userEvent.selectOptions(endpoint, "lesson");
    expect(getJson).toHaveBeenCalledWith("/costs/summary?range=7d&endpoint=lesson", expect.anything());
    // The option lists load once per range: initial summary plus the range-only one.
    expect(vi.mocked(getJson).mock.calls.filter(([p]) => p === "/costs/summary?range=7d")).toHaveLength(2);
  });

  it("shows each side of the tokens when one is not reported, and no total", async () => {
    serve({ ...summaryPage, summary: { ...summaryPage.summary, output_tokens: null } });
    render(<CostsView />);
    const summary = within(await screen.findByRole("region", { name: "Summary" }));
    expect(summary.getByText("1,310 in · output not reported")).toBeInTheDocument();
    expect(summary.getByText("Tokens").nextElementSibling).toHaveTextContent("not reported");
  });

  it("shows the date as well as the time when the range is longer than a day", async () => {
    serve();
    render(<CostsView />);
    const recent = within(await screen.findByRole("region", { name: "Recent calls" }));
    expect(recent.getAllByText(/^\d{1,2} Oct \d\d:\d\d:\d\d$/).length).toBeGreaterThan(0);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Range" }), "24h");
    expect(await recent.findAllByText(/^\d\d:\d\d:\d\d$/)).not.toHaveLength(0);
  });

  it("groups a request's attempts behind an expand button and names anomalies in words", async () => {
    serve();
    render(<CostsView />);
    const recent = within(await screen.findByRole("region", { name: "Recent calls" }));
    expect(recent.queryByText("Failed: timeout")).not.toBeInTheDocument();
    const button = recent.getByRole("button", { name: /2 attempts/ });
    expect(button).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(recent.getByText("Failed: timeout")).toBeInTheDocument();
    await userEvent.click(button);
    expect(recent.queryByText("Failed: timeout")).not.toBeInTheDocument();
    expect(recent.getByText(/Anomaly: Grade answer at 14:02 used 4.1 times/)).toBeInTheDocument();
  });

  it("names both the failure and the anomaly of one attempt", async () => {
    serve();
    vi.mocked(getJson).mockImplementation(async (path: string) => {
      if (path.startsWith("/costs/summary")) return summaryPage;
      if (path.startsWith("/costs/calls")) {
        return [{ ...calls[2], anomaly: "Lesson took 7 times as long as usual" }];
      }
      return prices;
    });
    render(<CostsView />);
    const recent = within(await screen.findByRole("region", { name: "Recent calls" }));
    expect(recent.getByText("Failed: timeout. Anomaly: Lesson took 7 times as long as usual")).toBeInTheDocument();
  });

  it("offers Retry when the numbers can't be loaded", async () => {
    vi.mocked(getJson).mockRejectedValueOnce(new TypeError("Failed to fetch"));
    serve();
    render(<CostsView />);
    expect(await screen.findByText("Couldn't load the costs.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("region", { name: "By tenant" })).toBeInTheDocument();
  });
});
