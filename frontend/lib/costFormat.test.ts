import { describe, expect, it } from "vitest";
import { duration, keyedModelName, modelName, money, percent, tokens } from "./costFormat";

describe("costFormat", () => {
  it("money keeps tiny amounts non-zero and says when the price is unknown", () => {
    expect(money("0.00042")).toBe("$0.00042");
    expect(money("0.0003")).toBe("$0.0003");
    expect(money("0.42")).toBe("$0.42");
    expect(money("0.0123")).toBe("$0.012");
    expect(money("12.5")).toBe("$12.50");
    expect(money("0")).toBe("$0.00");
    expect(money(null)).toBe("price unknown");
  });

  it("tokens use thousands separators and say when not reported", () => {
    expect(tokens(1840000)).toBe("1,840,000");
    expect(tokens(0)).toBe("0");
    expect(tokens(null)).toBe("not reported");
  });

  it("duration is ms under a second, else seconds with one decimal", () => {
    expect(duration(840)).toBe("840 ms");
    expect(duration(999)).toBe("999 ms");
    expect(duration(1000)).toBe("1.0 s");
    expect(duration(2900)).toBe("2.9 s");
    expect(duration(null)).toBe("—");
  });

  it("percent is one decimal, a dash when unknown", () => {
    expect(percent(0.083)).toBe("8.3%");
    expect(percent(0)).toBe("0.0%");
    expect(percent(null)).toBe("—");
  });

  it("modelName writes the provider, then the model without its vendor prefix", () => {
    expect(modelName("groq", "openai/gpt-oss-120b")).toBe("Groq, gpt-oss-120b");
    expect(modelName("openrouter", "some-model")).toBe("OpenRouter, some-model");
    expect(modelName("acme", "m1")).toBe("acme, m1");
    expect(keyedModelName("groq/openai/gpt-oss-120b")).toBe("Groq, gpt-oss-120b");
  });
});
