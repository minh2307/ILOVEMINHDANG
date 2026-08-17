import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { StatusBadge } from '../components/ui/StatusBadge';
import { Search, RotateCw } from 'lucide-react';

interface Job {
  job_id: string;
  short_id: string;
  status: string;
  display_status: string;
  stage: string;
  source_url: string;
  updated_at: string;
  attempt: number;
  error_message: string;
}

export default function JobsList() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  
  // Use URL parameter for initial status if available, fallback to 'ALL'
  const urlStatus = searchParams.get('status') || 'ALL';
  const [statusFilter, setStatusFilter] = useState(urlStatus);

  // Update URL when filter changes
  useEffect(() => {
    if (statusFilter !== 'ALL') {
      setSearchParams({ status: statusFilter });
    } else {
      setSearchParams({});
    }
  }, [statusFilter, setSearchParams]);

  // Handle URL changes from outside (e.g. clicking a link in DashboardHome)
  useEffect(() => {
    const currentUrlStatus = searchParams.get('status') || 'ALL';
    if (currentUrlStatus !== statusFilter) {
      setStatusFilter(currentUrlStatus);
    }
  }, [searchParams]);

  useEffect(() => {
    const fetchJobs = async () => {
      try {
        const res = await fetch('/api/dashboard/jobs');
        const data = await res.json();
        setJobs(data.items || data);
      } catch (err) {
        console.error('Failed to fetch jobs', err);
      } finally {
        setLoading(false);
      }
    };
    
    fetchJobs();
    const interval = setInterval(fetchJobs, 5000);
    return () => clearInterval(interval);
  }, []);

  if (loading && jobs.length === 0) {
    return <div className="flex items-center justify-center h-64 tech-mono text-[var(--text-tertiary)] animate-pulse">Loading jobs...</div>;
  }

  const filteredJobs = jobs.filter(job => {
    if (search && !job.short_id.includes(search) && !job.source_url.includes(search)) return false;
    if (statusFilter !== 'ALL') {
      if (statusFilter === 'FAILED' && !job.status.includes('FAIL') && !job.status.includes('ERROR')) return false;
      if (statusFilter === 'SUCCESS' && !job.status.includes('SUCCESS') && !job.status.includes('COMPLETED')) return false;
      if (statusFilter === 'WARNING' && !job.status.includes('WAITING') && !job.status.includes('REVIEW')) return false;
    }
    return true;
  });

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-medium tracking-tight">Jobs</h2>
          <p className="text-[var(--text-secondary)] mt-1">All automation pipeline executions</p>
        </div>
        
        <div className="flex items-center gap-3">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-tertiary)]" size={14} />
            <input 
              type="text" 
              placeholder="Search ID or URL..." 
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-9 pr-4 py-1.5 bg-[var(--bg-surface)] border border-[var(--border-strong)] rounded-sm text-sm focus:outline-none focus:border-[var(--color-brand-info)] transition-colors w-64 text-[var(--text-primary)]"
            />
          </div>
          
          <select 
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-3 py-1.5 bg-[var(--bg-surface)] border border-[var(--border-strong)] rounded-sm text-sm focus:outline-none focus:border-[var(--color-brand-info)] text-[var(--text-primary)]"
          >
            <option value="ALL">All Statuses</option>
            <option value="FAILED">Failed / Error</option>
            <option value="SUCCESS">Success / Completed</option>
            <option value="WARNING">Warning / Manual Review</option>
          </select>
          
          <button className="p-1.5 surface rounded-sm hover:bg-[var(--bg-elevated)] transition-colors text-[var(--text-secondary)]">
            <RotateCw size={16} />
          </button>
        </div>
      </div>

      <div className="surface rounded-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-[var(--border-strong)] bg-[var(--bg-app)]">
                <th className="px-4 py-3">Job ID</th>
                <th className="px-4 py-3">Source URL</th>
                <th className="px-4 py-3">Stage</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3 text-right">Attempt</th>
                <th className="px-4 py-3 text-right">Last Updated</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border-subtle)]">
              {filteredJobs.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-12 text-center text-[var(--text-tertiary)] tech-mono">
                    No jobs matching filters
                  </td>
                </tr>
              ) : filteredJobs.map(job => (
                <tr key={job.job_id} className="interactive-row group">
                  <td className="px-4 py-3">
                    <Link to={`/jobs/${job.job_id}`} className="tech-mono text-[var(--color-brand-info)] hover:underline">
                      {job.short_id}
                    </Link>
                  </td>
                  <td className="px-4 py-3 max-w-[200px] truncate">
                    <a href={job.source_url} target="_blank" rel="noreferrer" className="text-[var(--text-secondary)] hover:text-white truncate block">
                      {job.source_url}
                    </a>
                  </td>
                  <td className="px-4 py-3 text-[var(--text-primary)]">{job.stage}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={job.status} />
                  </td>
                  <td className="px-4 py-3 text-right tech-mono text-[var(--text-secondary)]">{job.attempt}</td>
                  <td className="px-4 py-3 text-right tech-mono text-[var(--text-secondary)] text-[11px]">
                    {job.updated_at ? job.updated_at.replace('T', ' ').substring(0, 19) : 'N/A'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
