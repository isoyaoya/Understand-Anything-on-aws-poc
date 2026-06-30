// src/api/graph.ts — API client for cloud backend (S3-based)

const GRAPHS_BASE = "/graphs";

interface ProjectEntry {
  project_id: string;
  name: string;
  org: string;
  github_url: string;
  analyzed_at: string;
  languages: string[];
  frameworks: string[];
}

/**
 * Fetch list of available projects from S3 (via CloudFront)
 */
export async function fetchProjects(): Promise<ProjectEntry[]> {
  const response = await fetch(`${GRAPHS_BASE}/projects.json`);
  if (!response.ok) return [];
  const data = await response.json();
  return data.projects || [];
}

/**
 * Fetch full graph data for a project (from S3 via CloudFront)
 */
export async function fetchGraph(projectId: string) {
  const response = await fetch(
    `${GRAPHS_BASE}/${projectId}/knowledge-graph.json`
  );
  if (!response.ok) throw new Error(`Failed to fetch graph for ${projectId}`);
  return response.json();
}
