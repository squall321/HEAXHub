import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import type { Manifest } from "@/lib/api/types";
import { categoryLabel, colors } from "@/styles/tokens";

export function ManifestPreview({ manifest }: { manifest: Manifest }) {
  const accent = colors.category[manifest.app_type] ?? colors.category.cli_tool;
  return (
    <Card className="overflow-hidden" style={{ borderTop: `3px solid ${accent}` }}>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle>{manifest.name}</CardTitle>
          <Badge variant="muted">v{manifest.version}</Badge>
          <Badge variant="info">{categoryLabel[manifest.app_type]}</Badge>
          <Badge variant="secondary">{manifest.execution_target}</Badge>
        </div>
        {manifest.description && (
          <p className="mt-2 whitespace-pre-line text-sm text-muted-foreground">
            {manifest.description}
          </p>
        )}
      </CardHeader>
      <Separator />
      <CardContent className="space-y-4 pt-6">
        <Spec label="ID" value={<code>{manifest.id}</code>} />
        <Spec label="Owner" value={manifest.owner} />
        <Spec label="Launch" value={<code>{manifest.launch.mode}</code>} />
        {manifest.launch.command && (
          <Spec label="Command" value={<code className="whitespace-pre">{manifest.launch.command}</code>} />
        )}
        {manifest.launch.url && <Spec label="URL" value={<code>{manifest.launch.url}</code>} />}

        {manifest.inputs && manifest.inputs.length > 0 && (
          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              입력
            </div>
            <div className="space-y-1.5">
              {manifest.inputs.map((inp) => (
                <div
                  key={inp.name}
                  className="flex items-center gap-2 rounded-md border bg-muted/30 px-3 py-2 text-sm"
                >
                  <code className="font-mono text-xs">{inp.name}</code>
                  <Badge variant="outline" className="text-[10px]">
                    {inp.type}
                  </Badge>
                  {inp.required && (
                    <Badge variant="warning" className="text-[10px]">
                      required
                    </Badge>
                  )}
                  {inp.label && <span className="text-muted-foreground">— {inp.label}</span>}
                </div>
              ))}
            </div>
          </div>
        )}

        {manifest.outputs && manifest.outputs.length > 0 && (
          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              출력
            </div>
            <ul className="space-y-1 text-sm">
              {manifest.outputs.map((o) => (
                <li key={o.name} className="flex items-center gap-2">
                  <code className="font-mono text-xs">{o.name}</code>
                  <span className="text-muted-foreground">{o.path}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {manifest.resources && (
          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              리소스
            </div>
            <div className="flex flex-wrap gap-2 text-xs">
              {manifest.resources.cpu && (
                <Badge variant="muted">CPU {manifest.resources.cpu}</Badge>
              )}
              {manifest.resources.memory_gb && (
                <Badge variant="muted">RAM {manifest.resources.memory_gb}GB</Badge>
              )}
              {manifest.resources.gpu && <Badge variant="info">GPU</Badge>}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Spec({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[120px_1fr] gap-3 text-sm">
      <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div>{value}</div>
    </div>
  );
}
