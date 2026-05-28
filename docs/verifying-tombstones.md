# Verifying tombstones after a release

This is the maintainer-facing checklist for proving that a release's two
deprecation tombstones — the [`agentops-toolkit` PyPI metapackage](../tombstones/pypi/pyproject.toml)
and the [`AgentOpsToolkit.agentops-skills` VSIX](../tombstones/vscode/package.json) —
are live, intact, and redirecting users to the renamed
`agentops-accelerator` / `AgentOpsAccelerator.agentops-skills` artifacts.

The CI workflow runs a pre-publish smoke test against TestPyPI
(see [`.github/workflows/release.yml`](../.github/workflows/release.yml) line
~223 and [`.github/workflows/staging.yml`](../.github/workflows/staging.yml)
line ~222), but a post-publish pass is still required because the production
PyPI index, the VS Code Marketplace, and the GitHub Release are all
separate systems with their own propagation and indexing windows.

## When to run

Run the verification harness **after** `release.yml` reports green for
`v<version>` and **before** filing the CDN deprecation request:

1. Push the `v<version>` tag.
2. Wait for the `release.yml` workflow to complete on `main`. All jobs
   (build, publish-pypi, publish-tombstone-pypi, publish-vsix,
   publish-vsix-tombstone, github-release) must be green.
3. Run the automated harness below.
4. Walk the VS Code UX manual checklist below.
5. Only when both pass: file the [CDN deprecation request](../tombstones/vscode/CDN_DEPRECATION_REQUEST.md).

If any check fails, do **not** unpublish — follow the rollback path at the
bottom of this page.

## One-shot automated verification

From the repo root:

```bash
python3 scripts/verify_tombstones.py --version 0.3.0
```

Expected summary block on success:

```text
──────────────────────────────────────────────
 Tombstone verification — v0.3.0
──────────────────────────────────────────────
 PyPI  (8/8 checks): PASS
 VSIX  (5/5 checks): PASS
 GH    (11/11 checks): PASS
──────────────────────────────────────────────
Overall: PASS
```

Exit codes:

| Code | Meaning |
|------|---------|
| `0`  | All non-skipped checks passed. Proceed to the manual VS Code checklist. |
| `1`  | One or more checks failed. Roll back per the section below. |
| `2`  | A required prerequisite tool (`vsce`, `gh`) is missing. Install it or pass the matching `--skip-*` flag. |

### What each category asserts

**PyPI (`Check 1`)** — installs `agentops-toolkit==<version>` into a throwaway
venv created with `sys.executable` and then verifies:

1. The venv was created.
2. `pip` was upgraded inside the venv.
3. `pip install agentops-toolkit==<version>` succeeded.
4. `pip show agentops-toolkit` lists `agentops-accelerator` under `Requires:`.
5. The pulled-in `agentops-accelerator` version is `>= <version>`.
6. `python -c "import agentops"` resolves to a path that is *not* owned by
   the tombstone distribution (shadow-free).
7. `python -m agentops --version` exits 0.
8. `importlib.metadata.files("agentops-toolkit")` reports zero `.py` files —
   proves the tombstone wheel is a pure metadata redirect and cannot shadow
   the real `agentops/` package on `sys.path`.

This mirrors the pre-publish CI verification but adds the shadow-free check
and version-floor assertion that production needs.

**VSIX (`Check 2`)** — runs `vsce show AgentOpsToolkit.agentops-skills --json`
and verifies:

1. `vsce` is on `PATH` and the JSON parses.
2. `publisher.publisherName == "AgentOpsToolkit"`.
3. `<version>` appears in the `versions[]` list.
4. `<version>` is the first (newest) entry — `vsce` sorts newest first.
5. `displayName` contains a deprecation hint (`deprecated`, `renamed`, or
   `agentops-skills` — case-insensitive substring match).

If `vsce` is missing: `npm install -g @vscode/vsce`, or pass `--skip-vsix`.

**GH (`Check 3`)** — runs `gh release view v<version> --json …` and verifies:

1. `gh` is on `PATH` and the JSON parses.
2. `tagName == "v<version>"`.
3. `isDraft == false`.
4. `isPrerelease == false`.
5. The release body is non-empty.
6. Each of the six expected assets is checked individually (substring match
   per filename — six separate ticks in the per-check output, contributing
   six of the eleven total GH checks):
   - `agentops_accelerator-<version>.tar.gz`
   - `agentops_accelerator-<version>-py3-none-any.whl`
   - `agentops_toolkit-<version>.tar.gz`
   - `agentops_toolkit-<version>-py3-none-any.whl`
   - `agentops-skills.vsix`
   - `agentops-toolkit-tombstone.vsix`

If `gh` is missing: install from <https://cli.github.com/>, or pass
`--skip-gh-release`.

Useful flags:

| Flag | Purpose |
|------|---------|
| `--repository testpypi` | Run the PyPI check against TestPyPI (post-staging dry-run). |
| `--skip-pypi` / `--skip-vsix` / `--skip-gh-release` | Skip a category — useful when iterating on one surface. |
| `--gh-repo OWNER/REPO` | Target a fork or mirror (default: `Azure/agentops`). |
| `--verbose` | Echo full subprocess stdout/stderr (the default keeps the summary tidy). |

## VS Code UX manual checklist

These flows exercise the live extension's deprecation prompt and cannot be
automated against the Marketplace — they require an interactive VS Code
session against a clean profile (so prior `globalState` sentinels do not
mask the prompt). Run each in a fresh `code --profile` so the per-install
sentinel starts empty.

1. **Prompt appears on first activation.**

   ```bash
   code --profile fresh-toolkit-1 \
        --install-extension AgentOpsToolkit.agentops-skills@0.3.0
   ```

   Reload the window. Expected: an information notification appears reading:

   > **AgentOps Toolkit was renamed to AgentOps Accelerator**

   …with three buttons: `Install replacement`, `Open in Marketplace`,
   `Dismiss`.

2. **`Install replacement` installs the new extension and suppresses the prompt.**

   In the `fresh-toolkit-1` profile, click `Install replacement`. Expected:
   VS Code installs `AgentOpsAccelerator.agentops-skills` automatically.
   Reload the window again — the prompt **must not** re-appear (the per-install
   sentinel suppresses it).

3. **`Dismiss` suppresses the prompt without installing the replacement.**

   ```bash
   code --profile fresh-toolkit-2 \
        --install-extension AgentOpsToolkit.agentops-skills@0.3.0
   ```

   Reload, click `Dismiss`, reload again. Expected: prompt does **not**
   re-appear; the new extension is **not** installed.

4. **`Open in Marketplace` opens the renamed listing.**

   ```bash
   code --profile fresh-toolkit-3 \
        --install-extension AgentOpsToolkit.agentops-skills@0.3.0
   ```

   Reload, click `Open in Marketplace`. Expected: the system browser opens to
   <https://marketplace.visualstudio.com/items?itemName=AgentOpsAccelerator.agentops-skills>.

All four flows must pass before filing the CDN deprecation request.

## CDN deprecation follow-up

Once automated + manual verification are both green:

1. File the deprecation request per
   [`tombstones/vscode/CDN_DEPRECATION_REQUEST.md`](../tombstones/vscode/CDN_DEPRECATION_REQUEST.md).
2. Wait 24–48 hours after Microsoft confirms receipt.
3. Verify: the legacy listing at
   <https://marketplace.visualstudio.com/items?itemName=AgentOpsToolkit.agentops-skills>
   now shows the standard **Deprecated** banner and a pointer to the
   recommended replacement extension
   (`AgentOpsAccelerator.agentops-skills`).
4. Update the tracking issue with the post-deprecation timestamp and a
   screenshot.

## Rollback

If any verification check fails, **do not unpublish.** Both PyPI and the
VS Code Marketplace penalize unpublishes (PyPI bans re-uploading the same
version filename; the Marketplace forces a 24-hour cool-down). Instead:

1. Bump the version to the next patch release (e.g. `v0.3.1`) per
   [SemVer](https://semver.org/) and the repo's `CHANGELOG.md` convention.
2. Fix the tombstone source in [`tombstones/pypi/pyproject.toml`](../tombstones/pypi/pyproject.toml)
   and/or [`tombstones/vscode/package.json`](../tombstones/vscode/package.json).
3. Re-publish through the standard
   [release process](./release-process.md).
4. Record the rollback under the **Unreleased** heading in
   [`CHANGELOG.md`](../CHANGELOG.md) per Keep-a-Changelog conventions, and
   reference the failing check in the entry.
5. Re-run `python3 scripts/verify_tombstones.py --version <new-version>`
   before filing the (now superseded) CDN deprecation request.
