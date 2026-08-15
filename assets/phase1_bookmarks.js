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

  const results = [];         // { url, title, author_name, author_url, collected_at, status }
  const seenIds = new Set();  // work IDs already collected, across every page
  let total429s       = 0;
  let skippedNonWorks = 0;

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  function timestamp() {
    return new Date().toISOString();
  }

  // Quote a value for CSV, doubling embedded quotes and flattening whitespace
  // (titles can contain commas, quotes, and the occasional stray newline).
  function csvField(value) {
    return `"${String(value ?? '').replace(/\s+/g, ' ').replaceAll('"', '""')}"`;
  }

  function downloadCSV(rows) {
    // work_url stays the first column — Phase 2 reads only the first quoted field.
    const header = 'work_url,title,author_name,author_url,collected_at,status';
    const lines  = rows.map(r =>
      [r.url, r.title, r.author_name, r.author_url, r.collected_at, r.status]
        .map(csvField)
        .join(',')
    );
    const csv   = [header, ...lines].join('\n');
    const blob  = new Blob([csv], { type: 'text/csv' });
    const stamp = new Date().toISOString().slice(0, 16).replace(/[-T:]/g, ''); // YYYYMMDDHHMM
    const a     = document.createElement('a');
    a.href      = URL.createObjectURL(blob);
    a.download  = `phase1_bookmarks_${stamp}.csv`;
    a.click();
    console.log(`📥 CSV downloaded: ${rows.length} rows`);
  }

  // Fetch a bookmarks page and extract one record per bookmarked work.
  // Returns { works: {url,title,author_name,author_url}[],
  //           status: 'success'|'not_found'|'rate_limited_fatal'|'error'|'http_NNN' }
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
        return { works: [], status: 'error' };
      }

      if (response.ok) {
        const html   = await response.text();
        const parser = new DOMParser();
        const doc    = parser.parseFromString(html, 'text/html');

        // Check if this page actually has bookmarks (empty page = past last page)
        const items = doc.querySelectorAll('li.bookmark');
        if (items.length === 0) {
          console.log(`  Page ${pageNum} has no bookmarks — looks like we've hit the end.`);
          return { works: [], status: 'not_found' };
        }

        // Walk each blurb so title/author stay paired with the right work.
        const works = [...items].flatMap(item => {
          const titleLink = item.querySelector('h4 a[href]');
          if (!titleLink) return []; // shouldn't happen, but skip rather than crash

          const href   = titleLink.getAttribute('href');
          const idHit  = href.match(/^\/works\/(\d+)/);

          // Bookmarks can point at series or external works — skip those, but
          // say so rather than dropping them silently.
          if (!idHit) {
            skippedNonWorks++;
            console.log(`  ↷ Skipping non-work bookmark: ${href}`);
            return [];
          }

          // Dedupe on work ID, globally. Pagination shifts if a bookmark is
          // added mid-run, which can show the same work on two pages.
          const workId = idHit[1];
          if (seenIds.has(workId)) {
            console.log(`  ↷ Already collected, skipping duplicate: /works/${workId}`);
            return [];
          }
          seenIds.add(workId);

          // Anonymous works have no rel="author" link; co-authored works have several
          const authorLinks = [...item.querySelectorAll('h4 a[rel="author"]')];
          const authorName  = authorLinks.length
            ? authorLinks.map(a => a.textContent.trim()).join('; ')
            : 'Anonymous';
          const authorUrl   = authorLinks
            .map(a => a.getAttribute('href'))
            .join('; ');

          return [{
            url:         `https://archiveofourown.org/works/${workId}`,
            title:       titleLink.textContent.trim(),
            author_name: authorName,
            author_url:  authorUrl,
          }];
        });

        console.log(`  Found ${works.length} new works on page ${pageNum} (${items.length} bookmarks on page)`);
        return { works, status: 'success' };

      } else if (response.status === 429) {
        total429s++;
        attempt++;
        console.warn(`  429 Too Many Requests (attempt ${attempt}, total 429s: ${total429s})`);

        if (total429s >= MAX_429_TOTAL) {
          console.error(`  Hit ${MAX_429_TOTAL} total 429 errors — saving and exiting.`);
          return { works: [], status: 'rate_limited_fatal' };
        }

        const wait = attempt === 1 ? RETRY_WAIT_1 : RETRY_WAIT_2;
        console.log(`  Waiting ${wait / 1000}s before retry...`);
        await sleep(wait);

      } else {
        console.error(`  HTTP ${response.status} on page ${pageNum}`);
        return { works: [], status: `http_${response.status}` };
      }
    }

    // Three 429s on the same page
    console.error(`  Three consecutive 429s on page ${pageNum} — saving and exiting.`);
    return { works: [], status: 'rate_limited_fatal' };
  }

  // --------------------------------------------------------------------------
  // Main loop
  // --------------------------------------------------------------------------
  console.log(`🔖 AO3 Bookmark Collector — Phase 1`);
  console.log(`   User: ${USERNAME} | Pages: ${START_PAGE}–${END_PAGE}`);
  console.log('   Starting in 2 seconds...');
  await sleep(2000);

  for (let page = START_PAGE; page <= END_PAGE; page++) {
    const { works, status } = await fetchPage(page);

    if (status === 'not_found') {
      console.log(`✅ Reached end of bookmarks at page ${page - 1}.`);
      break;
    }

    if (status === 'rate_limited_fatal') {
      // Everything collected on earlier pages is already in `results`.
      results.push({ url: `[ABORTED on page ${page}]`, title: '', author_name: '', author_url: '', collected_at: timestamp(), status: 'rate_limited_fatal' });
      console.error('🛑 Aborting — saving CSV now.');
      downloadCSV(results);
      return;
    }

    const ts = timestamp();
    works.forEach(w => results.push({ ...w, collected_at: ts, status }));

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
  if (skippedNonWorks) {
    console.log(`   ↷ Skipped ${skippedNonWorks} non-work bookmarks (series / external works).`);
  }
  console.log('Waiting 5 seconds then downloading CSV...');
  await sleep(5000);
  downloadCSV(results);

})();