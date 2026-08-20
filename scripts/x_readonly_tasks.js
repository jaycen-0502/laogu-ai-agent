const USERNAME_PATTERN = /^[A-Za-z0-9_]{1,15}$/;
const ACCOUNT_ID_PATTERN = /^\d+$/;
const RESERVED = new Set(['home', 'explore', 'notifications', 'messages', 'i', 'settings', 'search', 'compose']);

function timeoutOf(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.round(parsed) : 30000;
}

function usernameFromHref(href) {
  try {
    const parts = new URL(String(href || ''), 'https://x.com').pathname.split('/').filter(Boolean);
    const name = parts.length === 1 ? parts[0] : '';
    return USERNAME_PATTERN.test(name) && !RESERVED.has(name.toLowerCase()) ? `@${name}` : '';
  } catch { return ''; }
}

function accountIdFromTwid(value) {
  let decoded = String(value || '').replace(/^"|"$/g, '');
  try { decoded = decodeURIComponent(decoded); } catch {}
  const match = decoded.match(/^u=(\d+)$/);
  return match && ACCOUNT_ID_PATTERN.test(match[1]) ? match[1] : '';
}

async function visible(locator) {
  const count = await locator.count().catch(() => 0);
  for (let i = 0; i < count; i += 1) {
    if (await locator.nth(i).isVisible().catch(() => false)) return locator.nth(i);
  }
  return null;
}

async function identity(page, context, browserFetch, timeoutMs) {
  const deadline = Date.now() + Math.min(timeoutMs, 20000);
  let profileLink = null;
  let loginControl = null;
  while (Date.now() < deadline && !profileLink && !loginControl) {
    profileLink = await visible(page.locator('a[data-testid="AppTabBar_Profile_Link"]'));
    loginControl = await visible(page.locator('a[data-testid="loginButton"], input[autocomplete="username"], form[action*="/login"] input[name="text"]'));
    if (!profileLink && !loginControl) await page.waitForTimeout(300);
  }
  if (loginControl && !profileLink) return { loginStatus: 'NOT_LOGGED_IN', xUsername: '', xAccountId: '', identityVerified: true };
  if (!profileLink) return { loginStatus: 'UNKNOWN', xUsername: '', xAccountId: '', identityVerified: false };

  let xUsername = usernameFromHref(await profileLink.getAttribute('href').catch(() => ''));
  const cookies = await context.cookies(['https://x.com', 'https://twitter.com']);
  const twid = cookies.find(cookie => cookie && cookie.name === 'twid');
  let xAccountId = accountIdFromTwid(twid && twid.value);
  if ((!xUsername || !xAccountId) && typeof browserFetch === 'function') {
    const response = await browserFetch(page, {
      url: 'https://x.com/i/api/1.1/account/settings.json', method: 'GET', credentials: 'include', timeoutMs,
    }).catch(() => null);
    const body = response && response.ok === true ? response.bodyJSON : null;
    const name = String(body && body.screen_name || '');
    const id = String(body && (body.id_str || body.id) || '');
    if (!xUsername && USERNAME_PATTERN.test(name)) xUsername = `@${name}`;
    if (!xAccountId && ACCOUNT_ID_PATTERN.test(id)) xAccountId = id;
  }
  if (!xUsername || !xAccountId) return { loginStatus: 'UNKNOWN', xUsername: '', xAccountId: '', identityVerified: false };
  return { loginStatus: 'LOGGED_IN', xUsername, xAccountId, identityVerified: true };
}

function countValue(value) {
  const text = String(value || '').replace(/,/g, '').trim();
  const match = text.match(/([\d.]+)([KMB])?/i);
  if (!match) return null;
  const factor = { K: 1000, M: 1000000, B: 1000000000 }[String(match[2] || '').toUpperCase()] || 1;
  const number = Number(match[1]) * factor;
  return Number.isFinite(number) ? Math.round(number) : null;
}

async function text(locator) { return locator ? (await locator.innerText().catch(() => '')).trim() : ''; }

async function profileData(page, account) {
  const display = await text(await visible(page.locator('[data-testid="UserName"]')));
  const bio = await text(await visible(page.locator('[data-testid="UserDescription"]')));
  const links = page.locator('a[href$="/followers"], a[href$="/following"]');
  const counts = { followers_count: null, following_count: null };
  const count = await links.count().catch(() => 0);
  for (let i = 0; i < count; i += 1) {
    const href = await links.nth(i).getAttribute('href').catch(() => '');
    const value = countValue(await text(links.nth(i)));
    if (String(href).endsWith('/followers')) counts.followers_count = value;
    if (String(href).endsWith('/following')) counts.following_count = value;
  }
  return { ...account, display_name: display || null, bio: bio || null, ...counts, profile_url: account.xUsername ? `https://x.com/${account.xUsername.slice(1)}` : null };
}

async function posts(page, limit = 20) {
  const articles = page.locator('article');
  const rows = [];
  const count = Math.min(await articles.count().catch(() => 0), limit);
  for (let i = 0; i < count; i += 1) {
    const article = articles.nth(i);
    const link = await visible(article.locator('a[href*="/status/"]'));
    const href = link ? await link.getAttribute('href').catch(() => '') : '';
    const match = String(href).match(/\/status\/(\d+)/);
    rows.push({ post_id: match ? match[1] : null, text: (await text(article)) || null, created_at: null });
  }
  return rows;
}

module.exports.run = async ({ useBrowser, browserFetch, selector = {}, params = {}, log }) => {
  const started = Date.now();
  const timeoutMs = timeoutOf(params.timeoutMs);
  const taskType = String(params.taskType || '');
  if (!['x.check_login', 'x.read_profile', 'x.read_timeline', 'x.search'].includes(taskType) || params.readOnly !== true) {
    return { ok: false, status: 'error', reason: 'Only registered read-only task types are allowed' };
  }
  const query = String(params.query || '').trim();
  if (taskType === 'x.search' && !query) return { ok: false, status: 'error', reason: 'query is required' };
  const targetUrl = taskType === 'x.search' ? `https://x.com/search?q=${encodeURIComponent(query)}&src=typed_query` : 'https://x.com/home';
  const runtime = await useBrowser({ selector, startUrls: [targetUrl], skipDefaultStartUrls: true, url: targetUrl, waitUntil: 'domcontentloaded', timeoutMs, reuseCurrentPage: true });
  const page = runtime && runtime.page;
  const context = runtime && runtime.context;
  if (!page || !context) return { ok: false, status: 'error', loginStatus: 'UNKNOWN', reason: 'Browser runtime did not return page/context' };
  const account = await identity(page, context, browserFetch, timeoutMs);
  if (taskType === 'x.check_login') return { ok: true, status: 'success', result: account, url: page.url(), title: await page.title().catch(() => '') };
  if (account.loginStatus !== 'LOGGED_IN') return { ok: false, status: 'error', result: account, url: page.url(), title: await page.title().catch(() => ''), reason: 'A verified logged-in account is required' };
  let result = { ...account };
  if (taskType === 'x.read_profile') {
    await page.goto(`https://x.com/${account.xUsername.slice(1)}`, { waitUntil: 'domcontentloaded', timeout: timeoutMs });
    result = await profileData(page, account);
  }
  if (taskType === 'x.read_timeline') result = { ...account, posts: await posts(page) };
  if (taskType === 'x.search') result = { ...account, query, posts: await posts(page) };
  result.url = page.url();
  result.title = await page.title().catch(() => '');
  result.success = true;
  result.duration = Math.round((Date.now() - started) / 1000) / 1000;
  log(taskType, 'SUCCESS');
  return { ok: true, status: 'success', result };
};
