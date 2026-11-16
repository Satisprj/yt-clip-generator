import { NavLink, Outlet } from 'react-router-dom'
import BackendStatus from './BackendStatus.jsx'

function navClass({ isActive }) {
  return `rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
    isActive ? 'bg-slate-800 text-white' : 'text-slate-400 hover:text-white'
  }`
}

export default function Layout() {
  return (
    <div className="flex min-h-screen flex-col bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800/80">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-4">
          <NavLink to="/" className="flex items-center gap-2 font-semibold tracking-tight">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-rose-500 text-sm">✂</span>
            <span className="hidden sm:inline">AI Clip Generator</span>
          </NavLink>
          <nav className="flex items-center gap-1">
            <NavLink to="/" end className={navClass}>New</NavLink>
            <NavLink to="/history" className={navClass}>History</NavLink>
            <NavLink to="/settings" className={navClass}>Settings</NavLink>
          </nav>
          <BackendStatus />
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-10">
        <Outlet />
      </main>

      <footer className="border-t border-slate-800/80 py-6 text-center text-xs text-slate-500">
        Only process videos you own or have permission to use.
      </footer>
    </div>
  )
}
