# heax-demo-go

A small but representative Go HTTP demo for the HEAXHub `go_service` stack.
Stdlib only — no external dependencies.

## What it shows

- `http.ServeMux` with four routes
  - `/`           — HTML index (uses `html/template`)
  - `/health`     — plain-text liveness probe
  - `/api/info`   — JSON snapshot of runtime info
  - `/api/counter`— bumps and returns the in-memory counter
- An in-memory counter guarded by `sync.Mutex` (goroutine-safe shared state)
- Graceful shutdown on `SIGINT` / `SIGTERM` with a 10 s drain window
- Sub-path mounting via `HEAX_BASE_PATH` (or `BASE_PATH`, which is what
  `config/stacks.yaml::go_service` advertises)

## Env

| Variable         | Default | Purpose                                  |
|------------------|---------|------------------------------------------|
| `PORT`           | `8080`  | TCP port to bind                         |
| `HEAX_BASE_PATH` | (none)  | Sub-path prefix, e.g. `/apps/heax-demo-go` |
| `BASE_PATH`      | (none)  | Fallback for `HEAX_BASE_PATH`            |

## Build & run

```bash
go build -o bin/server .
./bin/server
# → listens on :8080
curl -s http://127.0.0.1:8080/health           # ok
curl -s http://127.0.0.1:8080/api/info | jq .
curl -s http://127.0.0.1:8080/api/counter      # {"counter":1}
```

Sub-path:

```bash
HEAX_BASE_PATH=/apps/heax-demo-go ./bin/server
curl -s http://127.0.0.1:8080/apps/heax-demo-go/health
```

## Layout

```
main.go      # single-file demo (handlers + template + main)
go.mod       # module + go 1.22, no external deps
README.md    # this file
.gitignore   # binaries and editor noise
```
