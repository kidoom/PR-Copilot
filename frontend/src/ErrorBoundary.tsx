import { Component, type ErrorInfo, type ReactNode } from "react"

interface ErrorBoundaryProps {
  children: ReactNode
}

interface ErrorBoundaryState {
  error: Error | null
}

export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("UI render failed", error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen bg-background p-6 text-foreground">
          <div className="mx-auto max-w-xl rounded-lg border bg-card p-5 shadow-sm">
            <h1 className="text-lg font-semibold">PR Copilot 遇到界面错误</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              页面已恢复而非显示空白。请刷新后重新尝试分析。
            </p>
            <pre className="mt-4 overflow-auto rounded-md bg-muted p-3 text-xs">
              {this.state.error.message}
            </pre>
            <button
              className="mt-4 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"
              onClick={() => {
                this.setState({ error: null })
                window.location.reload()
              }}
              type="button"
            >
              刷新页面
            </button>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
