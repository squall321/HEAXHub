export function Footer() {
  return (
    <footer className="border-t bg-card/40">
      <div className="flex flex-col gap-2 px-6 py-5 text-xs text-muted-foreground md:flex-row md:items-center md:justify-between">
        <div>
          <span className="font-semibold text-foreground">HEAXHub</span> · 사내 자동화 통합 포탈
        </div>
        <div className="flex items-center gap-4">
          <span>v0.1.0</span>
          <a
            href="/docs"
            className="transition-colors hover:text-foreground"
          >
            문서
          </a>
          <a
            href="mailto:cae-automation@company.com"
            className="transition-colors hover:text-foreground"
          >
            문의
          </a>
        </div>
      </div>
    </footer>
  );
}
