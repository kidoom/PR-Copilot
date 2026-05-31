import { useState } from "react"
import { ChevronDown, ChevronRight, ExternalLink } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import type { NormalizedFinding, EvidenceRef } from "@/types"

const SEVERITY_COLORS: Record<string, string> = {
  critical: "border-l-red-600 bg-red-50 dark:bg-red-950/20",
  high: "border-l-orange-500 bg-orange-50 dark:bg-orange-950/20",
  medium: "border-l-yellow-500 bg-yellow-50 dark:bg-yellow-950/20",
  low: "border-l-blue-500 bg-blue-50 dark:bg-blue-950/20",
  info: "border-l-slate-400 bg-slate-50 dark:bg-slate-900/40",
  informational: "border-l-slate-400 bg-slate-50 dark:bg-slate-900/40",
}

const SEVERITY_BADGE: Record<string, "destructive" | "secondary" | "outline"> = {
  critical: "destructive",
  high: "destructive",
  medium: "secondary",
  low: "outline",
  info: "outline",
  informational: "outline",
}

function EvidenceItem({
  evidence,
  onFileClick,
}: {
  evidence: EvidenceRef
  onFileClick?: (file: string) => void
}) {
  return (
    <div className="rounded border bg-background/50 px-2 py-1.5 text-xs">
      <div className="flex items-center gap-2">
        <button
          className="font-mono text-blue-600 hover:underline dark:text-blue-400"
          onClick={() => onFileClick?.(evidence.file)}
        >
          {evidence.file}
          {evidence.line != null && `:${evidence.line}`}
        </button>
        {evidence.source && (
          <Badge variant="outline" className="text-[10px]">
            {evidence.source}
          </Badge>
        )}
      </div>
      {evidence.snippet && (
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded bg-muted/50 p-1.5 text-[11px] text-muted-foreground">
          {evidence.snippet}
        </pre>
      )}
    </div>
  )
}

export function FindingCard({
  finding,
  onFileClick,
}: {
  finding: NormalizedFinding
  onFileClick?: (file: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const colorClass =
    SEVERITY_COLORS[finding.severity] || SEVERITY_COLORS.medium

  return (
    <div
      className={`rounded-lg border-l-4 ${colorClass} p-3 transition-colors`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Badge variant={SEVERITY_BADGE[finding.severity] || "secondary"}>
              {finding.severity}
            </Badge>
            <span className="text-xs text-muted-foreground">
              {Math.round(finding.confidence * 100)}% confidence
            </span>
          </div>
          <p className="mt-1 break-words text-sm font-medium">{finding.claim}</p>
          {finding.evidence.length > 0 && (
            <button
              className="mt-1 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              onClick={() => setExpanded(!expanded)}
            >
              {expanded ? (
                <ChevronDown className="h-3 w-3" />
              ) : (
                <ChevronRight className="h-3 w-3" />
              )}
              {finding.evidence.length} evidence
              {finding.evidence.length !== 1 ? "s" : ""}
            </button>
          )}
        </div>
        {finding.evidence.length > 0 && finding.evidence[0].file && (
          <button
            className="flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
            onClick={() => onFileClick?.(finding.evidence[0].file)}
            title="Open in diff viewer"
          >
            <ExternalLink className="h-3 w-3" />
          </button>
        )}
      </div>
      {expanded && finding.evidence.length > 0 && (
        <div className="mt-2 space-y-1.5">
          {finding.evidence.map((ev, i) => (
            <EvidenceItem key={i} evidence={ev} onFileClick={onFileClick} />
          ))}
        </div>
      )}
    </div>
  )
}

export function SeveritySummary({ findings }: { findings: NormalizedFinding[] }) {
  const counts: Record<string, number> = {
    critical: 0,
    high: 0,
    medium: 0,
    low: 0,
    info: 0,
  }
  for (const f of findings) {
    const severity = f.severity === "informational" ? "info" : f.severity
    counts[severity] = (counts[severity] || 0) + 1
  }

  return (
    <div className="flex items-center gap-2 text-xs">
      {counts.critical > 0 && (
        <Badge variant="destructive">{counts.critical} critical</Badge>
      )}
      {counts.high > 0 && (
        <Badge variant="destructive">{counts.high} high</Badge>
      )}
      {counts.medium > 0 && (
        <Badge variant="secondary">{counts.medium} medium</Badge>
      )}
      {counts.low > 0 && <Badge variant="outline">{counts.low} low</Badge>}
      {counts.info > 0 && <Badge variant="outline">{counts.info} info</Badge>}
    </div>
  )
}
