import {
	existsSync,
	mkdirSync,
	mkdtempSync,
	readdirSync,
	readFileSync,
	symlinkSync,
	writeFileSync,
} from "node:fs";
import { platform, tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
	assertWithinVault,
	atomicWriteText,
	VaultPathError,
} from "../../src/vault/atomic_write.js";

function freshTmp(prefix: string): string {
	return mkdtempSync(join(tmpdir(), `mneme-mcp-${prefix}-`));
}

// Symlink creation on Windows needs admin or developer mode. Skip the
// symlink tests there so non-elevated dev runs do not red-light CI.
const symlinkAvailable = platform() !== "win32";

describe("assertWithinVault", () => {
	it("accepts a child path inside the vault", () => {
		const root = freshTmp("within-ok");
		const child = join(root, "nested", "file.md");
		expect(() => assertWithinVault(root, child)).not.toThrow();
	});

	it("accepts the vault root itself", () => {
		const root = freshTmp("within-root");
		expect(() => assertWithinVault(root, root)).not.toThrow();
	});

	it("rejects ../ escapes", () => {
		const root = freshTmp("within-esc");
		const outsidePath = join(root, "..", "outside.md");
		expect(() => assertWithinVault(root, outsidePath)).toThrow(VaultPathError);
	});

	it("rejects unrelated absolute paths", () => {
		const root = freshTmp("within-abs");
		const elsewhere = freshTmp("elsewhere");
		expect(() => assertWithinVault(root, join(elsewhere, "x.md"))).toThrow(
			VaultPathError,
		);
	});

	it("rejects sibling that shares a prefix string but not a directory", () => {
		// /tmp/vault and /tmp/vault-attack share a string prefix but the
		// latter is NOT inside the former. The separator check matters.
		const base = freshTmp("prefix");
		const vault = join(base, "vault");
		const attacker = join(base, "vault-attack", "file.md");
		mkdirSync(vault, { recursive: true });
		expect(() => assertWithinVault(vault, attacker)).toThrow(VaultPathError);
	});

	it.skipIf(!symlinkAvailable)(
		"rejects writes through a symlink directory that escapes the vault",
		() => {
			// Codex Pass 1 regression: an attacker inside the vault places a
			// symlink that points outside. Without realpath resolution the
			// lexical prefix check would accept the path. After the Pass 1
			// fix, the realpath check rejects it.
			const base = freshTmp("symlink-dir");
			const vault = join(base, "vault");
			const outside = join(base, "outside");
			mkdirSync(vault, { recursive: true });
			mkdirSync(outside, { recursive: true });
			const escapeLink = join(vault, "escape");
			symlinkSync(outside, escapeLink, "dir");
			const escapedFile = join(escapeLink, "leaked.md");
			expect(() => assertWithinVault(vault, escapedFile)).toThrow(
				VaultPathError,
			);
		},
	);

	it.skipIf(!symlinkAvailable)(
		"rejects writes through a pre-existing symlink file that escapes",
		() => {
			const base = freshTmp("symlink-file");
			const vault = join(base, "vault");
			const outsideFile = join(base, "outside.md");
			mkdirSync(vault, { recursive: true });
			writeFileSync(outsideFile, "secret\n");
			const linkTarget = join(vault, "escape.md");
			symlinkSync(outsideFile, linkTarget, "file");
			expect(() => assertWithinVault(vault, linkTarget)).toThrow(
				VaultPathError,
			);
		},
	);

	it.skipIf(!symlinkAvailable)(
		"accepts paths reached through symlinks that stay inside the vault",
		() => {
			const base = freshTmp("symlink-inside");
			const vault = join(base, "vault");
			const real = join(vault, "real");
			mkdirSync(real, { recursive: true });
			const inside = join(vault, "alias");
			symlinkSync(real, inside, "dir");
			const child = join(inside, "doc.md");
			expect(() => assertWithinVault(vault, child)).not.toThrow();
		},
	);

	it.skipIf(!symlinkAvailable)(
		"atomic write refuses a parent symlink that escapes the vault",
		() => {
			const base = freshTmp("atomic-symlink-escape");
			const vault = join(base, "vault");
			const outside = join(base, "outside");
			mkdirSync(vault, { recursive: true });
			mkdirSync(outside, { recursive: true });
			const escapeLink = join(vault, "escape");
			symlinkSync(outside, escapeLink, "dir");
			const escapedFile = join(escapeLink, "leaked.md");

			expect(() =>
				atomicWriteText(escapedFile, "secret\n", { vaultRoot: vault }),
			).toThrow(VaultPathError);
			expect(existsSync(join(outside, "leaked.md"))).toBe(false);
		},
	);

	it("accepts the vault root even when it has no realpath ancestor", () => {
		// Defensive: when vault root resolution returns the literal absolute
		// path (e.g. running off a tmpdir without symlinks) the function
		// still accepts the root.
		const root = freshTmp("noop-real");
		expect(() => assertWithinVault(root, join(root, "x.md"))).not.toThrow();
	});
});

describe("atomicWriteText", () => {
	it("writes content to a fresh file", () => {
		const root = freshTmp("atomic-fresh");
		const target = join(root, "daily", "2026-05-19.md");
		atomicWriteText(target, "# Hello\n\nBody.\n");
		expect(readFileSync(target, "utf8")).toBe("# Hello\n\nBody.\n");
	});

	it("overwrites an existing file (Windows-safe)", () => {
		const root = freshTmp("atomic-over");
		const target = join(root, "existing.md");
		writeFileSync(target, "OLD\n");
		atomicWriteText(target, "NEW\n");
		expect(readFileSync(target, "utf8")).toBe("NEW\n");
	});

	it("creates intermediate directories as needed", () => {
		const root = freshTmp("atomic-mkdir");
		const target = join(root, "a", "b", "c", "deep.md");
		atomicWriteText(target, "deep\n");
		expect(existsSync(target)).toBe(true);
	});

	it("leaves no leftover .tmp sibling on success", () => {
		const root = freshTmp("atomic-cleanup");
		const target = join(root, "clean.md");
		atomicWriteText(target, "ok\n");
		const leftover = readdirSync(root).filter((f) => f.includes(".tmp"));
		expect(leftover).toEqual([]);
	});

	it("UTF-8 with Turkish diacritics round-trips", () => {
		const root = freshTmp("atomic-tr");
		const target = join(root, "tr.md");
		const content = "Komotini şehri ışıkları ile İstanbul.\n";
		atomicWriteText(target, content);
		expect(readFileSync(target, "utf8")).toBe(content);
	});
});
