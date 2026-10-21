# Upstream contribution and privacy boundary

The project workspace preserves local acceptance records, protected backups,
historical reports and environment-specific operational notes. **Do not publish
this complete repository snapshot or its commit history as an upstream PR or
public release.** Public status in this working tree is not a clean distribution
allowlist.

## Candidate/export boundary

- The firmware candidate artifact contains only firmware products and
  provenance; it does not package this workspace, history, local build outputs,
  raw captures or recovery archives.
- The bridge candidate generator exports only its explicit source/test/package
  path allowlist as a patch against a pinned upstream base. It excludes private
  workspace guidance, README, records, backups and arbitrary untracked files;
  portable host `AGENTS.md` guidance is included and reviewed for upstream use. It checks the
  patch for local paths, embedded IPs, non-fixture device-address literals,
  local device-name markers and known host-specific service paths. A finding
  fails the candidate and the report omits matched values. This automated scan
  is a guardrail, not a substitute for inspecting the exact generated patch.
- Workflow artifacts are short-lived candidates; they do not publish a release
  or PR. Review the exact upstream and overlay SHAs and inspect the patch before
  any later distribution.

To prepare a local review snapshot, use a fresh destination outside this
checkout:

```sh
python3 tools/public_export.py --output /path/to/new-public-export
```

The tool reads committed `HEAD` only, validates a per-file allowlist and privacy
scan, writes a one-commit Git snapshot with no remote configured, and includes
`PUBLIC-EXPORT-MANIFEST.json`. It refuses an existing/non-private destination;
it neither pushes nor submits anything.

## Preparing future PRs

Prepare separate PR branches from clean upstream repositories (MeshCore and
`Cisien/meshcoretomqtt` respectively), with only the relevant firmware/host
implementation, tests and generic setup documentation. Preserve the upstream
license and attribution. Do not carry this workspace's Git history, private
remote configuration, STATUS/hardware evidence, operator-specific backup
helper, local configuration, build directory or device records into those
branches. Use synthetic identities/configs in tests. Keep P1-Pro validation
claims distinct from experimental/unvalidated support for other boards or host
models. Submitting, pushing, merging or publishing those PRs/releases requires a
separate explicit decision.

The candidate/export tools do not submit PRs or publish releases. Any upstream
proposal is a separate, explicitly reviewed maintainer-facing action, not an
automatic consequence of a green candidate build.
