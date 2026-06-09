import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plus, Trash2 } from "lucide-react";
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
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { agentsApi } from "@/lib/api/agents";
import type { WindowsAgent, WindowsAgentIssueResponse } from "@/lib/api/types";
import { formatDateTime, timeAgo } from "@/lib/utils/format";
import { AgentTokenDialog } from "./AgentTokenDialog";

const COL_COUNT = 9;

export function AgentsTable() {
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [tokenResult, setTokenResult] = useState<WindowsAgentIssueResponse | null>(null);
  const [deleting, setDeleting] = useState<WindowsAgent | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["admin", "agents"],
    queryFn: () => agentsApi.list(),
    refetchInterval: 15_000,
  });

  const rotate = useMutation({
    mutationFn: (id: string) => agentsApi.rotateToken(id),
    onSuccess: (res) => {
      toast.success("새 등록 토큰을 발급했습니다.");
      setTokenResult(res);
      qc.invalidateQueries({ queryKey: ["admin", "agents"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "토큰 재발급 실패"),
  });

  const remove = useMutation({
    mutationFn: (id: string) => agentsApi.remove(id),
    onSuccess: () => {
      toast.success("에이전트가 삭제되었습니다.");
      qc.invalidateQueries({ queryKey: ["admin", "agents"] });
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
          백엔드 `/admin/agents` 엔드포인트가 준비되면 표시됩니다.
        </p>
      </div>
    );
  }

  const items = data ?? [];

  return (
    <>
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          Windows EXE 배포 대상 PC. 등록 토큰은 발급 시 단 한 번만 표시됩니다.
        </p>
        <Button onClick={() => setCreating(true)}>
          <Plus className="mr-1 h-4 w-4" /> 신규 발급
        </Button>
      </div>

      <div className="rounded-lg border bg-card">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>이름</TableHead>
              <TableHead>풀</TableHead>
              <TableHead>호스트명</TableHead>
              <TableHead>종류</TableHead>
              <TableHead>버전</TableHead>
              <TableHead>상태</TableHead>
              <TableHead>마지막 접속</TableHead>
              <TableHead>등록</TableHead>
              <TableHead className="w-32 text-right">관리</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((a) => (
              <TableRow key={a.id}>
                <TableCell className="font-medium text-xs">{a.name}</TableCell>
                <TableCell className="text-xs">{a.pool}</TableCell>
                <TableCell className="font-mono text-xs">{a.hostname ?? "—"}</TableCell>
                <TableCell className="text-xs">{a.device_kind ?? "—"}</TableCell>
                <TableCell className="text-xs">{a.agent_version ?? "—"}</TableCell>
                <TableCell>
                  <Badge
                    variant={
                      a.disabled
                        ? "destructive"
                        : a.status === "online"
                          ? "success"
                          : a.status === "busy"
                            ? "default"
                            : "muted"
                    }
                  >
                    {a.disabled ? "비활성" : a.status}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {a.last_seen ? timeAgo(a.last_seen) : "—"}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {formatDateTime(a.created_at)}
                </TableCell>
                <TableCell className="text-right">
                  <Button
                    size="icon"
                    variant="ghost"
                    onClick={() => rotate.mutate(a.id)}
                    disabled={rotate.isPending}
                    aria-label="토큰 재발급"
                  >
                    <KeyRound className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    onClick={() => setDeleting(a)}
                    aria-label="삭제"
                  >
                    <Trash2 className="h-3.5 w-3.5 text-rose-600" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
            {items.length === 0 && (
              <TableRow>
                <TableCell colSpan={COL_COUNT} className="py-12 text-center text-sm text-muted-foreground">
                  등록된 Windows Agent가 없습니다.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      {creating && (
        <AgentCreateDialog
          onClose={() => setCreating(false)}
          onIssued={(r) => {
            setCreating(false);
            setTokenResult(r);
          }}
        />
      )}

      {tokenResult && (
        <AgentTokenDialog result={tokenResult} onClose={() => setTokenResult(null)} />
      )}

      <Dialog open={Boolean(deleting)} onOpenChange={(o) => !o && setDeleting(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>에이전트 삭제</DialogTitle>
            <DialogDescription>
              `{deleting?.name}` 을 삭제하면 이 PC에서는 더 이상 작업을 수신하지 못합니다.
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

function AgentCreateDialog({
  onClose,
  onIssued,
}: {
  onClose: () => void;
  onIssued: (result: WindowsAgentIssueResponse) => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [pool, setPool] = useState("hwax-launcher");
  const [hostname, setHostname] = useState("");

  const create = useMutation({
    mutationFn: () =>
      agentsApi.create({
        name,
        pool,
        hostname: hostname || undefined,
        device_kind: "launcher",
      }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["admin", "agents"] });
      onIssued(r);
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "발급 실패"),
  });

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Windows Agent 등록</DialogTitle>
          <DialogDescription>
            등록 후 표시되는 토큰을 Agent 설치 마법사에 입력하세요.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label>이름</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="ws-홍길동 / 부서-용도 (고유)"
            />
          </div>
          <div>
            <Label>풀</Label>
            <Input
              value={pool}
              onChange={(e) => setPool(e.target.value)}
              placeholder="hwax-launcher"
            />
          </div>
          <div>
            <Label>호스트명 (선택)</Label>
            <Input
              value={hostname}
              onChange={(e) => setHostname(e.target.value)}
              placeholder="WORKSTATION-01"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            취소
          </Button>
          <Button
            disabled={!name || !pool || create.isPending}
            onClick={() => create.mutate()}
          >
            발급
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
