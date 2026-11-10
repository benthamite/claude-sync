// Syncs claude.ai conversations to the local claude-sync listener.
//
// Everything runs here in the service worker, which uses the browser's
// claude.ai login cookies via the claude.ai host permission. No tab is needed;
// the content script only nudges a sync when a claude.ai page loads.

const SERVER = 'http://127.0.0.1:8767';
const CLAUDE = 'https://claude.ai';
const PAGE = 100;
const BATCH = 5;
const PAGE_LOAD_INTERVAL_MS = 10 * 60 * 1000;
const ALARM_INTERVAL_MS = 30 * 60 * 1000;
const HEARTBEAT_STALE_MS = 3 * 60 * 1000;  // a run that stopped reporting
const FETCH_GAP_MS = 1000;  // pace conversation fetches to stay under rate limits
const LIST_GAP_MS = 300;    // pace list pages
const MAX_PAGES = 1000;     // guards every paged list against a cursor loop

// The headers claude.ai's own Cowork sidebar sends with its session list.
const COWORK_HEADERS = {
  'anthropic-version': '2023-06-01',
  'anthropic-beta': 'ccr-byoc-2025-07-29',
  'anthropic-client-feature': 'ccr',
};

const get = async (k) => (await chrome.storage.local.get(k))[k];
const set = (obj) => chrome.storage.local.set(obj);
const sleep = (ms) => new Promise((res) => setTimeout(res, ms));

const VERSION = chrome.runtime.getManifest().version;

// Hashed the same way by extension_code_hash() in bin/claude-sync, which
// reports the hash of these files on disk. Keep the two lists identical.
const CODE_FILES = ["manifest.json", "background.js", "content.js", "popup.html", "popup.js"];

async function sha256hex(data) {
  const digest = await crypto.subtle.digest('SHA-256', data);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function codeHash() {
  let lines = '';
  for (const name of CODE_FILES) {
    const r = await fetch(chrome.runtime.getURL(name));
    lines += `${name}\n${await sha256hex(await r.arrayBuffer())}\n`;
  }
  return sha256hex(new TextEncoder().encode(lines));
}

function ensureAlarm() {
  chrome.alarms.create('tick', {periodInMinutes: 15});
}
chrome.runtime.onStartup.addListener(ensureAlarm);

// Fires on install and on every reload, including the self-reload below.
chrome.runtime.onInstalled.addListener(async () => {
  ensureAlarm();
  // A reload kills any run in progress; drop its state so it doesn't block
  // syncs (and the popup's button) until its heartbeat goes stale.
  // The files read now are the code that was just loaded.
  await set({run: null, loadedCode: await codeHash()});
  const pending = await get('pendingSync');
  if (pending) {
    await set({pendingSync: null});
    maybeSync(0, pending.force || []);
  }
});

// Each stage write also refreshes the run's heartbeat and, being an extension
// API call, keeps the service worker alive during long runs.
async function stage(name) {
  await set({run: {stage: name, heartbeat: Date.now()}});
}

// Chrome stops a service worker after 30 s without extension events or API
// calls, so long waits must keep touching an extension API.
async function wakefulSleep(ms, label) {
  for (let left = ms; left > 0; left -= 10000) {
    await stage(`${label} (${Math.ceil(left / 1000)}s left)`);
    await sleep(Math.min(left, 10000));
  }
}

async function setBadge(error) {
  await chrome.action.setBadgeText({text: error ? '!' : ''});
  await chrome.action.setBadgeBackgroundColor({color: '#c62828'});
  const last = await get('lastSuccess');
  const when = last ? new Date(last).toLocaleString() : 'never';
  await chrome.action.setTitle({
    title: `Claude Sync\nLast success: ${when}` + (error ? `\nError: ${error}\nClick for details.` : ''),
  });
}

async function server(method, path, body) {
  let r;
  try {
    r = await fetch(SERVER + path, {
      method,
      headers: body ? {'Content-Type': 'application/json'} : {},
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(120000),
    });
  } catch (e) {
    throw new Error(`listener not reachable at ${SERVER} (is \`claude-sync serve\` running?): ${e.message}`);
  }
  const json = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`listener ${path}: HTTP ${r.status} ${json.error || ''}`);
  return json;
}

async function api(path, headers = {}) {
  for (let attempt = 0; ; attempt++) {
    const r = await fetch(CLAUDE + path, {
      headers,
      credentials: 'include',
      signal: AbortSignal.timeout(120000),
    });
    if (r.status === 429 && attempt < 8) {
      const wait = Number(r.headers.get('Retry-After')) || 30 * (attempt + 1);
      await wakefulSleep(wait * 1000, `rate limited; waiting ${wait}s`);
      continue;
    }
    if (!r.ok) throw Object.assign(new Error(`${path.split('?')[0]}: HTTP ${r.status}`), {status: r.status});
    return r.json();
  }
}

// The organizations to sync: those named in the listener's config.json, or
// else every one with chats (a claude.ai login can belong to several, such
// as a personal one and a Team or Enterprise one; API-only ones have none).
async function listOrgs(wanted) {
  let all;
  try {
    all = await api('/api/organizations');
  } catch (e) {
    if (e.status === 401 || e.status === 403) {
      throw Object.assign(new Error(`claude.ai refused the request (HTTP ${e.status}); ` +
                                    'log in at claude.ai, or open it once to pass its browser check'),
                          {kind: 'login'});
    }
    throw e;
  }
  if (!Array.isArray(all)) {
    throw Object.assign(new Error('no claude.ai session; log in at claude.ai'), {kind: 'login'});
  }
  if (wanted) {
    const missing = wanted.filter((u) => !all.some((o) => o.uuid === u));
    if (missing.length) {
      throw new Error(`organizations in config.json not in this claude.ai login: ${missing.join(', ')}`);
    }
    return all.filter((o) => wanted.includes(o.uuid));
  }
  return all.filter((o) => (o.capabilities || []).includes('chat'));
}

// The conversation list's fields that the listener keeps per chat.
function metaOf(c, org) {
  return {
    uuid: c.uuid, org: org.uuid, org_name: org.name || null, name: c.name || '',
    created_at: c.created_at, updated_at: c.updated_at, model: c.model || null,
    project_uuid: c.project_uuid || null, project: (c.project && c.project.name) || null,
    is_starred: !!c.is_starred, is_archived: !!c.is_archived,
  };
}

async function listConversations(org, errors) {
  const base = `/api/organizations/${org.uuid}`;
  const byId = new Map();
  // Newest first; consistency=strong skips the search index the app's
  // sidebar reads, which can lag.
  for (let off = 0, page = 0; page < MAX_PAGES; off += PAGE, page++) {
    if (page) await sleep(LIST_GAP_MS);
    const j = await api(`${base}/chat_conversations_v2?limit=${PAGE}&offset=${off}&consistency=strong`);
    for (const c of j.data || []) byId.set(c.uuid, metaOf(c, org));
    if (!j.has_more || !(j.data || []).length) break;
  }
  // Project chats, in case the main list leaves them out. Untested, for lack
  // of an account with project chats; a failure here doesn't stop the sync.
  try {
    const projects = await api(`${base}/projects`);
    for (const p of Array.isArray(projects) ? projects : []) {
      await sleep(LIST_GAP_MS);
      const j = await api(`${base}/projects/${p.uuid}/conversations`);
      for (const c of Array.isArray(j) ? j : (j.data || [])) {
        if (!byId.has(c.uuid)) {
          byId.set(c.uuid, metaOf({...c, project_uuid: c.project_uuid || p.uuid,
                                   project: c.project || {name: p.name}}, org));
        }
      }
    }
  } catch (e) {
    errors.push(`${org.name || org.uuid} projects: ${e.message}`);
  }
  return [...byId.values()];
}

function conversationPath(org, uuid) {
  return `/api/organizations/${org}/chat_conversations/${uuid}` +
    '?tree=True&rendering_mode=messages&render_all_tools=true';
}

// A chat asked for by "Sync now" that the list didn't include: look for it in
// each organization.
async function findConversation(uuid, orgs) {
  for (const org of orgs) {
    try {
      return {org, conversation: await api(conversationPath(org.uuid, uuid))};
    } catch (e) {
      if (e.status !== 404 && e.status !== 403) throw e;
    }
  }
  throw new Error('not found in any synced organization');
}

// The same fields as claude.ai's Cowork sidebar shows: no session content.
async function listCowork(orgs) {
  const sessions = [];
  for (const org of orgs) {
    let cursor = null;
    for (let page = 0; page < MAX_PAGES; page++) {
      const p = new URLSearchParams([['tags', 'cowork-remote'], ['limit', '50']]);
      if (cursor) p.append('cursor', cursor);
      const j = await api(`/v1/code/sessions?${p}`, {...COWORK_HEADERS, 'x-organization-uuid': org.uuid});
      for (const s of j.data || []) {
        sessions.push({
          id: s.id,
          org: org.uuid,
          title: s.title ?? null,
          created_at: s.created_at ?? null,
          updated_at: s.updated_at ?? null,
          last_event_at: s.last_event_at ?? null,
          user_message_count: s.user_message_count ?? null,
          folders: (s.client_metadata?.remote_cowork?.userSelectedFolders || [])
            .map((f) => f.path).filter(Boolean),
        });
      }
      if (!j.next_cursor || !(j.data || []).length) break;
      cursor = j.next_cursor;
      await sleep(LIST_GAP_MS);
    }
  }
  return {orgs: orgs.map((o) => o.uuid), sessions};
}

async function runSync(force = []) {
  const errors = [];
  const summary = {listed: 0, fetched: 0, cowork_sessions: null};
  let error = null;
  let errorKind = null;
  try {
    // Ask the listener first, so a stopped or paused listener ends the run
    // before anything is requested from claude.ai.
    await stage('checking listener');
    const loadedCode = await get('loadedCode');
    const state = await server('GET', `/state?v=${VERSION}&code=${loadedCode || ''}`);
    const ids = state.ids;
    // The listener reports the hash of the extension code on disk. If it
    // differs from the loaded code, reload to pick it up, then rerun this sync.
    // Reload at most once per disk version, so a mismatch that a reload can't
    // fix (e.g. the extension loaded from another folder) fails loudly
    // instead of looping.
    if (state.extension_code && loadedCode && state.extension_code !== loadedCode) {
      if ((await get('reloadedFor')) === state.extension_code) {
        throw new Error('extension code on disk differs from the loaded code even after a ' +
                        'reload; is the extension loaded from this repository?');
      }
      await set({reloadedFor: state.extension_code, pendingSync: {force}, run: null});
      chrome.runtime.reload();
      await new Promise(() => {});  // the reload ends this worker
    }
    await stage('organizations');
    const orgs = await listOrgs(state.orgs);
    const listing = [];
    for (const org of orgs) {
      await stage(`listing ${org.name || org.uuid}`);
      listing.push(...await listConversations(org, errors));
    }
    summary.listed = listing.length;
    await stage(`listed ${listing.length}`);
    // "Sync now" also refetches the chat open in the current tab, in case
    // the list's updated_at trails its newest messages; the listener writes
    // it only if it changed.
    const forced = new Set(force);
    const todo = listing.filter((m) =>
      forced.has(m.uuid) || Date.parse(m.updated_at) / 1000 > (ids[m.uuid] || 0) + 0.001);
    for (const uuid of forced) if (!listing.some((m) => m.uuid === uuid)) todo.push({uuid});
    const orgById = new Map(orgs.map((o) => [o.uuid, o]));
    let batch = [];
    for (const [i, meta] of todo.entries()) {
      await stage(`fetching ${i + 1}/${todo.length}`);
      if (i > 0) await sleep(FETCH_GAP_MS);
      try {
        const found = meta.org
          ? {org: orgById.get(meta.org), conversation: await api(conversationPath(meta.org, meta.uuid))}
          : await findConversation(meta.uuid, orgs);
        batch.push({org: found.org.uuid, meta: meta.org ? meta : {org_name: found.org.name || null},
                    conversation: found.conversation});
        summary.fetched++;
      } catch (e) {
        errors.push(`${meta.uuid}: ${e.message}`);
      }
      if (batch.length >= BATCH || i === todo.length - 1) {
        if (batch.length) await server('POST', '/conversations', {items: batch});
        batch = [];
      }
    }
    await stage('listing Cowork sessions');
    let cowork = null;
    try {
      cowork = await listCowork(orgs);
      summary.cowork_sessions = cowork.sessions.length;
    } catch (e) {
      errors.push(`Cowork sessions: ${e.message}`);
    }
    await stage('finishing');
    const slim = listing.map(({uuid, org, org_name, name, is_starred, is_archived, project_uuid, project}) =>
      ({uuid, org, org_name, name, is_starred, is_archived, project_uuid, project}));
    await server('POST', '/sync-done', {listing: slim, summary, errors, cowork,
                                        extension: {version: VERSION, code: loadedCode}});
    if (errors.length) error = `${errors.length} error(s); first: ${errors[0]}`;
    await set({lastSuccess: Date.now()});
  } catch (e) {
    error = String(e && e.message || e);
    errorKind = e && e.kind || null;
  }
  await set({lastFinished: Date.now(), lastSummary: summary, lastError: error,
             lastErrorKind: errorKind, run: null});
  await setBadge(error);
}

let running = null;
// Chats a "Sync now" asked for while a run was already going; a follow-up run
// fetches them so the click isn't lost.
let queuedForce = null;

async function maybeSync(minIntervalMs, force = []) {
  if (running) {
    if (force.length) queuedForce = [...(queuedForce || []), ...force];
    return running;
  }
  const run = await get('run');
  if (run && Date.now() - run.heartbeat < HEARTBEAT_STALE_MS) return;  // another worker instance
  if (run) {
    // The worker was stopped mid-run without reaching the end of runSync.
    const error = `previous run died at stage: ${run.stage}`;
    await set({lastError: error, lastErrorKind: null, run: null});
    await setBadge(error);
  }
  const lastFinished = (await get('lastFinished')) || 0;
  if (Date.now() - lastFinished < minIntervalMs) return;
  running = runSync(force).finally(() => {
    running = null;
    if (queuedForce) {
      const next = queuedForce;
      queuedForce = null;
      maybeSync(0, next);
    }
  });
  return running;
}

chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === 'tick') maybeSync(ALARM_INTERVAL_MS);
});

// The toolbar icon opens popup.html, whose "Sync now" button sends sync-now.
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'page-loaded') maybeSync(PAGE_LOAD_INTERVAL_MS);
  if (msg.type === 'sync-now') maybeSync(0, msg.force || []);
});
