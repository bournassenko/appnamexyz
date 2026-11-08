import { api } from './api';
export type ProductionMode = 'swell' | 'print' | 'emboss';
export interface ProductionOptions { mode: ProductionMode; relief_height_mm?: number; braille_height_mm: number; base_thickness_mm: number; raster_dpi: number }
export interface ProductionScene { width_mm: number; height_mm: number; base_thickness_mm: number; relief_height_mm: number; braille_height_mm: number; mode: ProductionMode; graphics: { type: string; coordinates: number[][][] | number[][][][] }; braille_dots: number[][]; graphic_dots: number[][]; braille_dot_count: number; layout_passed: boolean }
export interface ProductionJob { id: string; status: string; stage: string; progress: number; error?: string; metadata?: Record<string, unknown> }
export const productionApi = {
  preview: (id: string, options: ProductionOptions, signal: AbortSignal) => api<ProductionScene>(`/projects/${id}/production/preview`, { method: 'POST', body: JSON.stringify(options), signal }),
  start: (id: string, options: ProductionOptions, format: string) => api<ProductionJob>(`/projects/${id}/production/exports`, { method: 'POST', body: JSON.stringify({ ...options, format }) }),
  job: (id: string, job: string, signal: AbortSignal) => api<ProductionJob>(`/projects/${id}/production/jobs/${job}`, { signal })
};
