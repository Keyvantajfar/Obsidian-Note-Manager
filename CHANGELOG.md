# Changelog

## [Unreleased]
### Added
- Crash log at `<config>/logs/vaultmgr.log` for the windowed app; internal server errors are recorded there.
- `vaultmgr --cli`, `--tui`, `--serve` and `--gui` are accepted as aliases of the subcommands.
- `packaging/ui_smoke.py`: optional end-to-end UI check in headless Chromium; CI now also syntax-checks the UI scripts.

### Fixed
- Rare TUI crash at startup/shutdown caused by a method name clash with Textual's internals.

## [0.9.0]
### Added
- Desktop app: a local web UI (`vaultweb`: FastAPI + plain HTML/CSS/JS, no frontend framework) shown in an app-style
  window — pywebview if installed, else a Chromium `--app` window, else a browser tab. The app exits when its window closes.
- `vaultmgr` is now the app (`vaultmgr [gui] [VAULT]`, `serve`, `tui`, `cli`, `version`); the terminal UI moved to `vaultmgr-tui`.
- Standalone binaries for Windows, macOS and Linux (PyInstaller), built and smoke-tested by CI and attached to releases.
- `vaultcore.workflows`: the workspace table shared by every UI.
- Full UI for every tab: Explorer (with note text editor), Tagger, Organizer, Dates, Stage (dry-run Apply, Undo, history), Jobs,
  Tags, Rules and instructions, Settings (model profiles, automation, header fields, prompts). Light and dark themes.

### Changed
- Recent vaults are now saved when a vault is opened.
