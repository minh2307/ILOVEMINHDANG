import { BrowserRouter as Router, Routes, Route, Link, useLocation } from 'react-router-dom';
import { Activity, LayoutDashboard, FileText, Settings, Server } from 'lucide-react';
import DashboardHome from './pages/DashboardHome';
import JobsList from './pages/JobsList';
import JobDetail from './pages/JobDetail';
import SystemHealth from './pages/SystemHealth';
import ManualReviewCenter from './pages/ManualReviewCenter';
import ProjectScheduler from './pages/ProjectScheduler';
import { CommandPalette } from './components/ui/CommandPalette';
import { useEffect, useState } from 'react';

function NavItem({ to, icon: Icon, label }: { to: string, icon: any, label: string }) {
  const location = useLocation();
  const isActive = location.pathname === to || (to !== '/' && location.pathname.startsWith(to));
  
  return (
    <Link 
      to={to} 
      className={`flex items-center gap-3 px-3 py-2 rounded-md transition-all text-[13px] font-medium
        ${isActive 
          ? 'bg-white/10 text-white' 
          : 'text-[var(--text-secondary)] hover:text-white hover:bg-white/5'
        }`}
    >
      <Icon size={16} strokeWidth={isActive ? 2.5 : 2} />
      {label}
    </Link>
  );
}

function Layout({ children }: { children: React.ReactNode }) {
  const [health, setHealth] = useState<any>(null);

  useEffect(() => {
    fetch('/api/dashboard/summary')
      .then(r => r.json())
      .then(d => setHealth(d))
      .catch(() => {});
  }, []);

  return (
    <div className="flex h-screen bg-[var(--bg-app)] text-[var(--text-primary)]">
      {/* Sidebar - Precise, border-driven, dark */}
      <aside className="w-60 border-r border-[var(--border-subtle)] bg-[var(--bg-surface)] flex flex-col z-10 shadow-2xl">
        <div className="h-14 px-5 border-b border-[var(--border-subtle)] flex items-center shrink-0">
          <div className="flex items-center gap-2.5">
            <div className="w-6 h-6 rounded bg-white text-black flex items-center justify-center">
              <Activity size={14} strokeWidth={3} />
            </div>
            <span className="font-semibold tracking-tight text-sm uppercase tracking-widest text-[var(--text-primary)]">MinhDang Ops</span>
          </div>
        </div>
        
        <div className="flex-1 overflow-y-auto py-4 px-3">
          <div className="mb-6">
            <h3 className="px-3 text-[10px] font-bold uppercase tracking-widest text-[var(--text-tertiary)] mb-2">Overview</h3>
            <nav className="space-y-0.5">
              <NavItem to="/" icon={LayoutDashboard} label="Dashboard" />
            </nav>
          </div>
          
          <div className="mb-6">
            <h3 className="px-3 text-[10px] font-bold uppercase tracking-widest text-[var(--text-tertiary)] mb-2">Workflow</h3>
            <nav className="space-y-0.5">
              <NavItem to="/jobs" icon={FileText} label="Jobs" />
            </nav>
          </div>
          
          <div>
            <h3 className="px-3 text-[10px] font-bold uppercase tracking-widest text-[var(--text-tertiary)] mb-2">System</h3>
            <nav className="space-y-0.5">
              <NavItem to="/scheduler" icon={Settings} label="Scheduler" />
              <NavItem to="/system" icon={Server} label="System Health" />
            </nav>
          </div>
        </div>

        {/* User / Health footprint */}
        <div className="p-4 border-t border-[var(--border-subtle)] text-[11px] text-[var(--text-secondary)] tech-mono flex flex-col gap-1.5">
          <div className="flex justify-between items-center">
            <span>Operator</span>
            <span className="text-[var(--text-primary)]">admin</span>
          </div>
          <div className="flex justify-between items-center">
            <span>Env</span>
            <span className="text-[var(--text-primary)]">production</span>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 flex flex-col min-w-0 bg-[var(--bg-app)]">
        <header className="h-14 border-b border-[var(--border-subtle)] bg-[var(--bg-app)]/80 backdrop-blur-sm flex items-center justify-between px-6 shrink-0 sticky top-0 z-10">
          <div className="flex items-center gap-4 text-xs font-medium text-[var(--text-secondary)] tech-mono">
            {health ? (
               <div className="flex items-center gap-2 text-emerald-400">
                 <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(16,185,129,0.5)]"></span>
                 System Healthy • Last event: {health.last_event_at ? health.last_event_at.split('T')[1].substring(0,8) : 'N/A'}
               </div>
            ) : (
               <div className="flex items-center gap-2 text-amber-400">
                 <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse"></span>
                 Connecting...
               </div>
            )}
          </div>
          <div className="text-[11px] text-[var(--text-tertiary)] tech-mono">
            MinhDang Automation Architecture
          </div>
        </header>
        
        <div className="flex-1 overflow-auto p-8 max-w-[1600px] mx-auto w-full">
          {children}
        </div>
      </main>
      <CommandPalette />
    </div>
  );
}

function App() {
  return (
    <Router>
      <Layout>
        <Routes>
          <Route path="/" element={<DashboardHome />} />
          <Route path="/jobs" element={<JobsList />} />
          <Route path="/jobs/:id" element={<JobDetail />} />
          <Route path="/manual-review" element={<ManualReviewCenter />} />
          <Route path="/system" element={<SystemHealth />} />
          <Route path="/scheduler" element={<ProjectScheduler />} />
        </Routes>
      </Layout>
    </Router>
  );
}

export default App;
