import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { secretsApi } from "@/lib/api/secrets";
import type { Secret, SecretScope } from "@/lib/api/types";
import { formatDateTime } from "@/lib/utils/format";

const SCOPES: SecretScope[] = ["global", "app", "job"];

export function SecretsTable() {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<Secret | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<Secret | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["admin", "secrets"],
    queryFn: () => secretsApi.list(),
  });

  const remove = useMutation({
    mutationFn: (id: string) => secretsApi.remove(id),
    onSuccess: () => {
      toast.success("시크릿이 삭제되었습니다.");
      qc.invalidateQueries({ queryKey: ["admin", "secrets"] });
      setDeleting(null);
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "삭제 실패"),
  });

  if (isLoading) return <Skeleton className="h-96 w-full" />;
  if (error) {
    return (
      <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-6 text-sm">
        <p className="font-medium">아직 사용할 수 없습니다.</p>
        <p className="mt-1 text-muted-foreground">
          백엔드 `/admin/secrets` 엔드포인트가 준비되면 자동으로 표시됩니다.
        </p>
      </div>
    );
  }

  const items = data?.items ?? [];

  return (
    <>
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          저장 후에는 값이 다시 표시되지 않습니다. 분실 시 새로 발급해야 합니다.
        </p>
        <Button onClick={() => setCreating(true)}>
          <Plus className="mr-1 h-4 w-4" /> 새 시크릿
        </Button>
      </div>

      <div className="rounded-lg border bg-card">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>키</TableHead>
              <TableHead>범위</TableHead>
              <TableHead>대상</TableHead>
              <TableHead>설명</TableHead>
              <TableHead>생성</TableHead>
              <TableHead className="w-24 text-right">관리</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((s) => (
              <TableRow key={s.id}>
                <TableCell className="font-mono text-xs">{s.key}</TableCell>
                <TableCell>
                  <Badge variant="muted">{s.scope}</Badge>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {s.scope_ref ?? "—"}
                </TableCell>
                <TableCell className="text-sm">{s.description ?? "—"}</TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {formatDateTime(s.created_at)}
                </TableCell>
                <TableCell className="text-right">
                  <Button
                    size="icon"
                    variant="ghost"
                    onClick={() => setEditing(s)}
                    aria-label="값 변경"
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    onClick={() => setDeleting(s)}
                    aria-label="삭제"
                  >
                    <Trash2 className="h-3.5 w-3.5 text-rose-600" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
            {items.length === 0 && (
              <TableRow>
                <TableCell colSpan={6} className="py-12 text-center text-sm text-muted-foreground">
                  등록된 시크릿이 없습니다.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      {creating && <SecretCreateDialog onClose={() => setCreating(false)} />}
      {editing && <SecretEditDialog secret={editing} onClose={() => setEditing(null)} />}

      <Dialog open={Boolean(deleting)} onOpenChange={(o) => !o && setDeleting(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>시크릿 삭제</DialogTitle>
            <DialogDescription>
              `{deleting?.key}` 를 삭제하면 이 값을 사용하는 작업이 실패할 수 있습니다.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDeleting(null)}>
              취소
            </Button>
            <Button
              variant="destructive"
              onClick={() => deleting && remove.mutate(deleting.id)}
              disabled={remove.isPending}
            >
              삭제
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function SecretCreateDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [scope, setScope] = useState<SecretScope>("global");
  const [scopeRef, setScopeRef] = useState("");
  const [description, setDescription] = useState("");

  const create = useMutation({
    mutationFn: () =>
      secretsApi.create({
        key,
        value,
        scope,
        scope_ref: scopeRef || null,
        description: description || null,
      }),
    onSuccess: () => {
      toast.success("시크릿이 저장되었습니다. 값은 더 이상 표시되지 않습니다.");
      qc.invalidateQueries({ queryKey: ["admin", "secrets"] });
      onClose();
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "저장 실패"),
  });

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>새 시크릿</DialogTitle>
          <DialogDescription>
            값은 AES-GCM으로 암호화 저장되며, 저장 후에는 다시 조회할 수 없습니다.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label>키</Label>
            <Input
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="DATABASE_URL"
            />
          </div>
          <div>
            <Label>값</Label>
            <Textarea
              rows={3}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="* 저장 후 다시 표시되지 않습니다"
              className="font-mono text-xs"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>범위</Label>
              <Select value={scope} onValueChange={(v) => setScope(v as SecretScope)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SCOPES.map((s) => (
                    <SelectItem key={s} value={s}>
                      {s}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>대상 (선택)</Label>
              <Input
                value={scopeRef}
                onChange={(e) => setScopeRef(e.target.value)}
                placeholder={scope === "app" ? "app_id" : scope === "job" ? "job_id" : ""}
                disabled={scope === "global"}
              />
            </div>
          </div>
          <div>
            <Label>설명</Label>
            <Input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="용도 메모"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            취소
          </Button>
          <Button
            disabled={!key || !value || create.isPending}
            onClick={() => create.mutate()}
          >
            저장
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SecretEditDialog({ secret, onClose }: { secret: Secret; onClose: () => void }) {
  const qc = useQueryClient();
  const [value, setValue] = useState("");
  const [description, setDescription] = useState(secret.description ?? "");

  const update = useMutation({
    mutationFn: () =>
      secretsApi.update(secret.id, {
        value: value || undefined,
        description: description || null,
      }),
    onSuccess: () => {
      toast.success("시크릿이 갱신되었습니다.");
      qc.invalidateQueries({ queryKey: ["admin", "secrets"] });
      onClose();
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "갱신 실패"),
  });

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>시크릿 갱신 — {secret.key}</DialogTitle>
          <DialogDescription>
            값을 비워두면 기존 값이 유지됩니다. 새 값을 입력하면 즉시 교체됩니다.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label>새 값 (선택)</Label>
            <Textarea
              rows={3}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="비워두면 변경 안 함"
              className="font-mono text-xs"
            />
          </div>
          <div>
            <Label>설명</Label>
            <Input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            취소
          </Button>
          <Button onClick={() => update.mutate()} disabled={update.isPending}>
            저장
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
