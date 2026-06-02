/**
 * Unit tests for emitSearchTelemetry.
 *
 * Asserts:
 *   1. The telemetry directory and JSONL file are created on first call.
 *   2. The written line is valid JSON with the correct shape.
 *   3. A second call appends a new line (JSONL semantics).
 *   4. The function is non-fatal: it swallows errors silently.
 */

import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { emitSearchTelemetry } from "../../src/retrieval/telemetry.js";

function freshStateDir(prefix: string): string {
  return mkdtempSync(join(tmpdir(), `mneme-tel-${prefix}-`));
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

describe("emitSearchTelemetry", () => {
  it("creates the telemetry directory and JSONL file", () => {
    const stateDir = freshStateDir("create");
    const queryHash = "abc123";

    emitSearchTelemetry(stateDir, queryHash, 5, 42);

    const filePath = join(stateDir, "telemetry", `retrieval-${today()}.jsonl`);
    expect(existsSync(filePath)).toBe(true);
  });

  it("writes a line with the correct JSON shape", () => {
    const stateDir = freshStateDir("shape");
    const queryHash = "deadbeef";

    emitSearchTelemetry(stateDir, queryHash, 3, 17);

    const filePath = join(stateDir, "telemetry", `retrieval-${today()}.jsonl`);
    const line = readFileSync(filePath, "utf8").trim();
    const parsed = JSON.parse(line) as Record<string, unknown>;

    expect(parsed.query_hash).toBe(queryHash);
    expect(parsed.hit_count).toBe(3);
    expect(parsed.elapsed_ms).toBe(17);
    expect(typeof parsed.ts).toBe("string");
    // ts must be a valid ISO timestamp
    expect(new Date(parsed.ts as string).getTime()).not.toBeNaN();
  });

  it("appends a second line on a second call (JSONL semantics)", () => {
    const stateDir = freshStateDir("append");

    emitSearchTelemetry(stateDir, "hash1", 1, 10);
    emitSearchTelemetry(stateDir, "hash2", 2, 20);

    const filePath = join(stateDir, "telemetry", `retrieval-${today()}.jsonl`);
    const lines = readFileSync(filePath, "utf8")
      .split("\n")
      .filter((l) => l.trim().length > 0);

    expect(lines.length).toBe(2);

    const first = JSON.parse(lines[0]!) as Record<string, unknown>;
    const second = JSON.parse(lines[1]!) as Record<string, unknown>;
    expect(first.query_hash).toBe("hash1");
    expect(second.query_hash).toBe("hash2");
  });

  it("is non-fatal when stateDir is an unwritable path", () => {
    // Pass an invalid path (\0 in name is illegal on all platforms).
    // The function must not throw.
    expect(() =>
      emitSearchTelemetry("\0invalid\0", "qh", 0, 0),
    ).not.toThrow();
  });

  it("query_hash field matches sha256 of a normalized query externally computed", () => {
    const stateDir = freshStateDir("hash-match");
    // The caller is responsible for computing the hash before calling;
    // we just verify the field is stored verbatim.
    const normalizedQuery = "istanbul kayit";
    const expectedHash = createHash("sha256")
      .update(normalizedQuery)
      .digest("hex");

    emitSearchTelemetry(stateDir, expectedHash, 7, 55);

    const filePath = join(stateDir, "telemetry", `retrieval-${today()}.jsonl`);
    const parsed = JSON.parse(readFileSync(filePath, "utf8").trim()) as Record<
      string,
      unknown
    >;
    expect(parsed.query_hash).toBe(expectedHash);
  });

  // ---------------------------------------------------------------------------
  // Per-backend fields (telemetry-parity spec)
  // ---------------------------------------------------------------------------

  it("emits empty backends/per_backend_hits/per_backend_latency_ms when not provided", () => {
    const stateDir = freshStateDir("defaults");

    emitSearchTelemetry(stateDir, "qh", 0, 0);

    const filePath = join(stateDir, "telemetry", `retrieval-${today()}.jsonl`);
    const parsed = JSON.parse(readFileSync(filePath, "utf8").trim()) as Record<
      string,
      unknown
    >;
    expect(parsed.backends).toEqual([]);
    expect(parsed.per_backend_hits).toEqual({});
    expect(parsed.per_backend_latency_ms).toEqual({});
  });

  it("writes per-backend fields when provided", () => {
    const stateDir = freshStateDir("per-backend");

    emitSearchTelemetry(
      stateDir,
      "testhash",
      5,
      20.8,
      ["fts5", "dense"],
      { fts5: 3, dense: 2 },
      { fts5: 12.5, dense: 8.3 },
    );

    const filePath = join(stateDir, "telemetry", `retrieval-${today()}.jsonl`);
    const parsed = JSON.parse(readFileSync(filePath, "utf8").trim()) as Record<
      string,
      unknown
    >;

    expect(parsed.backends).toEqual(["fts5", "dense"]);
    expect(parsed.per_backend_hits).toEqual({ fts5: 3, dense: 2 });
    expect(parsed.per_backend_latency_ms).toEqual({ fts5: 12.5, dense: 8.3 });
  });

  it("per-backend fields are always present in the JSONL (never missing keys)", () => {
    // The CLI parser must never see missing keys; even the no-arg call must
    // include the keys with empty array/object values.
    const stateDir = freshStateDir("always-present");

    emitSearchTelemetry(stateDir, "qh2", 1, 5);

    const filePath = join(stateDir, "telemetry", `retrieval-${today()}.jsonl`);
    const raw = readFileSync(filePath, "utf8").trim();
    const parsed = JSON.parse(raw) as Record<string, unknown>;

    expect("backends" in parsed).toBe(true);
    expect("per_backend_hits" in parsed).toBe(true);
    expect("per_backend_latency_ms" in parsed).toBe(true);
  });

  // ---------------------------------------------------------------------------
  // FIX 2: hit_count must equal sum(per_backend_hits) when a type filter is
  // active. searchTool now passes filtered.length (post-filter) as hitCount so
  // hit_count === per_backend_hits.fts5 regardless of filter.
  // ---------------------------------------------------------------------------

  it("hit_count equals per_backend_hits.fts5 when a type filter is active (FIX 2)", () => {
    // Simulate the values searchTool emits after the FIX 2 change:
    // raw = 5 total FTS5 hits, type filter keeps 2, so filtered.length = 2.
    // Both hitCount and perBackendHits.fts5 must be 2 (not 5).
    const stateDir = freshStateDir("fix2-type-filter");
    const filteredCount = 2;

    emitSearchTelemetry(
      stateDir,
      "fix2hash",
      filteredCount,           // hitCount = filtered.length (post-filter)
      30,
      ["fts5"],
      { fts5: filteredCount }, // per_backend_hits.fts5 = filtered.length
      { fts5: 30 },
    );

    const filePath = join(stateDir, "telemetry", `retrieval-${today()}.jsonl`);
    const parsed = JSON.parse(readFileSync(filePath, "utf8").trim()) as Record<
      string,
      unknown
    >;

    const perBackendHits = parsed.per_backend_hits as Record<string, number>;
    expect(parsed.hit_count).toBe(filteredCount);
    expect(perBackendHits.fts5).toBe(filteredCount);
    // The invariant: top-level hit_count === sum(per_backend_hits)
    expect(parsed.hit_count).toBe(perBackendHits.fts5);
  });
});
