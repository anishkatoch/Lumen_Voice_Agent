import { describe, it, expect } from "vitest";
import { suggestKbInstructions } from "./page";

describe("suggestKbInstructions", () => {
  it("returns empty string with no documents attached", () => {
    expect(suggestKbInstructions([])).toBe("");
  });

  it("names the single attached document (e.g. a resume) by display name", () => {
    const text = suggestKbInstructions([{ display_name: "Anish Resume.pdf", file_name: "resume.pdf" }]);
    expect(text).toContain('"Anish Resume.pdf"');
    expect(text.toLowerCase()).toContain("source of truth");
  });

  it("falls back to file_name when display_name is empty", () => {
    const text = suggestKbInstructions([{ display_name: "", file_name: "resume.pdf" }]);
    expect(text).toContain('"resume.pdf"');
  });

  it("names every document when multiple are attached, since the backend only allows search on documents kb_instructions actually names", () => {
    const text = suggestKbInstructions([
      { display_name: "Resume.pdf", file_name: "resume.pdf" },
      { display_name: "Job Description.pdf", file_name: "jd.pdf" },
    ]);
    expect(text).toContain('"Resume.pdf"');
    expect(text).toContain('"Job Description.pdf"');
  });
});
