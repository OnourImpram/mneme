import { defineConfig } from "vitest/config";

export default defineConfig({
	test: {
		include: ["tests/**/*.test.ts"],
		environment: "node",
		// better-sqlite3 is a native N-API addon that can crash on teardown
		// inside worker_threads (vitest's default "threads" pool), seen as a
		// STATUS_ACCESS_VIOLATION (exit 0xC0000005) on Node 20 Windows CI.
		// Running each test file in a child process isolates native teardown
		// and is the recommended pool for native addons.
		pool: "forks",
		// Fault-injection files run in parallel child processes and can exceed
		// Vitest's 5 s default on slower Windows hosts while remaining bounded.
		testTimeout: 10_000,
		coverage: {
			provider: "v8",
			reporter: ["text", "json", "html"],
			include: ["src/**/*.ts"],
			exclude: ["src/index.ts"],
			thresholds: {
				lines: 80,
				functions: 80,
				branches: 80,
				statements: 80,
			},
		},
	},
});
