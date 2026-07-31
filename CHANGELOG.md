# Changelog

## Alpha 0.2 — 2026-07-28

### Added

- Preview of Instagram Saved publications before import.
- Search modes for new, all saved, and recent publications.
- Filters by publication type, structure, and author.
- Editable names and descriptions directly in the results table.
- Global committed-edit Undo/Redo:
  - `Cmd/Ctrl+Z` restores a committed table edit.
  - `Cmd/Ctrl+Shift+Z` reapplies it.
- Reset controls for individual edited cells.
- Global “Reset all changes” action.
- Carousel component selection.
- Automatic naming and configurable numbering.
- Lazy thumbnail loading for visible rows.
- Thumbnail disk cache and limited parallel loading.
- Adjustable workspace panels:
  - horizontal search/results divider;
  - vertical results/naming divider;
  - saved divider positions.
- Alpha 0.2 version label.

### Workspace update

- Improved adaptive workspace sizing and scrolling.
- Added vertical scrolling to the naming and numbering panel.
- Removed horizontal scrolling from the left search panel.
- Added responsive wrapping and width adaptation for search controls.
- Increased the minimum usable width of the search panel.
- Added double-click reset for both workspace dividers.
- Increased the default height allocated to the results table.
- Reset previously saved divider positions from the initial workspace build.

### Changed

- Replaced persistent per-row widgets with lightweight table items and delegates.
- Improved table performance and scrolling responsiveness.
- Fixed near-quadratic table header resizing during population.
- Stabilized the results header when edit controls appear.
- Removed redundant textual thumbnail status.
- Moved “Thumbnails” and “Select all” controls to opposite sides.
- Increased the default application window size.
- Improved support for large result tables.

### Fixed

- Table edits can now be undone after the cell editor closes.
- Escape commits the current cell edit instead of silently discarding it.
- Redo history is cleared after a new edit branch.
- Thumbnail loading no longer blocks the main interface.
- Returning to previously viewed rows reuses cached thumbnails.
- The search panel no longer changes width when the global reset button appears.

### Known limitations

- “Check all saved” still has a temporary 500-publication ceiling.
- Search progress is not yet fully connected to real pagination stages.
- Import progress does not yet use the animated segmented progress design.
- Very large Saved collections still require unlimited cursor-based retrieval.
- Workspace panels are resizable but not yet detachable or dockable.

### Next planned milestone

- Unlimited retrieval of Instagram Saved publications.
- Progressive population of tables containing 2,000–5,000+ rows.
- Detailed search stages and real counters.
- Smooth decimal segmented progress animation.
- Detailed Eagle import progress and adaptive retry handling.
