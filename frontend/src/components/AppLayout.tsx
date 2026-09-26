import { Suspense } from 'react'
import { Outlet } from 'react-router-dom'
import PageSpinner from './PageSpinner'
import Sidebar from './Sidebar'
import TopBar from './TopBar'

export default function AppLayout() {
  return (
    <div className="flex h-full overflow-hidden">
      <Sidebar />
      {/* One scrolling surface: the top bar moves with the content instead
          of staying put. The sidebar keeps its own scroll. */}
      <div className="scroll-slim flex flex-1 flex-col overflow-y-auto [scrollbar-gutter:stable]">
        <TopBar />
        <main className="flex-1 py-6 pl-4 pr-1.5">
          <Suspense fallback={<PageSpinner />}>
            <Outlet />
          </Suspense>
        </main>
      </div>
    </div>
  )
}
