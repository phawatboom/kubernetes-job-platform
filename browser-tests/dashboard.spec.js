import { test, expect } from '@playwright/test';

test('complete a real report and inspect its result', async ({page}) => {
  const errors=[]; page.on('pageerror', e=>errors.push(e.message));
  await page.goto('/');
  await expect(page.getByRole('status').first()).toContainText('Cluster connected');
  await page.getByRole('button', {name:'Dispatch report'}).click();
  await expect(page.locator('.report')).toHaveCount(1);
  await expect(page.locator('.badge')).toHaveText('succeeded', {timeout:90000});
  await page.getByText('Inspect durable result').click();
  await expect(page.locator('pre')).toContainText('10000');
  await page.screenshot({path:'test-results/dashboard.png',fullPage:true});
  expect(errors).toEqual([]);
});

test('worker failure recovers through the real Job controller', async ({page}) => {
  await page.goto('/');
  await expect(page.getByRole('status').first()).toContainText('Cluster connected');
  await page.getByRole('radio',{name:'Fail, then recover'}).check();
  await page.getByRole('button', {name:'Dispatch report'}).click();
  await expect(page.locator('.badge')).toHaveText('succeeded',{timeout:100000});
  await expect(page.locator('.info')).toContainText('2 worker attempts');
});

test('mobile controls are usable without horizontal overflow', async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  await page.goto('/');
  await expect(page.getByRole('status').first()).toContainText('Cluster connected');
  await expect(page.getByRole('button',{name:'Dispatch report'})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.getByRole('radio',{name:'Exhaust the budget'}).focus();
  await page.keyboard.press('Space');
  await expect(page.getByRole('radio',{name:'Exhaust the budget'})).toBeChecked();
  await page.screenshot({path:'test-results/mobile.png',fullPage:true});
});
