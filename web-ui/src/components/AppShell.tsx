import { Clapperboard, Menu, PanelLeftOpen, Plus, Settings, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useDialogFocus } from "../hooks/useDialogFocus";
import { JobNavigator } from "./JobNavigator";

const NAVIGATOR_COLLAPSED_KEY = "zhsub.navigator.collapsed";

export function AppShell() {
  const [navigatorOpen, setNavigatorOpen] = useState(false);
  const [navigatorCollapsed, setNavigatorCollapsed] = useState(
    () => window.localStorage.getItem(NAVIGATOR_COLLAPSED_KEY) === "true",
  );
  const location = useLocation();
  const studioActive = location.pathname === "/" || location.pathname.startsWith("/jobs/");
  const jobRoute = location.pathname.startsWith("/jobs/");
  const closeNavigator = useCallback(() => setNavigatorOpen(false), []);
  const drawerRef = useDialogFocus<HTMLElement>(navigatorOpen, closeNavigator);

  useEffect(() => closeNavigator(), [closeNavigator, location.pathname]);

  const setCollapsed = (collapsed: boolean) => {
    setNavigatorCollapsed(collapsed);
    window.localStorage.setItem(NAVIGATOR_COLLAPSED_KEY, String(collapsed));
  };

  return (
    <div className={`app-shell ${jobRoute && !navigatorCollapsed ? "has-navigator" : "navigator-hidden"}`}>
      <header className="mobile-header">
        <button className="icon-button" onClick={() => setNavigatorOpen(true)} aria-label="Mở danh sách công việc">
          <Menu size={20} />
        </button>
        <BrandMark compact />
        <div className="mobile-header-actions">
          <NavLink className="icon-button" to="/settings" aria-label="Mở cài đặt"><Settings size={19} /></NavLink>
          <NavLink className="icon-button" to="/new" aria-label="Tạo công việc mới"><Plus size={20} /></NavLink>
        </div>
      </header>

      <aside className="app-rail" aria-label="Điều hướng chính">
        <BrandMark />
        <nav>
          <Link className={`rail-link ${studioActive ? "active" : ""}`} to="/" aria-current={studioActive ? "page" : undefined}>
            <Clapperboard size={20} strokeWidth={1.8} />
            <span>Studio</span>
          </Link>
          <NavLink className={({ isActive }) => `rail-link ${isActive ? "active" : ""}`} to="/new">
            <Plus size={20} strokeWidth={1.8} />
            <span>Job mới</span>
          </NavLink>
        </nav>
        <NavLink className={({ isActive }) => `rail-link rail-settings ${isActive ? "active" : ""}`} to="/settings">
          <Settings size={20} strokeWidth={1.8} />
          <span>Cài đặt</span>
        </NavLink>
        <div className="rail-version">v0.1</div>
      </aside>

      {navigatorOpen && <aside ref={drawerRef} className="navigator-drawer open" role="dialog" aria-modal="true" aria-label="Danh sách công việc">
        <div className="drawer-head">
          <span>Công việc</span>
          <button className="icon-button" onClick={closeNavigator} aria-label="Đóng danh sách"><X size={19} /></button>
        </div>
        <JobNavigator onNavigate={closeNavigator} />
      </aside>}
      {navigatorOpen && <button className="drawer-scrim" onClick={closeNavigator} aria-label="Đóng danh sách công việc" />}

      {jobRoute && !navigatorCollapsed && <aside className="job-navigator-pane">
        <JobNavigator onCollapse={() => setCollapsed(true)} />
      </aside>}
      {jobRoute && navigatorCollapsed && <button className="navigator-expand" onClick={() => setCollapsed(false)} aria-label="Mở cột công việc"><PanelLeftOpen size={18} /></button>}
      <main className="workspace"><Outlet /></main>
    </div>
  );
}

function BrandMark({ compact = false }: { compact?: boolean }) {
  return (
    <Link className={`brand ${compact ? "compact" : ""}`} to="/" aria-label="zhsub Studio">
      <span className="brand-glyph">字</span>
      {!compact && <span className="brand-word">zhsub</span>}
    </Link>
  );
}
