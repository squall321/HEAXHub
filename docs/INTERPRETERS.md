# Interpreter Pool

HEAXHub does not bundle Python/Node interpreters. Each operator host declares the
runtimes it actually has available through a single YAML file:

```
config/interpreters.yaml
```

The path is configurable via the `INTERPRETERS_CONFIG` env var (relative paths
resolve against the project root).

## File format

```yaml
python:
  "3.10": /usr/bin/python3.10
  "3.11": /usr/bin/python3.11
  "3.12": /usr/bin/python3.12
node:
  "20": /usr/bin/node
```

Top-level keys are the language id (`python`, `node`). Values map a version
label to an absolute interpreter path. Version labels can be exact patches
(`3.11.4`) or major.minor (`3.11`); both forms participate in the fallback
resolver below.

## Resolution algorithm

When a build task asks for `python_version: "3.11"`, the pool tries, in order:

1. **Exact match** — version string equals a configured key (`"3.11"` ➝
   `/usr/bin/python3.11`).
2. **Major.minor match** — if the request is `"3.11.4"` and only `"3.11"` is
   configured, the major.minor entry wins.
3. **Newest within the same major series** — if neither exact nor major.minor
   match, the highest configured `3.x` is used.
4. **Hard failure** — `RuntimeError` is raised with the available versions.

When no version is requested at all (e.g. the manifest omits `build.python_version`),
the newest configured interpreter is used.

The same chain applies to `node_for(version)`.

The resolved binary path is captured in `build/status.json`:

```json
{
  "python_version_requested": "3.11",
  "python_version_used": "/usr/bin/python3.11",
  "reason": "exact_or_minor_match"
}
```

`reason` is one of `exact_or_minor_match`, `fallback`, or
`no_version_requested_using_newest`.

## Extending the pool with pyenv

If you manage Pythons through pyenv, you can wire them in directly without
moving binaries:

```yaml
python:
  "3.10": /home/heaxhub/.pyenv/versions/3.10.14/bin/python
  "3.11": /home/heaxhub/.pyenv/versions/3.11.9/bin/python
  "3.12": /home/heaxhub/.pyenv/versions/3.12.3/bin/python
  "3.13": /home/heaxhub/.pyenv/versions/3.13.0/bin/python
node:
  "18": /home/heaxhub/.nvm/versions/node/v18.20.4/bin/node
  "20": /home/heaxhub/.nvm/versions/node/v20.13.1/bin/node
```

Generate the entries from a live pyenv install:

```bash
pyenv versions --bare | while read v; do
  major_minor=$(echo "$v" | cut -d. -f1,2)
  printf '  "%s": %s/versions/%s/bin/python\n' "$major_minor" "$(pyenv root)" "$v"
done
```

Reload at runtime is supported by `interpreter_pool.reload_config()` (used in
tests). In production, restart the Celery workers after editing the YAML so the
in-process cache picks up the change.

## Operator checklist

- The interpreter paths must be **absolute** and the binary must be executable
  by the Celery worker user.
- Removing a version that an existing app's manifest pins by patch is safe as
  long as a compatible major.minor or major series interpreter is still
  present; the resolver will fall back and `build/status.json.reason` will
  say `fallback`.
- If a manifest asks for `python_version: "2.7"` and only `3.x` are configured,
  the build fails fast with a clear error mentioning the available versions.
