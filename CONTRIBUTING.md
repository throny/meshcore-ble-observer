# Contributing

Keep firmware changes and host-bridge changes separate, and follow each upstream
project's contribution guidelines. Start from a clean upstream checkout and
include only the relevant implementation, tests and portable setup instructions.

Do not publish private settings, backups, raw captures or local hardware records.
Use made-up device identities in tests and preserve upstream licences and credits.
This public snapshot is not a reason to copy a private workspace or its history
into an upstream PR.

## What the build tools publish

- Firmware artifacts contain the build outputs and records of how they were
  built, not private workspace history or recovery archives.
- The host patch includes only an approved list of source, test and packaging
  files. Portable `AGENTS.md` guidance is included; private notes and unrelated
  untracked files are not.
- A privacy scan rejects local paths, real device/network identifiers and known
  deployment details without printing the matching values. Review the resulting
  patch yourself too: the scan cannot guarantee that it is safe to publish.
- Downloadable artifacts are test candidates. Review their exact upstream and
  observer revisions before distributing or installing them.

To create a clean source snapshot for review, choose a new destination outside
the checkout:

```sh
python3 tools/public_export.py --output /path/to/new-public-export
```

The tool uses committed `HEAD`, checks its file list and runs the privacy scan.
It creates a new Git history with one commit, no remote and a
`PUBLIC-EXPORT-MANIFEST.json` file. It refuses an existing destination or one
that is not private. It does not push or submit anything.

## Before sending a PR

Review the patch for unrelated files and private data. Describe exactly which
hardware and tests you used; P1-Pro results do not establish support for other
boards or hosts. A passing build does not approve a device update or release.
The candidate/export tools do not submit PRs or publish releases: review and
publication are separate steps chosen by the contributor.
