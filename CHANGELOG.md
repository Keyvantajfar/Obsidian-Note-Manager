# Changelog

## [0.2.0]
### Added
- Desktop app: a local web UI (`vaultweb`: FastAPI + plain HTML/CSS/JS, no frontend framework) shown in an app-style
  window — pywebview if installed, else a Chromium `--app` window, else a browser tab. The app exits when its window closes.
- `vaultmgr` is now the app (`vaultmgr [gui] [VAULT]`, `serve`, `tui`, `cli`, `version`); the terminal UI moved to `vaultmgr-tui`.
- Standalone binaries for Windows, macOS and Linux (PyInstaller), built and smoke-tested by CI and attached to releases.
- `vaultcore.workflows`: the workspace table shared by every UI.

### Changed
- Recent vaults are now saved when a vault is opened.
