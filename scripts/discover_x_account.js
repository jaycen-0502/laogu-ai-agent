const USERNAME_PATTERN = /^[A-Za-z0-9_]{1,15}$/;
const ACCOUNT_ID_PATTERN = /^\d+$/;
const RESERVED_PATHS = new Set([
  'home',
  'explore',
  'notifications',
  'messages',
  'i',
  'settings',
  'search',
  'compose',
]);

function normalizeTimeout(value, fallback = 30000) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.round(parsed) : fallback;
}

function usernameFromProfileHref(href) {
  const value = String(href || '').trim();
  if (!value) return '';
  let pathname = value;
  try {
    pathname = new URL(value, 'https://x.com').pathname;
  } catch {}
  const parts = pathname.split('/').filter(Boolean);
  if (parts.length !== 1) return '';
  const username = parts[0];
  if (RESERVED_PATHS.has(username.toLowerCase())) return '';
  return USERNAME_PATTERN.test(username) ? `@${username}` : '';
}

function accountIdFromTwid(value) {
  let decoded = String(value || '').trim().replace(/^"|"$/g, '');
  try {
    decoded = decodeURIComponent(decoded);
  } catch {}
  const match = decoded.match(/^u=(\d+)$/);
  return match && ACCOUNT_ID_PATTERN.test(match[1]) ? match[1] : '';
}

function identityFromAccountSettings(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { xUsername: '', xAccountId: '' };
  }
  const rawUsername = String(body.screen_name || '').trim();
  const rawId = String(body.id_str || body.id || '').trim();
  const xUsername = USERNAME_PATTERN.test(rawUsername) ? '@' + rawUsername : '';
  const xAccountId = ACCOUNT_ID_PATTERN.test(rawId) ? rawId : '';
  return { xUsername, xAccountId };
}

async function firstVisible(locator) {
  const count = await locator.count().catch(() => 0);
  for (let index = 0; index < count; index += 1) {
    if (await locator.nth(index).isVisible().catch(() => false)) {
      return locator.nth(index);
    }
  }
  return null;
}

async function waitForIdentitySurface(page, timeoutMs) {
  const deadline = Date.now() + Math.min(timeoutMs, 20000);
  while (Date.now() < deadline) {
    const profileLink = await firstVisible(
      page.locator('a[data-testid="AppTabBar_Profile_Link"]')
    );
    if (profileLink) return { type: 'profile', locator: profileLink };

    const loginControl = await firstVisible(
      page.locator(
        'a[data-testid="loginButton"], input[autocomplete="username"], '
        + 'form[action*="/login"] input[name="text"]'
      )
    );
    if (loginControl) return { type: 'login', locator: loginControl };
    await page.waitForTimeout(300);
  }
  return { type: 'unknown', locator: null };
}

module.exports.run = async ({
  useBrowser,
  browserFetch,
  selector = {},
  params = {},
  log,
}) => {
  const timeoutMs = normalizeTimeout(params.timeoutMs);
  const targetUrl = 'https://x.com/home';
  if (params.readOnly !== true) {
    return {
      ok: false,
      loginStatus: 'UNKNOWN',
      xUsername: '',
      xAccountId: '',
      identityVerified: false,
      reason: 'readOnly=true is required',
      url: '',
      title: '',
    };
  }

  const runtime = await useBrowser({
    selector,
    startUrls: [targetUrl],
    skipDefaultStartUrls: true,
    url: targetUrl,
    waitUntil: 'domcontentloaded',
    timeoutMs,
    reuseCurrentPage: true,
  });
  const page = runtime && runtime.page;
  const context = runtime && runtime.context;
  if (!page || !context) {
    return {
      ok: false,
      loginStatus: 'UNKNOWN',
      xUsername: '',
      xAccountId: '',
      identityVerified: false,
      reason: 'Laogu useBrowser did not return a page and context',
      url: '',
      title: '',
    };
  }

  const surface = await waitForIdentitySurface(page, timeoutMs);
  const url = page.url();
  const title = await page.title().catch(() => '');

  if (surface.type === 'login') {
    log('loginStatus', 'NOT_LOGGED_IN');
    return {
      ok: true,
      loginStatus: 'NOT_LOGGED_IN',
      xUsername: '',
      xAccountId: '',
      identityVerified: true,
      identitySource: 'explicit-login-control',
      reason: '',
      url,
      title,
    };
  }

  if (surface.type !== 'profile') {
    return {
      ok: false,
      loginStatus: 'UNKNOWN',
      xUsername: '',
      xAccountId: '',
      identityVerified: false,
      reason: 'Neither the authenticated profile link nor an explicit login control was visible',
      url,
      title,
    };
  }

  const profileHref = await surface.locator.getAttribute('href').catch(() => '');
  let xUsername = usernameFromProfileHref(profileHref);
  const cookies = await context.cookies(['https://x.com', 'https://twitter.com']);
  const twid = cookies.find((cookie) => cookie && cookie.name === 'twid');
  let xAccountId = accountIdFromTwid(twid && twid.value);
  let identitySource = 'AppTabBar_Profile_Link+twid';

  if ((!xUsername || !xAccountId) && typeof browserFetch === 'function') {
    const settingsResponse = await browserFetch(page, {
      url: 'https://x.com/i/api/1.1/account/settings.json',
      method: 'GET',
      credentials: 'include',
      timeoutMs,
    }).catch(() => null);
    if (settingsResponse && settingsResponse.ok === true) {
      const fallback = identityFromAccountSettings(settingsResponse.bodyJSON);
      if (fallback.xUsername && fallback.xAccountId) {
        xUsername = fallback.xUsername;
        xAccountId = fallback.xAccountId;
        identitySource = 'account-settings-api';
      }
    }
  }

  if (!xUsername || !xAccountId) {
    return {
      ok: false,
      loginStatus: 'UNKNOWN',
      xUsername: '',
      xAccountId: '',
      identityVerified: false,
      reason: !xUsername
        ? 'Authenticated profile link did not contain a valid X username'
        : 'Authenticated X twid cookie did not contain a stable numeric account ID',
      url,
      title,
    };
  }

  log('loginStatus', 'LOGGED_IN');
  log('xUsername', xUsername);
  return {
    ok: true,
    loginStatus: 'LOGGED_IN',
    xUsername,
    xAccountId,
    identityVerified: true,
    identitySource,
    reason: '',
    url,
    title,
  };
};

module.exports._internals = {
  usernameFromProfileHref,
  accountIdFromTwid,
  identityFromAccountSettings,
};
