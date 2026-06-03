import type { ReactNode } from "react";

export const metadata = {
  title: "HEAXHub · Next.js Demo",
  description: "HEAXHub Next.js stack demo. base-path aware SPA.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      <body
        style={{
          fontFamily:
            "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
          margin: 0,
          padding: "2rem",
          background: "#0b1020",
          color: "#e6ecf5",
          minHeight: "100vh",
        }}
      >
        {children}
      </body>
    </html>
  );
}
