# Changelog

All notable changes to diff.lab are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
