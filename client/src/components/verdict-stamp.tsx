const VERDICT_COPY: Record<string, { label: string; sub: string }> = {
  pass: { label: "PASS", sub: "promoted to production" },
  fail: { label: "FAIL", sub: "held, evidence attached below" },
  error: { label: "ERROR", sub: "could not complete" },
  pending: { label: "PENDING", sub: "identified, not yet evaluated" },
};

export function VerdictStamp({ verdict }: { verdict: string }) {
  const copy = VERDICT_COPY[verdict] ?? { label: verdict.toUpperCase(), sub: "" };
  const tone =
    verdict === "pass" ? "pass" : verdict === "fail" || verdict === "error" ? "fail" : "ink";

  return (
    <div className="flex justify-center py-2">
      <div
        className="verdict-stamp"
        style={{
          borderColor: `var(--${tone})`,
          color: `var(--${tone})`,
        }}
        role="status"
        aria-label={`Verdict: ${copy.label}`}
      >
        <span className="text-4xl sm:text-5xl font-extrabold tracking-[0.15em] font-mono">
          {copy.label}
        </span>
        {copy.sub && (
          <span className="mt-1 text-[11px] tracking-[0.2em] uppercase font-mono opacity-80">
            {copy.sub}
          </span>
        )}
      </div>
      <style>{`
        .verdict-stamp {
          display: inline-flex;
          flex-direction: column;
          align-items: center;
          padding: 1.1rem 2.25rem;
          border: 4px double currentColor;
          border-radius: 999px;
          transform: rotate(-4deg);
          animation: stamp-thud 260ms cubic-bezier(0.2, 1.6, 0.4, 1) both;
        }
        @keyframes stamp-thud {
          0% { transform: rotate(-4deg) scale(1.5); opacity: 0; }
          70% { transform: rotate(-4deg) scale(0.96); opacity: 1; }
          100% { transform: rotate(-4deg) scale(1); opacity: 1; }
        }
      `}</style>
    </div>
  );
}
