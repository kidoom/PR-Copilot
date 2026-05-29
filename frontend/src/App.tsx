import { useState } from "react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Spinner } from "@/components/ui/spinner"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { analyzePr } from "@/api"
import type { PrContextResponse } from "@/types"

function App() {
  const [prUrl, setPrUrl] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<PrContextResponse | null>(null)

  const handleAnalyze = async () => {
    if (!prUrl.trim()) {
      setError("Please enter a GitHub PR URL")
      return
    }
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const data = await analyzePr(prUrl)
      setResult(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-background">
      <div className="mx-auto max-w-5xl px-4 py-8">
        <h1 className="mb-8 text-3xl font-bold tracking-tight">PR Copilot</h1>

        <div className="mb-6 flex gap-2">
          <Input
            placeholder="https://github.com/owner/repo/pull/123"
            value={prUrl}
            onChange={(e) => setPrUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAnalyze()}
            disabled={loading}
          />
          <Button onClick={handleAnalyze} disabled={loading}>
            {loading && <Spinner className="mr-2 h-4 w-4" />}
            {loading ? "Analyzing..." : "Analyze"}
          </Button>
        </div>

        {error && (
          <Alert variant="destructive" className="mb-6">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {result && (
          <div className="space-y-6">
            <Card>
              <CardHeader>
                <CardTitle className="text-xl">
                  <a
                    href={result.pr.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="hover:underline"
                  >
                    {result.pr.title}
                  </a>
                </CardTitle>
                <CardDescription>
                  {result.pr.author} · {result.pr.base_branch} ←{" "}
                  {result.pr.head_branch}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="flex gap-4 text-sm text-muted-foreground">
                  <span className="text-green-600">+{result.pr.additions}</span>
                  <span className="text-red-600">-{result.pr.deletions}</span>
                  <span>{result.pr.changed_files} files</span>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-lg">Summary</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                {result.derived.docs_only && (
                  <Badge variant="secondary">Docs only</Badge>
                )}
                {result.derived.has_source_without_tests && (
                  <Badge variant="destructive">
                    Source changes without tests
                  </Badge>
                )}
                {result.derived.high_risk_files.length > 0 && (
                  <div>
                    <span className="font-medium">High-risk files: </span>
                    {result.derived.high_risk_files.map((f) => (
                      <Badge key={f} variant="destructive" className="mr-1">
                        {f}
                      </Badge>
                    ))}
                  </div>
                )}
                {!result.derived.docs_only &&
                  !result.derived.has_source_without_tests &&
                  result.derived.high_risk_files.length === 0 && (
                    <span className="text-muted-foreground">
                      No high-risk signals detected
                    </span>
                  )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-lg">
                  Changed Files ({result.files.length})
                </CardTitle>
              </CardHeader>
              <CardContent>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>File</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Language</TableHead>
                      <TableHead className="text-right">+/-</TableHead>
                      <TableHead>Risk</TableHead>
                      <TableHead className="text-right">Priority</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {result.files.map((file) => (
                      <TableRow
                        key={file.filename}
                        className={
                          file.is_high_risk_path
                            ? "bg-destructive/5 font-medium"
                            : ""
                        }
                      >
                        <TableCell className="max-w-xs truncate font-mono text-xs">
                          {file.filename}
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant={
                              file.status === "added"
                                ? "default"
                                : file.status === "removed"
                                  ? "destructive"
                                  : "secondary"
                            }
                          >
                            {file.status}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {file.language}
                        </TableCell>
                        <TableCell className="text-right text-xs">
                          <span className="text-green-600">
                            +{file.additions}
                          </span>{" "}
                          <span className="text-red-600">
                            -{file.deletions}
                          </span>
                        </TableCell>
                        <TableCell>
                          {file.risk_hints.map((h) => (
                            <Badge
                              key={h}
                              variant="outline"
                              className="mr-1 text-xs"
                            >
                              {h}
                            </Badge>
                          ))}
                        </TableCell>
                        <TableCell className="text-right text-xs">
                          {file.priority_score_hint}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  )
}

export default App
