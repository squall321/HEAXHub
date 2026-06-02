import { motion, useReducedMotion } from "framer-motion";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils/cn";

type Size = "sm" | "md" | "lg";

// Full → collapsed mapping. Every char in FULL maps to either:
//   - a slot in the final short form ("H","E","A","X","H","u","b" 그리고 공백 1개)
//   - or fades out as a "filler" character.
// The trick: we render the full string, then animate every filler char's
// width → 0 + opacity → 0, leaving exactly the short form behind.
const FULL = "Hardware Engineering AI Transformation Hub";
//             ↑           ↑          ↑↑ ↑              ↑↑↑
//             H           E          AX (with space)   Hub
// Char positions kept in the final short form:
//   0  'H' (Hardware)
//   9  'E' (Engineering)  — actual 9 (after "Hardware "+space at 8)
//   21 'A' (AI)
//   22 'I' → rendered as 'X' via OVERRIDES
//   23 ' ' (between "AI" and "Transformation")
//   39 'H' (Hub)
//   40 'u'
//   41 'b'
const SHORT_CHARS = new Set([0, 9, 21, 22, 23, 39, 40, 41]);

// Character override: index 22 displays "X" not "I".
const OVERRIDES: Record<number, string> = { 22: "X" };
// Indices that should render with the amber accent color.
const ACCENT_INDICES = new Set([21, 22]);

interface BrandLogoProps {
  /** Size preset. */
  size?: Size;
  /** Force a static short form (no animation). Useful in nav headers. */
  staticShort?: boolean;
  /** Class applied to the outer span. */
  className?: string;
  /** Tone preset for color. */
  tone?: "light" | "dark";
}

const SIZE_CLASS: Record<Size, string> = {
  sm: "text-base",
  md: "text-2xl md:text-3xl",
  lg: "text-3xl md:text-5xl lg:text-6xl",
};

export function BrandLogo({
  size = "md",
  staticShort = false,
  className,
  tone = "dark",
}: BrandLogoProps) {
  const prefersReducedMotion = useReducedMotion();
  const [hasPlayed, setHasPlayed] = useState(true);

  useEffect(() => {
    if (staticShort) return;
    try {
      const key = "heaxhub.brandlogo.played";
      const played = sessionStorage.getItem(key) === "1";
      setHasPlayed(played);
      if (!played) sessionStorage.setItem(key, "1");
    } catch {
      setHasPlayed(false);
    }
  }, [staticShort]);

  const shouldAnimate = !staticShort && !prefersReducedMotion && !hasPlayed;

  const toneClass =
    tone === "light"
      ? "text-slate-900 dark:text-white"
      : "text-white";

  if (!shouldAnimate) {
    return (
      <span
        className={cn(
          "inline-flex items-baseline gap-[0.05em] font-black tracking-tight",
          SIZE_CLASS[size],
          toneClass,
          className,
        )}
        aria-label="HEAXHub — Hardware Engineering AI Transformation Hub"
      >
        <span>HE</span>
        <span className="text-amber-300">AX</span>
        <span>Hub</span>
      </span>
    );
  }

  return (
    <motion.span
      className={cn(
        "inline-flex items-baseline whitespace-nowrap font-black tracking-tight",
        SIZE_CLASS[size],
        toneClass,
        className,
      )}
      aria-label="HEAXHub — Hardware Engineering AI Transformation Hub"
      initial={false}
    >
      {FULL.split("").map((ch, i) => {
        const keep = SHORT_CHARS.has(i);
        const display = OVERRIDES[i] ?? ch;
        const isAccent = ACCENT_INDICES.has(i);

        if (keep) {
          return (
            <motion.span
              key={i}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{
                delay: 0.05 * i + 0.3,
                duration: 0.4,
                ease: "easeOut",
              }}
              className={cn(isAccent && "text-amber-300")}
              style={{ display: "inline-block", whiteSpace: "pre" }}
            >
              {display}
            </motion.span>
          );
        }

        // Filler char: appear with the full word, then collapse width+opacity.
        return (
          <motion.span
            key={i}
            initial={{ opacity: 0, width: "auto" }}
            animate={{
              opacity: [0, 1, 1, 0],
              width: ["0ch", "auto", "auto", "0ch"],
            }}
            transition={{
              times: [0, 0.15, 0.55, 0.85],
              duration: 2.8,
              ease: "easeInOut",
            }}
            style={{
              display: "inline-block",
              overflow: "hidden",
              whiteSpace: "pre",
            }}
          >
            {ch}
          </motion.span>
        );
      })}
    </motion.span>
  );
}
