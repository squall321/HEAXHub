import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { InstallerUploader } from "@/components/admin/InstallerUploader";
import { RequireAuth } from "@/components/common/RequireAuth";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { appsApi } from "@/lib/api/apps";

export const Route = createFileRoute("/admin/installers")({
  component: () => (
    <RequireAuth roles={["admin"]}>
      <AdminInstallersPage />
    </RequireAuth>
  ),
});

function AdminInstallersPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["apps", { app_type: "windows_gui" }],
    queryFn: () => appsApi.list({ app_type: "windows_gui", page_size: 200 }),
  });
  const [appId, setAppId] = useState<string | null>(null);

  return (
    <div className="mx-auto max-w-7xl px-6 py-8 md:px-10">
      <h1 className="text-3xl font-bold tracking-tight">설치 파일</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Windows GUI 앱을 위한 설치 패키지 (.exe / .msi / .zip) 업로드 및 SHA256 관리.
      </p>

      <div className="mt-6 space-y-4">
        <Card>
          <CardContent className="flex items-center gap-3 p-4">
            <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              앱 선택
            </label>
            {isLoading ? (
              <Skeleton className="h-9 w-64" />
            ) : (
              <Select value={appId ?? ""} onValueChange={(v) => setAppId(v || null)}>
                <SelectTrigger className="w-80">
                  <SelectValue placeholder="windows_gui 앱을 선택하세요" />
                </SelectTrigger>
                <SelectContent>
                  {(data?.items ?? []).map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {a.name} ({a.id})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </CardContent>
        </Card>

        {appId && <InstallerUploader appId={appId} />}
      </div>
    </div>
  );
}
