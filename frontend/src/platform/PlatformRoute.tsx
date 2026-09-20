import { Navigate, useLocation } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { useAuth } from '../auth/AuthContext'
import type { ReactNode } from 'react'

/** Gate for the platform console.
 *
 *  The mirror of ProtectedRoute, and deliberately not a variant of it. It
 *  checks `is_platform` rather than merely "is signed in", because a tenant
 *  session is a perfectly valid session — it simply has no business here, and
 *  the API would refuse every call it made with a 403 that looks like a bug.
 *
 *  A tenant user who lands on a platform URL is sent back to the application
 *  they do belong to rather than to a sign-in screen: they are signed in
 *  already, and asking them to authenticate again would imply the credential
 *  was the problem.
 */
export default function PlatformRoute({ children }: { children: ReactNode }) {
  const { session, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center text-pf-muted">
        <Loader2 className="animate-spin" />
      </div>
    )
  }
  if (!session) {
    return (
      <Navigate to="/platform/login" state={{ from: location.pathname }} replace />
    )
  }
  if (!session.is_platform) {
    return <Navigate to="/" replace />
  }
  return <>{children}</>
}
