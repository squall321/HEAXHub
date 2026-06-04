// heax-demo-go — a small but representative Go HTTP demo for HEAXHub.
//
// Single-file binary that demonstrates:
//   - http.ServeMux with multiple routes
//   - html/template rendering with the HEAXHub palette
//   - an in-memory counter guarded by sync.Mutex (the canonical Go signature
//     of goroutines + shared state)
//   - graceful shutdown on SIGINT / SIGTERM
//   - PORT and HEAX_BASE_PATH (BASE_PATH fallback) env wiring so the same
//     binary works both at "/" and under a HEAXHub sub-path like "/apps/foo".
//
// No external dependencies — stdlib only.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"html/template"
	"log"
	"net/http"
	"os"
	"os/signal"
	"runtime"
	"strings"
	"sync"
	"syscall"
	"time"
)

// counter is a tiny goroutine-safe integer. It's the smallest piece of code
// that justifies sync.Mutex in a demo — a real service would use atomic.Int64
// or a database, but the lock makes the pattern explicit for readers.
type counter struct {
	mu sync.Mutex
	n  int64
}

func (c *counter) inc() int64 {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.n++
	return c.n
}

func (c *counter) get() int64 {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.n
}

// indexData feeds the HTML template.
type indexData struct {
	Title    string
	Hostname string
	GoVer    string
	Path     string
	BasePath string
	Counter  int64
	Now      string
}

const indexTmpl = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{.Title}}</title>
<style>
  :root {
    --navy: #0f172a;
    --navy-2: #1e293b;
    --indigo: #4f46e5;
    --indigo-2: #6366f1;
    --amber: #f59e0b;
    --bg: #f8fafc;
    --card: #ffffff;
    --text: #0f172a;
    --muted: #475569;
    --border: #e2e8f0;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI",
          Roboto, Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
  }
  header {
    background: linear-gradient(135deg, var(--navy) 0%, var(--indigo) 100%);
    color: #f8fafc;
    padding: 28px 32px;
  }
  header h1 { margin: 0 0 4px; font-size: 22px; }
  header p  { margin: 0; color: #cbd5e1; font-size: 13px; }
  main { max-width: 760px; margin: 24px auto; padding: 0 16px; }
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px 24px;
    margin-bottom: 16px;
  }
  .card h2 { margin: 0 0 12px; font-size: 16px; color: var(--navy-2); }
  dl { display: grid; grid-template-columns: 160px 1fr; gap: 6px 12px; margin: 0; }
  dt { color: var(--muted); }
  dd { margin: 0; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }
  .counter {
    display: inline-block;
    background: #eef2ff;
    color: var(--indigo);
    font-weight: 600;
    padding: 4px 10px;
    border-radius: 999px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  a.btn {
    display: inline-block;
    background: var(--amber);
    color: var(--navy);
    text-decoration: none;
    padding: 8px 14px;
    border-radius: 6px;
    font-weight: 600;
    font-size: 13px;
    margin-right: 8px;
  }
  a.btn.secondary {
    background: transparent;
    color: var(--indigo);
    border: 1px solid var(--indigo);
  }
  footer {
    text-align: center;
    color: var(--muted);
    font-size: 12px;
    padding: 24px 0 32px;
  }
</style>
</head>
<body>
<header>
  <h1>{{.Title}}</h1>
  <p>HEAXHub · go_service stack demo</p>
</header>
<main>
  <div class="card">
    <h2>Runtime</h2>
    <dl>
      <dt>Go version</dt> <dd>{{.GoVer}}</dd>
      <dt>Hostname</dt>   <dd>{{.Hostname}}</dd>
      <dt>Request path</dt><dd>{{.Path}}</dd>
      <dt>Base path</dt>  <dd>{{if .BasePath}}{{.BasePath}}{{else}}(none){{end}}</dd>
      <dt>Server time</dt><dd>{{.Now}}</dd>
    </dl>
  </div>

  <div class="card">
    <h2>In-memory counter</h2>
    <p>Hits served by this process: <span class="counter">{{.Counter}}</span></p>
    <p>
      <a class="btn" href="{{.BasePath}}/api/counter">POST-like bump (GET ok)</a>
      <a class="btn secondary" href="{{.BasePath}}/api/info">/api/info</a>
      <a class="btn secondary" href="{{.BasePath}}/health">/health</a>
    </p>
  </div>
</main>
<footer>heax-demo-go · stdlib only · graceful shutdown enabled</footer>
</body>
</html>
`

func main() {
	port := getenv("PORT", "8080")
	// Honor the documented HEAX_BASE_PATH, but also accept BASE_PATH which is
	// what config/stacks.yaml::go_service actually advertises. Both flow
	// through the same normalization so the templates and routes agree.
	basePath := normalizeBase(firstNonEmpty(
		os.Getenv("HEAX_BASE_PATH"),
		os.Getenv("BASE_PATH"),
	))

	hostname, _ := os.Hostname()
	tmpl := template.Must(template.New("index").Parse(indexTmpl))
	hits := &counter{}

	mux := http.NewServeMux()

	// Index — renders the HTML template and bumps the counter.
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		// Only treat the exact base path (or "/") as the index; everything
		// else under ServeMux's "/" prefix should 404 so we don't pretend
		// to serve arbitrary URLs.
		if r.URL.Path != "/" && r.URL.Path != basePath && r.URL.Path != basePath+"/" {
			http.NotFound(w, r)
			return
		}
		data := indexData{
			Title:    "heax-demo-go",
			Hostname: hostname,
			GoVer:    runtime.Version(),
			Path:     r.URL.Path,
			BasePath: basePath,
			Counter:  hits.inc(),
			Now:      time.Now().UTC().Format(time.RFC3339),
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		if err := tmpl.Execute(w, data); err != nil {
			log.Printf("template: %v", err)
		}
	})

	// /health — plain text, cheap, no counter side-effects. Matches both
	// /health and (when configured) the sub-path equivalent.
	mux.HandleFunc("/health", healthHandler)
	if basePath != "" {
		mux.HandleFunc(basePath+"/health", healthHandler)
	}

	// /api/info — JSON snapshot of the runtime, useful for HEAXHub health
	// dashboards and for sanity-checking the binary in the field.
	infoHandler := func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{
			"app":       "heax-demo-go",
			"go":        runtime.Version(),
			"hostname":  hostname,
			"path":      r.URL.Path,
			"base_path": basePath,
			"counter":   hits.get(),
			"now":       time.Now().UTC().Format(time.RFC3339),
		})
	}
	mux.HandleFunc("/api/info", infoHandler)
	if basePath != "" {
		mux.HandleFunc(basePath+"/api/info", infoHandler)
	}

	// /api/counter — bumps and returns the new value as JSON.
	counterHandler := func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{
			"counter": hits.inc(),
		})
	}
	mux.HandleFunc("/api/counter", counterHandler)
	if basePath != "" {
		mux.HandleFunc(basePath+"/api/counter", counterHandler)
	}

	srv := &http.Server{
		Addr:              ":" + port,
		Handler:           logMiddleware(mux),
		ReadHeaderTimeout: 5 * time.Second,
	}

	// Graceful shutdown: catch SIGINT/SIGTERM, give in-flight requests a
	// short grace window, then exit. This is the canonical Go web-server
	// pattern and prevents the "killed mid-response" surprise during HEAXHub
	// rolling restarts.
	shutdown := make(chan os.Signal, 1)
	signal.Notify(shutdown, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		log.Printf("heax-demo-go listening on :%s (base_path=%q, go=%s)",
			port, basePath, runtime.Version())
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("listen: %v", err)
		}
	}()

	<-shutdown
	log.Printf("shutdown signal received, draining...")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		log.Printf("shutdown: %v", err)
	}
	log.Printf("bye")
}

func healthHandler(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	_, _ = w.Write([]byte("ok\n"))
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

// logMiddleware is a one-liner access log; HEAXHub aggregates stdout from
// service launches, so structured-but-cheap is the right tradeoff here.
func logMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		next.ServeHTTP(w, r)
		log.Printf("%s %s %s", r.Method, r.URL.Path, time.Since(start))
	})
}

func getenv(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}

// normalizeBase strips trailing slashes and ensures a leading "/" so that
// "/apps/foo", "apps/foo/", and "/apps/foo/" all yield "/apps/foo".
func normalizeBase(p string) string {
	p = strings.TrimSpace(p)
	if p == "" || p == "/" {
		return ""
	}
	if !strings.HasPrefix(p, "/") {
		p = "/" + p
	}
	p = strings.TrimRight(p, "/")
	return p
}

// Compile-time guard: keep fmt imported even if all uses get refactored
// away. (Cheap, and keeps the file robust under future edits.)
var _ = fmt.Sprintf
