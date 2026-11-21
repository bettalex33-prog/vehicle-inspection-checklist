# Changelog

## v2.0.0 — November 21, 2026

### New features

- **Open Dashboard** opens an optional child window for shared inspection
  progress, maintenance status, mileage forecasts, service costs, recent
  service history, and data quality.
- **Refresh** captures the four current stores on demand and publishes a view
  after validation. Vehicle scope uses stable profile IDs. Explicit mileage
  applies to one selected profile for the current session.
- **Backup health** assesses one explicitly selected ZIP independently of
  dashboard refresh. It checks archive compatibility and integrity and
  compares archived bytes with the current stores. Clearing the selection
  clears the assessment.
- The local macOS bundle includes the optional dashboard modules. Closing the
  dashboard leaves the main application running. Closing the main application
  with the dashboard open closes both windows and exits the launcher with
  status 0.

### Safety guarantees

- Opening the dashboard does not read the stores. Refresh and backup assessment
  are explicit. The dashboard has no background monitoring or persistent
  session settings.
- Dashboard operations are read-only. Backup assessment does not extract,
  create, or restore archives. The backup-health panel and its diagnostics omit
  backup paths. Changed evidence is rejected at revalidation, which does not
  provide cross-process locking.
- Packaged final cleanup tolerates the exact already-destroyed Tk condition.
  Startup and mainloop failures still produce visible errors and nonzero exit
  status.

### Compatibility

- The local bundle targets Apple Silicon on macOS 15.5 or later and requires
  the installed ARM64 Python 3.15 framework runtime with Tcl/Tk 9 Aqua.
  Packaging copies an explicit production allowlist from a trusted local
  checkout. The bundle uses the existing external runtime.
- Source commands and tests retain Python 3.10 compatibility, with tkinter
  required for GUI modules and tests. Runtime paths, persistence schemas, and
  backup formats are unchanged. New backups retain manifest version 1 and the
  `1.9.0` compatibility label.

### Validation

- The release-preparation checkout passed 566 tests on Python 3.10.13 and
  ARM64 Python 3.15.0, including three release checks. In-memory compilation,
  duplicate detection, and whitespace checks passed.
- A disposable v2.0.0 candidate was inspected without launching it. It
  contained 24 production Python modules, the launcher, and the plist.
- Native shutdown was verified on the integrated shutdown fix before the
  version bump. The launcher returned status 0 with no remaining windows,
  process, or native failure alert after dashboard close and reopen followed
  by main Close.

### Git inventory since v1.9.0

The implementation range begins after the v1.9.0 commit
`9e739a765c70be7021ea629e1740e43cfea5a1d3` and ends at
`f94046cc21dfbeb4c68b2d74f6bb8c307d623429`. In commit order:

| Commit | Subject | Release change |
| --- | --- | --- |
| `eeacd6e` | Add dashboard presentation model | Projects validated facts into the dashboard view. |
| `d5fb74d` | Add read-only dashboard source capture | Reads explicit stores and revalidates captured evidence. |
| `102fdc9` | Add transactional dashboard session controller | Validates and publishes refreshed views as a single session update. |
| `d9668df` | Add optional fleet dashboard GUI | Adds the passive child window and explicit refresh controls. |
| `ecb839f` | Complete optional dashboard window | Completes the presentation, input handling, and action guards. |
| `975c150` | Add read-only dashboard backup health assessment | Validates one archive and compares its store bytes with current evidence. |
| `e4dfb9c` | Integrate backup health into optional dashboard | Adds independent archive choice, assessment, and clearing. |
| `b5bb205` | Integrate optional dashboard with application | Adds the optional checklist entry point and child-window lifecycle. |
| `310b5ec` | Package optional dashboard modules | Includes all six dashboard and backup-health modules in the local bundle. |
| `f94046c` | Handle packaged application shutdown cleanly | Treats the already-destroyed Tk root as completed cleanup. |

Earlier version notes remain in the [README](README.md).
