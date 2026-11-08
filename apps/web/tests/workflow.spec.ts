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
  for (const theme of ['light', 'dark']) {
    await page.getByRole('button', { name: 'Open application settings' }).click();
    await page.getByRole('combobox', { name: 'Theme', exact: true }).selectOption(theme);
    await page.getByRole('button', { name: 'Done', exact: true }).click();
    const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
    expect(results.violations).toEqual([]);
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
  await page.getByRole('button', { name: /^SVG(?: map)?$/ }).click();
  expect((await download).suggestedFilename()).toBe('accessible-map.svg');
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect(results.violations).toEqual([]);
});
