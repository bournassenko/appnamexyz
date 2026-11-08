import { env } from '$env/dynamic/public';
export const apiBase = env.PUBLIC_API_URL || '';
export const diagnostics: { method: string; path: string; status: number; duration: number }[] = [];
export function resource(path: string): string { return `${apiBase}${path}`; }
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const start = performance.now();
  const uploading = options.body instanceof FormData;
  const controller = new AbortController();
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, uploading ? 1800000 : 30000);
  const cancel = () => controller.abort();
  if (options.signal?.aborted) cancel();
  else options.signal?.addEventListener('abort', cancel, { once: true });
  try {
    const response = await fetch(resource(`/api/v1${path}`), { ...options, headers: uploading ? options.headers : { 'Content-Type': 'application/json', ...options.headers }, signal: controller.signal });
    diagnostics.unshift({ method: options.method || 'GET', path, status: response.status, duration: Math.round(performance.now() - start) });
    diagnostics.splice(40);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || `Request failed (${response.status}).`);
    return data as T;
  } catch (error) {
    if (timedOut) throw new Error('The request timed out. Check your connection and try again.');
    throw error;
  } finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener('abort', cancel);
  }
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

export interface OutputConfig { profile: string; page_width_mm: number; page_height_mm: number; line_width: number; label_size: number; symbol_size: number; minimum_spacing_mm: number; legend: boolean; texture: string; contrast: string; tactile_title: string; braille_table: string; label_mode: string; split_crowded_maps: boolean; max_map_views: number; key_position: string }
export const defaultOutput: OutputConfig = { profile: 'bana2022', page_width_mm: 292.1, page_height_mm: 279.4, line_width: 1.2, label_size: 14, symbol_size: 5, minimum_spacing_mm: 3.2, legend: true, texture: 'none', contrast: 'high', tactile_title: 'Map', braille_table: 'en-ueb-g2.ctb', label_mode: 'numbered', split_crowded_maps: false, max_map_views: 1, key_position: 'below' };
export const outputProfiles = [{ id: 'bana2022', name: 'BANA 2022 preparation' }, { id: 'digital', name: 'Digital preview' }, { id: 'print', name: 'Printable document' }, { id: 'swell', name: 'Swell paper oriented (unvalidated)' }];
export interface ValidationIssue { code: string; rule: string; message: string; element_ids: string[]; severity: string; view_number?: number; view_numbers?: number[]; measured_mm: number | null; required_mm: number | null }
export interface ValidationReport { page?: { width_mm: number; height_mm: number }; status: string; standard?: string; source_url?: string; automated_checks_passed: boolean; certified: boolean; issues: ValidationIssue[]; manual_checks: { rule: string; task: string }[]; pages: { index: number; role: string; recommended_side: string; name?: string; kind?: string; view_number?: number; source_bounds?: number[]; zoom?: number }[]; panels?: { number: number; name: string; source_bounds: number[]; zoom: number; element_ids: string[]; map_page_index: number; key_page_indices: number[]; full_labels: number; numbered_labels: number; automated_checks_passed: boolean }[]; layout?: { crowded: boolean; automatically_split: boolean; map_views: number; maximum_views: number; limit_reached: boolean; coverage_complete: boolean; visible_elements: number; covered_elements: number }; labels?: { element_id: string; text: string; braille: string; key: string; bounds_mm: number[]; view_number?: number }[]; map_page_index: number; print_instructions?: string }
