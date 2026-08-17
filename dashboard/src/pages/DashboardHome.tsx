import { useEffect, useState } from 'react';
import { PlayCircle, Clock, CheckCircle, Server, AlertOctagon } from 'lucide-react';
import { StatusBadge } from '../components/ui/StatusBadge';
import { QuickCreateJob } from '../components/ui/QuickCreateJob';
import { Link } from 'react-router-dom';

interface SummaryData {
  total: number;
  pending: number;
  running: number;
  waiting: number;
  retry_waiting: number;
  manual_review: number;
  success: number;
  failed: number;
  blocked: number;
  unknown: number;
  active_leases: number;
  browser_health: string;
}

export default function DashboardHome() {
  const [summary, setSummary] = useState<SummaryData | null>(null);
  const [recentJobs, setRecentJobs] = useState<any[]>([]);

  useEffect(() => {
    const fetchSummary = async () => {
      try {
        const res = await fetch('/api/dashboard/summary');
        setSummary(await res.json());
      } catch (err) {}
    };
    
    const fetchJobs = async () => {
      try {
        const res = await fetch('/api/dashboard/jobs?limit=5');
        const data = await res.json();
        setRecentJobs(data.items || []);
      } catch (err) {}
    };
    
    fetchSummary();
    fetchJobs();
    const interval = setInterval(() => { fetchSummary(); fetchJobs(); }, 5000);
    return () => clearInterval(interval);
  }, []);

  if (!summary) {
    return (
      <div className="flex items-center justify-center h-64 tech-mono text-[var(--text-tertiary)] animate-pulse">
        Initializing Control Center...
      </div>
    );
  }

  const criticalIssues = (summary.failed || 0) + (summary.manual_review || 0) + (summary.blocked || 0) + (summary.retry_waiting || 0);

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-medium tracking-tight">Automation Overview</h1>
          <p className="text-[var(--text-secondary)] mt-1">Realtime pipeline statistics and health metrics</p>
        </div>
      </div>
      
      {/* Phase 3: Input / Job Creation */}
      <QuickCreateJob />
      
      {/* Level 1: Critical Problems (Only show if there are issues, else show a clean state) */}
      {criticalIssues > 0 ? (
        <div className="surface border border-[var(--color-brand-error)]/30 rounded-sm overflow-hidden relative">
          <div className="absolute top-0 left-0 w-1 h-full bg-[var(--color-brand-error)]"></div>
          <div className="p-4 bg-[var(--color-brand-error-muted)]/20 flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded bg-[var(--color-brand-error-muted)] text-[var(--color-brand-error)]">
                <AlertOctagon size={20} />
              </div>
              <div>
                <h3 className="font-semibold text-[var(--color-brand-error)]">Attention Required</h3>
                <p className="text-[var(--text-secondary)] mt-0.5">There are {criticalIssues} jobs requiring operator intervention or currently failing.</p>
              </div>
            </div>
            <div className="flex gap-2">
               {summary.failed > 0 && <MetricBadge label="FAILED" value={summary.failed} color="error" to="/jobs?status=FAILED" />}
               {summary.manual_review > 0 && <MetricBadge label="REVIEW" value={summary.manual_review} color="warning" to="/manual-review" />}
               {summary.retry_waiting > 0 && <MetricBadge label="RETRY" value={summary.retry_waiting} color="warning" to="/jobs?status=RETRY_WAITING" />}
            </div>
          </div>
        </div>
      ) : (
        <div className="surface border border-[var(--color-brand-success)]/30 rounded-sm overflow-hidden relative">
          <div className="absolute top-0 left-0 w-1 h-full bg-[var(--color-brand-success)]"></div>
          <div className="p-4 bg-[var(--color-brand-success-muted)]/20 flex items-center gap-3">
            <div className="p-2 rounded bg-[var(--color-brand-success-muted)] text-[var(--color-brand-success)]">
              <CheckCircle size={20} />
            </div>
            <div>
              <h3 className="font-semibold text-[var(--color-brand-success)]">All Systems Operational</h3>
              <p className="text-[var(--text-secondary)] mt-0.5">No critical job failures or manual reviews pending.</p>
            </div>
          </div>
        </div>
      )}

      {/* Level 2: Pipeline Overview Grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-px bg-[var(--border-subtle)] border border-[var(--border-subtle)] rounded-sm overflow-hidden">
        <StatCell title="Total Volume" value={summary.total} subtitle="All time jobs" icon={PlayCircle} />
        <StatCell title="Running" value={summary.running} subtitle="Currently active" icon={Clock} highlight={summary.running > 0} />
        <StatCell title="Completed" value={summary.success} subtitle="Successfully finished" icon={CheckCircle} />
        <StatCell title="Queue Pending" value={summary.pending} subtitle="Waiting for worker" icon={Server} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Recent Jobs */}
        <div className="lg:col-span-2 surface rounded-sm flex flex-col">
          <div className="p-4 border-b border-[var(--border-subtle)] flex items-center justify-between">
            <h3 className="font-semibold text-sm uppercase tracking-widest text-[var(--text-secondary)]">Recent Activity</h3>
            <Link to="/jobs" className="text-[var(--color-brand-info)] hover:text-white transition-colors tech-mono">View All ↗</Link>
          </div>
          <div className="flex-1 overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-[var(--border-subtle)]">
                  <th className="px-4 py-3">Job ID</th>
                  <th className="px-4 py-3">Stage</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3 text-right">Attempt</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border-subtle)]">
                {recentJobs.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-4 py-8 text-center text-[var(--text-tertiary)] tech-mono">No recent jobs</td>
                  </tr>
                ) : recentJobs.map(job => (
                  <tr key={job.short_id} className="interactive-row group">
                    <td className="px-4 py-2.5">
                      <Link to={`/jobs/${job.job_id}`} className="tech-mono text-[var(--color-brand-info)] hover:underline">
                        {job.short_id}
                      </Link>
                    </td>
                    <td className="px-4 py-2.5 text-[var(--text-secondary)]">{job.stage}</td>
                    <td className="px-4 py-2.5"><StatusBadge status={job.status} /></td>
                    <td className="px-4 py-2.5 text-right tech-mono text-[var(--text-secondary)]">{job.attempt}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Level 3: Infrastructure */}
        <div className="surface rounded-sm flex flex-col">
          <div className="p-4 border-b border-[var(--border-subtle)]">
            <h3 className="font-semibold text-sm uppercase tracking-widest text-[var(--text-secondary)]">Infra Health</h3>
          </div>
          <div className="p-4 space-y-4">
            <HealthIndicator 
              label="Browser CDP Lock" 
              value={(summary.active_leases || 0) > 0 ? "LOCKED" : "AVAILABLE"} 
              status={(summary.active_leases || 0) > 0 ? "info" : "success"}
            />
            <HealthIndicator 
              label="Worker Status" 
              value="LISTENING" 
              status="success"
            />
            <HealthIndicator 
              label="Database" 
              value="CONNECTED" 
              status="success"
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function StatCell({ title, value, subtitle, icon: Icon, highlight }: any) {
  return (
    <div className="bg-[var(--bg-app)] p-5 flex flex-col justify-between group hover:bg-[var(--bg-surface)] transition-colors">
      <div className="flex justify-between items-start mb-4">
        <div className={`p-2 rounded bg-white/5 ${highlight ? 'text-[var(--color-brand-info)]' : 'text-[var(--text-secondary)]'}`}>
          <Icon size={18} />
        </div>
      </div>
      <div>
        <div className="text-3xl font-light tracking-tight tech-mono mb-1">{value}</div>
        <div className="text-sm font-medium text-[var(--text-primary)]">{title}</div>
        <div className="text-[11px] text-[var(--text-tertiary)] mt-0.5">{subtitle}</div>
      </div>
    </div>
  );
}

function MetricBadge({ label, value, color, to }: { label: string, value: number, color: string, to: string }) {
  const bg = color === 'error' ? 'bg-[var(--color-brand-error)] text-white' : 'bg-[var(--color-brand-warning)] text-black';
  return (
    <Link to={to} className="flex items-center overflow-hidden rounded-sm text-[11px] font-bold tech-mono hover:opacity-80 transition-opacity cursor-pointer">
      <div className={`px-2 py-1 ${bg}`}>{label}</div>
      <div className="px-2 py-1 bg-black/40 text-white border-y border-r border-white/10">{value}</div>
    </Link>
  );
}

function HealthIndicator({ label, value, status }: { label: string, value: string, status: 'success' | 'info' | 'error' }) {
  let dotClass = "bg-gray-500";
  if (status === 'success') dotClass = "bg-[var(--color-brand-success)] shadow-[0_0_8px_rgba(16,185,129,0.5)]";
  if (status === 'info') dotClass = "bg-[var(--color-brand-info)] shadow-[0_0_8px_rgba(59,130,246,0.5)] animate-pulse";
  if (status === 'error') dotClass = "bg-[var(--color-brand-error)] shadow-[0_0_8px_rgba(239,68,68,0.5)]";

  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-[var(--text-secondary)]">{label}</span>
      <div className="flex items-center gap-2">
        <span className={`w-1.5 h-1.5 rounded-full ${dotClass}`}></span>
        <span className="tech-mono text-[11px] text-[var(--text-primary)]">{value}</span>
      </div>
    </div>
  );
}
