import React from "react";

interface State {
  error: Error | null;
}

/** Top-level error boundary: renders the failure instead of a silent blank
 * screen. Operations teams debug from the message, not from a black box. */
export default class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  State
> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error("EmberEye crashed:", error.stack, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex h-screen items-center justify-center bg-ink-950 p-8">
          <div className="max-w-xl rounded-xl border border-alert/40 bg-ink-900 p-6">
            <h1 className="text-lg font-bold text-alert">EmberEye hit an unexpected error</h1>
            <p className="mt-2 font-mono text-xs text-paper">{this.state.error.message}</p>
            <p className="mt-3 text-xs text-mist">
              Reload the page. If the error persists, check the API health endpoint and the backend logs.
            </p>
            <button
              onClick={() => location.reload()}
              className="mt-4 rounded-md border border-ink-600 px-3 py-1.5 text-xs font-semibold text-paper hover:border-ember-500/60"
            >
              RELOAD
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
