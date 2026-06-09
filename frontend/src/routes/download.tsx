import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { AuthLayout } from "./login";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/download")({
  component: DownloadPage,
});

interface PublicLatest {
  app_id: string;
  version: string;
  sha256: string;
  size_bytes: number | null;
  signed: boolean;
  uploaded_at: string | null;
  download_url: string;
}

const API_BASE =
  import.meta.env.VITE_API_BASE ?? `${import.meta.env.BASE_URL}api/v1`;

function fmtSize(bytes: number | null): string {
  if (!bytes) return "—";
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${(bytes / 1024).toFixed(0)} KB`;
}

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function DownloadPage() {
  const [latest, setLatest] = useState<PublicLatest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let abort = false;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/installers/hwax-agent/public-latest`);
        if (res.status === 404) {
          if (!abort) {
            setLatest(null);
            setError("아직 게시된 빌드가 없습니다. 운영자가 곧 배포할 예정입니다.");
          }
          return;
        }
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        const data = (await res.json()) as PublicLatest;
        if (!abort) setLatest(data);
      } catch (e) {
        if (!abort) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!abort) setLoading(false);
      }
    })();
    return () => {
      abort = true;
    };
  }, []);

  const downloadHref = latest ? `${API_BASE}/installers/hwax-agent/public-download` : "#";

  return (
    <AuthLayout>
      <Card>
        <CardHeader className="text-center">
          <CardTitle className="text-xl">HWAX Agent 다운로드</CardTitle>
          <CardDescription>
            윈도우 트레이 런처. 설치 후 사내 HEAXHub와 페어링하면 카탈로그의 도구를 받아 실행할 수 있습니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {loading && (
            <p className="text-center text-sm text-muted-foreground">최신 빌드를 확인 중…</p>
          )}

          {!loading && error && (
            <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-center text-sm">
              {error}
            </div>
          )}

          {!loading && latest && (
            <>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                <dt className="text-muted-foreground">버전</dt>
                <dd className="font-mono">{latest.version}</dd>

                <dt className="text-muted-foreground">크기</dt>
                <dd>{fmtSize(latest.size_bytes)}</dd>

                <dt className="text-muted-foreground">게시 일시</dt>
                <dd>{fmtDate(latest.uploaded_at)}</dd>

                <dt className="text-muted-foreground">서명</dt>
                <dd>{latest.signed ? "✓ 서명됨" : "—"}</dd>

                <dt className="text-muted-foreground">SHA-256</dt>
                <dd className="break-all font-mono text-[11px]">
                  {latest.sha256}
                </dd>
              </dl>

              <Button asChild className="w-full">
                <a href={downloadHref} download>
                  다운로드 (Windows x64)
                </a>
              </Button>

              <div className="text-xs leading-relaxed text-muted-foreground">
                설치 후 시스템 트레이의 런처 아이콘을 클릭하여 페어링 코드를 입력하세요. 페어링 토큰은 IT 운영자에게 요청해 받으세요.
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </AuthLayout>
  );
}
