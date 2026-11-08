<script lang="ts">
  import { Map } from 'lucide-svelte';
</script>

<svg class="map-stack" viewBox="0 0 72 81" fill="none" aria-hidden="true">
  <path d="M36 39 69 58 36 77 3 58Z" fill="currentColor" opacity="0.06"/>
  <path d="M36 29 68 47.5 36 66 4 47.5Z" fill="var(--soft)" stroke="var(--border)" stroke-linejoin="round"/>
  <path d="M36 17 68 35.5 36 54 4 35.5Z" fill="var(--surface)" stroke="var(--border)" stroke-linejoin="round"/>
  <path d="M36 5 68 23.5 36 42 4 23.5Z" fill="var(--surface)" stroke="currentColor" stroke-opacity="0.45" stroke-linejoin="round"/>
  <g transform="matrix(.866 .5 -.866 .5 36 10)">
    <Map x="-13" y="0" size={26} strokeWidth={1.6}/>
  </g>
</svg>

<style>
  .map-stack{display:block;width:96px;height:108px;flex-shrink:0;color:var(--muted)}
</style>
