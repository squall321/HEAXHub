import Counter from "./counter";

export default function HomePage() {
  const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "(none)";

  return (
    <main style={{ maxWidth: "720px", margin: "0 auto" }}>
      <h1 style={{ fontSize: "2.5rem", marginBottom: "0.5rem" }}>
        HEAXHub · Next.js Demo
      </h1>
      <p style={{ opacity: 0.75, marginTop: 0 }}>
        base-path aware SPA — Caddy /apps/{"{id}"}/ 뒤에서 동작.
      </p>

      <section
        style={{
          marginTop: "2rem",
          padding: "1rem 1.25rem",
          background: "#101638",
          borderRadius: "0.5rem",
          border: "1px solid #1d2858",
        }}
      >
        <div style={{ fontSize: "0.85rem", opacity: 0.7 }}>basePath</div>
        <code style={{ fontSize: "1.1rem" }}>{basePath || "(none)"}</code>
      </section>

      <section style={{ marginTop: "2rem" }}>
        <div style={{ fontSize: "0.85rem", opacity: 0.7, marginBottom: "0.5rem" }}>
          Counter (client component)
        </div>
        <Counter />
      </section>
    </main>
  );
}
