import { Link, useNavigate } from "@tanstack/react-router";
import { LogIn, LogOut, Search, Settings, User as UserIcon } from "lucide-react";
import { useEffect, useState } from "react";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { BrandLogo } from "@/components/common/BrandLogo";
import { ThemeToggle } from "@/components/common/ThemeToggle";
import { useAuth } from "@/lib/auth/useAuth";

export function Header() {
  const { user, isLoggedIn, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, []);

  return (
    <header className="sticky top-0 z-40 w-full border-b bg-background/85 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="flex h-14 items-center justify-between gap-4 px-4 md:px-6">
        <div className="flex items-center gap-6">
          <Link to="/" className="flex items-center gap-2">
            <div
              className="h-7 w-7 rounded-md"
              style={{
                background:
                  "linear-gradient(135deg,#020617 0%,#1e1b4b 50%,#4338ca 100%)",
              }}
            />
            <BrandLogo size="sm" staticShort tone="light" />
            <span className="hidden rounded-full bg-amber-500/20 px-2 py-0.5 text-[10px] font-bold text-amber-600 dark:text-amber-300 md:inline">
              v0.1
            </span>
          </Link>
        </div>

        <button
          type="button"
          onClick={() => setOpen(true)}
          className="hidden flex-1 max-w-md items-center gap-2 rounded-lg border bg-muted/40 px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted md:flex"
        >
          <Search className="h-4 w-4" />
          <span className="flex-1 text-left">앱 · 작업 · 문서 검색</span>
          <kbd className="rounded border bg-background px-1.5 py-0.5 text-[10px] font-semibold">
            ⌘K
          </kbd>
        </button>

        <div className="flex items-center gap-2">
          <ThemeToggle />
          {isLoggedIn && user ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" className="h-9 gap-2 px-2">
                  <div className="flex h-7 w-7 items-center justify-center rounded-full bg-primary/15 text-xs font-bold text-primary">
                    {(user.display_name ?? user.email ?? "?").slice(0, 1).toUpperCase()}
                  </div>
                  <span className="hidden text-sm font-medium md:inline">{user.display_name ?? user.email}</span>
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                <DropdownMenuLabel>
                  <div className="text-sm font-semibold">{user.display_name}</div>
                  <div className="text-xs font-normal text-muted-foreground">{user.email}</div>
                  <div className="mt-1 inline-block rounded-full bg-secondary px-2 py-0.5 text-[10px] uppercase">
                    {user.role}
                  </div>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => navigate({ to: "/jobs" })}>
                  <UserIcon className="mr-2 h-4 w-4" /> 내 실행 이력
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => navigate({ to: "/submit" })}>
                  <Settings className="mr-2 h-4 w-4" /> 새 앱 신청
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => logout()}>
                  <LogOut className="mr-2 h-4 w-4" /> 로그아웃
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : (
            <Button asChild size="sm" variant="default">
              <Link to="/login">
                <LogIn className="mr-2 h-4 w-4" /> 로그인
              </Link>
            </Button>
          )}
        </div>
      </div>

      <CommandDialog open={open} onOpenChange={setOpen}>
        <CommandInput placeholder="앱 이름, 태그, 작업 ID 검색…" />
        <CommandList>
          <CommandEmpty>일치하는 결과가 없습니다.</CommandEmpty>
          <CommandGroup heading="바로 이동">
            <CommandItem
              onSelect={() => {
                setOpen(false);
                navigate({ to: "/apps" });
              }}
            >
              앱 카탈로그
            </CommandItem>
            <CommandItem
              onSelect={() => {
                setOpen(false);
                navigate({ to: "/jobs" });
              }}
            >
              내 실행 이력
            </CommandItem>
            <CommandItem
              onSelect={() => {
                setOpen(false);
                navigate({ to: "/submit" });
              }}
            >
              새 앱 신청
            </CommandItem>
          </CommandGroup>
        </CommandList>
      </CommandDialog>
    </header>
  );
}
