# Reviewed Python engine updates

The Windows desktop runtime can refresh the reviewed, read-only
`agent/x_automation_engine.py` from the coordination server. The server never
accepts Python source from the web UI: it serves only the file included in the
deployed GitHub release.

## Release flow

1. Change `agent/x_automation_engine.py` and keep the workflow read-only.
2. Run the updater and server tests locally.
3. Merge the change to `main` and run the normal server upgrade script.
4. The authenticated Agent endpoint exposes a SHA-256 manifest and source.
5. Each Windows Agent fetches the manifest during its heartbeat cycle and again
   immediately before a desktop automation run.
6. The source is compiled, AST-checked, hashed, loaded, and atomically cached
   below `agent_data/engine_cache/`.

## Safety behavior

- Agent Bearer authentication and the device-binding header are required.
- Only `https://api.jaycwl.org` is accepted by the default client policy.
- The manifest must be marked `read_only` and contain a valid SHA-256 digest.
- System, filesystem, process, socket, and dynamic import primitives are
  rejected before activation.
- A failed download, timeout, hash check, compile check, or compatibility check
  leaves the current engine untouched.
- If the active cache is damaged, the previous cache is selected atomically.
- The bundled engine remains the final fallback, so a server outage does not
  stop the desktop console.

## Configuration

`LAOGU_ENGINE_AUTO_UPDATE=true` is the default in the portable example. Set it
to `false` for a fixed desktop build. No Agent token belongs in Git or in a
public release archive; credentials remain in the protected `agent_data`
directory.
