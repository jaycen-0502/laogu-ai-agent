# Reviewed Python engine updates

The Windows desktop runtime can refresh the reviewed, read-only
multiple named, read-only Python engines from the coordination server. The
bundled `agent/x_automation_engine.py` remains the default fallback. Admins can
publish engines from the Web Script Center using an engine ID such as
`new-account` and a display name such as `新号`.

## Release flow

1. In the Web Script Center, enter an engine ID, display name, description and
   version, then upload a Python file exposing `XAutomationEngine`.
2. The server validates UTF-8, Python syntax and the `XAutomationEngine.run`
   interface, stores it under
   `/var/lib/laogu/agent-data/engine_publish/<engine-id>/` in production (or
   `LAOGU_ENGINE_PUBLISH_DIR` when configured), and exposes a signed-by-HTTPS,
   SHA-256 manifest to authenticated Agents.
3. The Windows control center lists the available engine names in the
   automation dialog. Selecting one downloads it on demand and caches it under
   `agent_data/engine_cache/<engine-id>/versions/`.
4. The selected engine is loaded for that task. The default engine keeps the
   bundled fallback; a custom engine must have a valid local cache when the
   server is offline.

## Safety behavior

- Agent Bearer authentication and the device-binding header are required.
- Only `https://api.jaycwl.org` is accepted by the default client policy.
- The manifest must be marked `read_only` and contain a valid SHA-256 digest.
- Upload is restricted to `ADMIN`. Administrator-published engines are trusted
  application code, so system, filesystem and network modules are allowed.
- Sensitive imports and dynamic/file operations are retained as manifest audit
  findings and shown after publishing; they do not block a trusted upload.
- Legacy or non-admin-trusted bundles keep the restrictive static policy.
- A failed download, timeout, hash check, compile check, or compatibility check
  leaves the current engine untouched.
- If the active cache is damaged, the previous cache is selected atomically.
- The bundled engine remains the final fallback, so a server outage does not
  stop the desktop console.
- Published engines are persistent server data and are included in the
  encrypted server backup/restore flow.

## Configuration

`LAOGU_ENGINE_AUTO_UPDATE=true` is the default in the portable example. Set it
to `false` for a fixed desktop build. No Agent token belongs in Git or in a
public release archive; credentials remain in the protected `agent_data`
directory.
