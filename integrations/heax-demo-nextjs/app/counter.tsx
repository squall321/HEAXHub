"use client";

import { useState } from "react";

export default function Counter() {
  const [count, setCount] = useState(0);
  return (
    <div
      style={{
        display: "inline-flex",
        gap: "0.75rem",
        alignItems: "center",
        padding: "0.75rem 1rem",
        background: "#16204a",
        borderRadius: "0.5rem",
        border: "1px solid #2a376e",
      }}
    >
      <button
        type="button"
        onClick={() => setCount((c) => c - 1)}
        style={btnStyle}
      >
        −
      </button>
      <span style={{ minWidth: "3ch", textAlign: "center", fontVariantNumeric: "tabular-nums" }}>
        {count}
      </span>
      <button
        type="button"
        onClick={() => setCount((c) => c + 1)}
        style={btnStyle}
      >
        +
      </button>
    </div>
  );
}

const btnStyle: React.CSSProperties = {
  background: "#3850c4",
  color: "#fff",
  border: "none",
  borderRadius: "0.375rem",
  padding: "0.25rem 0.75rem",
  fontSize: "1rem",
  cursor: "pointer",
};
