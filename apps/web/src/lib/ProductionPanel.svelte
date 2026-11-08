<script lang="ts">
  import { resource } from './api';
  import { productionApi, type ProductionMode, type ProductionOptions, type ProductionScene, type ProductionJob } from './production';
  import type { createViewer } from './production-viewer';
  let { projectId, revision }: { projectId: string; revision: number } = $props();
  let open = $state(false);
  let mode = $state<ProductionMode>('swell');
  let base = $state(1.2);
  let relief = $state(0.8);
  let dpi = $state(300);
  let host = $state<HTMLDivElement>();
  let scene = $state<ProductionScene | null>(null);
  let viewer = $state<ReturnType<typeof createViewer> | null>(null);
  let loading = $state(false);
  let error = $state('');
  let unavailable = $state('');
  let job = $state<ProductionJob | null>(null);
  let download = $state('');
  let light = $state(3.9);
  const options = $derived<ProductionOptions>({ mode, base_thickness_mm: base, braille_height_mm: 0.48, ...(mode === 'print' ? { relief_height_mm: relief } : {}), raster_dpi: dpi });
  let pollController: AbortController | null = null;
  $effect(() => {
    const id = projectId, version = revision;
    const settings = options;
    if (!open || !host) return;
    const container = host;
    const controller = new AbortController();
    let disposed = false;
    let instance: ReturnType<typeof createViewer> | null = null;
    loading = true; error = ''; unavailable = ''; scene = null; download = ''; job = null;
    pollController?.abort();
    productionApi.preview(id, settings, controller.signal).then(async data => {
      if (disposed) return;
      scene = data;
      try {
        const module = await import('./production-viewer');
        if (disposed) return;
        instance = module.createViewer(container, data);
        viewer = instance;
      } catch {
        unavailable = '3D preview needs WebGL 2. Use a compatible browser to view the model. Export files are still available.';
      }
    }).catch(reason => { if (!disposed) error = reason instanceof Error ? reason.message : 'Could not load the production preview.'; }).finally(() => { if (!disposed) loading = false; });
    return () => { disposed = true; controller.abort(); instance?.dispose(); viewer = null; pollController?.abort(); };
  });
  async function generate(format: string) {
    pollController?.abort();
    const controller = new AbortController();
    pollController = controller;
    error = ''; download = ''; job = { id: '', status: 'queued', stage: 'Starting export', progress: 0 };
    const id = projectId;
    try {
      let current = await productionApi.start(id, options, format);
      while (!controller.signal.aborted) {
        job = current;
        if (current.status === 'failed') throw new Error(current.error || 'The production export failed.');
        if (current.status === 'completed') { download = resource(`/api/v1/projects/${id}/production/jobs/${current.id}/download`); break; }
        await new Promise<void>(resolve => { const timer = setTimeout(resolve, 700); controller.signal.addEventListener('abort', () => { clearTimeout(timer); resolve(); }, { once: true }); });
        if (controller.signal.aborted) return;
        current = await productionApi.job(id, current.id, controller.signal);
      }
    } catch (reason) { if (!controller.signal.aborted) { error = reason instanceof Error ? reason.message : 'Export failed.'; job = null; } }
  }
  const exporting = $derived(job?.status === 'queued' || job?.status === 'running');
</script>

<section class="panel production-panel">
  <button class="secondary" aria-expanded={open} aria-controls="production-content" onclick={() => open = !open}>{open ? 'Close 3D preview' : '3D preview & production'}</button>
  {#if open}
    <div id="production-content">
      <h2>Production preview</h2>
      <div class="modes" role="group" aria-label="Production method">
        {#each [{ id: 'swell', name: 'Swell paper' }, { id: 'print', name: '3D printing' }, { id: 'emboss', name: 'Embosser' }] as item}
          <button class="secondary" aria-pressed={mode === item.id} onclick={() => mode = item.id as ProductionMode}>{item.name}</button>
        {/each}
      </div>
      <p class="small muted">{mode === 'swell' ? 'Raised black lines and braille on paper. Expansion is illustrative.' : mode === 'print' ? 'Solid plate with raised map features and braille. Check your printer’s build volume; do not scale braille.' : 'Generic dot-graphics approximation. Device-specific spacing and output are not validated. BRF exports the text key only.'}</p>
      {#if mode === 'print'}<div class="parameters"><label>Base thickness (mm)<input type="number" min="0.6" max="5" step="0.1" bind:value={base}/></label><label>Feature height (mm)<input type="number" min="0.2" max="2" step="0.1" bind:value={relief}/></label></div>{/if}
      <div class="stage" bind:this={host} aria-busy={loading}></div>
      {#if loading}<p role="status">Preparing 3D preview…</p>{/if}
      {#if unavailable}<p role="status">{unavailable}</p>{/if}
      {#if viewer}<div class="controls" role="group" aria-label="3D view controls"><button class="secondary" aria-label="Rotate model left" onclick={() => viewer?.rotate(-0.2)}>Rotate left</button><button class="secondary" aria-label="Rotate model right" onclick={() => viewer?.rotate(0.2)}>Rotate right</button><button class="secondary" aria-label="Zoom into 3D model" onclick={() => viewer?.zoom(0.8)}>Zoom in</button><button class="secondary" aria-label="Zoom out of 3D model" onclick={() => viewer?.zoom(1.25)}>Zoom out</button><button class="secondary" onclick={() => viewer?.reset()}>Reset view</button><label>Light direction<input type="range" min="0" max="6.28" step="0.1" bind:value={light} oninput={() => viewer?.light(light)}/></label></div>{/if}
      {#if scene}<p class="small">{scene.width_mm.toFixed(1)} × {scene.height_mm.toFixed(1)} mm · {scene.braille_dot_count} braille dots · {scene.braille_height_mm} mm braille height</p><p class="small muted">Approximate material appearance. Physical proof and hardware calibration required.</p>
        <div class="controls">
          {#if mode === 'print'}<button class="secondary" disabled={exporting} onclick={() => generate('stl')}>Generate STL</button>{:else if mode === 'emboss'}<button class="secondary" disabled={exporting} onclick={() => generate('brf')}>Generate BRF key (text only)</button>{:else}<label>Resolution<select bind:value={dpi}><option value={300}>300 DPI</option><option value={150}>150 DPI</option></select></label><button class="secondary" disabled={exporting} onclick={() => generate('png')}>Generate swell-paper PNG</button>{/if}
        </div>
      {/if}
      {#if job}<p role="status">{job.stage}{exporting ? ` · ${job.progress}%` : ''}</p>{/if}
      {#if download}<a class="secondary download" href={download} download>Download generated file</a>{/if}
      {#if error}<p role="alert">{error}</p>{/if}
    </div>
  {/if}
</section>

<style>
  .production-panel { margin-top: 1rem; }
  h2 { margin-top: 1.25rem; }
  .modes, .controls, .parameters { display: flex; flex-wrap: wrap; gap: .65rem; align-items: center; margin: .9rem 0; }
  .modes button[aria-pressed='true'] { outline: 2px solid currentColor; outline-offset: 2px; }
  .stage { width: 100%; height: clamp(260px, 50vh, 520px); border: 1px solid var(--border, #888); border-radius: 8px; overflow: hidden; }
  label { display: flex; flex-direction: column; gap: .35rem; font-size: .85rem; }
  input[type='number'] { max-width: 9rem; }
  .download { display: inline-flex; margin-top: .6rem; }
  p[role='alert'] { font-weight: 600; }
</style>
