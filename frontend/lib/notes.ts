import { megabytes } from "./format";

// The API's limits for notes (backend notes.py and store.py).
export const MAX_BYTES = 20 * 1024 * 1024;
export const MAX_FILES = 5;
const EXTENSIONS = [".pdf", ".txt", ".md"];

function extension(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot > 0 ? filename.slice(dot).toLowerCase() : "";
}

/** The file's kind as its badge shows it ("PDF"). */
export function kindLabel(filename: string): string {
  return extension(filename).slice(1, 5).toUpperCase() || "FILE";
}

/**
 * Why the API would refuse this file by its name or size, in the API's own words, or null.
 * Checked before uploading: an early refusal can arrive as a reset connection mid-upload.
 */
export function uploadRefusal(file: { name: string; size: number }): string | null {
  if (!EXTENSIONS.includes(extension(file.name))) return "Ebb reads PDF, TXT or MD files.";
  if (file.size > MAX_BYTES) {
    return (
      `${file.name} is ${megabytes(file.size)} — over the 20 MB limit. ` +
      "Split it into chapters, or upload just the pages you're studying."
    );
  }
  return null;
}
