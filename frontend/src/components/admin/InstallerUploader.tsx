import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Trash2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { installersApi } from "@/lib/api/agents";
import { formatBytes, formatDateTime } from "@/lib/utils/format";

interface InstallerUploaderProps {
  appId: string;
}

export function InstallerUploader({ appId }: InstallerUploaderProps) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [os, setOs] = useState("windows-x64");
  const [version, setVersion] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["apps", appId, "installers"],
    queryFn: () => installersApi.list(appId),
  });

  const upload = useMutation({
    mutationFn: (fd: FormData) => installersApi.upload(appId, fd),
    onSuccess: () => {
      toast.success("설치 파일을 업로드했습니다.");
      qc.invalidateQueries({ queryKey: ["apps", appId, "installers"] });
      setVersion("");
      if (fileRef.current) fileRef.current.value = "";
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "업로드 실패"),
  });

  const remove = useMutation({
    mutationFn: (id: string) => installersApi.remove(appId, id),
    onSuccess: () => {
      toast.success("삭제되었습니다.");
      qc.invalidateQueries({ queryKey: ["apps", appId, "installers"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "삭제 실패"),
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) {
      toast.error("파일을 선택하세요.");
      return;
    }
    if (!version) {
      toast.error("버전을 입력하세요.");
      return;
    }
    const fd = new FormData();
    fd.append("file", file);
    fd.append("os", os);
    fd.append("version", version);
    upload.mutate(fd);
  }

  if (isLoading) return <Skeleton className="h-72 w-full" />;
  if (error) {
    return (
      <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-6 text-sm">
        <p className="font-medium">아직 사용할 수 없습니다.</p>
        <p className="mt-1 text-muted-foreground">
          백엔드 `/apps/{appId}/installers` 엔드포인트가 준비되면 표시됩니다.
        </p>
      </div>
    );
  }

  const items = data ?? [];

  return (
    <div className="space-y-4">
      <form onSubmit={handleSubmit} className="rounded-lg border bg-card p-4">
        <div className="grid gap-3 sm:grid-cols-[2fr_1fr_1fr_auto] sm:items-end">
          <div>
            <Label>설치 파일</Label>
            <Input ref={fileRef} type="file" accept=".exe,.msi,.zip" />
          </div>
          <div>
            <Label>OS</Label>
            <Input value={os} onChange={(e) => setOs(e.target.value)} />
          </div>
          <div>
            <Label>버전</Label>
            <Input
              value={version}
              onChange={(e) => setVersion(e.target.value)}
              placeholder="1.4.2"
            />
          </div>
          <Button type="submit" disabled={upload.isPending}>
            <Upload className="mr-1 h-4 w-4" />
            업로드
          </Button>
        </div>
      </form>

      <div className="rounded-lg border bg-card">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>파일</TableHead>
              <TableHead>OS</TableHead>
              <TableHead>버전</TableHead>
              <TableHead>크기</TableHead>
              <TableHead>SHA256</TableHead>
              <TableHead>업로드</TableHead>
              <TableHead className="w-24 text-right">관리</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((p) => (
              <TableRow key={p.id}>
                <TableCell className="font-mono text-xs">{p.filename}</TableCell>
                <TableCell>
                  <Badge variant="muted">{p.os}</Badge>
                </TableCell>
                <TableCell className="text-xs">{p.version}</TableCell>
                <TableCell className="text-xs">{formatBytes(p.size_bytes)}</TableCell>
                <TableCell
                  className="max-w-[18ch] truncate font-mono text-[10px] text-muted-foreground"
                  title={p.sha256}
                >
                  {p.sha256}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {formatDateTime(p.uploaded_at)}
                </TableCell>
                <TableCell className="text-right">
                  <Button size="icon" variant="ghost" asChild>
                    <a href={p.download_url} download>
                      <Download className="h-3.5 w-3.5" />
                    </a>
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    onClick={() => remove.mutate(p.id)}
                    disabled={remove.isPending}
                  >
                    <Trash2 className="h-3.5 w-3.5 text-rose-600" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
            {items.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} className="py-12 text-center text-sm text-muted-foreground">
                  업로드된 설치 파일이 없습니다.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
