import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { Job } from "@/lib/api/types";
import { formatDateTime, formatDuration } from "@/lib/utils/format";
import { JobStatusBadge } from "./JobStatusBadge";

interface JobTableProps {
  jobs?: Job[];
  isLoading?: boolean;
  showApp?: boolean;
}

export function JobTable({ jobs, isLoading, showApp = true }: JobTableProps) {
  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }
  if (!jobs || jobs.length === 0) {
    return (
      <div className="rounded-lg border border-dashed bg-card/40 py-12 text-center text-sm text-muted-foreground">
        실행 이력이 없습니다.
      </div>
    );
  }
  return (
    <div className="rounded-lg border bg-card">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Job ID</TableHead>
            {showApp && <TableHead>앱</TableHead>}
            <TableHead>상태</TableHead>
            <TableHead>시작</TableHead>
            <TableHead>소요</TableHead>
            <TableHead className="w-12" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {jobs.map((j) => (
            <TableRow key={j.id} className="cursor-pointer">
              <TableCell>
                <Link
                  to="/jobs/$jobId"
                  params={{ jobId: j.id }}
                  className="font-mono text-xs hover:underline"
                >
                  {j.id}
                </Link>
              </TableCell>
              {showApp && (
                <TableCell>
                  <Link
                    to="/apps/$appId"
                    params={{ appId: j.app_id }}
                    className="text-sm font-medium hover:underline"
                  >
                    {j.app_name ?? j.app_id}
                  </Link>
                </TableCell>
              )}
              <TableCell>
                <JobStatusBadge status={j.status} />
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">
                {formatDateTime(j.started_at ?? j.created_at)}
              </TableCell>
              <TableCell className="font-mono text-xs">{formatDuration(j.duration_sec)}</TableCell>
              <TableCell>
                <Link to="/jobs/$jobId" params={{ jobId: j.id }}>
                  <ChevronRight className="h-4 w-4 text-muted-foreground" />
                </Link>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
