import { isLegalPath, LegalFooter, LegalPage } from './LegalPages'

const navigationItems = [
  'Dashboard',
  'Orders',
  'Customers',
  'Products',
  'Conversations',
  'Settings',
]

function App() {
  const path = window.location.pathname.replace(/\/+$/, '')
  if (isLegalPath(path)) return <LegalPage path={path} />

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <div className="flex min-h-screen">
        <aside className="w-64 border-r border-slate-200 bg-white px-5 py-6">
          <h1 className="text-lg font-semibold tracking-tight">WhatsApp AI Commerce</h1>
          <nav aria-label="Main navigation" className="mt-8">
            <ul className="space-y-1">
              {navigationItems.map((item) => (
                <li key={item}>
                  <span className="block rounded-md px-3 py-2 text-sm text-slate-600">
                    {item}
                  </span>
                </li>
              ))}
            </ul>
          </nav>
        </aside>
        <main className="flex-1 p-8">
          <h2 className="text-2xl font-semibold">Dashboard</h2>
          <p className="mt-2 text-slate-600">WhatsApp AI Commerce</p>
        </main>
      </div>
      <LegalFooter />
    </div>
  )
}

export default App
