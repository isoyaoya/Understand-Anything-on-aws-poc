import { useState, useEffect } from "react";
import { fetchProjects } from "../api/graph";

interface ProjectEntry {
  project_id: string;
  name: string;
  org: string;
  github_url: string;
  analyzed_at: string;
  languages: string[];
  frameworks: string[];
}

interface Props {
  currentProjectId: string | null;
  onSelect: (projectId: string) => void;
}

export default function ProjectSelector({ currentProjectId, onSelect }: Props) {
  const [projects, setProjects] = useState<ProjectEntry[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    fetchProjects().then(setProjects).catch(() => setProjects([]));
  }, []);

  if (projects.length === 0) return null;

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm bg-elevated border border-border-subtle hover:border-accent/50 text-text-secondary hover:text-text-primary transition-colors"
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
        </svg>
        <span className="max-w-[150px] truncate">
          {currentProjectId || "Select Project"}
        </span>
        <svg className={`w-3 h-3 transition-transform ${open ? "rotate-180" : ""}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="absolute top-full left-0 mt-1 w-72 bg-surface border border-border-medium rounded-lg shadow-xl z-50 overflow-hidden">
          <div className="max-h-64 overflow-y-auto">
            {projects.map((p) => (
              <button
                key={p.project_id}
                onClick={() => { onSelect(p.project_id); setOpen(false); }}
                className={`w-full text-left px-4 py-3 hover:bg-elevated transition-colors border-b border-border-subtle last:border-0 ${
                  currentProjectId === p.project_id ? "bg-accent/10" : ""
                }`}
              >
                <div className="text-sm font-medium text-text-primary">{p.project_id}</div>
                <div className="text-xs text-text-muted mt-0.5 flex items-center gap-2">
                  {p.languages.length > 0 && (
                    <span>{p.languages.slice(0, 3).join(", ")}</span>
                  )}
                  {p.analyzed_at && (
                    <span>· {new Date(p.analyzed_at).toLocaleDateString()}</span>
                  )}
                </div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
