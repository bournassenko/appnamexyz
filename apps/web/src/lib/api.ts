import { env } from '$env/dynamic/public';
export const apiBase = env.PUBLIC_API_URL || '';
export const diagnostics: { method: string; path: string; status: number; duration: number }[] = [];
export function resource(path: string): string { return `${apiBase}${path}`; }
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const start = performance.now();
  const uploading = options.body instanceof FormData;
  const response = await fetch(resource(`/api/v1${path}`), { ...options, headers: uploading ? options.headers : { 'Content-Type': 'application/json', ...options.headers }, signal: AbortSignal.timeout(uploading ? 1800000 : 30000) });
  diagnostics.unshift({ method: options.method || 'GET', path, status: response.status, duration: Math.round(performance.now() - start) });
  diagnostics.splice(40);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error?.message || `Request failed (${response.status}).`);
  return data as T;
}
export interface Geometry { type: string; coordinates: number[][]; parts: number[][][] }
export interface Element { id: string; category: string; label: string; description: string; visible: boolean; confidence: number; geometry: Geometry; provenance: string }
export interface Representation { project_id: string; description: string; map_type: string; elements: Element[]; warnings: string[]; processing_metadata: Record<string, unknown>; modifications: string[]; export_settings: OutputConfig }
export interface Project { id: string; revision: number; latest_revision: number; source: { filename: string; width: number; height: number }; config: Config }
export interface Config { engine: string; language: string; map_type: string; include: string[]; simplification: number; density: string; preprocess: boolean; output: OutputConfig }
export interface Job { id: string; status: string; stage: string; progress: number; message?: string; error?: string; clarification?: boolean }
export interface Status { enabled: boolean; ai_available: boolean; active_jobs: number; session_hours: number; max_upload_mb: number }
export interface Branding { name: string; tagline: string; description: string; themeColor: string; logo: string; favicon: string; publicUrl: string }
export interface Engine { id: string; name: string; available: boolean; description: string }

export interface OutputConfig { profile: string; page_width_mm: number; page_height_mm: number; line_width: number; label_size: number; symbol_size: number; minimum_spacing_mm: number; legend: boolean; texture: string; contrast: string; tactile_title: string; braille_table: string }
export const defaultOutput: OutputConfig = { profile: 'bana2022', page_width_mm: 292.1, page_height_mm: 279.4, line_width: 1.2, label_size: 14, symbol_size: 5, minimum_spacing_mm: 3.2, legend: true, texture: 'none', contrast: 'high', tactile_title: 'Map', braille_table: 'en-ueb-g1.ctb' };
export const outputProfiles = [{ id: 'bana2022', name: 'BANA 2022 preparation' }, { id: 'digital', name: 'Digital preview' }, { id: 'print', name: 'Printable document' }, { id: 'swell', name: 'Swell paper oriented (unvalidated)' }];
export interface ValidationIssue { code: string; rule: string; message: string; element_ids: string[]; severity: string; measured_mm: number | null; required_mm: number | null }
export interface ValidationReport { status: string; standard?: string; source_url?: string; automated_checks_passed: boolean; certified: boolean; issues: ValidationIssue[]; manual_checks: { rule: string; task: string }[]; pages: { index: number; role: string; recommended_side: string }[]; map_page_index: number; print_instructions?: string }
