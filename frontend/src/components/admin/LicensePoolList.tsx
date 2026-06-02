import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { licensesApi } from "@/lib/api/licenses";
import type { LicensePool } from "@/lib/api/types";
import { formatDateTime } from "@/lib/utils/format";
import { LicenseUsageChart } from "./LicenseUsageChart";

export function LicensePoolList() {
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<LicensePool | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["admin", "licenses"],
    queryFn: () => licensesApi.list(),
  });

  const remove = useMutation({
    mutationFn: (name: string) => licensesApi.remove(name),
    onSuccess: () => {
      toast.success("라이선스 풀이 삭제되었습니다.");
      qc.invalidateQueries({ queryKey: ["admin", "licenses"] });
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
          백엔드 `/admin/licenses` 엔드포인트가 준비되면 표시됩니다.
        </p>
      </div>
    );
  }

  const pools = data ?? [];

  return (
    <>
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          FlexLM/RLM 풀별 토큰 점유 현황과 24시간 사용 추이.
        </p>
        <Button onClick={() => setCreating(true)}>
          <Plus className="mr-1 h-4 w-4" /> 새 풀
        </Button>
      </div>

      <div className="space-y-3">
        {pools.map((p) => {
          const ratio = p.total_tokens === 0 ? 0 : (p.in_use_tokens / p.total_tokens) * 100;
          const open = expanded === p.name;
          return (
            <Card key={p.id}>
              <CardContent className="space-y-3 p-5">
                <div className="flex items-start justify-between gap-3">
                  <button
                    type="button"
                    className="flex flex-1 items-center gap-2 text-left"
                    onClick={() => setExpanded(open ? null : p.name)}
                  >
                    {open ? (
                      <ChevronDown className="h-4 w-4 text-muted-foreground" />
                    ) : (
                      <ChevronRight className="h-4 w-4 text-muted-foreground" />
                    )}
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-semibold">{p.name}</span>
                        {p.blocking && <Badge variant="warning">blocking</Badge>}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {[p.vendor, p.feature].filter(Boolean).join(" / ") || "vendor 정보 없음"}
                      </div>
                    </div>
                  </button>
                  <div className="text-right">
                    <div className="text-sm font-medium">
                      {p.in_use_tokens} / {p.total_tokens}
                    </div>
                    <Progress value={ratio} className="mt-1 w-32" />
                  </div>
                  <Button
                    size="icon"
                    variant="ghost"
                    onClick={() => setDeleting(p)}
                    aria-label="삭제"
                  >
                    <Trash2 className="h-3.5 w-3.5 text-rose-600" />
                  </Button>
                </div>

                {open && (
                  <div className="space-y-3 border-t pt-3">
                    <div className="flex items-center justify-between text-xs text-muted-foreground">
                      <span>24시간 사용량 추세</span>
                      <LicenseUsageChart name={p.name} />
                    </div>
                    <HoldingsTable name={p.name} />
                  </div>
                )}
              </CardContent>
            </Card>
          );
        })}
        {pools.length === 0 && (
          <Card>
            <CardContent className="py-12 text-center text-sm text-muted-foreground">
              등록된 라이선스 풀이 없습니다.
            </CardContent>
          </Card>
        )}
      </div>

      {creating && <PoolCreateDialog onClose={() => setCreating(false)} />}

      <Dialog open={Boolean(deleting)} onOpenChange={(o) => !o && setDeleting(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>라이선스 풀 삭제</DialogTitle>
            <DialogDescription>
              `{deleting?.name}` 풀을 사용하는 앱은 다음 실행부터 토큰을 획득할 수 없습니다.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDeleting(null)}>
              취소
            </Button>
            <Button
              variant="destructive"
              onClick={() => deleting && remove.mutate(deleting.name)}
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

function HoldingsTable({ name }: { name: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["admin", "licenses", name, "holdings"],
    queryFn: () => licensesApi.holdings(name),
    refetchInterval: 10_000,
  });

  if (isLoading) return <Skeleton className="h-16 w-full" />;
  const items = data ?? [];
  if (items.length === 0) {
    return (
      <div className="rounded-md bg-muted/30 p-3 text-xs text-muted-foreground">
        현재 점유 중인 작업이 없습니다.
      </div>
    );
  }
  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Job ID</TableHead>
            <TableHead>앱</TableHead>
            <TableHead>토큰</TableHead>
            <TableHead>점유 시각</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((h) => (
            <TableRow key={h.id}>
              <TableCell className="font-mono text-xs">{h.job_id.slice(0, 8)}</TableCell>
              <TableCell className="text-xs">{h.job_app_id ?? "—"}</TableCell>
              <TableCell>{h.tokens}</TableCell>
              <TableCell className="text-xs text-muted-foreground">
                {formatDateTime(h.acquired_at)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function PoolCreateDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [vendor, setVendor] = useState("");
  const [feature, setFeature] = useState("");
  const [totalTokens, setTotalTokens] = useState(4);
  const [description, setDescription] = useState("");
  const [blocking, setBlocking] = useState(true);

  const create = useMutation({
    mutationFn: () =>
      licensesApi.create({
        name,
        vendor: vendor || undefined,
        feature: feature || undefined,
        total_tokens: totalTokens,
        description: description || undefined,
        blocking,
      }),
    onSuccess: () => {
      toast.success("라이선스 풀이 생성되었습니다.");
      qc.invalidateQueries({ queryKey: ["admin", "licenses"] });
      onClose();
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "생성 실패"),
  });

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>새 라이선스 풀</DialogTitle>
          <DialogDescription>
            manifest의 `license.pool` 값과 일치하는 이름을 사용하세요.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label>풀 이름</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="lsdyna-mpp"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>벤더</Label>
              <Input
                value={vendor}
                onChange={(e) => setVendor(e.target.value)}
                placeholder="ANSYS"
              />
            </div>
            <div>
              <Label>Feature</Label>
              <Input
                value={feature}
                onChange={(e) => setFeature(e.target.value)}
                placeholder="lsdyna_mpp"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>총 토큰 수</Label>
              <Input
                type="number"
                value={totalTokens}
                min={1}
                onChange={(e) => setTotalTokens(Number(e.target.value))}
              />
            </div>
            <div className="flex flex-col">
              <Label className="mb-2">Blocking 큐</Label>
              <div className="flex h-9 items-center gap-2 rounded-md border px-3">
                <Switch checked={blocking} onCheckedChange={setBlocking} />
                <span className="text-xs text-muted-foreground">
                  {blocking ? "대기 후 실행" : "즉시 실패"}
                </span>
              </div>
            </div>
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
          <Button
            disabled={!name || totalTokens < 1 || create.isPending}
            onClick={() => create.mutate()}
          >
            저장
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
