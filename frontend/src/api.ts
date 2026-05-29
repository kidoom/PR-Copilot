import type { PrContextResponse } from "./types"

export async function analyzePr(
  prUrl: string,
  githubToken?: string,
): Promise<PrContextResponse> {
  const res = await fetch("/api/pr/context", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      pr_url: prUrl,
      github_token: githubToken || undefined,
    }),
  })

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: "Unknown error" }))
    throw new Error(body.detail || `Request failed: ${res.status}`)
  }

  return res.json()
}
