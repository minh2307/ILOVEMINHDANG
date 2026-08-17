import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, Plus, AlertTriangle, Eye, Server } from 'lucide-react';

export function CommandPalette() {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const commands = [
    { id: 'create-job', label: 'Create new job', icon: Plus, action: () => { navigate('/'); } },
    { id: 'search-jobs', label: 'Search jobs', icon: Search, action: () => { navigate('/jobs'); } },
    { id: 'failed-jobs', label: 'View failed jobs', icon: AlertTriangle, action: () => { navigate('/jobs?status=FAILED'); } },
    { id: 'manual-review', label: 'Manual review center', icon: Eye, action: () => { navigate('/jobs?status=WARNING'); } },
    { id: 'system-health', label: 'System health', icon: Server, action: () => { navigate('/system'); } },
  ];

  const filteredCommands = commands.filter(cmd => 
    cmd.label.toLowerCase().includes(query.toLowerCase())
  );

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        setIsOpen(prev => !prev);
      }
      if (e.key === 'Escape') {
        setIsOpen(false);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 100);
      setQuery('');
      setSelectedIndex(0);
    }
  }, [isOpen]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex(prev => (prev + 1) % filteredCommands.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex(prev => (prev - 1 + filteredCommands.length) % filteredCommands.length);
    } else if (e.key === 'Enter' && filteredCommands[selectedIndex]) {
      e.preventDefault();
      filteredCommands[selectedIndex].action();
      setIsOpen(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-start justify-center pt-[15vh] z-[100]" onClick={() => setIsOpen(false)}>
      <div 
        className="surface w-full max-w-xl rounded-sm shadow-2xl border border-[var(--border-strong)] overflow-hidden animate-in fade-in zoom-in-95 duration-200"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center px-4 py-3 border-b border-[var(--border-subtle)] bg-[var(--bg-app)]">
          <Search size={18} className="text-[var(--text-tertiary)] mr-3" />
          <input
            ref={inputRef}
            type="text"
            className="flex-1 bg-transparent border-none outline-none text-sm text-[var(--text-primary)] placeholder-[var(--text-tertiary)]"
            placeholder="Search or run command... (e.g., 'failed')"
            value={query}
            onChange={e => { setQuery(e.target.value); setSelectedIndex(0); }}
            onKeyDown={handleKeyDown}
          />
          <div className="text-[10px] tech-mono px-1.5 py-0.5 rounded border border-[var(--border-strong)] text-[var(--text-tertiary)] ml-2">ESC</div>
        </div>

        <div className="max-h-80 overflow-y-auto py-2 bg-[var(--bg-surface)]">
          {filteredCommands.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm text-[var(--text-tertiary)]">
              No commands found
            </div>
          ) : (
            filteredCommands.map((cmd, index) => {
              const Icon = cmd.icon;
              const isSelected = index === selectedIndex;
              return (
                <div
                  key={cmd.id}
                  className={`px-4 py-2.5 flex items-center gap-3 cursor-pointer transition-colors ${
                    isSelected ? 'bg-[var(--color-brand-info)] text-white' : 'text-[var(--text-secondary)] hover:bg-[var(--bg-elevated)]'
                  }`}
                  onClick={() => { cmd.action(); setIsOpen(false); }}
                  onMouseEnter={() => setSelectedIndex(index)}
                >
                  <Icon size={16} className={isSelected ? 'text-white' : 'text-[var(--text-tertiary)]'} />
                  <span className="text-sm font-medium">{cmd.label}</span>
                  {isSelected && <span className="ml-auto text-[10px] tech-mono opacity-60">↵ ENTER</span>}
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
