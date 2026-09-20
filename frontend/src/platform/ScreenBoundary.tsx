import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertTriangle, RotateCw } from 'lucide-react'

/** Catches a crash in one console screen instead of losing the console.
 *
 *  React unmounts the entire tree when a render throws, so one screen reading
 *  a field that is not there took the whole page to white — no sidebar, no
 *  header, no way back, and nothing on screen saying what happened. The
 *  operator's only clue was in a console they were not looking at.
 *
 *  This happened for a real and recurring reason. A deploy is not atomic: a
 *  browser tab open while the backend rolls forward keeps running the old
 *  bundle against the new API, or the new bundle against the old one, and a
 *  response missing a field a screen reads is the normal result rather than a
 *  bug in either half. The screen cannot prevent it and should not have to.
 *
 *  So this sits around the outlet, not around the app: the sidebar, header and
 *  navigation stay alive, so whoever hits it can read what broke and click
 *  somewhere else. Reloading is offered because after a deploy that genuinely
 *  is the fix — the page comes back on the matching bundle.
 *
 *  `key` on the boundary is the route path, so navigating away clears the
 *  error. Without that, React keeps a boundary in its failed state forever and
 *  every subsequent screen renders this page instead of itself.
 */

interface Props {
  children: ReactNode
  /** Shown above the message, so the operator knows which screen failed. */
  where?: string
}

interface State {
  error: Error | null
}

export default class ScreenBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Logged rather than swallowed. A boundary that renders a tidy message and
    // tells nobody turns a loud failure into a quiet one, which is worse: the
    // white screen at least got reported.
    console.error('[platform console] screen crashed', error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    // A missing property is overwhelmingly the version-skew case, and saying
    // so is more useful than the raw TypeError — the operator's next action is
    // a reload, not a bug report.
    const skew = /reading '\w+'|is not a function|undefined/.test(error.message)

    return (
      <div className="p-6">
        <div className="max-w-2xl rounded-lg border border-pf-border bg-white p-6 shadow-pf-card">
          <div className="flex items-start gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-pf-warn-bg text-pf-warn-text">
              <AlertTriangle size={17} />
            </div>
            <div className="min-w-0">
              <h1 className="text-pf-card text-pf-navy">
                This screen could not be drawn
              </h1>
              <p className="mt-1.5 text-pf-td text-pf-body">
                {skew
                  ? 'The data that came back is not the shape this screen '
                    + 'expects. That usually means the page has been open '
                    + 'across an update — reloading picks up the matching '
                    + 'version.'
                  : 'Something in this screen failed while rendering.'}
              </p>
              <p className="mt-3 text-pf-help text-pf-muted">
                Nothing was changed. Every other part of the console still
                works, and the sidebar will take you anywhere else.
              </p>
              <pre className="mt-3 overflow-x-auto rounded bg-pf-bg px-3 py-2 font-mono text-[11px] text-pf-muted">
                {this.props.where ? `${this.props.where}\n` : ''}
                {error.message}
              </pre>
              <button type="button" onClick={() => window.location.reload()}
                className="mt-4 inline-flex items-center gap-1.5 rounded-md bg-pf-teal px-4 py-2 text-pf-btn text-white transition hover:bg-pf-hover">
                <RotateCw size={13} /> Reload the console
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }
}
