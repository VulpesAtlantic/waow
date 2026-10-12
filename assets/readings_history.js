// =============================================================================
// AO3 Reading History Collector
// Paste this entire script into the Chrome DevTools console while logged into
// AO3. It walks your reading history pages and downloads a CSV of
// work URL, title, last-visited date, and visit count.
//
// Everything needed is on the history page itself, so this is a single pass —
// no per-work follow-up fetches like Phase 2.
//
// If it has to give up (rate limiting, errors), it still downloads everything
// collected so far, and the filename + console tell you which page to resume on.
// To stop early on purpose, run this in the console:  window.stopReadings = true
// It will finish the current page, save, and tell you where to resume.
// =============================================================================

(async () => {

  // --------------------------------------------------------------------------
  // CONFIGURATION — edit these before running
  // --------------------------------------------------------------------------
  const USERNAME   = 'willowphile';   // your AO3 username
  const START_PAGE = 1;               // page to start on (change if resuming)
  const END_PAGE   = 999;             // last page to collect (stops early on an empty page)

  const DELAY_MS      = 10_000;   // 10 seconds between pages
  const RETRY_WAITS   = [60_000, 120_000, 300_000]; // backoff per retry on the same page
  const MAX_429_TOTAL = 8;        // give up if we hit this many 429s across the run
  // --------------------------------------------------------------------------

  const MONTHS = { Jan: '01', Feb: '02', Mar: '03', Apr: '04', May: '05', Jun: '06',
                   Jul: '07', Aug: '08', Sep: '09', Oct: '10', Nov: '11', Dec: '12' };

  const results = [];         // { url, title, last_visited, visit_count, page }
  const seenIds = new Set();  // work IDs already collected, across every page
  let total429s     = 0;
  let skippedNoLink = 0;
  window.stopReadings = false;

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  // Quote a value for CSV, doubling embedded quotes and flattening whitespace.
  function csvField(value) {
    return `"${String(value ?? '').replace(/\s+/g, ' ').replaceAll('"', '""')}"`;
  }

  // "03 Oct 2024" → "2024-10-03" (falls back to the raw text if it doesn't parse)
  function isoDate(text) {
    const m = text.match(/(\d{1,2}) (\w{3}) (\d{4})/);
    if (!m || !MONTHS[m[2]]) return text;
    return `${m[3]}-${MONTHS[m[2]]}-${m[1].padStart(2, '0')}`;
  }

  function downloadCSV(rows, suffix) {
    const header = 'work_url,title,last_visited,visit_count,page';
    const lines  = rows.map(r =>
      [r.url, r.title, r.last_visited, r.visit_count, r.page].map(csvField).join(',')
    );
    const csv   = [header, ...lines].join('\n');
    const blob  = new Blob([csv], { type: 'text/csv' });
    const stamp = new Date().toISOString().slice(0, 16).replace(/[-T:]/g, ''); // YYYYMMDDHHMM
    const a     = document.createElement('a');
    a.href      = URL.createObjectURL(blob);
    a.download  = `readings_${suffix}_${stamp}.csv`;
    a.click();
    console.log(`📥 CSV downloaded (${a.download}): ${rows.length} rows`);
  }

  // Pull the fields we want out of one <li class="reading"> blurb.
  function parseItem(item, pageNum) {
    const titleLink = item.querySelector('h4.heading a[href^="/works/"]');
    const idHit     = titleLink?.getAttribute('href').match(/^\/works\/(\d+)/);

    // Deleted / hidden works show up in history without a work link.
    if (!idHit) {
      skippedNoLink++;
      const label = item.querySelector('h4.heading')?.textContent.replace(/\s+/g, ' ').trim();
      console.log(`  ↷ Skipping entry with no work link: ${label || '(no heading)'}`);
      return null;
    }

    // Dedupe on work ID. If you open a work mid-run, it jumps to page 1 and
    // everything above its old spot shifts down — which shows up here as a
    // repeat, never a gap.
    const workId = idHit[1];
    if (seenIds.has(workId)) {
      console.log(`  ↷ Already collected, skipping duplicate: /works/${workId}`);
      return null;
    }
    seenIds.add(workId);

    const viewed  = item.querySelector('h4.viewed')?.textContent.replace(/\s+/g, ' ') ?? '';
    const visited = viewed.match(/Last visited:\s*(\d{1,2} \w{3} \d{4})/);
    const count   = viewed.match(/Visited (once|(\d+) times?)/);

    return {
      url:          `https://archiveofourown.org/works/${workId}`,
      title:        titleLink.textContent.trim(),
      last_visited: visited ? isoDate(visited[1]) : '',
      visit_count:  count ? (count[1] === 'once' ? 1 : Number(count[2])) : '',
      page:         pageNum,
    };
  }

  // Fetch one history page, retrying with backoff on 429s, 5xx, and network errors.
  // Returns { works, status: 'success' | 'end' | 'logged_out' | 'fatal' }
  async function fetchPage(pageNum) {
    const url = `https://archiveofourown.org/users/${USERNAME}/readings?page=${pageNum}`;
    console.log(`→ Fetching page ${pageNum}: ${url}`);

    for (let attempt = 0; attempt <= RETRY_WAITS.length; attempt++) {
      if (attempt > 0) {
        const wait = RETRY_WAITS[attempt - 1];
        console.log(`  Waiting ${wait / 1000}s before retry ${attempt}/${RETRY_WAITS.length}...`);
        await sleep(wait);
      }

      let response;
      try {
        response = await fetch(url, { credentials: 'include' });
      } catch (err) {
        console.warn(`  Network error on page ${pageNum}:`, err);
        continue;
      }

      if (response.ok) {
        // History is private — logged-out requests get bounced to the login page.
        if (response.url.includes('/users/login')) {
          return { works: [], status: 'logged_out' };
        }

        const doc   = new DOMParser().parseFromString(await response.text(), 'text/html');
        const items = doc.querySelectorAll('li.reading');
        if (items.length === 0) return { works: [], status: 'end' };

        const works = [...items].map(item => parseItem(item, pageNum)).filter(Boolean);
        console.log(`  Found ${works.length} new works on page ${pageNum} (${items.length} entries on page)`);
        return { works, status: 'success' };
      }

      if (response.status === 429) {
        total429s++;
        console.warn(`  429 Too Many Requests (total 429s this run: ${total429s})`);
        if (total429s >= MAX_429_TOTAL) {
          console.error(`  Hit ${MAX_429_TOTAL} total 429s — giving up.`);
          return { works: [], status: 'fatal' };
        }
      } else if (response.status >= 500) {
        console.warn(`  HTTP ${response.status} on page ${pageNum} (AO3 having a moment)`);
      } else {
        console.error(`  HTTP ${response.status} on page ${pageNum} — not retryable.`);
        return { works: [], status: 'fatal' };
      }
    }

    console.error(`  Out of retries on page ${pageNum}.`);
    return { works: [], status: 'fatal' };
  }

  // Save what we have and print where to pick back up.
  function bailOut(page, reason) {
    console.error(`🛑 ${reason}`);
    if (page > START_PAGE) {
      console.error(`   Pages ${START_PAGE}–${page - 1} are complete and saved.`);
    }
    console.error(`   ▶ Next time, set START_PAGE = ${page}`);
    downloadCSV(results, `p${START_PAGE}-${page - 1}_PARTIAL_resume_at_p${page}`);
  }

  // --------------------------------------------------------------------------
  // Main loop
  // --------------------------------------------------------------------------
  console.log(`📚 AO3 Reading History Collector`);
  console.log(`   User: ${USERNAME} | Pages: ${START_PAGE}–${END_PAGE}`);
  console.log('   (Run  window.stopReadings = true  to stop and save after the current page.)');
  console.log('   Starting in 2 seconds...');
  await sleep(2000);

  let lastPage = START_PAGE - 1;
  for (let page = START_PAGE; page <= END_PAGE; page++) {
    const { works, status } = await fetchPage(page);

    if (status === 'end') {
      console.log(`✅ Reached end of history at page ${page - 1}.`);
      break;
    }
    if (status === 'logged_out') {
      bailOut(page, 'Got redirected to the login page — log into AO3 in this tab and re-run.');
      return;
    }
    if (status === 'fatal') {
      bailOut(page, `Gave up on page ${page}.`);
      return;
    }

    results.push(...works);
    lastPage = page;

    if (window.stopReadings) {
      bailOut(page + 1, 'Stopped by request.');
      return;
    }
    if (page < END_PAGE) {
      console.log(`  ⏳ Waiting ${DELAY_MS / 1000}s before next page...`);
      await sleep(DELAY_MS);
    }
  }

  // --------------------------------------------------------------------------
  // Done
  // --------------------------------------------------------------------------
  console.log(`\n✅ Done. Collected ${results.length} works from pages ${START_PAGE}–${lastPage}.`);
  if (skippedNoLink) {
    console.log(`   ↷ Skipped ${skippedNoLink} entries with no work link (deleted / hidden works).`);
  }
  downloadCSV(results, `p${START_PAGE}-${lastPage}`);

})();
