import type { ScoredRunner } from '../lib/types';
import { CheckCircle2, MinusCircle } from 'lucide-react';

export function RunnerAuditDrawer({ runner }: { runner: ScoredRunner }) {
  const categories = Array.from(new Set(runner.audit_23pt.map((f) => f.category)));

  return (
    <div className="border-t border-slate-800 bg-slate-950/90 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 className="text-sm font-semibold text-slate-100">
            23-Point Audit Breakdown — #{runner.runner_number} {runner.name}
          </h4>
          <p className="text-xs text-slate-400">
            Composite {runner.composite_score.toFixed(1)}/100 · Rated ${runner.rated_price.toFixed(2)} vs Live ${runner.live_win > 0 ? runner.live_win.toFixed(2) : '—'}
            {runner.ai_note ? ` · ${runner.ai_note}` : ''}
          </p>
        </div>
        <div className="flex items-center gap-3 text-[11px] text-slate-400">
          <span className="inline-flex items-center gap-1">
            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" /> Verified Signal
          </span>
          <span className="inline-flex items-center gap-1">
            <MinusCircle className="h-3.5 w-3.5 text-slate-500" /> Neutral 5.0 (No data available)
          </span>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {categories.map((cat) => (
          <div key={cat} className="rounded-xl border border-slate-800/80 bg-slate-900/60 p-3">
            <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-emerald-400">
              {cat}
            </div>
            <div className="space-y-2.5">
              {runner.audit_23pt
                .filter((f) => f.category === cat)
                .map((f) => {
                  const pct = Math.min(100, Math.max(0, (f.score / 10) * 100));
                  const barColor = !f.has_data
                    ? 'bg-slate-600'
                    : f.score >= 7.0
                    ? 'bg-emerald-500'
                    : f.score >= 5.5
                    ? 'bg-sky-500'
                    : f.score >= 4.5
                    ? 'bg-slate-400'
                    : 'bg-rose-500';
                  return (
                    <div key={f.key} className="text-xs">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium text-slate-200">{f.label}</span>
                        <span className="shrink-0 tabular-nums text-[11px] text-slate-300">
                          <strong className={!f.has_data ? 'text-slate-400' : f.score >= 7 ? 'text-emerald-400' : 'text-slate-100'}>
                            {f.score.toFixed(1)}
                          </strong>
                          /10 <span className="text-slate-500">({f.weight}% · +{f.weighted_points.toFixed(2)})</span>
                        </span>
                      </div>
                      <div className="my-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                        <div className={`h-full rounded-full ${barColor}`} style={{ width: `${pct}%` }} />
                      </div>
                      <p className={`text-[11px] leading-snug ${f.has_data ? 'text-slate-400' : 'italic text-slate-500'}`}>
                        {f.reason}
                      </p>
                    </div>
                  );
                })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
