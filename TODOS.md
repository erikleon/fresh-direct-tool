# TODOs

Deferred work, with enough context to pick up cold.

## MCP packaging — deferred distribution surfaces

Current state: the MCP server ships as an installable tool from the Git URL
(`uv tool install "git+https://github.com/erikleon/fresh-direct-tool.git[mcp]"`),
which exposes `fdplanner-mcp` on PATH with no machine-specific path. State lives
in a per-user data dir (`FDPLANNER_DATA_DIR` to override). The items below were
considered and deferred, not rejected outright.

### Claude Desktop `.mcpb` bundle
- **What:** Build a double-click MCP bundle (`.mcpb` / DXT) with a `manifest.json`
  declaring the Python server (`app.mcp_server:main`) and prompting for config
  (`FDPLANNER_WEEKLY_BUDGET`, SMTP vars, `FDPLANNER_ANTHROPIC_API_KEY`).
- **Why:** Best install UX for non-CLI household users — no terminal for setup.
- **Context:** Desktop-specific format. A Python bundle still needs a runtime
  present, and the FreshDirect login stays a manual `fdplanner login` step
  (headed Chrome for 2FA/captcha can't be bundled), so "one-click" is partial.
- **Depends on:** nothing; the entry point and `[mcp]` extra already exist.

### Publish to PyPI
- **What:** `uv build && uv publish` so the package installs by name and `uvx
  --from "freshdirect-planner[mcp]" fdplanner-mcp` works zero-install.
- **Why:** Widest reach; cleanest zero-install story.
- **Context:** Declined for now — it's a single-household tool, and PyPI is a
  public, permanent namespace with immutable versions. Would need a version-bump
  + release discipline (`version` in `pyproject.toml` is the source of truth) and
  ideally CI release automation. Revisit only if this is meant for other people.
- **Depends on:** a claimed PyPI name; release process.
