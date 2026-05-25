/**
 * Unit tests for the isToolError runtime type guard exported from src/index.ts.
 *
 * Covers:
 *   - true cases  : {ok: false}, {ok: false, error: "..."}
 *   - false cases : {ok: true}, {}, null, undefined, 0, [], "string"
 */

import { describe, expect, it } from "vitest";
import { isToolError } from "../src/tool_error.js";

describe("isToolError", () => {
  it("returns true for {ok: false}", () => {
    expect(isToolError({ ok: false })).toBe(true);
  });

  it("returns true for {ok: false, error: 'msg'}", () => {
    expect(isToolError({ ok: false, error: "something went wrong" })).toBe(
      true,
    );
  });

  it("returns true for {ok: false, error: {code: 'X'}}", () => {
    expect(isToolError({ ok: false, error: { code: "INDEX_NOT_FOUND" } })).toBe(
      true,
    );
  });

  it("returns false for {ok: true}", () => {
    expect(isToolError({ ok: true })).toBe(false);
  });

  it("returns false for {} (missing ok)", () => {
    expect(isToolError({})).toBe(false);
  });

  it("returns false for null", () => {
    expect(isToolError(null)).toBe(false);
  });

  it("returns false for undefined", () => {
    expect(isToolError(undefined)).toBe(false);
  });

  it("returns false for a number", () => {
    expect(isToolError(0)).toBe(false);
    expect(isToolError(42)).toBe(false);
  });

  it("returns false for an array", () => {
    expect(isToolError([])).toBe(false);
    expect(isToolError([{ ok: false }])).toBe(false);
  });

  it("returns false for a string", () => {
    expect(isToolError("ok: false")).toBe(false);
  });

  it("returns false for {ok: null}", () => {
    expect(isToolError({ ok: null })).toBe(false);
  });

  it("returns false for {ok: 0}", () => {
    // Numeric 0 is falsy but not strictly false
    expect(isToolError({ ok: 0 })).toBe(false);
  });
});
