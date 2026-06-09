import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { WindowsAgentIssueResponse } from "@/lib/api/types";

interface AgentTokenDialogProps {
  result: WindowsAgentIssueResponse;
  onClose: () => void;
}

/**
 * One-time enrollment token display.
 * The plaintext token is shown ONLY here, immediately after creation/rotation.
 */
export function AgentTokenDialog({ result, onClose }: AgentTokenDialogProps) {
  const [copied, setCopied] = useState(false);

  async function copyToken() {
    try {
      await navigator.clipboard.writeText(result.token);
      setCopied(true);
      toast.success("토큰이 복사되었습니다.");
    } catch {
      toast.error("클립보드 복사 실패");
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>등록 토큰 발급 — {result.agent.name}</DialogTitle>
          <DialogDescription>
            이 토큰은 단 한 번만 표시됩니다. Windows Agent 설치 시 한 번 입력하면 그 이후로는
            불필요합니다. 분실 시 새로 발급해야 합니다.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Enrollment Token
          </label>
          <div className="flex items-center gap-2 rounded-md border bg-muted/30 p-3">
            <code className="flex-1 break-all font-mono text-xs">{result.token}</code>
            <Button size="sm" variant="outline" onClick={copyToken}>
              {copied ? (
                <Check className="h-3.5 w-3.5" />
              ) : (
                <Copy className="h-3.5 w-3.5" />
              )}
            </Button>
          </div>
          <p className="text-xs text-rose-700 dark:text-rose-400">
            창을 닫으면 토큰을 다시 볼 수 없습니다. 안전한 곳에 저장하세요.
          </p>
        </div>

        <DialogFooter>
          <Button onClick={onClose}>확인했습니다</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
