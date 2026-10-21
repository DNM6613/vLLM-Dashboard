import { Component, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

// Top-level render-error fallback (audit 2026-10-20 P2-17): without it a
// single bad render (e.g. an unexpected backend payload shape) unmounts the
// whole 24/7 monitoring page to a blank screen with no recovery entry.
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error('Page render crashed:', error);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen bg-bg flex items-center justify-center">
          <div className="max-w-md w-full mx-4 rounded-xl border border-danger/40 bg-bg-card p-6 text-center">
            <p className="text-lg font-medium mb-2">页面渲染异常 / Page failed to render</p>
            <p className="text-sm text-text-muted mb-4 font-mono break-all">{this.state.error.message}</p>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="px-4 py-2 rounded-lg bg-bg-hover text-sm font-medium hover:bg-border transition-colors"
            >
              刷新页面 / Reload
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
