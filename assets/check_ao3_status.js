// =============================================================================
// AO3 Work Status Checker
// Paste this entire script into the Chrome DevTools console on any AO3 page
// while logged in, then run. It loads the archive's file list from
// fic_data.json on the live site, checks each work on AO3, and downloads a CSV.
//
// Statuses:
//   deleted - AO3 returned 404
//   hidden  - page only says the work "will be revealed soon" (unrevealed
//             challenge/collection)
//   live    - page loads normally
//   login   - AO3 redirected to the login page (you're logged out)
//   error   - anything else after retries (HTTP status / network error noted)
//
// Stopping early: the CSV is only saved at the end, but you can run
// window.ao3StatusDownload() in the console at any time to save what's done.
// =============================================================================

(async () => {

  // --------------------------------------------------------------------------
  // CONFIGURATION
  // --------------------------------------------------------------------------
  const MANIFEST_URL    = 'https://agathario.github.io/ficArchive/fic_data.json';
  const DELAY_MS        = 5_000;                 // between requests
  const RETRY_WAITS_429 = [60_000, 120_000, 300_000];
  const TRANSIENT_WAITS = [30_000, 90_000];      // 5xx / network errors

  // Leave empty to check everything. To recheck only some works (e.g. the
  // error rows from a previous run), list their work IDs here:
  //   const ONLY_IDS = ['59315482', '61234567'];
  const ONLY_IDS = [];
  // --------------------------------------------------------------------------

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  const results = [];
  const order   = { deleted: 0, hidden: 1, login: 2, error: 3, live: 4 };

  function downloadCSV() {
    const sorted = [...results].sort((a, b) =>
      order[a.status] - order[b.status] || a.filename.localeCompare(b.filename));
    const header = 'filename,url,status,note';
    const lines  = sorted.map(r => `"${r.filename}","${r.url}","${r.status}","${r.note}"`);
    const csv    = [header, ...lines].join('\n');
    const blob   = new Blob([csv], { type: 'text/csv' });
    const stamp  = new Date().toISOString().slice(0, 16).replace(/[-T:]/g, ''); // YYYYMMDDHHMM
    const a      = document.createElement('a');
    a.href       = URL.createObjectURL(blob);
    a.download   = `ao3_status_${stamp}.csv`;
    a.click();
    console.log(`📥 CSV downloaded: ${results.length} rows`);
  }
  window.ao3StatusDownload = downloadCSV;

  function classify(response, html) {
    if (response.status === 404) return ['deleted', ''];
    if (response.url.includes('/users/login')) return ['login', 'redirected to login'];
    if (!response.ok) return ['error', `HTTP ${response.status}`];
    if (/will be revealed soon/i.test(html)) return ['hidden', ''];
    return ['live', ''];
  }

  async function checkWork(url) {
    let tries429 = 0, triesTransient = 0;
    while (true) {
      let reason;
      try {
        const response = await fetch(`${url}?view_adult=true`, { credentials: 'include' });
        if (response.status === 429 && tries429 < RETRY_WAITS_429.length) {
          const wait = RETRY_WAITS_429[tries429++];
          console.warn(`  ⚠️  429 Too Many Requests — waiting ${wait / 1000}s...`);
          await sleep(wait);
          continue;
        }
        if (response.status < 500) {
          return classify(response, await response.text());
        }
        reason = `HTTP ${response.status}`;
      } catch (err) {
        reason = err.name || 'network error';
      }
      if (triesTransient < TRANSIENT_WAITS.length) {
        const wait = TRANSIENT_WAITS[triesTransient++];
        console.warn(`  ⚠️  ${reason} — retrying in ${wait / 1000}s...`);
        await sleep(wait);
        continue;
      }
      return ['error', reason];
    }
  }

  // --------------------------------------------------------------------------
  // Load file list
  // --------------------------------------------------------------------------
  let filenames;
  try {
    const manifest = await (await fetch(MANIFEST_URL, { cache: 'no-store' })).json();
    filenames = manifest.fics.map(f => f.filename).filter(n => /^\d+_/.test(n));
  } catch (err) {
    console.error(`❌ Couldn't load ${MANIFEST_URL}:`, err);
    return;
  }
  if (ONLY_IDS.length) {
    filenames = filenames.filter(n => ONLY_IDS.includes(n.split('_')[0]));
  }

  const mins = Math.round(filenames.length * DELAY_MS / 60_000);
  console.log(`🔎 Checking ${filenames.length} works (~${mins} min)...`);

  // --------------------------------------------------------------------------
  // Check each work
  // --------------------------------------------------------------------------
  for (let i = 0; i < filenames.length; i++) {
    const filename = filenames[i];
    const url      = `https://archiveofourown.org/works/${filename.split('_')[0]}`;
    const [status, note] = await checkWork(url);
    results.push({ filename, url, status, note });

    const tag = `[${i + 1}/${filenames.length}]`;
    if (status !== 'live') {
      console.log(`  ${tag} ${status.toUpperCase().padEnd(8)} ${filename} ${note}`);
    } else if ((i + 1) % 25 === 0) {
      console.log(`  ${tag} ...`);
    }
    if (status === 'login' && results.filter(r => r.status === 'login').length >= 3) {
      console.error('❌ Repeated login redirects — log in to AO3 and rerun. Saving what we have.');
      break;
    }
    await sleep(DELAY_MS);
  }

  // --------------------------------------------------------------------------
  // Summary
  // --------------------------------------------------------------------------
  console.log('\n=== Summary ===');
  for (const s of Object.keys(order)) {
    const n = results.filter(r => r.status === s).length;
    if (n) console.log(`  ${s.padEnd(8)} ${n}`);
  }
  const errorIds = results.filter(r => r.status === 'error').map(r => `'${r.url.split('/').pop()}'`);
  if (errorIds.length) {
    console.log(`\nTo recheck errors, set ONLY_IDS to:\n[${errorIds.join(', ')}]`);
  }
  downloadCSV();

})();
