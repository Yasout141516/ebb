import { describe, expect, it } from "vitest";
import { megabytes } from "./format";
import { MAX_BYTES, kindLabel, uploadRefusal } from "./notes";

describe("notes checks", () => {
  it("megabytes has one decimal", () => {
    expect(megabytes(27_682_406)).toBe("26.4 MB");
    expect(megabytes(0)).toBe("0.0 MB");
  });

  it("accepts PDF, TXT and MD in any case", () => {
    for (const name of ["a.pdf", "b.TXT", "c.Md"]) {
      expect(uploadRefusal({ name, size: 10 })).toBeNull();
    }
  });

  it("refuses other types with the API's copy", () => {
    expect(uploadRefusal({ name: "slides.pptx", size: 10 })).toBe("Ebb reads PDF, TXT or MD files.");
    expect(uploadRefusal({ name: ".pdf", size: 10 })).toBe("Ebb reads PDF, TXT or MD files.");
  });

  it("refuses a file over 20 MB with the API's copy", () => {
    expect(uploadRefusal({ name: "big.pdf", size: MAX_BYTES })).toBeNull();
    expect(uploadRefusal({ name: "Textbook.pdf", size: 27_682_406 })).toBe(
      "Textbook.pdf is 26.4 MB — over the 20 MB limit. " +
        "Split it into chapters, or upload just the pages you're studying.",
    );
  });

  it("kindLabel", () => {
    expect(kindLabel("notes.pdf")).toBe("PDF");
    expect(kindLabel("readme.md")).toBe("MD");
  });
});
