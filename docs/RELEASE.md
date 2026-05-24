# Release Checklist

This checklist keeps the public GitHub release path aligned with package,
plugin, runtime, and documentation metadata.

## v1.0.1 Repo Hardening Gate

1. Merge the hardening PR only after all required CI contexts are green and the
   required review is complete.
2. Confirm version consistency on `main`.

   ```bash
   python tools/version_bump.py --check 1.0.1
   python tools/repo_integrity.py
   python tools/validate_codex_plugin.py packages/mneme-codex-plugin
   ```

3. Confirm `CHANGELOG.md` has separate `[1.0.1]` and `[1.0.0]` sections.
4. Confirm `README.md`, plugin manifests, runtime constants, and marketplace
   metadata all report `1.0.1`.
5. Run the release workflow in dry-run mode with `target_version=1.0.1`.
6. If dry-run passes, create and push the tag.

   ```bash
   git tag -a v1.0.1 -m "mneme v1.0.1"
   git push public v1.0.1
   ```

7. Confirm the GitHub Release is generated from the tag and includes the
   `mneme-cc-plugin` tarball plus SHA256 sidecar.
8. Confirm branch protection remains active on `main`, including required
   status checks, one approving review, force-push disabled, and deletion
   disabled.

## Do Not Release If

- Any required CI context is missing, skipped unexpectedly, or red.
- `tools/version_bump.py --check` reports disagreement.
- Codex plugin validation fails.
- `mneme install --dry-run` writes to the target vault.
- `mneme-mcp --version` requires a vault or emits a fatal error.
