import { useEffect, useState } from "react";
import { AlertCircle, Check } from "lucide-react";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils/cn";

interface YamlEditorProps {
  value: string;
  onChange: (next: string) => void;
  rows?: number;
  placeholder?: string;
  className?: string;
}

/**
 * Lightweight YAML textarea with minimal structural validation.
 *
 * NOTE: We deliberately do NOT pull in Monaco — too heavy for an admin tab.
 * We perform shallow checks: empty input is allowed; lines must look like
 * `key: value`, or list items `- ...`, or indented continuations. Anything
 * else flagged as a warning, not an error (operator can still save).
 */
export function YamlEditor({
  value,
  onChange,
  rows = 16,
  placeholder,
  className,
}: YamlEditorProps) {
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(validateShallow(value));
  }, [value]);

  return (
    <div className={cn("space-y-1.5", className)}>
      <Textarea
        rows={rows}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder ?? "schema_version: 2\nname: my-app\n..."}
        className="font-mono text-xs"
        spellCheck={false}
      />
      <div className="flex items-center gap-1.5 text-xs">
        {error ? (
          <>
            <AlertCircle className="h-3.5 w-3.5 text-amber-600" />
            <span className="text-amber-700 dark:text-amber-400">{error}</span>
          </>
        ) : (
          <>
            <Check className="h-3.5 w-3.5 text-emerald-600" />
            <span className="text-muted-foreground">표면 검사 통과 (서버에서 정밀 검증)</span>
          </>
        )}
      </div>
    </div>
  );
}

function validateShallow(text: string): string | null {
  if (!text.trim()) return null;
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (!line.trim()) continue;
    if (line.trimStart().startsWith("#")) continue;
    if (/^\s*-\s*/.test(line)) continue;
    if (/^\s*[A-Za-z0-9_./[\]"-]+\s*:/.test(line)) continue;
    if (/^\s+\S/.test(line)) continue; // continuation
    return `${i + 1}행이 YAML 형식이 아닐 수 있습니다`;
  }
  return null;
}
