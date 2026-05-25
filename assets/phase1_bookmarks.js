// =============================================================================
// AO3 Bookmark Collector — Phase 1
// Paste this entire script into the Chrome DevTools console while logged into
// AO3. It will collect work URLs from your bookmarks pages and download a CSV.
// =============================================================================

(async () => {

  // --------------------------------------------------------------------------
  // CONFIGURATION — edit these before running
  // --------------------------------------------------------------------------
  const USERNAME   = 'willowphile';   // your AO3 username
  const START_PAGE = 1;               // page to start on (change if resuming)
  const END_PAGE   = 999;             // set to the last page you want to collect
                                      // (check your bookmarks page for total count)
  const DELAY_MS          = 10_000;  // 10 seconds between pages
  const RETRY_WAIT_1      = 60_000;  // first retry wait after 429
  const RETRY_WAIT_2      = 120_000; // second retry wait after 429
  const MAX_429_TOTAL     = 5;       // abort if we hit this many 429s total
  // --------------------------------------------------------------------------

  const results = [];       // { url, collected_at, status }
  let total429s  = 0;

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  function timestamp() {
    return new Date().toISOString();
  }

  function downloadCSV(rows) {
    const header = 'work_url,collected_at,status';
    const lines  = rows.map(r =>
      `"${r.url}","${r.collected_at}","${r.status}"`
    );
    const csv  = [header, ...lines].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const a    = document.createElement('a');
    a.href     = URL.createObjectURL(blob);
    a.download = 'phase1_bookmarks.csv';
    a.click();
    console.log(`📥 CSV downloaded: ${rows.length} rows`);
  }

  // Fetch a bookmarks page and extract work URLs.
  // Returns { urls: string[], status: 'success'|'not_found'|'rate_limited'|'error' }
  async function fetchPage(pageNum) {
    const url = `https://archiveofourown.org/users/${USERNAME}/bookmarks?page=${pageNum}`;
    console.log(`→ Fetching page ${pageNum}: ${url}`);

    let attempt = 0;
    while (attempt < 3) {
      let response;
      try {
        response = await fetch(url, { credentials: 'include' });
      } catch (err) {
        console.error(`  Network error on page ${pageNum}:`, err);
        return { urls: [], status: 'error' };
      }

      if (response.ok) {
        const html   = await response.text();
        const parser = new DOMParser();
        const doc    = parser.parseFromString(html, 'text/html');

        // Check if this page actually has bookmarks (empty page = past last page)
        const items = doc.querySelectorAll('li.bookmark');
        if (items.length === 0) {
          console.log(`  Page ${pageNum} has no bookmarks — looks like we've hit the end.`);
          return { urls: [], status: 'not_found' };
        }

        const links = [...doc.querySelectorAll('h4 a[href^="/works/"]')]
          .map(a => a.href.startsWith('http') ? a.href : `https://archiveofourown.org${a.getAttribute('href')}`)
          // filter out series links that sneak in
          .filter(href => /\/works\/\d+/.test(href))
          // dedupe within the page
          .filter((v, i, arr) => arr.indexOf(v) === i);

        console.log(`  Found ${links.length} work links on page ${pageNum}`);
        return { urls: links, status: 'success' };

      } else if (response.status === 429) {
        total429s++;
        attempt++;
        console.warn(`  429 Too Many Requests (attempt ${attempt}, total 429s: ${total429s})`);

        if (total429s >= MAX_429_TOTAL) {
          console.error(`  Hit ${MAX_429_TOTAL} total 429 errors — saving and exiting.`);
          return { urls: [], status: 'rate_limited_fatal' };
        }

        const wait = attempt === 1 ? RETRY_WAIT_1 : RETRY_WAIT_2;
        console.log(`  Waiting ${wait / 1000}s before retry...`);
        await sleep(wait);

      } else {
        console.error(`  HTTP ${response.status} on page ${pageNum}`);
        return { urls: [], status: `http_${response.status}` };
      }
    }

    // Three 429s on the same page
    console.error(`  Three consecutive 429s on page ${pageNum} — saving and exiting.`);
    return { urls: [], status: 'rate_limited_fatal' };
  }

  // --------------------------------------------------------------------------
  // Main loop
  // --------------------------------------------------------------------------
  console.log(`🔖 AO3 Bookmark Collector — Phase 1`);
  console.log(`   User: ${USERNAME} | Pages: ${START_PAGE}–${END_PAGE}`);
  console.log('   Starting in 2 seconds...');
  await sleep(2000);

  for (let page = START_PAGE; page <= END_PAGE; page++) {
    const { urls, status } = await fetchPage(page);

    if (status === 'not_found') {
      console.log(`✅ Reached end of bookmarks at page ${page - 1}.`);
      break;
    }

    if (status === 'rate_limited_fatal') {
      urls.forEach(url => results.push({ url, collected_at: timestamp(), status: 'success' }));
      results.push({ url: `[ABORTED on page ${page}]`, collected_at: timestamp(), status: 'rate_limited_fatal' });
      console.error('🛑 Aborting — saving CSV now.');
      downloadCSV(results);
      return;
    }

    const ts = timestamp();
    urls.forEach(url => results.push({ url, collected_at: ts, status }));

    if (page < END_PAGE) {
      console.log(`  ⏳ Waiting ${DELAY_MS / 1000}s before next page...`);
      await sleep(DELAY_MS);
    }
  }

  // --------------------------------------------------------------------------
  // Done
  // --------------------------------------------------------------------------
  const successes = results.filter(r => r.status === 'success').length;
  console.log(`\n✅ Phase 1 complete. Collected ${successes} work URLs across ${results.length} rows.`);
  console.log('Waiting 5 seconds then downloading CSV...');
  await sleep(5000);
  downloadCSV(results);

})();