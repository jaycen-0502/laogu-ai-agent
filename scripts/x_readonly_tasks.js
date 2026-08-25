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

function legacyCountValue(value) {
  const text = String(value || '')
    .replace(/[\s\u00a0]/g, '')
    .replace(/[,，]/g, '')
    .trim();
  const match = text.match(/([\d]+(?:\.\d+)?)([KMB万萬亿億])?/i);
  if (!match) return null;
  const suffix = String(match[2] || '').toUpperCase();
  const factor = {
    K: 1000,
    M: 1000000,
    B: 1000000000,
    '万': 10000,
    '萬': 10000,
    '亿': 100000000,
    '億': 100000000,
  }[suffix] || 1;
  const number = Number(match[1]) * factor;
  return Number.isFinite(number) ? Math.round(number) : null;
}

// Robust counter parser for X's compact and localized labels. This later
// declaration intentionally preserves compatibility with older callers.
function countValue(value) {
  const text = String(value || '')
    .normalize('NFKC')
    .replace(/[\s\u00a0,，、]/g, '')
    .trim();
  const match = text.match(/(\d+(?:\.\d+)?)(K|M|B|万|亿)?/i);
  if (!match) return null;
  const suffix = String(match[2] || '').toUpperCase();
  const factor = { K: 1e3, M: 1e6, B: 1e9, '万': 1e4, '亿': 1e8 }[suffix] || 1;
  const number = Number(match[1]) * factor;
  return Number.isFinite(number) && number >= 0 ? Math.round(number) : null;
}

async function text(locator) { return locator ? (await locator.innerText().catch(() => '')).trim() : ''; }

async function waitForProfileSurface(page, username, timeoutMs) {
  const handle = String(username || '').replace(/^@/, '');
  const surface = page.locator(
    `[data-testid="UserName"], [data-testid="UserDescription"], `
    + `a[href="/${handle}/following"], a[href="/${handle}/followers"], `
    + `a[href="/${handle}/verified_followers"]`
  );
  await surface.first().waitFor({
    state: 'visible',
    timeout: Math.min(Math.max(timeoutMs, 1000), 15000),
  }).catch(() => null);

  // UserName often appears before the counters. Give the profile header a
  // short bounded hydration window instead of reading React's empty shell.
  const countLinks = page.locator(
    `a[href*="/${handle}/following"], a[href*="/${handle}/followers"], `
    + `a[href*="/${handle}/verified_followers"]`
  );
  await countLinks.first().waitFor({
    state: 'visible',
    timeout: Math.min(Math.max(Math.floor(timeoutMs / 3), 1000), 5000),
  }).catch(() => null);
}

async function profileData(page, account, timeoutMs) {
  await waitForProfileSurface(page, account.xUsername, timeoutMs);
  const userName = await visible(page.locator('[data-testid="UserName"]'));
  const displayLocator = userName
    ? await visible(userName.locator('span'))
    : null;
  const display = await text(displayLocator || userName);
  const bio = await text(await visible(page.locator('[data-testid="UserDescription"]')));
  const links = page.locator(
    'a[href*="/followers"], a[href*="/verified_followers"], a[href*="/following"]'
  );
  const counts = { followers_count: null, following_count: null };
  const count = await links.count().catch(() => 0);
  for (let i = 0; i < count; i += 1) {
    const href = await links.nth(i).getAttribute('href').catch(() => '');
    const aria = await links.nth(i).getAttribute('aria-label').catch(() => '');
    const value = countValue(`${await text(links.nth(i))} ${aria || ''}`);
    let path = String(href || '');
    try { path = new URL(path, 'https://x.com').pathname; } catch {}
    if (/\/(followers|verified_followers)\/?$/i.test(path) && value !== null) {
      counts.followers_count = value;
    }
    if (/\/following\/?$/i.test(path) && value !== null) {
      counts.following_count = value;
    }
  }

  // Some X layouts expose the counter only through aria-labels.
  if (counts.followers_count === null) {
    const labels = page.locator('[aria-label*="Followers"], [aria-label*="followers"], [aria-label*="粉丝"]');
    const n = await labels.count().catch(() => 0);
    for (let i = 0; i < n && counts.followers_count === null; i += 1) {
      counts.followers_count = countValue(await labels.nth(i).getAttribute('aria-label').catch(() => ''));
    }
  }
  if (counts.following_count === null) {
    const labels = page.locator('[aria-label*="Following"], [aria-label*="following"], [aria-label*="关注"]');
    const n = await labels.count().catch(() => 0);
    for (let i = 0; i < n && counts.following_count === null; i += 1) {
      counts.following_count = countValue(await labels.nth(i).getAttribute('aria-label').catch(() => ''));
    }
  }
  const warnings = [];
  if (counts.followers_count === null) warnings.push('followers_count unavailable');
  if (counts.following_count === null) warnings.push('following_count unavailable');
  return {
    ...account,
    display_name: display || null,
    bio: bio || null,
    ...counts,
    profile_data_status: warnings.length ? 'PARTIAL' : 'COMPLETE',
    profile_data_warnings: warnings,
    profile_url: account.xUsername ? `https://x.com/${account.xUsername.slice(1)}` : null,
  };
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
    result = await profileData(page, account, timeoutMs);
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

module.exports._internals = { countValue };
