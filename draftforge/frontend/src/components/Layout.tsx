import { NavLink, Outlet } from "react-router-dom";
import ThemeToggle from "./ThemeToggle";

const NAV_ITEMS = [
  { to: "/new-project", label: "New Project" },
  { to: "/outline", label: "Outline Review" },
  { to: "/dashboard", label: "Run Dashboard" },
  { to: "/review", label: "Review" },
  { to: "/downloads", label: "Downloads" },
  { to: "/settings", label: "Settings" },
];

function navLinkClass(isActive: boolean): string {
  const base = "px-3 py-1.5 text-sm rounded transition-colors";
  return isActive
    ? `${base} bg-surface-raised text-text-primary font-medium`
    : `${base} text-text-secondary hover:text-text-primary`;
}

export default function Layout() {
  return (
    <div className="min-h-screen bg-surface text-text-primary flex flex-col">
      <header className="border-b border-border-subtle">
        <div className="mx-auto max-w-6xl px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-6">
            <span className="font-semibold tracking-tight">DraftForge</span>
            <nav className="flex items-center gap-1">
              {NAV_ITEMS.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) => navLinkClass(isActive)}
                >
                  {item.label}
                </NavLink>
              ))}
            </nav>
          </div>
          <ThemeToggle />
        </div>
      </header>
      <main className="flex-1 mx-auto w-full max-w-6xl px-4 py-8">
        <Outlet />
      </main>
    </div>
  );
}
