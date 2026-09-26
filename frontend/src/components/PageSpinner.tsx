import { Loader2 } from 'lucide-react'

/** The fallback while a lazily loaded screen's chunk arrives. The same
 *  centred Loader2 the auth guards show; `full` fills the viewport (no shell
 *  yet), otherwise it sits in the content area so the shell stays put. */
export default function PageSpinner({ full = false }: { full?: boolean }) {
  return (
    <div
      role="status"
      aria-label="Loading"
      className={`flex items-center justify-center text-slate-400 ${full ? 'h-screen' : 'min-h-[40vh]'}`}>
      <Loader2 className="animate-spin" />
    </div>
  )
}
