import { Link } from "@tanstack/react-router";
import { motion } from "framer-motion";
import { ArrowUpRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { categoryLabel, colors } from "@/styles/tokens";
import type { AppSummary } from "@/lib/api/types";
import { timeAgo } from "@/lib/utils/format";
import { StatusBadge } from "./StatusBadge";

interface AppCardProps {
  app: AppSummary;
  compact?: boolean;
}

export function AppCard({ app, compact = false }: AppCardProps) {
  const accent = colors.category[app.app_type] ?? colors.category.cli_tool;

  return (
    <motion.div
      whileHover={{ y: -2 }}
      transition={{ type: "spring", stiffness: 220, damping: 18 }}
    >
      <Link to="/apps/$appId" params={{ appId: app.id }} className="block">
        <Card
          className="group h-full overflow-hidden border-t-[3px] hover:shadow-lg"
          style={{ borderTopColor: accent }}
        >
          <div className="flex h-full flex-col p-5">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span
                    className="rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider"
                    style={{ background: `${accent}1f`, color: accent }}
                  >
                    {categoryLabel[app.app_type]}
                  </span>
                  <StatusBadge status={app.status} />
                </div>
                <h3 className="mt-2 truncate text-base font-bold tracking-tight">{app.name}</h3>
                <p className="mt-0.5 truncate text-xs text-muted-foreground">{app.id}</p>
              </div>
              <ArrowUpRight className="h-4 w-4 text-muted-foreground transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-foreground" />
            </div>

            {!compact && (
              <p className="mt-3 line-clamp-2 text-sm text-muted-foreground">
                {app.description ?? "설명이 없습니다."}
              </p>
            )}

            <div className="mt-auto flex items-center justify-between pt-4 text-xs text-muted-foreground">
              <div className="flex items-center gap-1.5">
                {app.current_version && (
                  <span className="font-mono">v{app.current_version}</span>
                )}
                {app.tags?.slice(0, 2).map((tag) => (
                  <Badge key={tag} variant="muted" className="text-[10px]">
                    {tag}
                  </Badge>
                ))}
              </div>
              <span>{timeAgo(app.updated_at)}</span>
            </div>
          </div>
        </Card>
      </Link>
    </motion.div>
  );
}
