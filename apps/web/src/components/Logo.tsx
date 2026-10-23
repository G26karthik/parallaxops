/** Parallax mark: three layers at different depths shifting against each other. */
export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" role="img" aria-label="ParallaxOps">
      <title>ParallaxOps</title>
      <rect x="3" y="12" width="17" height="17" rx="3" fill="var(--teal)" opacity="0.35" />
      <rect x="8" y="7" width="17" height="17" rx="3" fill="var(--teal)" opacity="0.6" />
      <rect x="13" y="2" width="17" height="17" rx="3" fill="var(--accent)" />
      <circle cx="21.5" cy="10.5" r="2.4" fill="var(--bg)" />
    </svg>
  );
}

export function Wordmark() {
  return (
    <span className="flex items-center gap-2.5">
      <LogoMark />
      <span className="text-[15px] font-semibold tracking-tight">
        Parallax<span className="text-accent">Ops</span>
      </span>
    </span>
  );
}
