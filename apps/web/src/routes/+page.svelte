<script lang="ts">
  import { onMount } from 'svelte';
  import { Dialog, Popover } from 'bits-ui';
  import { ArrowRight, Upload, Settings, Eye, SlidersHorizontal, Sparkles, Download, Undo2, Redo2, ZoomIn, ZoomOut, Maximize, Check, Map, X, Send, Code, ArrowUpRight } from 'lucide-svelte';
  import { api, resource, diagnostics, defaultOutput, outputProfiles, type ValidationReport, type Branding, type Config, type Engine, type Job, type Project, type Representation, type Status } from '$lib/api';
  import { fallbackBranding } from '$lib/branding';
  import { defaults, loadPreferences, savePreferences } from '$lib/preferences';
  import MapStack from '$lib/MapStack.svelte';
  import '../style.css';

  let brand = $state<Branding>(fallbackBranding);
  let preferences = $state({ ...defaults });
  let mode = $state('easy');
  let status = $state<Status>({ enabled: true, ai_available: false, active_jobs: 0, session_hours: 12, max_upload_mb: 1024 });
  let engines = $state<Engine[]>([]);
  let project = $state<Project | null>(null);
  let rep = $state<Representation | null>(null);
  let job = $state<Job | null>(null);
  let busy = $state(false);
  let error = $state('');
  let notice = $state('');
  let instruction = $state('');
  let previewVersion = $state(0);
  let zoom = $state(1);
  let compare = $state(true);
  let showEnglish = $state(false);
  let settingsOpen = $state(false);
  let messages = $state<{ role: string; text: string }[]>([]);
  let fileInput: HTMLInputElement;
  let diagnosticsData = $state('');
  let validation = $state<ValidationReport | null>(null);
  let previewPage = $state<number | null>(null);
  let config = $state<Config>({ engine: 'auto', language: 'eng', map_type: 'automatic', include: ['borders', 'rivers', 'roads', 'regions', 'cities', 'labels', 'landmarks', 'other'], simplification: 0.003, density: 'balanced', preprocess: true, output: { ...defaultOutput } });
  const categories = ['borders', 'rivers', 'roads', 'regions', 'cities', 'labels', 'landmarks', 'other'];
  const visibleCount = $derived(rep?.elements.filter(e => e.visible).length || 0);
  const uploadLimit = $derived(status.max_upload_mb >= 1024 ? `${status.max_upload_mb / 1024} GB` : `${status.max_upload_mb} MB`);
  const outputDirty = $derived(!!rep && JSON.stringify(config.output) !== JSON.stringify(rep.export_settings));
  const exportBlocked = $derived(rep?.export_settings.profile === 'bana2022' && !validation?.automated_checks_passed);
  const selectedView = $derived(validation?.panels?.find(view => view.number === validation?.pages.find(page => page.index === previewPage)?.view_number) || validation?.panels?.[0]);
  const selectedKey = $derived(validation?.labels?.filter(label => label.key && label.view_number === selectedView?.number).sort((first, second) => Number(first.key) - Number(second.key)) || []);
  const layoutErrors = $derived(validation?.issues.filter(issue => issue.severity === 'error') || []);
  const previewUrl = $derived(project ? resource(`/api/v1/projects/${project.id}/preview?v=${previewVersion}${previewPage === null ? '' : `&page=${previewPage}`}${showEnglish && rep?.export_settings.profile === 'bana2022' ? '&english=true' : ''}`) : '');

  onMount(() => {
    preferences = loadPreferences();
    mode = preferences.mode;
    config.engine = preferences.engine;
    config.output.profile = preferences.profile;
    void initialize();
  });

  $effect(() => {
    if (typeof document !== 'undefined') {
      const dark = preferences.theme === 'dark' || preferences.theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches;
      document.documentElement.dataset.theme = preferences.theme === 'system' ? (dark ? 'dark' : 'light') : preferences.theme;
      document.documentElement.dataset.contrast = preferences.highContrast ? 'high' : 'normal';

      document.documentElement.style.fontSize = `${preferences.textSize}%`;
      document.documentElement.dataset.motion = preferences.reducedMotion ? 'reduced' : 'normal';
      savePreferences(preferences);
    }
  });

  async function initialize() {
    try {
      [brand, status, engines] = await Promise.all([api<Branding>('/branding'), api<Status>('/status'), api<Engine[]>('/engines')]);
      const saved = sessionStorage.getItem('project');
      if (saved) {
        try { project = await api<Project>(`/projects/${saved}`); config = project.config; config.output = { ...defaultOutput, ...config.output }; if (project.revision >= 0) await refresh(); }
        catch { sessionStorage.removeItem('project'); }
      }
    } catch (e) { fail(e); }
  }
  function fail(e: unknown) { error = e instanceof Error ? e.message : 'An unexpected error occurred. Please try again.'; }
  async function guard(action: () => Promise<void>) { error = ''; notice = ''; busy = true; try { await action(); } catch (e) { fail(e); } finally { busy = false; } }
  async function upload(file?: File) {
    if (!file) return;
    await guard(async () => {
      if (file.size > status.max_upload_mb * 1024 * 1024) throw new Error(`Choose an image no larger than ${uploadLimit}.`);
      const data = new FormData(); data.append('file', file);
      notice = 'Uploading your image…';
      project = await api<Project>('/projects', { method: 'POST', body: data });
      sessionStorage.setItem('project', project.id);
      rep = null; validation = null; previewPage = null; job = null; messages = []; notice = 'Your map is ready to analyze.';
    });
  }
  async function refresh() {
    if (!project) return;
    [rep, project] = await Promise.all([api<Representation>(`/projects/${project.id}/representation`), api<Project>(`/projects/${project.id}`)]);
    config.output = { ...defaultOutput, ...rep.export_settings };
    validation = await api<ValidationReport>(`/projects/${project.id}/validation`);
    previewPage = validation.status === 'not_applicable' ? null : validation.map_page_index;
    previewVersion++; zoom = 1;
    if (preferences.developer) diagnosticsData = JSON.stringify(await api(`/projects/${project.id}/diagnostics`), null, 2);
  }
  async function waitForJob(value: Job) {
    if (!project) return;
    job = value;
    for (let attempt = 0; attempt < 150; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 1500));
      job = await api<Job>(`/projects/${project.id}/jobs/${value.id}`);
      if (job.status === 'failed') throw new Error(job.error || 'Processing failed.');
      if (job.status === 'completed') {
        if (!job.clarification) await refresh();
        notice = job.message || 'Your map is ready.';
        return;
      }
    }
    throw new Error('Processing is taking longer than expected. Reload the page to check the project.');
  }
  async function analyze() {
    await guard(async () => {
      if (!project) return;
      project = await api<Project>(`/projects/${project.id}/configuration`, { method: 'PATCH', body: JSON.stringify(config) });
      await waitForJob(await api<Job>(`/projects/${project.id}/analyze`, { method: 'POST' }));
    });
  }
  async function refine(text = instruction) {
    if (!text.trim() || !project) return;
    instruction = ''; messages.push({ role: 'You', text });
    await guard(async () => {
      if (!project) return;
      await waitForJob(await api<Job>(`/projects/${project.id}/refine`, { method: 'POST', body: JSON.stringify({ instruction: text }) }));
      messages.push({ role: 'Assistant', text: job?.message || 'Changes applied.' });
    });
  }
  async function history(direction: string) { await guard(async () => { if (project) { await api(`/projects/${project.id}/history/${direction}`, { method: 'POST' }); await refresh(); } }); }
  async function editElement(id: string, update: Record<string, unknown>) { await guard(async () => { if (project) { await api(`/projects/${project.id}/elements/${id}`, { method: 'PATCH', body: JSON.stringify(update) }); await refresh(); } }); }
  async function download(format: string) {
    await guard(async () => {
      if (!project) return;
      const result = await api<{ url: string; filename: string }>(`/projects/${project.id}/exports/${format}${previewPage !== null && ['svg', 'png'].includes(format) ? `?page=${previewPage}` : ''}`, { method: 'POST' });
      const link = document.createElement('a'); link.href = resource(result.url); link.download = result.filename; document.body.appendChild(link); link.click(); link.remove(); notice = `${format.toUpperCase()} export generated.`;
    });
  }
  async function applyOutput() {
    await guard(async () => {
      if (!project || !rep) return;
      await api(`/projects/${project.id}/output`, { method: 'PATCH', body: JSON.stringify(config.output) });
      await refresh();
    });
  }
  function downloadValidation() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(validation, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = 'bana-2022-validation.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }
  function formatLabel(format: string) {
    if (format === 'bundle') return 'Production ZIP';
    return format.toUpperCase() + (rep?.export_settings.profile === 'bana2022' && validation && validation.pages.length > 1 && format !== 'pdf' ? ' page' : '');
  }
  async function copyDiagnostics() { try { await navigator.clipboard.writeText(diagnosticsData); notice = 'Diagnostics copied.'; } catch { error = 'Clipboard unavailable. Select and copy the JSON below.'; } }
</script>

<svelte:head><title>{brand.name}</title><meta name="description" content={brand.description} /><link rel="icon" href={brand.favicon} /></svelte:head>

<a class="skip-link" href="#main">Skip to main content</a>
<header class="topbar">
  <a href="/" class="brand" aria-label={`${brand.name} home`}><img src={brand.logo} alt="" width="36" height="36" /><span>{brand.name}</span></a>
  <div class="header-actions">
    <Popover.Root><Popover.Trigger class="accessibility-trigger"><Eye size={18}/><span>Accessibility</span></Popover.Trigger><Popover.Portal><Popover.Content class="accessibility-popover" sideOffset={8} align="end" role="dialog" aria-labelledby="accessibility-heading"><div class="dialog-heading"><h2 id="accessibility-heading">Accessibility</h2><Popover.Close class="icon-button" aria-label="Close accessibility settings"><X size={18}/></Popover.Close></div><label>Color theme<select bind:value={preferences.theme}><option value="system">System</option><option value="light">Black Text, White Background</option><option value="dark">White Text, Black Background</option><option value="blue-text">Blue Text, White Background</option><option value="blue-background">White Text, Blue Background</option></select></label><label class="check"><input type="checkbox" bind:checked={preferences.highContrast}/>High contrast</label><label>Text size: {preferences.textSize}%<input type="range" min="100" max="150" step="10" bind:value={preferences.textSize}/></label><label class="check"><input type="checkbox" bind:checked={preferences.reducedMotion}/>Reduce motion</label></Popover.Content></Popover.Portal></Popover.Root>
    <button class="settings-button" onclick={() => settingsOpen = true} aria-label="Open application settings"><Settings size={19} /><span>Settings</span></button>
  </div>
</header>

<main id="main">
  <div class="intro"><h1>Map conversion</h1></div>
  <nav class="workflow" aria-label="Transformation progress">{#each ['Upload', 'Analyze', 'Refine', 'Export'] as step, index}<div class:active={rep ? index >= 2 : project ? index === 1 : index === 0}><span>{(project && index === 0) || (rep && index === 1) ? '✓' : String(index + 1).padStart(2, '0')}</span>{step}</div>{#if index < 3}<div class="step-line"></div>{/if}{/each}</nav>
  <div class="notifications"><div role="alert">{#if error}<p class="error">{error}</p>{/if}</div><div role="status" aria-live="polite">{#if notice}<p class="notice">{notice}</p>{/if}</div></div>
  {#if !status.enabled}<p class="warning">Processing is paused. You can still preview and download existing results.</p>{/if}
  <div class="workspace">
    <aside class="sidebar">
      <div class="mode-tabs" aria-label="Application mode"><button class:chosen={mode === 'easy'} aria-pressed={mode === 'easy'} onclick={() => mode = 'easy'}><Sparkles size={15}/>Easy Mode</button><button class:chosen={mode === 'advanced'} aria-pressed={mode === 'advanced'} onclick={() => mode = 'advanced'}><SlidersHorizontal size={15}/>Advanced Mode</button></div>
      <section class="panel upload-panel"><div class="section-heading"><h2>Step 1: Upload</h2></div>
        <label class="dropzone" class:has-file={!!project} ondragover={(event) => event.preventDefault()} ondrop={(event) => { event.preventDefault(); if (!busy) void upload(event.dataTransfer?.files[0]); }}>
          <span class="upload-icon"><Upload size={23}/></span><strong>{project ? project.source.filename : 'Choose a map image'}</strong><span>{project ? 'Choose another image to start a new project' : 'or drag and drop it here'}</span><span class="file-hint">PNG, JPG, WebP · up to {uploadLimit}</span><input bind:this={fileInput} type="file" accept="image/png,image/jpeg,image/webp" disabled={busy} aria-label="Upload a map image" onchange={() => upload(fileInput.files?.[0])}/>
        </label><div class="privacy-note"><Check size={15}/><span>Files expire after {status.session_hours} hours.</span></div>
      </section>
      {#if mode === 'advanced'}
        <section class="panel advanced"><div class="section-heading"><SlidersHorizontal size={18}/><h2>Transformation settings</h2></div>
          <fieldset disabled={busy}><legend>Recognition</legend><label>Recognition engine<select bind:value={config.engine}>{#each engines as engine}<option value={engine.id} disabled={!engine.available}>{engine.name}{!engine.available ? ' (not configured)' : ''}</option>{/each}</select></label><label>Document language<select bind:value={config.language}><option value="eng">English</option><option value="fra">French</option><option value="spa">Spanish</option><option value="deu">German</option></select></label><label>Map type<select bind:value={config.map_type}><option value="automatic">Automatic</option><option value="political">Political</option><option value="physical">Physical geography</option><option value="hand-drawn">Hand-drawn</option></select></label><label class="check"><input type="checkbox" bind:checked={config.preprocess}/>Automatic preprocessing</label></fieldset>
          <fieldset disabled={busy}><legend>Content to include</legend><div class="filter-grid">{#each categories as category}<label class="check"><input type="checkbox" value={category} bind:group={config.include}/>{category}</label>{/each}</div><label>Information density<select bind:value={config.density}><option value="low">Low</option><option value="balanced">Balanced</option><option value="high">High</option></select></label><label>Simplification<input type="range" min="0" max="0.03" step="0.001" bind:value={config.simplification}/></label></fieldset>
          <fieldset disabled={busy}><legend>Output</legend><label>Production profile<select bind:value={config.output.profile}>{#each outputProfiles as profile}<option value={profile.id}>{profile.name}</option>{/each}</select></label><div class="field-pair"><label>Page width (mm)<input type="number" min="100" max="420" bind:value={config.output.page_width_mm}/></label><label>Page height (mm)<input type="number" min="100" max="420" bind:value={config.output.page_height_mm}/></label></div>{#if config.output.profile !== 'bana2022'}<div class="field-pair"><label>Line width (mm)<input type="number" min="0.3" max="5" step="0.1" bind:value={config.output.line_width}/></label><label>Label size<input type="number" min="8" max="40" bind:value={config.output.label_size}/></label></div><div class="field-pair"><label>Spacing target (mm)<input type="number" min="1" max="20" bind:value={config.output.minimum_spacing_mm}/></label><label>Symbol size<input type="number" min="2" max="20" bind:value={config.output.symbol_size}/></label></div>{/if}<label>Region texture<select bind:value={config.output.texture}><option value="none">None</option><option value="dots">Dots</option><option value="diagonal">Diagonal lines</option></select></label><label class="check"><input type="checkbox" bind:checked={config.output.legend}/>Include key</label><p class="small muted">{config.output.profile === 'bana2022' ? 'BANA dimensions override digital line, symbol, and print label sizes.' : 'Digital profile: BANA rules are not applied.'}</p></fieldset>
        </section>
      {/if}
      <section class="panel"><div class="section-heading"><h2>Step 2: Analysis</h2></div><div class="engine-note"><Sparkles size={16}/><span>{status.ai_available ? 'OpenAI configured' : 'Local processing'}</span></div><p class="small muted">{status.ai_available && !['opencv', 'ocr'].includes(config.engine) ? 'Image sent to OpenAI for analysis.' : 'Image processed locally.'}</p><button class="primary wide" disabled={!project || busy || !status.enabled} onclick={analyze}>{busy ? 'Processing…' : rep ? 'Analyze again' : 'Analyze & generate'}<ArrowRight size={17}/></button>
        {#if busy && job}<div class="job-progress"><label for="job-progress">{job.stage}</label><progress id="job-progress" max="100" value={job.progress}></progress><span>{job.progress}%</span></div>{/if}
      </section>
    </aside>
    <div class="main-column">
      <section class="panel preview-panel" aria-labelledby="preview-heading"><div class="preview-header"><div><h2 id="preview-heading">Preview</h2></div>{#if rep}<span class="ready-badge"><Check size={13}/> Ready to review</span>{/if}</div>
        {#if rep && project}
          {#if validation?.layout?.automatically_split}<p class="layout-summary" role="status">{validation.layout.map_views} map views · {validation.layout.covered_elements} of {validation.layout.visible_elements} features included{validation.layout.limit_reached ? ' · view limit reached' : ''}</p>{/if}
          {#if validation && validation.pages.length > 1}<div class="page-selector"><label>Preview page<select bind:value={previewPage}>{#each validation.pages as page}<option value={page.index}>{page.name || (page.role === 'map' ? 'Map' : page.role.replace('-', ' '))}{page.kind === 'map' && page.zoom ? ` · ${page.zoom}×` : ''}</option>{/each}</select></label></div>{/if}
          <div class="preview-toolbar"><label class="check"><input type="checkbox" bind:checked={compare}/>Compare original</label>{#if rep.export_settings.profile === 'bana2022'}<button class="secondary preview-translation" aria-pressed={showEnglish} onclick={() => showEnglish = !showEnglish}>{showEnglish ? 'Show braille' : 'Show English'}</button>{/if}<div class="toolbar-buttons"><button class="icon-button" aria-label="Undo last revision" disabled={busy || project.revision <= 0} onclick={() => history('undo')}><Undo2 size={17}/></button><button class="icon-button" aria-label="Redo revision" disabled={busy || project.revision >= project.latest_revision} onclick={() => history('redo')}><Redo2 size={17}/></button><span class="divider"></span><button class="icon-button" aria-label="Zoom out" onclick={() => zoom = Math.max(0.5, zoom - 0.25)}><ZoomOut size={17}/></button><span class="small">{Math.round(zoom * 100)}%</span><button class="icon-button" aria-label="Zoom in" onclick={() => zoom = Math.min(3, zoom + 0.25)}><ZoomIn size={17}/></button><button class="icon-button" aria-label="Fit map to screen" onclick={() => zoom = 1}><Maximize size={17}/></button></div></div>
          {#if showEnglish && rep.export_settings.profile === 'bana2022'}<p class="translation-note" role="status">English from the displayed braille. Contracted cells may contain several letters.</p>{/if}
          <div class="comparison" class:single={!compare}>{#if compare}<div class="preview-side"><span class="preview-label">ORIGINAL SOURCE</span><div class="image-scroll" tabindex="0" role="group" aria-label="Original map, scroll to pan"><div class="source-frame" style:width={`${zoom * 100}%`}><img src={resource(`/api/v1/projects/${project.id}/source`)} alt={validation?.layout?.automatically_split && selectedView ? `Original map. The outlined area corresponds to ${selectedView.name}.` : 'Original uploaded educational map'}/>{#if validation?.layout?.automatically_split && selectedView}<svg class="source-window" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true"><rect x={selectedView.source_bounds[0]} y={selectedView.source_bounds[1]} width={selectedView.source_bounds[2] - selectedView.source_bounds[0]} height={selectedView.source_bounds[3] - selectedView.source_bounds[1]} fill="none" stroke="white" stroke-width="5" vector-effect="non-scaling-stroke"/><rect x={selectedView.source_bounds[0]} y={selectedView.source_bounds[1]} width={selectedView.source_bounds[2] - selectedView.source_bounds[0]} height={selectedView.source_bounds[3] - selectedView.source_bounds[1]} fill="none" stroke="black" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>{/if}</div></div></div>{/if}<div class="preview-side"><span class="preview-label">ACCESSIBLE REPRESENTATION</span><div class="image-scroll" tabindex="0" role="group" aria-label="Generated map, scroll to pan"><img style:width={`${zoom * 100}%`} src={previewUrl} alt={showEnglish && rep.export_settings.profile === 'bana2022' ? `English back-translation of displayed braille. ${rep.description}` : rep.description} onerror={() => error = 'The preview could not be loaded. Refresh the page or switch the preview back to braille.'}/></div></div></div>
          {#if selectedView && validation?.layout?.automatically_split}<p class="layout-summary">{selectedView.name} · {selectedView.zoom}× source scale · {selectedView.element_ids.length} features</p>{/if}
          {#if selectedKey.length}<details class="numbered-key"><summary>Numbered key</summary><table><thead><tr><th scope="col">Reference</th><th scope="col">Full name</th></tr></thead><tbody>{#each selectedKey as entry}<tr><td>{entry.key} <span aria-label={`Braille ${entry.key}`}>{entry.braille}</span></td><td>{entry.text}</td></tr>{/each}</tbody></table></details>{/if}
          <div class="description"><div><Map size={18}/><strong>{rep.map_type}</strong><span class="small muted">{visibleCount} visible features</span></div><p>{rep.description}</p></div>
        {:else if project}
          <div class="source-ready"><img src={resource(`/api/v1/projects/${project.id}/source`)} alt="Original uploaded map ready for analysis"/><p>Source uploaded. Select <strong>Analyze & generate</strong> to begin.</p></div>
        {:else}
          <div class="empty-preview"><MapStack/><p>Upload a map to start.</p></div>
        {/if}
      </section>
      {#if rep}
        {#if rep.warnings.length}<details class="panel warnings"><summary>{rep.warnings.length} review notes and uncertainties</summary><ul>{#each rep.warnings as warning}<li>{warning}</li>{/each}</ul></details>{/if}
        <section class="panel feedback"><div class="section-heading"><span class="chat-icon"><Sparkles size={19}/></span><div><h2>Refine</h2></div></div><div class="chat-history" role="log" aria-label="Map refinement conversation" aria-live="polite">{#each messages as message}<div class:from-user={message.role === 'You'}><strong>{message.role}</strong><p>{message.text}</p></div>{/each}</div><div class="suggestions">{#each ['Remove the rivers', 'Simplify the map', 'Make labels larger'] as suggestion}<button disabled={busy || !status.enabled} onclick={() => refine(suggestion)}>{suggestion}<ArrowUpRight size={13}/></button>{/each}</div><form onsubmit={(event) => { event.preventDefault(); void refine(); }}><label class="sr-only" for="refinement">Describe a change to your map</label><input id="refinement" bind:value={instruction} placeholder="For example, keep only country borders…" maxlength="1500" disabled={busy || !status.enabled}/><button class="primary" type="submit" disabled={busy || !instruction.trim() || !status.enabled} aria-label="Apply map refinement"><Send size={18}/></button></form></section>
        <details class="panel element-editor"><summary>Map elements <span class="small muted">{visibleCount} of {rep.elements.length} visible</span></summary><div class="element-list">{#each rep.elements as element, index}<div class="element-row"><label class="check"><input type="checkbox" checked={element.visible} disabled={busy} onchange={(event) => editElement(element.id, { visible: event.currentTarget.checked })}/><span class="sr-only">Show element {index + 1}</span></label><input aria-label={`Label for element ${index + 1}`} value={element.label} placeholder={`Feature ${index + 1}`} disabled={busy} onchange={(event) => editElement(element.id, { label: event.currentTarget.value })}/><select aria-label={`Category for element ${index + 1}`} value={element.category} disabled={busy} onchange={(event) => editElement(element.id, { category: event.currentTarget.value })}>{#each categories as category}<option value={category}>{category}</option>{/each}</select><span class="small muted" title={`Provenance: ${element.provenance}`}>{Math.round(element.confidence * 100)}%</span></div>{/each}</div></details>
        <section class="panel output-settings"><h2>Output</h2><div class="output-fields"><label>Output profile<select bind:value={config.output.profile} disabled={busy}>{#each outputProfiles as profile}<option value={profile.id}>{profile.name}</option>{/each}</select></label>{#if config.output.profile === 'bana2022'}<label>Tactile title<input bind:value={config.output.tactile_title} maxlength="120" disabled={busy}/></label><label>Braille table<select bind:value={config.output.braille_table} disabled={busy}><option value="en-ueb-g2.ctb">English UEB, contracted</option></select></label><label>Map labels<select bind:value={config.output.label_mode} disabled={busy}><option value="auto">Full names, numbers when needed</option><option value="numbered">Numbered braille key</option><option value="full">Full names only</option></select></label><label>Key position<select bind:value={config.output.key_position} disabled={busy}><option value="below">Below map</option><option value="before">Before map (BANA)</option></select></label><label class="check"><input type="checkbox" bind:checked={config.output.split_crowded_maps} disabled={busy}/>Automatically split crowded maps</label>{#if config.output.split_crowded_maps}<label>Maximum map views<input type="number" min="1" max="6" bind:value={config.output.max_map_views} disabled={busy}/></label>{/if}<div class="field-pair"><label>Width (mm)<input type="number" min="100" max="420" step="0.1" bind:value={config.output.page_width_mm} disabled={busy}/></label><label>Height (mm)<input type="number" min="100" max="420" step="0.1" bind:value={config.output.page_height_mm} disabled={busy}/></label></div>{/if}</div><button class="secondary" disabled={busy || !outputDirty} onclick={applyOutput}>Apply output settings</button></section>
        {#if validation && rep.export_settings.profile === 'bana2022'}<section class="panel validation"><h2>BANA 2022 checks</h2><p class:validation-blocked={layoutErrors.length > 0} role="status">{validation.automated_checks_passed ? 'Automated checks passed. Human and physical review required.' : `Production export blocked: ${layoutErrors.length} layout errors.`}</p>{#if validation.issues.length}<ul>{#each validation.issues as issue}<li><strong>{issue.severity === 'error' ? 'Fix' : 'Review'} · {issue.rule}</strong><p>{issue.message}{#if issue.measured_mm !== null} Measured: {issue.measured_mm} mm. Required: {issue.required_mm} mm.{/if}</p>{#if issue.element_ids.length}<span class="small muted">{issue.element_ids.map(id => rep?.elements.find(element => element.id === id)?.label || id).join(', ')}</span>{/if}</li>{/each}</ul>{/if}<details><summary>Required human checks</summary><ul>{#each validation.manual_checks as check}<li><strong>{check.rule}</strong><p>{check.task}</p></li>{/each}</ul></details><p class="small muted">{validation.print_instructions}</p><button class="secondary" onclick={downloadValidation}>Download validation report</button>{#if validation.source_url}<a href={validation.source_url} target="_blank" rel="noreferrer" class="manual-link">BANA 2022 manual</a>{/if}</section>{/if}
        <section class="panel export-panel"><div><h2>Export</h2></div><div class="export-buttons">{#each (rep.export_settings.profile === 'bana2022' ? ['svg', 'pdf', 'png', 'bundle'] : ['svg', 'pdf', 'png']) as format}<button class="secondary" disabled={busy || exportBlocked || outputDirty} onclick={() => download(format)}><Download size={16}/>{formatLabel(format)}</button>{/each}</div><p class="small muted full-width">{rep.export_settings.profile === 'bana2022' ? 'PDF and Production ZIP include all views and keys. SVG and PNG export the selected page. Print at 100%. Physical proof required.' : 'Digital output. BANA rules are not applied.'}</p></section>
      {/if}
      {#if preferences.developer}<section class="panel developer"><div class="section-heading"><Code size={19}/><h2>Developer diagnostics</h2></div><p>Processing: <strong>{status.enabled ? 'Enabled' : 'Disabled'}</strong> · OpenAI: {status.ai_available ? 'Configured' : 'Unavailable'}</p>{#if rep}<button class="secondary" onclick={() => guard(async () => { await refresh(); })}>Refresh diagnostics</button><button class="secondary" onclick={copyDiagnostics}>Copy JSON</button><textarea class="diagnostics-json" readonly aria-label="Project diagnostics JSON" value={diagnosticsData || JSON.stringify(rep, null, 2)}></textarea>{/if}<details><summary>Recent API requests</summary><textarea class="diagnostics-json" readonly aria-label="API request log" value={JSON.stringify(diagnostics, null, 2)}></textarea></details></section>{/if}
    </div>
  </div>
</main>

<Dialog.Root bind:open={settingsOpen}><Dialog.Portal><Dialog.Overlay class="dialog-overlay"/><Dialog.Content class="settings-dialog"><div class="dialog-heading"><Dialog.Title class="dialog-title">Application settings</Dialog.Title><Dialog.Close class="icon-button" aria-label="Close settings"><X size={21}/></Dialog.Close></div><Dialog.Description class="muted">Preferences are saved in this browser.</Dialog.Description><div class="settings-fields"><label>Default mode<select bind:value={preferences.mode}><option value="easy">Easy Mode</option><option value="advanced">Advanced Mode</option></select></label><label>Default recognition engine<select bind:value={preferences.engine}>{#each engines as engine}<option value={engine.id} disabled={!engine.available}>{engine.name}</option>{/each}</select></label><label>Default output profile<select bind:value={preferences.profile}>{#each outputProfiles as profile}<option value={profile.id}>{profile.name}</option>{/each}</select></label><label class="check"><input type="checkbox" bind:checked={preferences.developer} onchange={() => { if (rep) void guard(refresh); }}/>Developer Mode</label><p class="small muted">Processing: {status.enabled ? 'enabled' : 'disabled'}. Sessions expire after {status.session_hours} hours.</p></div><Dialog.Close class="primary wide">Done</Dialog.Close></Dialog.Content></Dialog.Portal></Dialog.Root>
