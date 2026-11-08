import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(AbortSignal, 'timeout', { configurable: true, value: undefined }));
});

test('keyboard settings, advanced configuration, and accessible initial screen', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Skip to main content' })).toBeFocused();
  await page.getByRole('button', { name: 'Open application settings' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByLabel('Developer Mode', { exact: true }).check();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await expect(page.getByRole('heading', { name: 'Developer diagnostics' })).toBeVisible();
  await page.getByRole('button', { name: 'Advanced Mode', exact: true }).click();
  await expect(page.getByRole('combobox', { name: 'Recognition engine', exact: true })).toBeVisible();
  await expect(page.getByText('Content to include', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /Disable new processing|Enable processing/ })).toHaveCount(0);
  for (const theme of ['light', 'dark', 'blue-text', 'blue-background']) {
    await page.getByRole('button', { name: 'Accessibility', exact: true }).click();
    await expect(page.getByRole('dialog', { name: 'Accessibility', exact: true })).toBeVisible();
    await page.getByRole('combobox', { name: 'Color theme', exact: true }).selectOption(theme);
    const panelResults = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
    expect(panelResults.violations).toEqual([]);
    await page.keyboard.press('Escape');
    const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
    expect(results.violations).toEqual([]);
    await page.getByRole('button', { name: 'Accessibility', exact: true }).click();
    await page.getByRole('checkbox', { name: 'High contrast', exact: true }).check();
    await page.keyboard.press('Escape');
    await expect(page.locator('html')).toHaveAttribute('data-contrast', 'high');
    const highContrastResults = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
    expect(highContrastResults.violations).toEqual([]);
    await page.getByRole('button', { name: 'Accessibility', exact: true }).click();
    await page.getByRole('checkbox', { name: 'High contrast', exact: true }).uncheck();
    await page.keyboard.press('Escape');
  }
});

test('upload, analyze, refine, undo, and download a map', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Upload a map image').setInputFiles('tests/map.png');
  await expect(page.getByText('Your map is ready to analyze.')).toBeVisible();
  await page.getByRole('button', { name: 'Analyze & generate' }).click();
  await expect(page.getByRole('heading', { name: 'Refine', exact: true })).toBeVisible({ timeout: 190000 });
  await expect(page.getByRole('heading', { name: 'BANA 2022 checks' })).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Output profile', exact: true })).toHaveValue('bana2022');
  await page.getByRole('button', { name: 'Show English', exact: true }).click();
  await expect(page.locator('.preview-side img').last()).toHaveAttribute('src', /english=true/);
  await expect(page.locator('.preview-side img').last()).toHaveJSProperty('complete', true);
  await expect(page.locator('.preview-side img').last()).not.toHaveJSProperty('naturalWidth', 0);
  await page.getByRole('button', { name: 'Show braille', exact: true }).click();
  await expect(page.locator('.preview-side img').last()).not.toHaveAttribute('src', /english=true/);
  await page.getByRole('combobox', { name: 'Output profile', exact: true }).selectOption('digital');
  await page.getByRole('button', { name: 'Apply output settings', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'BANA 2022 checks' })).not.toBeVisible();
  await page.getByRole('button', { name: 'Make labels larger', exact: true }).click();
  await expect(page.getByText('Increased label size.').first()).toBeVisible();
  await page.getByRole('button', { name: 'Undo last revision' }).click();
  await expect(page.getByRole('button', { name: 'Redo revision' })).toBeEnabled();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: /^SVG(?: map| page)?$/ }).click();
  expect((await download).suggestedFilename()).toBe('accessible-map.svg');
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect(results.violations).toEqual([]);
});

test('numbered braille key uses complete names and exports without new AI analysis', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Advanced Mode', exact: true }).click();
  await page.getByRole('combobox', { name: 'Recognition engine', exact: true }).selectOption('opencv');
  await page.getByLabel('Upload a map image').setInputFiles('tests/map.png');
  await expect(page.getByText('Your map is ready to analyze.')).toBeVisible();
  await page.getByRole('button', { name: 'Analyze & generate', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Refine', exact: true })).toBeVisible({ timeout: 30000 });
  await page.locator('.element-editor summary').click();
  const label = page.getByRole('textbox', { name: 'Label for element 1', exact: true });
  await label.fill('North Springfield');
  await label.press('Tab');
  await expect(label).toBeEnabled();
  await expect(page.locator('.numbered-key summary')).toBeVisible();
  await page.locator('.numbered-key summary').click();
  await expect(page.getByRole('cell', { name: 'North Springfield', exact: true })).toBeVisible();
  await expect(page.locator('.numbered-key')).toContainText('⠼⠁');
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'PDF', exact: true }).click();
  expect((await download).suggestedFilename()).toBe('accessible-map.pdf');
});


test('mobile accessibility preferences persist and preserve BANA defaults', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 667 });
  await page.goto('/');
  await page.getByRole('button', { name: 'Accessibility', exact: true }).click();
  await page.getByRole('combobox', { name: 'Color theme', exact: true }).selectOption('dark');
  await page.getByRole('checkbox', { name: 'High contrast', exact: true }).check();
  await page.getByRole('checkbox', { name: 'Reduce motion', exact: true }).check();
  await page.getByRole('slider', { name: 'Text size:' }).focus();
  await page.keyboard.press('End');
  await expect(page.locator('html')).toHaveCSS('font-size', '24px');
  const panel = await page.getByRole('dialog', { name: 'Accessibility', exact: true }).boundingBox();
  expect(panel).not.toBeNull();
  expect(panel!.x).toBeGreaterThanOrEqual(0);
  expect(panel!.x + panel!.width).toBeLessThanOrEqual(375);
  await page.getByRole('button', { name: 'Close accessibility settings', exact: true }).click();
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-contrast', 'high');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(page.locator('html')).toHaveAttribute('data-motion', 'reduced');
  await expect(page.locator('html')).toHaveCSS('font-size', '24px');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole('button', { name: 'Open application settings' }).click();
  await expect(page.getByRole('combobox', { name: 'Default output profile', exact: true })).toHaveValue('bana2022');
});
