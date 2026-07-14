# Changelog

All notable changes to diff.lab are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Machine rescan** (`POST /rescan/<machine>`) — refresh a registered machine's tracked
  repos without re-registering it. A `roots`-based enrollment now records the machine's
  `host`/`user`/`port`/`roots` in `registry.yaml`; a rescan re-walks those roots, adds repos
  that appeared, and drops repos that were removed. The index page gains a **Rescan a
  machine** control (token-gated, same as `/register`). Explicit-`repos` enrollments record
  no roots and are not rescannable.

### Fixed

- **Git-gate rejected diff after the `diff HEAD` change** — the app started sending
  `git … diff HEAD` / `diff --numstat HEAD` (to include staged files) but the
  forced-command gate (`gate/git-gate.sh`, `gate/git-gate.ps1`) only permitted the
  ref-less forms, so gated (remote/homelab) machines surfaced "Remote gate refused
  command" on the diff view. The gate now accepts an optional trailing `HEAD` and
  passes it through to git; the ref-less forms still work for older app builds, and
  arbitrary refs remain rejected. **Deploying this requires reinstalling the gate
  script on each enrolled machine.**

## [0.2.0] - 2026-07-12

### Added

- **Canonical per-machine URLs** — diff viewer moved to `/d/<machine>/<name>`; index
  links and back-navigation updated accordingly.
- **Discovery endpoint** (`GET /api/targets`) — returns JSON array of all targets with
  `state` (dirty/clean/error/untracked), `file_count`, and `error` fields for tooling
  consumers.
- **`status_service.py`** — dedicated service layer for parallel status checks using
  `ThreadPoolExecutor`; replaces ad-hoc threading in views.
- **Forced-command SSH gate** (`gate/git-gate.sh`, `gate/git-gate.ps1`) — the container
  key is restricted to read-only git-upload-pack via forced command; `/pubkey` now vends
  the gated `authorized_keys` line.
- **Brand identity** — Pied Kingfisher favicon (SVG + PNG variants, dark/light), brand
  icon in nav, kingfisher wordmark replacing the generic diff.lab placeholder.
- **Index UI refresh** — machine grouping, clickable rows, sortable columns, file-count
  column, collapsible status section, progressive loading, human-readable error messages.
- **CI/CD deploy workflow** (`deploy.yml`) — automated build + push to GHCR and
  self-hosted runner redeploy on push to `OfBirds/difflab` main.
- **Expanded test suite** — coverage for untracked-only status, SSH quoting edge cases,
  dot-repo filtering, error taxonomy, and the `/api/targets` response shape.

### Changed

- Index summary moved above the targets table.
- SSH timeout raised; host-key handling hardened; `GIT_CONFIG_NOSYSTEM` set in compose
  to avoid polluting git config inside the container.

## [0.1.0] - 2026-07-11

### Added

- **Working-tree diff viewer** — Flask app that shows `git diff` output across
  configured local and SSH-remote git repositories; the thing Gitea and cgit
  don't show.
- **Syntax-highlighted diff view** (`/d/<name>`) with collapsible per-file
  sections and dark/light mode via `prefers-color-scheme`.
- **Raw diff endpoint** (`/raw/<name>`) serving `text/plain` for tooling
  consumers.
- **Index page** (`/`) listing only repos with uncommitted changes; clean repos
  are counted but hidden. Parallel status checks via `ThreadPoolExecutor`.
- **Machine enrollment** (`GET /pubkey`, `POST /register`): a host can join
  diff.lab with a single `curl` — no agent software, no manual config edits.
  The container generates an ed25519 keypair on first start; `/pubkey` vends the
  ready-to-paste `authorized_keys` line; `/register` discovers git repos via
  POSIX `find` or PowerShell `Get-ChildItem` fallback and writes them to
  `registry.yaml`.
- **Config validation** at startup with clear error messages; target names
  validated against `^[A-Za-z0-9][A-Za-z0-9._-]*$`.
- **Security hardening**: `shell=False` everywhere, repo path passed as a
  discrete `-C` argument, HTML autoescape on, 30 s git/SSH timeout, token
  comparison via `hmac.compare_digest`.
- **Docker image** — `python:3.12-slim` base; `/data` volume for SSH keypair
  and registry.
