import { Navigate, useLocation } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { useAuth } from './AuthContext'
import type { ReactNode } from 'react'

export default function ProtectedRoute({ children }: { children: ReactNode }) {
  const { session, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center text-slate-400">
        <Loader2 className="animate-spin" />
      </div>
    )
  }
  if (!session) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />
  }
  // The other direction of the same rule PlatformRoute enforces. A platform
  // operator belongs to no organisation, so every screen in here would ask for
  // a property they do not have and fail in a different way on each one.
  if (session.is_platform) {
    return <Navigate to="/platform" replace />
  }
  return <>{children}</>
}
