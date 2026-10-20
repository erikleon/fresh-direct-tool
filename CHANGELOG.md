# Changelog

## 0.5.0 - 2026-10-20

- Use a local Ollama model for grocery matching and profile refinement, with visible heuristic fallback and no automatic cloud fallback.
- Suggest dinners from the household recipe collection using the delivery week, purchase preferences and optional weather forecast.
- Preserve source ingredient quantities and offer unchecked shopping candidates for pantry, package and quantity review.
- Refresh recipe exports independently; hide stale forecasts and retain recipe context with each draft.
- Correct the Chrome user-agent version fallback on unrecognized platforms.
