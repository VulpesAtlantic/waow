// =============================================================================
// AO3 Download Link Collector — Phase 2
// Paste this entire script into the Chrome DevTools console while logged into
// AO3. Paste your Phase 1 CSV contents into PHASE1_CSV below, then run.
// =============================================================================

(async () => {

  // --------------------------------------------------------------------------
  // CONFIGURATION
  // --------------------------------------------------------------------------
  const DELAY_MS      = 10_000;   // 10 seconds between requests
  const RETRY_WAIT_1  = 60_000;
  const RETRY_WAIT_2  = 120_000;
  const MAX_429_TOTAL = 5;

  // Paste the raw text of your phase1_bookmarks.csv here (including the header).
  // Make sure it's wrapped in backticks. Example:
  //
  // const PHASE1_CSV = `work_url,collected_at,status
  // "https://archiveofourown.org/works/12345","2025-01-01T00:00:00.000Z","success"
  // ...`;
  //
  const PHASE1_CSV = `PASTE_YOUR_PHASE1_CSV_HERE`;
  // --------------------------------------------------------------------------

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  function timestamp() {
    return new Date().toISOString();
  }

  function parseCSV(raw) {
    const lines = raw.trim().split('\n');
    // skip header
    return lines.slice(1).map(line => {
      // basic quoted-CSV parse: grab first quoted field
      const match = line.match(/^"([^"]+)"/);
      return match ? match[1] : null;
    }).filter(Boolean);
  }

  function downloadCSV(rows) {
    const header = 'work_url,download_url,collected_at,status';
    const lines  = rows.map(r =>
      `"${r.work_url}","${r.download_url}","${r.collected_at}","${r.status}"`
    );
    const csv  = [header, ...lines].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const a    = document.createElement('a');
    a.href     = URL.createObjectURL(blob);
    a.download = 'phase2_download_links.csv';
    a.click();
    console.log(`📥 CSV downloaded: ${rows.length} rows`);
  }

  // Fetch a work page and extract the HTML download link.
  async function fetchDownloadLink(workUrl) {
    let attempt = 0;
    while (attempt < 3) {
      let response;
      try {
        response = await fetch(workUrl, { credentials: 'include' });
      } catch (err) {
        console.error(`  Network error for ${workUrl}:`, err);
        return { downloadUrl: '', status: 'error' };
      }

      if (response.ok) {
        const html   = await response.text();
        const parser = new DOMParser();
        const doc    = parser.parseFromString(html, 'text/html');

        // The download dropdown contains links like /downloads/12345/Title.html?updated_at=...
        const link = doc.querySelector('a[href*=".html?"]');
        if (!link) {
          // Some works may be locked, deleted, or have no HTML download
          console.warn(`  No HTML download link found at ${workUrl}`);
          return { downloadUrl: '', status: 'no_download_link' };
        }

        const href = link.getAttribute('href');
        const downloadUrl = href.startsWith('http')
          ? href
          : `https://archiveofourown.org${href}`;

        console.log(`  ✓ Found: ${downloadUrl}`);
        return { downloadUrl, status: 'success' };

      } else if (response.status === 429) {
        attempt++;
        console.warn(`  429 on ${workUrl} (attempt ${attempt})`);
        const wait = attempt === 1 ? RETRY_WAIT_1 : RETRY_WAIT_2;
        console.log(`  Waiting ${wait / 1000}s...`);
        await sleep(wait);

      } else if (response.status === 403 || response.status === 404) {
        console.warn(`  HTTP ${response.status} — work may be locked or deleted: ${workUrl}`);
        return { downloadUrl: '', status: `http_${response.status}` };

      } else {
        console.error(`  HTTP ${response.status} for ${workUrl}`);
        return { downloadUrl: '', status: `http_${response.status}` };
      }
    }

    return { downloadUrl: '', status: 'rate_limited_fatal' };
  }

  // --------------------------------------------------------------------------
  // Main
  // --------------------------------------------------------------------------
  if (PHASE1_CSV.includes('PASTE_YOUR_PHASE1_CSV_HERE')) {
    console.error('❌ You need to paste your Phase 1 CSV into the PHASE1_CSV variable first!');
    return;
  }

  const workUrls = parseCSV(PHASE1_CSV);
  console.log(`🔗 AO3 Download Link Collector — Phase 2`);
  console.log(`   ${workUrls.length} work URLs loaded from Phase 1 CSV`);
  console.log('   Starting in 2 seconds...');
  await sleep(2000);

  const results = [];
  let total429s  = 0;

  for (let i = 0; i < workUrls.length; i++) {
    const workUrl = workUrls[i];
    console.log(`\n[${i + 1}/${workUrls.length}] ${workUrl}`);

    const { downloadUrl, status } = await fetchDownloadLink(workUrl);

    if (status === 'rate_limited_fatal') {
      total429s++;
      results.push({ work_url: workUrl, download_url: '', collected_at: timestamp(), status });

      if (total429s >= MAX_429_TOTAL) {
        console.error(`🛑 Hit ${MAX_429_TOTAL} fatal rate limit events — saving CSV and aborting.`);
        downloadCSV(results);
        return;
      }
    } else {
      results.push({ work_url: workUrl, download_url: downloadUrl, collected_at: timestamp(), status });
    }

    if (i < workUrls.length - 1) {
      console.log(`  ⏳ Waiting ${DELAY_MS / 1000}s...`);
      await sleep(DELAY_MS);
    }
  }

  const successes = results.filter(r => r.status === 'success').length;
  const skipped   = results.filter(r => r.status !== 'success').length;
  console.log(`\n✅ Phase 2 complete. ${successes} download links found, ${skipped} skipped/failed.`);
  console.log('Waiting 5 seconds then downloading CSV...');
  await sleep(5000);
  downloadCSV(results);

})();