/* ============================================================
   References Checker v3 — Frontend Application Logic
   ============================================================ */

(function () {
  'use strict';

  /* ----------------------------------------------------------
     State
     ---------------------------------------------------------- */
  let currentProjectSlug = null;
  let currentProjectName = null;
  let sessionId = null;
  let totalRefs = 0;
  let processedCount = 0;
  let allResults = [];
  let allParsedRefs = [];  // parsed .bib entries — used to surface raw_bib even when result is missing or keys mismatch
  let eventSource = null;

  // Review state
  let texContent = null;
  let citations = [];
  let currentCiteIndex = 0;

  /* ----------------------------------------------------------
     DOM references (set in init)
     ---------------------------------------------------------- */
  let projectsView, dashboardView, uploadView, processingView, resultsView, reviewView, verifyView;
  let projectsGrid, noProjectsMsg, projectNameInput, createProjectBtn;
  let dropZone, fileInput, fileNameDisplay, uploadBtn, chooseBtn;
  let uploadError, uploadWarning, uploadProjectName;
  let progressLabel, progressCount, progressFill;
  let liveFeed;
  let statTotal, statPdf, statAbstract, statWebPage, statNotFound;
  let searchInput, resultsGrid, noResultsMsg;
  let downloadCsvBtn, downloadPdfBtn;
  let newCheckBtn, resultsProjectName;
  let texFileInput;
  let reviewRefKey, reviewRefTitle, reviewRefMeta;
  let reviewRefContent, reviewIframe, reviewAbstractText, reviewBibtexText, reviewNoContent;
  let reviewCounter, reviewTexFilename;
  let reviewTabPdf, reviewTabHtml, reviewTabAbstract, reviewTabBibtex, reviewTabMd;
  let reviewSetLinkBtn, reviewRefreshBtn;

  /* ----------------------------------------------------------
     SVG icon helpers
     ---------------------------------------------------------- */
  const SVG = {
    checkCircle: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M9 12l2 2 4-4"/></svg>',
    xCircle: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
    alertTriangle: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
    externalLink: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
    upload: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>',
    chevronDown: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>',
    refresh: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>',
    trash: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
    folder: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',
    link: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
  };

  /* ----------------------------------------------------------
     Utility
     ---------------------------------------------------------- */
  function el(id) { return document.getElementById(id); }

  function escapeHtml(str) {
    if (!str) return '';
    var d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
  }

  function showView(name) {
    projectsView.classList.remove('view--active');
    dashboardView.classList.remove('view--active');
    uploadView.classList.remove('view--active');
    processingView.classList.remove('view--active');
    resultsView.classList.remove('view--active');
    reviewView.classList.remove('view--active');
    if (verifyView) verifyView.classList.remove('view--active');
    if (name === 'projects') projectsView.classList.add('view--active');
    if (name === 'dashboard') dashboardView.classList.add('view--active');
    if (name === 'upload') uploadView.classList.add('view--active');
    if (name === 'processing') processingView.classList.add('view--active');
    if (name === 'results') resultsView.classList.add('view--active');
    if (name === 'review') reviewView.classList.add('view--active');
    if (name === 'verify' && verifyView) verifyView.classList.add('view--active');
    // Hide header in review/verify mode to maximize space (both use full-screen layouts)
    var header = document.getElementById('app-header');
    header.style.display = (name === 'review' || name === 'verify') ? 'none' : '';
  }

  /* ----------------------------------------------------------
     Status helpers
     ---------------------------------------------------------- */
  function statusIcon(status) {
    switch (status) {
      case 'found_pdf':
        return '<span class="result-card__status-icon result-card__status-icon--found_pdf" aria-label="Found with PDF">' + SVG.checkCircle + '</span>';
      case 'found_abstract':
        return '<span class="result-card__status-icon result-card__status-icon--found_abstract" aria-label="Abstract only">' + SVG.alertTriangle + '</span>';
      case 'found_web_page':
        return '<span class="result-card__status-icon result-card__status-icon--found_web_page" aria-label="Web page found">' + SVG.externalLink + '</span>';
      case 'bib_url_unreachable':
        return '<span class="result-card__status-icon result-card__status-icon--bib_url_unreachable" aria-label="Broken URL">' + SVG.xCircle + '</span>';
      case 'not_found':
        return '<span class="result-card__status-icon result-card__status-icon--not_found" aria-label="Not found">' + SVG.xCircle + '</span>';
      default:
        return '<span class="result-card__status-icon result-card__status-icon--error" aria-label="Error">' + SVG.xCircle + '</span>';
    }
  }

  function statusLabel(status) {
    switch (status) {
      case 'found_pdf': return 'PDF Available';
      case 'found_abstract': return 'Abstract Only';
      case 'found_web_page': return 'Web Page';
      case 'bib_url_unreachable': return 'Broken URL — fix the citation';
      case 'not_found': return 'Not Found';
      default: return 'Error';
    }
  }

  function statusBadge(status) {
    var cssClass = 'status-badge status-badge--' + (status || 'error');
    var icon = '';
    switch (status) {
      case 'found_pdf': icon = SVG.checkCircle; break;
      case 'found_abstract': icon = SVG.alertTriangle; break;
      case 'found_web_page': icon = SVG.externalLink; break;
      case 'bib_url_unreachable': icon = SVG.xCircle; break;
      default: icon = SVG.xCircle; break;
    }
    return '<span class="' + cssClass + '">' + icon + ' ' + escapeHtml(statusLabel(status)) + '</span>';
  }

  /* ----------------------------------------------------------
     Project list
     ---------------------------------------------------------- */
  function loadProjects() {
    fetch('/api/projects')
      .then(function (r) { return r.json(); })
      .then(function (projects) {
        projectsGrid.innerHTML = '';
        noProjectsMsg.style.display = projects.length === 0 ? 'block' : 'none';
        projects.forEach(function (p) {
          projectsGrid.appendChild(buildProjectCard(p));
        });
      });
  }

  function buildProjectCard(p) {
    var card = document.createElement('div');
    card.className = 'project-card';
    var statusClass = p.status === 'completed' ? 'project-card--completed' : (p.status === 'processing' ? 'project-card--processing' : '');
    if (statusClass) card.classList.add(statusClass);

    var html = '';
    html += '<div class="project-card__header">';
    html += '<span class="project-card__icon">' + SVG.folder + '</span>';
    html += '<h3 class="project-card__name">' + escapeHtml(p.name) + '</h3>';
    html += '<button type="button" class="project-card__delete" aria-label="Delete project" title="Delete project">' + SVG.trash + '</button>';
    html += '</div>';
    html += '<div class="project-card__stats">';
    html += '<span>Total: ' + (p.total || 0) + '</span>';
    if (p.total > 0) {
      html += '<span class="project-card__stat--pdf">PDF: ' + (p.found_pdf || 0) + '</span>';
      html += '<span class="project-card__stat--abstract">Abstract: ' + (p.found_abstract || 0) + '</span>';
      html += '<span class="project-card__stat--notfound">Not found: ' + (p.not_found || 0) + '</span>';
    }
    html += '</div>';
    html += '<div class="project-card__footer">';
    html += '<span class="project-card__date">' + (p.updated_at ? new Date(p.updated_at).toLocaleDateString() : '') + '</span>';
    html += '<span class="project-card__status-badge project-card__status-badge--' + p.status + '">' + (p.status || 'created') + '</span>';
    html += '</div>';

    card.innerHTML = html;

    // Open project on card click
    card.addEventListener('click', function (e) {
      if (e.target.closest('.project-card__delete')) return;
      openProject(p.slug, p.name);
    });

    // Delete button
    card.querySelector('.project-card__delete').addEventListener('click', function (e) {
      e.stopPropagation();
      if (confirm('Delete project "' + p.name + '" and all its files?')) {
        fetch('/api/projects/' + p.slug, { method: 'DELETE' })
          .then(function () { loadProjects(); });
      }
    });

    return card;
  }

  function createProject() {
    var name = projectNameInput.value.trim();
    if (!name) return;
    fetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name }),
    })
      .then(function (r) { return r.json(); })
      .then(function (proj) {
        projectNameInput.value = '';
        openProject(proj.slug, proj.name);
      });
  }

  function openProject(slug, name) {
    currentProjectSlug = slug;
    currentProjectName = name || slug;
    fetch('/api/projects/' + slug)
      .then(function (r) { return r.json(); })
      .then(function (proj) {
        currentProjectName = proj.name;
        // Build a lookup of raw_bib and all_fields from parsed_refs
        var bibLookup = {};
        (proj.parsed_refs || []).forEach(function (ref) {
          if (ref.bib_key) {
            bibLookup[ref.bib_key] = { raw_bib: ref.raw_bib || null, all_fields: ref.all_fields || null };
          }
        });
        // Merge raw_bib into results
        allResults = (proj.results || []).map(function (r) {
          var info = bibLookup[r.bib_key];
          if (info) {
            if (info.raw_bib) r.raw_bib = info.raw_bib;
            if (info.all_fields) r.all_fields = info.all_fields;
          }
          return r;
        });
        allParsedRefs = proj.parsed_refs || [];
        showDashboard(proj);
      });
  }

  function showDashboard(proj) {
    el('dash-project-name').textContent = proj.name;

    var results = proj.results || [];
    var parsedRefs = proj.parsed_refs || [];
    var citations = proj.citations || [];
    var checks = proj.claim_checks || {};
    var hasResults = results.length > 0;
    var hasTex = !!proj.tex_filename;
    var hasCitations = citations.length > 0;

    // ---- Status block ----
    // References
    var refTotal = parsedRefs.length || results.length;
    var refFound = results.filter(function (r) {
      return r.status && r.status !== 'not_found'
             && r.status !== 'insufficient_data'
             && r.status !== 'bib_url_unreachable';
    }).length;
    var refMissing = refTotal - refFound;
    _dashBar('dash-bar-refs', refFound, refTotal);
    el('dash-detail-refs').textContent = refTotal > 0
      ? (refFound + ' / ' + refTotal + ' found' + (refMissing > 0 ? ' (' + refMissing + ' missing)' : ''))
      : 'No .bib uploaded';

    // Reference .md
    var mdCount = results.filter(function (r) { return r.files && r.files.md; }).length;
    _dashBar('dash-bar-md', mdCount, results.length);
    el('dash-detail-md').textContent = results.length > 0
      ? (mdCount + ' / ' + results.length + ' built')
      : '--';

    // Citations
    _dashBar('dash-bar-cites', hasCitations ? 1 : 0, 1);
    el('dash-detail-cites').textContent = hasTex
      ? (citations.length + ' parsed' + (proj.tex_filename ? ' (' + proj.tex_filename + ')' : ''))
      : 'No .tex uploaded';

    // Claim check
    var checkedCount = 0, issueCount = 0;
    var verdictBuckets = { supported: 0, partial: 0, not_supported: 0, unknown: 0, manual: 0, unchecked: 0 };
    citations.forEach(function (c) {
      var ck = c.claim_check_key;
      var v = ck ? checks[ck] : null;
      if (v) {
        checkedCount++;
        if (v.manual) verdictBuckets.manual++;
        verdictBuckets[v.verdict] = (verdictBuckets[v.verdict] || 0) + 1;
        if (v.verdict === 'not_supported' || v.verdict === 'partial') issueCount++;
      } else {
        verdictBuckets.unchecked++;
      }
    });
    var checkPct = hasCitations ? checkedCount / citations.length : 0;
    _dashBar('dash-bar-check', checkedCount, citations.length || 1,
             issueCount > 0 ? 'warn' : null);
    el('dash-detail-check').textContent = hasCitations
      ? (checkedCount + ' / ' + citations.length + ' checked'
         + (issueCount > 0 ? ' (' + issueCount + ' issues)' : ''))
      : '--';

    // CTAs
    var reviewBtn = el('dash-review-btn');
    var verifyBtn = el('dash-verify-btn');
    reviewBtn.disabled = !hasTex || !hasCitations;
    verifyBtn.disabled = !hasCitations;

    // ---- Issues panel ----
    var issuesList = [];
    // 1. not_supported / partial verdicts
    citations.forEach(function (c, idx) {
      var ck = c.claim_check_key;
      var v = ck ? checks[ck] : null;
      if (v && (v.verdict === 'not_supported' || v.verdict === 'partial')) {
        issuesList.push({ badge: v.verdict === 'not_supported' ? '✗' : '⚠',
                          cls: v.verdict, key: c.bib_key,
                          reason: v.verdict === 'not_supported' ? 'not supported' : 'partial',
                          idx: idx });
      }
    });
    // 2. broken bib URL / identity mismatch / no .md content
    var refsByKey = {};
    results.forEach(function (r) { refsByKey[r.bib_key] = r; });
    citations.forEach(function (c, idx) {
      var ref = refsByKey[c.bib_key];
      if (ref && ref.status === 'bib_url_unreachable') {
        issuesList.push({ badge: '!', cls: 'not_supported', key: c.bib_key,
                          reason: 'broken bib URL — fix the citation', idx: idx });
      } else if (ref && ref.ref_match &&
                 (ref.ref_match.verdict === 'not_matched' ||
                  ref.ref_match.verdict === 'manual_not_matched')) {
        // Identity mismatch — either the LLM flagged it, or the user manually
        // marked it wrong (e.g. bib has hallucinated authors / wrong arXiv ID).
        // Both deserve to stay visible in the issues list until resolved.
        var reason = ref.ref_match.verdict === 'manual_not_matched'
          ? 'citation flagged as wrong (manual) — fix the bib'
          : 'downloaded text does not match title/authors';
        issuesList.push({ badge: '!', cls: 'not_supported', key: c.bib_key,
                          reason: reason, idx: idx });
      } else if (ref && !(ref.files && ref.files.md)) {
        issuesList.push({ badge: '?', cls: 'unknown', key: c.bib_key,
                          reason: 'no .md content', idx: idx });
      }
      if (!ref) {
        issuesList.push({ badge: '?', cls: 'unknown', key: c.bib_key,
                          reason: 'no reference found', idx: idx });
      }
    });
    // Dedupe by bib_key + reason, keep first occurrence
    var seen = {};
    issuesList = issuesList.filter(function (item) {
      var k = item.key + '|' + item.reason;
      if (seen[k]) return false;
      seen[k] = true;
      return true;
    });
    _renderDashIssues(issuesList.slice(0, 8));
    var issuesCountEl = el('dash-issues-count');
    if (issuesList.length > 0) {
      issuesCountEl.textContent = issuesList.length;
      issuesCountEl.style.display = '';
    } else {
      issuesCountEl.style.display = 'none';
    }
    var issuesOpenEl = el('dash-issues-open');
    if (issuesList.length > 0 && hasCitations) {
      issuesOpenEl.style.display = '';
      issuesOpenEl.onclick = function () { openVerifyView(); };
    } else {
      issuesOpenEl.style.display = 'none';
    }

    // ---- Recently verified panel ----
    var verified = [];
    citations.forEach(function (c, idx) {
      var ck = c.claim_check_key;
      var v = ck ? checks[ck] : null;
      if (v && v.verdict === 'supported') {
        verified.push({ key: c.bib_key, v: v, idx: idx });
      }
    });
    verified.sort(function (a, b) {
      return (b.v.checked_at || '').localeCompare(a.v.checked_at || '');
    });
    _renderDashVerified(verified.slice(0, 5));
    var verifiedOpenEl = el('dash-verified-open');
    if (verified.length > 5) {
      verifiedOpenEl.style.display = '';
      verifiedOpenEl.onclick = function () { openVerifyView(); };
    } else {
      verifiedOpenEl.style.display = 'none';
    }

    // ---- Reference breakdown ----
    var refStats = { pdf: 0, abs: 0, web: 0, broken: 0, nf: 0 };
    var rmStats = { matched: 0, not_matched: 0, manual: 0, unverifiable: 0, unchecked: 0 };
    results.forEach(function (r) {
      if (r.status === 'found_pdf') refStats.pdf++;
      else if (r.status === 'found_abstract') refStats.abs++;
      else if (r.status === 'found_web_page') refStats.web++;
      else if (r.status === 'bib_url_unreachable') refStats.broken++;
      else refStats.nf++;
      var rm = r.ref_match;
      if (!rm) rmStats.unchecked++;
      else if (rm.verdict === 'matched') rmStats.matched++;
      else if (rm.verdict === 'not_matched') rmStats.not_matched++;
      else if (rm.verdict === 'manual_matched' || rm.verdict === 'manual_not_matched') rmStats.manual++;
      else rmStats.unverifiable++;
    });
    _renderDashBreakdown('dash-ref-breakdown', [
      { dot: 'pdf',      label: 'PDF found',      count: refStats.pdf,    total: results.length },
      { dot: 'abstract', label: 'Abstract only',   count: refStats.abs,    total: results.length },
      { dot: 'webpage',  label: 'Web page',        count: refStats.web,    total: results.length },
      { dot: 'notfound', label: 'Broken URL',      count: refStats.broken, total: results.length },
      { dot: 'notfound', label: 'Not found',       count: refStats.nf,     total: results.length },
      { dot: 'pdf',      label: 'Identity matched',     count: rmStats.matched,      total: results.length },
      { dot: 'notfound', label: 'Identity NOT matched', count: rmStats.not_matched,  total: results.length },
      { dot: 'abstract', label: 'Identity unverifiable', count: rmStats.unverifiable, total: results.length },
      { dot: 'webpage',  label: 'Identity manual',      count: rmStats.manual,       total: results.length },
      { dot: 'notfound', label: 'Identity unchecked',   count: rmStats.unchecked,    total: results.length },
    ]);

    // ---- Top blocked hosts (v6.1 Phase D) ----
    // Fire off async stats fetch; the card stays hidden when there are no
    // failed downloads. Deliberately out-of-band from the dashboard render
    // so a slow stats API doesn't delay the main view.
    _loadBlockedHostsCard(proj.slug);

    // ---- Citation breakdown ----
    _renderDashBreakdown('dash-cite-breakdown', [
      { dot: 'supported',     label: 'Supported',       count: verdictBuckets.supported,     total: citations.length },
      { dot: 'partial',       label: 'Partial',          count: verdictBuckets.partial,       total: citations.length },
      { dot: 'not_supported', label: 'Not supported',    count: verdictBuckets.not_supported, total: citations.length },
      { dot: 'unknown',       label: 'Unknown',          count: verdictBuckets.unknown,       total: citations.length },
      { dot: 'manual',        label: 'Manual override',  count: verdictBuckets.manual,        total: citations.length },
      { dot: 'unchecked',     label: 'Not yet checked',  count: verdictBuckets.unchecked,     total: citations.length },
    ]);

    // ---- Activity log ----
    var activity = proj.activity || [];
    var actPanel = el('dash-activity-panel');
    if (activity.length > 0 && actPanel) {
      actPanel.style.display = '';
      var actList = el('dash-activity-list');
      actList.innerHTML = '';
      activity.slice(-10).reverse().forEach(function (a) {
        var row = document.createElement('div');
        row.className = 'dash-activity-row';
        row.innerHTML = '<span class="dash-activity-row__time">' + escapeHtml(_relativeTime(a.ts)) + '</span>'
          + '<span class="dash-activity-row__msg">' + escapeHtml(a.message || a.type) + '</span>';
        actList.appendChild(row);
      });
    } else if (actPanel) {
      actPanel.style.display = 'none';
    }

    // ---- Operations strip state ----
    el('dash-csv-btn').disabled = !hasResults;
    el('dash-pdf-btn').disabled = !hasResults;
    var buildMdBtn = el('dash-build-md-btn');
    if (buildMdBtn) buildMdBtn.disabled = !hasResults;
    var checkRmBtn = el('dash-check-refs-match-btn');
    if (checkRmBtn) checkRmBtn.disabled = !hasResults;
    var validityBtn = el('dash-validity-report-btn');
    if (validityBtn) validityBtn.disabled = !hasResults;
    var dlRefsBtn = el('dash-download-refs-btn');
    if (dlRefsBtn) dlRefsBtn.disabled = !hasResults;
    el('dash-view-refs-btn').disabled = !hasResults;

    // Cache results + parsed_refs for other views
    allResults = results;
    allParsedRefs = parsedRefs;

    showView('dashboard');
  }

  // Dashboard render helpers
  function _dashBar(barId, value, total, variant) {
    var fill = el(barId);
    if (!fill) return;
    var pct = total > 0 ? Math.round((value / total) * 100) : 0;
    fill.style.width = pct + '%';
    fill.className = 'dash-status__fill' + (variant === 'warn' ? ' dash-status__fill--warn' : '');
  }

  // v6.1 Phase D — Top blocked hosts card. Shows hosts that failed to
  // download along with a suggested tier (curl_cffi / playwright / manual).
  function _loadBlockedHostsCard(slug) {
    if (!slug) return;
    var card = el('dash-blocked-hosts-card');
    var list = el('dash-blocked-hosts-list');
    if (!card || !list) return;
    fetch('/api/projects/' + encodeURIComponent(slug) + '/download-stats')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var top = (data && data.stats && data.stats.top_blocked) || [];
        if (!top.length) { card.style.display = 'none'; return; }
        list.innerHTML = '';
        top.forEach(function (item) {
          var row = document.createElement('div');
          row.className = 'dash-blocked-row';
          var hostSpan = '<span class="dash-blocked-row__host">' +
                        escapeHtml(item.host) + '</span>';
          var countSpan = '<span class="dash-blocked-row__count">' +
                         item.refs + ' ref' + (item.refs === 1 ? '' : 's') + '</span>';
          var hint = _blockedSuggestionText(item.suggested);
          row.innerHTML = hostSpan + countSpan +
                          '<span class="dash-blocked-row__hint">' + hint + '</span>';
          list.appendChild(row);
        });
        card.style.display = '';
      })
      .catch(function () { card.style.display = 'none'; });
  }

  function _blockedSuggestionText(suggested) {
    if (suggested === 'curl_cffi') {
      return '→ enable <code>curl_cffi</code> tier in settings (Phase B, ~200 MB dep)';
    }
    if (suggested === 'playwright') {
      return '→ enable <code>playwright</code> tier in settings (Phase C, ~400 MB dep)';
    }
    return '→ use Upload PDF or Paste Content';
  }

  function _renderDashIssues(items) {
    var list = el('dash-issues-list');
    if (!list) return;
    if (!items.length) { list.innerHTML = '<p class="dash-panel__empty">No issues.</p>'; return; }
    list.innerHTML = '';
    items.forEach(function (item) {
      var row = document.createElement('div');
      row.className = 'dash-issue-row';
      row.innerHTML =
        '<span class="dash-issue-row__badge dash-issue-row__badge--' + item.cls + '">' + escapeHtml(item.badge) + '</span>' +
        '<span class="dash-issue-row__key">' + escapeHtml(item.key) + '</span>' +
        '<span class="dash-issue-row__reason">' + escapeHtml(item.reason) + '</span>';
      row.addEventListener('click', function () {
        if (item.idx != null) {
          openReviewView();
          setTimeout(function () { navigateToCitation(item.idx); }, 400);
        }
      });
      list.appendChild(row);
    });
  }

  function _renderDashVerified(items) {
    var list = el('dash-verified-list');
    if (!list) return;
    if (!items.length) { list.innerHTML = '<p class="dash-panel__empty">No verdicts yet.</p>'; return; }
    list.innerHTML = '';
    items.forEach(function (item) {
      var row = document.createElement('div');
      row.className = 'dash-issue-row';
      row.innerHTML =
        '<span class="dash-issue-row__badge dash-issue-row__badge--supported">&#10003;</span>' +
        '<span class="dash-issue-row__key">' + escapeHtml(item.key) + '</span>' +
        '<span class="dash-issue-row__reason">' + (item.v.manual ? 'manual' : escapeHtml(_relativeTime(item.v.checked_at))) + '</span>';
      row.addEventListener('click', function () {
        openReviewView();
        setTimeout(function () { navigateToCitation(item.idx); }, 400);
      });
      list.appendChild(row);
    });
  }

  function _renderDashBreakdown(containerId, rows) {
    var container = el(containerId);
    if (!container) return;
    container.innerHTML = '';
    rows.forEach(function (r) {
      if (r.count === 0 && r.total === 0) return;
      var pct = r.total > 0 ? Math.round((r.count / r.total) * 100) : 0;
      var row = document.createElement('div');
      row.className = 'dash-bd-row';
      row.innerHTML =
        '<span class="dash-bd-row__dot dash-bd-row__dot--' + r.dot + '"></span>' +
        '<span class="dash-bd-row__label">' + escapeHtml(r.label) + '</span>' +
        '<span class="dash-bd-row__count">' + r.count + '</span>' +
        '<span class="dash-bd-row__pct">' + (r.total > 0 ? pct + '%' : '') + '</span>';
      container.appendChild(row);
    });
    if (!container.children.length) {
      container.innerHTML = '<p class="dash-panel__empty">No data.</p>';
    }
  }

  function _relativeTime(isoStr) {
    if (!isoStr) return '';
    try {
      var d = new Date(isoStr);
      var now = new Date();
      var diffMs = now - d;
      if (diffMs < 0) return 'just now';
      var mins = Math.floor(diffMs / 60000);
      if (mins < 1) return 'just now';
      if (mins < 60) return mins + 'm ago';
      var hrs = Math.floor(mins / 60);
      if (hrs < 24) return hrs + 'h ago';
      var days = Math.floor(hrs / 24);
      if (days === 1) return 'yesterday';
      if (days < 30) return days + 'd ago';
      return d.toLocaleDateString();
    } catch (e) { return isoStr; }
  }

  function goToProjects() {
    if (eventSource) { eventSource.close(); eventSource = null; }
    currentProjectSlug = null;
    currentProjectName = null;
    sessionId = null;
    loadProjects();
    showView('projects');
  }

  function goToDashboard() {
    if (currentProjectSlug) {
      openProject(currentProjectSlug, currentProjectName);
    } else {
      goToProjects();
    }
  }

  function dashUploadBib(file) {
    if (!currentProjectSlug) return;
    if (!file.name.toLowerCase().endsWith('.bib')) { alert('Please select a .bib file'); return; }
    var formData = new FormData();
    formData.append('file', file);
    fetch('/api/projects/' + currentProjectSlug + '/upload', { method: 'POST', body: formData })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) { alert(data.error); return; }
        sessionId = data.session_id;
        totalRefs = data.total;
        processedCount = 0;
        allResults = [];
        allParsedRefs = [];
        progressCount.textContent = '0 / ' + totalRefs;
        progressFill.style.width = '0%';
        progressFill.classList.add('progress-bar__fill--active');
        liveFeed.innerHTML = '';
        showView('processing');
        startSSE();
      })
      .catch(function (err) { alert('Upload failed: ' + err.message); });
  }

  function dashUploadTex(file) {
    if (!currentProjectSlug) return;
    if (!file.name.toLowerCase().endsWith('.tex')) { alert('Please select a .tex file'); return; }
    var formData = new FormData();
    formData.append('file', file);
    fetch('/api/projects/' + currentProjectSlug + '/upload-tex', { method: 'POST', body: formData })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) { alert(data.error); return; }
        openReviewView();
      })
      .catch(function (err) { alert('Upload failed: ' + err.message); });
  }

  /* ----------------------------------------------------------
     Card rendering
     ---------------------------------------------------------- */
  function buildResultCard(result) {
    var card = document.createElement('article');
    card.className = 'result-card result-card--' + (result.status || 'error');
    card.setAttribute('role', 'region');
    card.setAttribute('aria-label', 'Reference: ' + (result.title || result.bib_key || 'Unknown'));
    card.dataset.bibKey = result.bib_key || '';
    card.dataset.title = (result.title || '').toLowerCase();
    card.dataset.authors = (result.authors || []).join(' ').toLowerCase();

    var html = '';

    // Bib key + refresh button
    html += '<div class="result-card__top-row">';
    if (result.bib_key) {
      html += '<span class="result-card__bib-key">' + escapeHtml(result.bib_key) + '</span>';
    }
    if (currentProjectSlug) {
      html += '<button type="button" class="result-card__set-link-btn" aria-label="Set link manually" title="Set link">' + SVG.link + '</button>';
      html += '<button type="button" class="result-card__refresh-btn" aria-label="Refresh this reference" title="Refresh">' + SVG.refresh + '</button>';
    }
    html += '</div>';

    // Header row: icon + title
    html += '<div class="result-card__header">';
    html += statusIcon(result.status);
    html += '<h3 class="result-card__title">' + escapeHtml(result.title || result.bib_key || 'Untitled') + '</h3>';
    html += '</div>';

    // Meta
    var metaParts = [];
    if (result.authors && result.authors.length) {
      metaParts.push('<span>' + escapeHtml(result.authors.join(', ')) + '</span>');
    }
    if (result.year) metaParts.push('<span>' + escapeHtml(String(result.year)) + '</span>');
    if (result.journal) metaParts.push('<span>' + escapeHtml(result.journal) + '</span>');
    if (metaParts.length) html += '<p class="result-card__meta">' + metaParts.join('') + '</p>';

    // Badges
    html += '<div class="result-card__badges">';
    html += statusBadge(result.status);
    var rmBadge = _matchSummary(result.ref_match);
    html += '<span class="status-badge status-badge--match-' + rmBadge.cls + '" title="' +
            escapeHtml(rmBadge.title) + '">Match ' + escapeHtml(rmBadge.label) + '</span>';
    // v6.1 Phase D — tier badge showing which download tier delivered the PDF.
    var pdfOrigin = (result.files_origin || {}).pdf;
    if (pdfOrigin && pdfOrigin.tier) {
      var tierCls = _tierBadgeClass(pdfOrigin.tier);
      var tierTitle = 'Downloaded via ' + pdfOrigin.tier +
                      (pdfOrigin.captured_at ? ' · ' + pdfOrigin.captured_at.split('T')[0] : '') +
                      (pdfOrigin.url ? '\n' + pdfOrigin.url : '');
      html += '<span class="tier-badge tier-badge--' + tierCls +
              '" title="' + escapeHtml(tierTitle) + '">via ' +
              escapeHtml(_tierLabel(pdfOrigin.tier)) + '</span>';
    }
    if (result.sources && result.sources.length) {
      result.sources.forEach(function (src) {
        html += '<span class="source-badge">' + escapeHtml(src) + '</span>';
      });
    }
    if (result.citation_count != null && result.citation_count > 0) {
      html += '<span class="source-badge">Cited: ' + escapeHtml(String(result.citation_count)) + '</span>';
    }
    html += '</div>';

    // Broken-URL banner: surface the underlying failure so the user knows to fix the bib URL.
    if (result.status === 'bib_url_unreachable') {
      html += '<p class="result-card__error-msg">' + escapeHtml(result.error || 'Bib URL is unreachable');
      if (result.url) {
        html += ' &mdash; <a href="' + escapeHtml(result.url) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(result.url) + '</a>';
      }
      html += '</p>';
    }

    // Local files indicators
    if (result.files && currentProjectSlug) {
      html += '<div class="result-card__files">';
      if (result.files.pdf) {
        html += '<a class="result-card__file-link result-card__file-link--pdf" href="/api/projects/' + currentProjectSlug + '/files/' + encodeURIComponent(result.files.pdf) + '" target="_blank">PDF</a>';
      }
      if (result.files.abstract) {
        html += '<a class="result-card__file-link result-card__file-link--txt" href="/api/projects/' + currentProjectSlug + '/files/' + encodeURIComponent(result.files.abstract) + '" target="_blank">TXT</a>';
      }
      if (result.files.page) {
        html += '<a class="result-card__file-link result-card__file-link--html" href="/api/projects/' + currentProjectSlug + '/files/' + encodeURIComponent(result.files.page) + '" target="_blank">HTML</a>';
      }
      html += '</div>';
    }

    // Actions
    html += '<div class="result-card__actions">';
    if (result.abstract) {
      var abstractId = 'abstract-' + (result.bib_key || Math.random().toString(36).substr(2));
      html += '<button type="button" class="result-card__abstract-toggle" aria-expanded="false" aria-controls="' + abstractId + '">';
      html += SVG.chevronDown + ' Show abstract</button>';
    }
    if (result.raw_bib) {
      html += '<button type="button" class="result-card__bibtex-toggle" aria-expanded="false">';
      html += SVG.chevronDown + ' BibTeX</button>';
    }
    if (result.pdf_url) {
      html += '<a class="result-card__pdf-link" href="' + escapeHtml(result.pdf_url) + '" target="_blank" rel="noopener noreferrer">' + SVG.externalLink + ' Open PDF</a>';
    } else if (result.url) {
      html += '<a class="result-card__pdf-link" href="' + escapeHtml(result.url) + '" target="_blank" rel="noopener noreferrer">' + SVG.externalLink + ' View online</a>';
    }
    html += '</div>';

    // Abstract
    if (result.abstract) {
      var absId = 'abstract-' + (result.bib_key || Math.random().toString(36).substr(2));
      html += '<div class="result-card__abstract" id="' + absId + '" role="region" aria-label="Abstract">' + escapeHtml(result.abstract) + '</div>';
    }

    // BibTeX record
    if (result.raw_bib) {
      html += '<div class="result-card__bibtex"><pre>' + escapeHtml(result.raw_bib) + '</pre></div>';
    }

    card.innerHTML = html;

    // Wire abstract toggle
    var toggleBtn = card.querySelector('.result-card__abstract-toggle');
    if (toggleBtn) {
      toggleBtn.addEventListener('click', function () {
        var abstractDiv = card.querySelector('.result-card__abstract');
        var expanded = toggleBtn.getAttribute('aria-expanded') === 'true';
        if (expanded) {
          abstractDiv.classList.remove('result-card__abstract--visible');
          toggleBtn.classList.remove('result-card__abstract-toggle--open');
          toggleBtn.setAttribute('aria-expanded', 'false');
          toggleBtn.innerHTML = SVG.chevronDown + ' Show abstract';
        } else {
          abstractDiv.classList.add('result-card__abstract--visible');
          toggleBtn.classList.add('result-card__abstract-toggle--open');
          toggleBtn.setAttribute('aria-expanded', 'true');
          toggleBtn.innerHTML = SVG.chevronDown + ' Hide abstract';
        }
      });
    }

    // Wire BibTeX toggle
    var bibtexBtn = card.querySelector('.result-card__bibtex-toggle');
    if (bibtexBtn) {
      bibtexBtn.addEventListener('click', function () {
        var bibtexDiv = card.querySelector('.result-card__bibtex');
        var expanded = bibtexBtn.getAttribute('aria-expanded') === 'true';
        if (expanded) {
          bibtexDiv.classList.remove('result-card__bibtex--visible');
          bibtexBtn.setAttribute('aria-expanded', 'false');
          bibtexBtn.innerHTML = SVG.chevronDown + ' BibTeX';
        } else {
          bibtexDiv.classList.add('result-card__bibtex--visible');
          bibtexBtn.setAttribute('aria-expanded', 'true');
          bibtexBtn.innerHTML = SVG.chevronDown + ' Hide BibTeX';
        }
      });
    }

    // Wire refresh button
    var refreshBtn = card.querySelector('.result-card__refresh-btn');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        refreshReference(result.bib_key, card);
      });
    }

    // Wire set-link button
    var setLinkBtn = card.querySelector('.result-card__set-link-btn');
    if (setLinkBtn) {
      setLinkBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        setManualLink(result.bib_key, card);
      });
    }

    return card;
  }

  function buildErrorCard(data) {
    var card = document.createElement('article');
    card.className = 'result-card result-card--error';
    card.setAttribute('role', 'region');
    card.dataset.title = (data.bib_key || '').toLowerCase();
    card.dataset.authors = '';
    var html = '';
    html += '<div class="result-card__header">';
    html += '<span class="result-card__status-icon result-card__status-icon--error">' + SVG.xCircle + '</span>';
    html += '<h3 class="result-card__title">' + escapeHtml(data.bib_key || 'Unknown reference') + '</h3>';
    html += '</div>';
    html += '<p class="result-card__error-msg">' + escapeHtml(data.message || 'An error occurred') + '</p>';
    html += '<div class="result-card__badges">' + statusBadge('not_found') + '</div>';
    card.innerHTML = html;
    return card;
  }

  /* ----------------------------------------------------------
     Refresh single reference
     ---------------------------------------------------------- */
  function refreshReference(bibKey, cardEl) {
    if (!currentProjectSlug) return;
    var refreshBtn = cardEl.querySelector('.result-card__refresh-btn');
    if (refreshBtn) {
      refreshBtn.classList.add('result-card__refresh-btn--spinning');
      refreshBtn.disabled = true;
    }

    fetch('/api/projects/' + currentProjectSlug + '/refresh/' + encodeURIComponent(bibKey), { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function () { pollRefreshStatus(bibKey, cardEl); })
      .catch(function () {
        if (refreshBtn) { refreshBtn.classList.remove('result-card__refresh-btn--spinning'); refreshBtn.disabled = false; }
      });
  }

  function pollRefreshStatus(bibKey, cardEl) {
    var interval = setInterval(function () {
      fetch('/api/projects/' + currentProjectSlug + '/refresh-status/' + encodeURIComponent(bibKey))
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.status === 'done') {
            clearInterval(interval);
            // Update allResults
            for (var i = 0; i < allResults.length; i++) {
              if (allResults[i].bib_key === bibKey) {
                allResults[i] = data.result;
                break;
              }
            }
            // Replace card and update stats
            var newCard = buildResultCard(data.result);
            cardEl.replaceWith(newCard);
            updateStatsBar();
          }
        });
    }, 2000);
  }

  /* ----------------------------------------------------------
     Set manual link
     ---------------------------------------------------------- */
  function setManualLink(bibKey, cardEl) {
    if (!currentProjectSlug) return;
    var url = prompt('Enter URL to the paper (PDF or web page):');
    if (!url || !url.trim()) return;
    url = url.trim();

    var setLinkBtn = cardEl.querySelector('.result-card__set-link-btn');
    if (setLinkBtn) { setLinkBtn.disabled = true; setLinkBtn.style.opacity = '0.4'; }

    fetch('/api/projects/' + currentProjectSlug + '/set-link/' + encodeURIComponent(bibKey), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: url }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.result) {
          for (var i = 0; i < allResults.length; i++) {
            if (allResults[i].bib_key === bibKey) {
              allResults[i] = data.result;
              break;
            }
          }
          var newCard = buildResultCard(data.result);
          cardEl.replaceWith(newCard);
          updateStatsBar();
        }
      })
      .catch(function () {
        if (setLinkBtn) { setLinkBtn.disabled = false; setLinkBtn.style.opacity = ''; }
      });
  }

  /* ----------------------------------------------------------
     Citation Review (View 4)
     ---------------------------------------------------------- */
  function uploadTex() {
    if (!currentProjectSlug || !texFileInput.files[0]) return;
    var file = texFileInput.files[0];
    var formData = new FormData();
    formData.append('file', file);

    fetch('/api/projects/' + currentProjectSlug + '/upload-tex', { method: 'POST', body: formData })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) { alert(data.error); return; }
        openReviewView();
      })
      .catch(function (err) { alert('Upload failed: ' + err.message); });
  }

  function openReviewView() {
    if (!currentProjectSlug) return;
    fetch('/api/projects/' + currentProjectSlug + '/tex')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) { alert(data.error); return; }
        texContent = data.tex_content;
        citations = data.citations || [];
        currentCiteIndex = 0;
        reviewTexFilename.textContent = data.tex_filename || '';

        // Set download link
        var dlBtn = el('review-download-tex-btn');
        dlBtn.href = '/api/projects/' + currentProjectSlug + '/download-tex';
        dlBtn.download = data.tex_filename || 'document.tex';

        // Wire Verify Table button
        var openVerifyBtn = el('review-open-verify-btn');
        if (openVerifyBtn) openVerifyBtn.onclick = function (e) { e.preventDefault(); openVerifyView(); };

        // Show view FIRST so CodeMirror can measure dimensions
        showView('review');

        // Load v4 verdicts in parallel
        Promise.all([loadClaimCheckSettings(), loadVerdictsFromProject()]).then(function () {
          renderReferencesPanel();
          if (citations.length > 0) renderVerdictHeader(currentCiteIndex);
        });

        // Then initialize editor (may need a frame for layout to settle)
        requestAnimationFrame(function () {
          renderTexPanel();
          if (citations.length > 0) {
            // Give CM a tick to fully render before navigating
            setTimeout(function () { navigateToCitation(0); }, 200);
          } else {
            reviewCounter.textContent = 'No citations found';
            showReferencePanel(null);
          }
        });
      });
  }

  // Client-side citation parser (mirrors tex_parser.py)
  var CITE_RE = /\\(?:cite[tp]?|parencite|textcite|autocite|fullcite|nocite)(?:\s*\[[^\]]*\])*\s*\{([^}]+)\}/g;

  function parseCitationsLocal(text) {
    var results = [];
    var match;
    CITE_RE.lastIndex = 0;
    while ((match = CITE_RE.exec(text)) !== null) {
      var keysStr = match[1];
      var citeCmd = match[0];
      var pos = match.index;
      var keys = keysStr.split(',').map(function (k) { return k.trim(); }).filter(Boolean);
      for (var i = 0; i < keys.length; i++) {
        results.push({
          bib_key: keys[i],
          position: pos,
          end_position: pos + citeCmd.length,
          cite_command: citeCmd,
        });
      }
    }
    return results;
  }

  function renderTexPanel() {
    var parentEl = el('review-cm-editor');
    parentEl.innerHTML = '';

    function tryInit() {
      if (window.cmEditor && window.cmEditor.init) {
        // init is async — wait for it, then set up cite ranges
        Promise.resolve(window.cmEditor.init(parentEl, texContent, onTexContentChanged))
          .then(function () {
            updateCiteRangesInEditor();
          });
      } else {
        setTimeout(tryInit, 200);
      }
    }
    tryInit();
  }

  function onTexContentChanged(newContent) {
    texContent = newContent;
    // Re-parse citations locally
    citations = parseCitationsLocal(newContent);
    updateCiteRangesInEditor();
    // Update counter
    if (currentCiteIndex >= citations.length) currentCiteIndex = Math.max(0, citations.length - 1);
    if (citations.length > 0) {
      var cite = citations[currentCiteIndex];
      reviewCounter.textContent = (currentCiteIndex + 1) + ' / ' + citations.length + ' (' + cite.bib_key + ')';
    } else {
      reviewCounter.textContent = 'No citations';
    }
  }

  function updateCiteRangesInEditor() {
    if (!window.cmEditor || !window.cmEditor.isReady()) return;
    var ranges = citations.map(function (c) { return { from: c.position, to: c.end_position }; });
    window.cmEditor.setCiteRanges(ranges);
  }

  function navigateToCitation(idx) {
    if (citations.length === 0) return;
    if (idx < 0) idx = citations.length - 1;
    if (idx >= citations.length) idx = 0;
    currentCiteIndex = idx;

    var cite = citations[idx];
    reviewCounter.textContent = (idx + 1) + ' / ' + citations.length + ' (' + cite.bib_key + ')';

    highlightCitation(cite);
    showReferencePanel(cite.bib_key);
    renderVerdictHeader(idx);
    highlightSelectedRefCard(cite.bib_key);

    // Persist last-viewed position for Resume Review on dashboard
    if (currentProjectSlug) {
      fetch('/api/projects/' + currentProjectSlug + '/last-viewed', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ citation_index: idx }),
      }).catch(function () {});
    }
  }

  function highlightCitation(cite) {
    if (!window.cmEditor || !window.cmEditor.isReady()) return;

    var text = window.cmEditor.getContent();
    var start = cite.position;
    var end = cite.end_position;

    // If positions don't match (user edited), search for the command
    if (text.substring(start, end) !== cite.cite_command) {
      var searchPos = text.indexOf(cite.cite_command);
      if (searchPos >= 0) {
        start = searchPos;
        end = searchPos + cite.cite_command.length;
      }
    }

    window.cmEditor.highlightRange(start, end);
  }

  function saveTexContent() {
    if (!currentProjectSlug) return;
    var content = window.cmEditor ? window.cmEditor.getContent() : texContent;
    texContent = content;

    fetch('/api/projects/' + currentProjectSlug + '/save-tex', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: content }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.citations) {
          citations = data.citations;
          updateCiteRangesInEditor();
          if (currentCiteIndex >= citations.length) currentCiteIndex = Math.max(0, citations.length - 1);
          if (citations.length > 0) navigateToCitation(currentCiteIndex);
          else reviewCounter.textContent = 'No citations found';
        }
        var saveBtn = el('review-save-tex-btn');
        saveBtn.textContent = 'Saved';
        setTimeout(function () { saveBtn.textContent = 'Save'; }, 1500);
      })
      .catch(function () { alert('Save failed'); });
  }

  // Look up a parsed_ref by exact bib_key match. Returns the parsed_ref dict or null.
  // Used to surface the BibTeX tab when a result hasn't been generated yet but the
  // entry exists in the .bib — so the user can initiate a manual search.
  function _findParsedRefFallback(bibKey) {
    if (!bibKey || !allParsedRefs || !allParsedRefs.length) return null;
    for (var i = 0; i < allParsedRefs.length; i++) {
      if (allParsedRefs[i].bib_key === bibKey) return allParsedRefs[i];
    }
    return null;
  }

  function showReferencePanel(bibKey) {
    reviewIframe.style.display = 'none';
    reviewIframe.src = '';
    reviewAbstractText.style.display = 'none';
    reviewBibtexText.style.display = 'none';
    var reviewMdText = el('review-md-text');
    if (reviewMdText) reviewMdText.style.display = 'none';
    reviewNoContent.style.display = 'none';

    // Reset tabs
    reviewTabPdf.classList.remove('review-tab--active');
    reviewTabHtml.classList.remove('review-tab--active');
    reviewTabAbstract.classList.remove('review-tab--active');
    reviewTabBibtex.classList.remove('review-tab--active');
    if (reviewTabMd) reviewTabMd.classList.remove('review-tab--active');
    reviewTabPdf.disabled = true;
    reviewTabHtml.disabled = true;
    reviewTabAbstract.disabled = true;
    reviewTabBibtex.disabled = true;
    if (reviewTabMd) reviewTabMd.disabled = true;
    reviewSetLinkBtn.disabled = false;
    reviewRefreshBtn.disabled = false;
    var dlWarnReset = el('review-dl-warning');
    if (dlWarnReset) dlWarnReset.style.display = 'none';

    if (!bibKey) {
      reviewRefKey.textContent = '';
      reviewRefTitle.textContent = '';
      reviewRefMeta.textContent = '';
      reviewNoContent.style.display = 'block';
      return;
    }

    // Find result
    var ref = null;
    for (var i = 0; i < allResults.length; i++) {
      if (allResults[i].bib_key === bibKey) { ref = allResults[i]; break; }
    }

    var addRefBtn = el('review-add-ref-btn');
    if (!ref) {
      reviewRefKey.textContent = bibKey;
      reviewRefTitle.textContent = 'Reference not found in project results';
      // If the exact bib_key is in parsed_refs (lookup hasn't produced a result yet),
      // surface the raw BibTeX so the user can initiate a manual search.
      var fallback = _findParsedRefFallback(bibKey);
      var meta = '<span style="color:var(--color-error);">This citation key does not match any reference in the .bib file. Click <strong>Add Reference</strong> to enter the bibliography metadata manually.</span>';
      if (fallback) {
        meta = '<span style="color:var(--color-muted);">Parsed from the .bib but lookup has not run yet — see the <strong>BibTeX</strong> tab below.</span>';
      }
      reviewRefMeta.innerHTML = meta;
      reviewSetLinkBtn.disabled = true;
      reviewRefreshBtn.disabled = true;
      if (addRefBtn) {
        addRefBtn.style.display = '';
        addRefBtn.dataset.bibKey = bibKey;
        addRefBtn.disabled = false;
      }
      // Surface the raw BibTeX if the exact bib_key was found in parsed_refs
      if (fallback && fallback.raw_bib) {
        reviewTabBibtex.disabled = false;
        switchTab('bibtex', { raw_bib: fallback.raw_bib });
      } else {
        reviewNoContent.style.display = 'block';
      }
      return;
    }
    if (addRefBtn) addRefBtn.style.display = 'none';

    // Broken-URL banner: status='bib_url_unreachable' means the bib URL itself
    // failed to download. We show a prominent warning and disable the data tabs
    // (PDF/HTML/Abstract/Markdown) since none have content — only BibTeX is useful.
    var brokenWarn = el('review-broken-url-warning');
    var isBrokenUrl = ref.status === 'bib_url_unreachable';
    if (brokenWarn) {
      if (isBrokenUrl) {
        var msg = '<strong>Broken bib URL — fix the citation.</strong> ';
        msg += escapeHtml(ref.error || 'The URL in this bib entry could not be downloaded.');
        if (ref.url) {
          msg += '<br><span class="review-broken-url-warning__url">URL: <a href="' +
                 escapeHtml(ref.url) + '" target="_blank" rel="noopener noreferrer">' +
                 escapeHtml(ref.url) + '</a></span>';
        }
        msg += '<br><span class="review-broken-url-warning__hint">Use <strong>Set Link</strong>, ' +
               '<strong>Upload PDF</strong>, or <strong>Paste Content</strong> in this panel ' +
               'to provide a working source.</span>';
        brokenWarn.innerHTML = msg;
        brokenWarn.style.display = 'block';
      } else {
        brokenWarn.style.display = 'none';
      }
    }

    // Reference identity match status (separate from broken-URL — can show together
    // with normal status when the LLM has weighed in on whether the downloaded text
    // actually matches the bib's title + authors).
    var rmEl = el('review-ref-match');
    if (rmEl) _renderRefMatchPanel(rmEl, ref, isBrokenUrl);

    // Warn when a pdf_url is set but we have no local PDF file (site likely bot-blocks).
    // The right-panel iframe will show the PDF from the remote URL (which works in a
    // browser), but claim-checking uses the local .md — which won't include the PDF body.
    var dlWarn = el('review-dl-warning');
    if (dlWarn) {
      var filesForWarn = ref.files || {};
      if (!isBrokenUrl && ref.pdf_url && !filesForWarn.pdf) {
        dlWarn.innerHTML = '<strong>PDF shown from remote URL</strong> — the file couldn\'t be downloaded locally ' +
          '(site probably bot-blocks). Claim-checking has only the abstract. ' +
          'Click <strong>Upload PDF</strong> (in this panel) to save the file so it\'s included in the <code>.md</code>.';
        dlWarn.style.display = 'block';
      } else {
        dlWarn.style.display = 'none';
      }
    }

    // Top bar: key + title
    reviewRefKey.textContent = '[' + bibKey + ']';
    reviewRefTitle.textContent = ref.title || 'Untitled';

    // Show all bib record fields
    var lines = [];
    if (ref.authors && ref.authors.length) {
      lines.push('<span class="review-refbar__field-label">Authors:</span> ' + escapeHtml(ref.authors.join(', ')));
    }
    var metaItems = [];
    if (ref.year) metaItems.push('<span class="review-refbar__field-label">Year:</span> ' + escapeHtml(String(ref.year)));
    if (ref.journal) metaItems.push('<span class="review-refbar__field-label">Journal:</span> ' + escapeHtml(ref.journal));
    if (metaItems.length) lines.push(metaItems.join(' <span class="review-refbar__sep">&bull;</span> '));
    if (ref.doi) lines.push('<span class="review-refbar__field-label">DOI:</span> ' + escapeHtml(ref.doi));

    var links = [];
    if (ref.pdf_url) links.push('<a href="' + escapeHtml(ref.pdf_url) + '" target="_blank" class="review-refbar__link">Open PDF</a>');
    if (ref.url) links.push('<a href="' + escapeHtml(ref.url) + '" target="_blank" class="review-refbar__link">Open Web</a>');
    var files = ref.files || {};
    if (files.pdf && currentProjectSlug) links.push('<a href="/api/projects/' + currentProjectSlug + '/files/' + encodeURIComponent(files.pdf) + '" target="_blank" class="review-refbar__link review-refbar__link--local">Local PDF</a>');
    if (files.page && currentProjectSlug) links.push('<a href="/api/projects/' + currentProjectSlug + '/files/' + encodeURIComponent(files.page) + '" target="_blank" class="review-refbar__link review-refbar__link--local">Local HTML</a>');
    if (links.length) lines.push(links.join(' '));

    reviewRefMeta.innerHTML = lines.join('<br>');

    // Enable available tabs. For broken URLs, only BibTeX is meaningful — the
    // bib URL itself failed to download, so loading it in the iframe would just
    // 403 again, and we deliberately did not run the lookup pipeline so there's
    // no abstract/PDF/MD either.
    var hasPdf = !isBrokenUrl && !!(files.pdf || ref.pdf_url);
    var hasHtml = !isBrokenUrl && !!(files.page || ref.url);
    var hasAbstract = !isBrokenUrl && !!ref.abstract;
    var hasMd = !!files.md;
    var hasBibtex = !!ref.raw_bib;

    reviewTabPdf.disabled = !hasPdf;
    reviewTabHtml.disabled = !hasHtml;
    reviewTabAbstract.disabled = !hasAbstract;
    if (reviewTabMd) reviewTabMd.disabled = !hasMd;
    reviewTabBibtex.disabled = !hasBibtex;

    // Auto-select best tab
    if (hasPdf) switchTab('pdf', ref);
    else if (hasHtml) switchTab('html', ref);
    else if (hasAbstract) switchTab('abstract', ref);
    else if (hasMd) switchTab('md', ref);
    else if (hasBibtex) switchTab('bibtex', ref);
    else {
      reviewNoContent.style.display = 'block';
      reviewNoContent.textContent = isBrokenUrl
        ? 'Bib URL is unreachable. Use "Set Link", "Upload PDF", or "Paste Content" to provide a working source.'
        : 'No content available. Use "Set Link" to add a URL.';
    }
  }

  function switchTab(tab, refOverride) {
    var ref = refOverride;
    if (!ref) {
      var bibKey = citations[currentCiteIndex] ? citations[currentCiteIndex].bib_key : null;
      if (bibKey) {
        for (var i = 0; i < allResults.length; i++) {
          if (allResults[i].bib_key === bibKey) { ref = allResults[i]; break; }
        }
      }
    }
    if (!ref) return;

    var files = ref.files || {};

    reviewIframe.style.display = 'none';
    reviewIframe.src = '';
    reviewAbstractText.style.display = 'none';
    reviewBibtexText.style.display = 'none';
    var mdResetEl = el('review-md-text');
    if (mdResetEl) mdResetEl.style.display = 'none';
    var pdfFooterReset = el('review-pdf-footer');
    if (pdfFooterReset) { pdfFooterReset.style.display = 'none'; pdfFooterReset.innerHTML = ''; }
    reviewNoContent.style.display = 'none';
    reviewTabPdf.classList.remove('review-tab--active');
    reviewTabHtml.classList.remove('review-tab--active');
    reviewTabAbstract.classList.remove('review-tab--active');
    reviewTabBibtex.classList.remove('review-tab--active');
    if (reviewTabMd) reviewTabMd.classList.remove('review-tab--active');

    // PDF source resolution: prefer the local copy; fall back to remote URL.
    var localPdfSrc = files.pdf
      ? '/api/projects/' + currentProjectSlug + '/files/' + encodeURIComponent(files.pdf)
      : null;
    var remotePdfUrl = ref.pdf_url || null;
    var pdfSrc = localPdfSrc || remotePdfUrl;

    if (tab === 'pdf' && pdfSrc) {
      reviewTabPdf.classList.add('review-tab--active');
      reviewIframe.src = pdfSrc;
      reviewIframe.style.display = 'block';

      // Footer with provenance: which copy is shown + link to the remote URL
      var pdfFooter = el('review-pdf-footer');
      if (pdfFooter) {
        var showingLocal = !!localPdfSrc;
        var parts = [];
        parts.push('<span class="review-pdf-footer__src review-pdf-footer__src--' +
                   (showingLocal ? 'local' : 'remote') + '">' +
                   (showingLocal ? 'Local' : 'Remote') + '</span>');
        parts.push('<span class="review-pdf-footer__label">Showing ' +
                   (showingLocal ? 'downloaded copy. ' : 'URL directly (not downloaded). ') + '</span>');
        if (remotePdfUrl) {
          parts.push('<span class="review-pdf-footer__label">Source:</span>');
          parts.push('<a class="review-pdf-footer__link" href="' + escapeHtml(remotePdfUrl) +
                     '" target="_blank" rel="noopener noreferrer">' +
                     escapeHtml(remotePdfUrl) + '</a>');
        } else if (showingLocal) {
          parts.push('<span class="review-pdf-footer__label">(no remote URL on record)</span>');
        }
        pdfFooter.innerHTML = parts.join(' ');
        pdfFooter.style.display = 'flex';
      }
    } else if (tab === 'html' && (files.page || ref.url)) {
      reviewTabHtml.classList.add('review-tab--active');
      // Prefer a locally saved page (downloaded HTML or pasted-content wrapper) — no iframe blocking
      if (files.page && currentProjectSlug) {
        reviewIframe.src = '/api/projects/' + currentProjectSlug + '/files/' + encodeURIComponent(files.page);
        reviewIframe.style.display = 'block';
      } else {
        // Try iframe with the live URL but show prominent fallback link since many sites block framing
        reviewIframe.src = ref.url;
        reviewIframe.style.display = 'block';
        reviewIframe.onerror = function () { reviewIframe.style.display = 'none'; };
      }
      if (ref.url) {
        reviewNoContent.innerHTML = '<div style="padding:1rem;text-align:center;">' +
          '<p style="margin-bottom:0.5rem;color:var(--color-muted);">If the page is blank, the site blocks embedded frames.</p>' +
          '<a href="' + escapeHtml(ref.url) + '" target="_blank" rel="noopener noreferrer" ' +
          'style="color:var(--color-primary);font-weight:700;font-size:1rem;">Open in new tab &rarr;</a></div>';
        reviewNoContent.style.display = 'block';
      } else {
        reviewNoContent.style.display = 'none';  // pasted-only: no fallback link needed
      }
    } else if (tab === 'abstract' && ref.abstract) {
      reviewTabAbstract.classList.add('review-tab--active');
      reviewAbstractText.textContent = ref.abstract;
      reviewAbstractText.style.display = 'block';
    } else if (tab === 'md' && files.md) {
      if (reviewTabMd) reviewTabMd.classList.add('review-tab--active');
      var mdEl = el('review-md-text');
      if (mdEl) {
        mdEl.style.display = 'block';
        mdEl.textContent = 'Loading...';
        fetch('/api/projects/' + currentProjectSlug + '/files/' + encodeURIComponent(files.md))
          .then(function (r) { return r.text(); })
          .then(function (text) { mdEl.textContent = text; })
          .catch(function (e) { mdEl.textContent = 'Failed to load .md: ' + e.message; });
      }
    } else if (tab === 'bibtex' && ref.raw_bib) {
      reviewTabBibtex.classList.add('review-tab--active');
      reviewBibtexText.textContent = ref.raw_bib;
      reviewBibtexText.style.display = 'block';
    } else {
      reviewNoContent.style.display = 'block';
    }
  }

  function nextCitation() { navigateToCitation(currentCiteIndex + 1); }
  function prevCitation() { navigateToCitation(currentCiteIndex - 1); }

  /* ==========================================================
     v4: claim-check verdicts (References panel + Verdict header + SSE)
     ========================================================== */

  var claimChecks = {};         // cache_key -> verdict object
  var citationCacheKeys = {};   // citation_index -> cache_key (mirrors server)
  var checkSessionId = null;
  var checkEventSource = null;
  var claimCheckEnabled = false;

  function loadClaimCheckSettings() {
    return fetch('/api/settings/claim-check')
      .then(function (r) { return r.json(); })
      .then(function (s) {
        claimCheckEnabled = !!(s.enabled && s.configured);
        var checkAllBtn = el('review-check-all-btn');
        if (checkAllBtn) {
          checkAllBtn.disabled = !claimCheckEnabled || citations.length === 0;
          checkAllBtn.title = claimCheckEnabled
            ? 'Run LLM check on every citation'
            : 'Configure OPENAI_API_KEY in settings.json or env to enable';
        }
        return s;
      })
      .catch(function () { /* ignore */ });
  }

  function loadVerdictsFromProject() {
    if (!currentProjectSlug) return Promise.resolve();
    return fetch('/api/projects/' + currentProjectSlug + '/citations-with-verdicts')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        claimChecks = data.claim_checks || {};
        citationCacheKeys = {};
        (data.citations || []).forEach(function (c) {
          if (c.claim_check_key) citationCacheKeys[c.index] = c.claim_check_key;
        });
      })
      .catch(function () { /* ignore */ });
  }

  function getVerdictForCitation(idx) {
    var ck = citationCacheKeys[idx];
    if (!ck) return null;
    return claimChecks[ck] || null;
  }

  // ---- References panel ----
  var refsFilters = { search: '', sort: 'doc', issuesOnly: false };

  function renderReferencesPanel() {
    var listEl = el('review-refs-list');
    if (!listEl) return;
    listEl.innerHTML = '';

    if (!citations.length) {
      listEl.innerHTML = '<div class="review-refs__empty">No citations.</div>';
      return;
    }

    // One row per CITATION OCCURRENCE, in document order.
    var rows = citations.map(function (c, idx) {
      var ref = null;
      for (var i = 0; i < allResults.length; i++) {
        if (allResults[i].bib_key === c.bib_key) { ref = allResults[i]; break; }
      }
      return { idx: idx, citation: c, ref: ref, verdict: getVerdictForCitation(idx) };
    });

    // How many times does each bib_key appear, and which occurrence index is this?
    var occurrenceCounts = {};
    var occurrenceIndex = {};
    citations.forEach(function (c) {
      occurrenceCounts[c.bib_key] = (occurrenceCounts[c.bib_key] || 0) + 1;
    });
    var seenSoFar = {};
    rows.forEach(function (r) {
      seenSoFar[r.citation.bib_key] = (seenSoFar[r.citation.bib_key] || 0) + 1;
      r.occurrence = seenSoFar[r.citation.bib_key];
      r.totalOccurrences = occurrenceCounts[r.citation.bib_key];
    });

    // Filter
    var search = (refsFilters.search || '').toLowerCase();
    if (search) {
      rows = rows.filter(function (r) {
        var hay = (r.citation.bib_key + ' ' + ((r.ref && r.ref.title) || '')).toLowerCase();
        return hay.indexOf(search) !== -1;
      });
    }
    if (refsFilters.issuesOnly) {
      rows = rows.filter(function (r) {
        if (!r.verdict) return false;
        return r.verdict.verdict !== 'supported';
      });
    }

    // Sort
    if (refsFilters.sort === 'worst') {
      rows.sort(function (a, b) {
        return verdictRank(b.verdict) - verdictRank(a.verdict);
      });
    } else if (refsFilters.sort === 'key') {
      rows.sort(function (a, b) {
        return a.citation.bib_key.localeCompare(b.citation.bib_key) || (a.idx - b.idx);
      });
    }
    // default 'doc' = by idx (already sorted that way)

    if (!rows.length) {
      listEl.innerHTML = '<div class="review-refs__empty">No matches.</div>';
      return;
    }

    rows.forEach(function (r) {
      var card = document.createElement('div');
      card.className = 'review-ref-card';
      if (r.idx === currentCiteIndex) card.classList.add('review-ref-card--selected');
      card.dataset.idx = r.idx;
      card.dataset.bibKey = r.citation.bib_key;

      var titleStr = (r.ref && r.ref.title) || '(no metadata)';
      var v = r.verdict;
      var verdictLabel, verdictCls;
      if (!v) { verdictLabel = '? Not checked'; verdictCls = 'review-ref-card__verdict--unchecked'; }
      else if (v.verdict === 'supported')      { verdictLabel = '✓ Supported'; verdictCls = 'review-ref-card__verdict--supported'; }
      else if (v.verdict === 'partial')        { verdictLabel = '⚠ Partial'; verdictCls = 'review-ref-card__verdict--partial'; }
      else if (v.verdict === 'not_supported')  { verdictLabel = '✗ Not supported'; verdictCls = 'review-ref-card__verdict--not_supported'; }
      else                                      { verdictLabel = '? Unknown'; verdictCls = 'review-ref-card__verdict--unknown'; }
      var manualMark = (v && v.manual) ? ' <span class="review-ref-card__manual" title="Manually set">✎</span>' : '';
      var conf = (v && v.confidence != null && !v.manual) ? (' ' + Math.round(v.confidence * 100) + '%') : '';

      var occLabel = r.totalOccurrences > 1
        ? ' <span class="review-ref-card__occ" title="Occurrence in document">(' + r.occurrence + '/' + r.totalOccurrences + ')</span>'
        : '';

      // Source label: pdf (green) / html (blue) / abstract (orange) / not found (red)
      var src = _refSourceLabel(r.ref);
      // Identity-match indicator: ✓ matched, ✗ not matched, ✎ manual, ? unchecked
      var match = _matchSummary(r.ref && r.ref.ref_match);

      card.innerHTML =
        '<div class="review-ref-card__top">' +
          '<span class="review-ref-card__line">L' + (r.citation.line || '?') + '</span>' +
          '<span class="review-ref-card__key">' + escapeHtml(r.citation.bib_key) + '</span>' +
          occLabel +
          '<span class="review-ref-card__src review-ref-card__src--' + src.cls + '" title="' + escapeHtml(src.title) + '">' + escapeHtml(src.label) + '</span>' +
          '<span class="review-ref-card__match review-ref-card__match--' + match.cls + '" title="' + escapeHtml(match.title) + '">' + escapeHtml(match.label) + '</span>' +
        '</div>' +
        '<div class="review-ref-card__title" title="' + escapeHtml(titleStr) + '">' + escapeHtml(titleStr) + '</div>' +
        '<div class="review-ref-card__verdict-row">' +
          '<button type="button" class="review-ref-card__verdict ' + verdictCls + '" data-act="set-verdict" data-idx="' + r.idx + '" title="Click to override status">' +
            escapeHtml(verdictLabel) + escapeHtml(conf) +
          '</button>' +
          manualMark +
        '</div>';

      listEl.appendChild(card);
    });
  }

  // v6.1 Phase D — download-tier badge helpers.
  // Groups tiers by category so the badge color is meaningful at a glance.
  var _TIER_CATEGORIES = {
    direct:          'green',
    oa_fallbacks:    'green',
    doi_negotiation: 'green',
    openreview:      'green',
    pmc:             'green',
    nber:            'green',
    repec:           'blue',
    core:            'blue',
    hal:             'blue',
    zenodo:          'blue',
    osf:             'blue',
    wayback:         'blue',
    curl_cffi:       'amber',
    playwright:      'amber',
    manual_set_link: 'purple',
    manual_upload:   'purple',
    manual_paste:    'purple',
  };
  var _TIER_LABELS = {
    direct:          'direct',
    oa_fallbacks:    'OA mirror',
    doi_negotiation: 'DOI',
    openreview:      'OpenReview',
    pmc:             'PMC',
    nber:            'NBER',
    repec:           'RePEc',
    core:            'CORE',
    hal:             'HAL',
    zenodo:          'Zenodo',
    osf:             'OSF',
    wayback:         'Wayback',
    curl_cffi:       'curl_cffi',
    playwright:      'Playwright',
    manual_set_link: 'manual link',
    manual_upload:   'uploaded',
    manual_paste:    'pasted',
  };
  function _tierBadgeClass(tier) { return _TIER_CATEGORIES[tier] || 'gray'; }
  function _tierLabel(tier)      { return _TIER_LABELS[tier] || tier; }

  // Short, human explainer per tier — used in the validity report + right-panel
  // footer as a one-line "why this source" hint.
  var _TIER_EXPLAINERS = {
    wayback:         'historic Web Archive snapshot — may be outdated',
    openreview:      'OpenReview accepted submission — may differ from camera-ready',
    oa_fallbacks:    'alternate open-access mirror via Unpaywall/OpenAlex',
    doi_negotiation: 'direct PDF via DOI content negotiation',
    core:            'institutional-repository copy via CORE aggregator',
    hal:             'HAL open-access archive',
    pmc:             'PubMed Central open-access copy',
    nber:            'NBER working-paper PDF',
    repec:           'RePEc mirror',
    zenodo:          'Zenodo research archive',
    osf:             'OSF Preprints',
    curl_cffi:       'fetched with browser TLS impersonation (site bot-blocked default fetch)',
    playwright:      'captured via headless browser (site needs JS rendering)',
    manual_set_link: 'manually set by you via Set Link',
    manual_upload:   'manually uploaded by you',
    manual_paste:    'manually pasted by you',
    direct:          null,  // no banner for direct — the default
  };

  // Reference identity match — short label/icon for the card.
  function _matchSummary(match) {
    if (!match) return { label: '?', cls: 'unchecked', title: 'Identity not checked' };
    var v = match.verdict;
    var ev = match.evidence || '';
    if (v === 'matched')             return { label: '✓', cls: 'matched',     title: 'Identity verified — title/authors match downloaded text. ' + ev };
    if (v === 'not_matched')         return { label: '✗', cls: 'not_matched', title: 'Identity MISMATCH — downloaded text does not match bib. ' + ev };
    if (v === 'manual_matched')      return { label: '✎✓', cls: 'manual',     title: 'Manually marked OK. ' + ev };
    if (v === 'manual_not_matched')  return { label: '✎✗', cls: 'manual_bad', title: 'Manually marked NOT MATCHED. ' + ev };
    return { label: '?', cls: 'unverifiable', title: ev || 'Could not verify' };
  }

  // Determine the source label shown on each reference card.
  // Priority: broken URL → "broken URL" (amber); no .md → "not found" (red);
  // pdf → green; html → blue; abstract-only → orange.
  function _refSourceLabel(ref) {
    var files = (ref && ref.files) || {};
    if (ref && ref.status === 'bib_url_unreachable') {
      return { label: 'broken URL', cls: 'broken',
               title: ref.error || 'Bib URL unreachable — fix the citation' };
    }
    if (!ref || !files.md) {
      return { label: 'not found', cls: 'notfound', title: 'No .md file' };
    }
    if (files.pdf) {
      return { label: 'pdf', cls: 'pdf', title: 'PDF source' };
    }
    if (files.page) {
      return { label: 'html', cls: 'html', title: 'HTML source' };
    }
    if (ref.abstract) {
      return { label: 'abstract', cls: 'abstract', title: 'Abstract only (no full text)' };
    }
    // .md exists but no pdf/html/abstract — shouldn't happen, but show a neutral label
    return { label: 'md', cls: 'abstract', title: 'Markdown only' };
  }

  // ---------------------------------------------------------
  // Reference identity match panel (right-pane, above tabs)
  // ---------------------------------------------------------
  function _renderRefMatchPanel(rmEl, ref, isBrokenUrl) {
    if (!ref || !ref.bib_key) { rmEl.style.display = 'none'; return; }
    if (isBrokenUrl) { rmEl.style.display = 'none'; return; }   // broken-URL banner already explains

    var match = ref.ref_match;
    var hasMd = !!(ref.files && ref.files.md);

    var verdict = match ? match.verdict : null;
    var label, sub, kind;
    if (!match) {
      label = '? Identity not checked';
      sub = hasMd ? 'Click <strong>Check</strong> to verify the downloaded text matches the bib title + authors.'
                  : 'No .md content yet — nothing to check.';
      kind = 'unchecked';
    } else if (verdict === 'matched') {
      label = '✓ Identity verified';
      sub = match.evidence || 'Title and authors found in the downloaded text.';
      kind = 'matched';
    } else if (verdict === 'not_matched') {
      label = '✗ Identity NOT matched — review';
      sub = (match.evidence || 'The downloaded text does not contain the claimed title/authors.') +
            ' &nbsp;<em>If this is wrong, mark as OK below.</em>';
      kind = 'not_matched';
    } else if (verdict === 'manual_matched') {
      label = '✎ ✓ Manually marked OK';
      sub = match.evidence || 'You overrode this to "matched".';
      kind = 'manual';
    } else if (verdict === 'manual_not_matched') {
      label = '✎ ✗ Manually marked NOT matched';
      sub = match.evidence || 'You overrode this to "not matched".';
      kind = 'manual_bad';
    } else {
      label = '? Could not verify';
      sub = match.evidence || '';
      kind = 'unverifiable';
    }

    var html = '';
    html += '<div class="review-ref-match__main">';
    html += '<span class="review-ref-match__label review-ref-match__label--' + kind + '">' + escapeHtml(label) + '</span>';
    html += '<div class="review-ref-match__actions">';
    html += '<button type="button" class="btn btn--outline btn--small" data-act="rm-recheck"' +
            (hasMd ? '' : ' disabled') + '>Check</button>';
    html += '<button type="button" class="btn btn--outline btn--small" data-act="rm-mark-ok" title="Mark identity as matched (overrides any LLM verdict)">Mark OK</button>';
    html += '<button type="button" class="btn btn--outline btn--small" data-act="rm-mark-bad" title="Mark identity as not matched">Mark NOT</button>';
    if (match) {
      html += '<button type="button" class="btn btn--ghost btn--small" data-act="rm-clear" title="Clear the verdict (treat as unchecked)">Clear</button>';
    }
    html += '</div></div>';
    html += '<div class="review-ref-match__sub">' + sub + '</div>';
    if (match && match.model && !match.manual) {
      html += '<div class="review-ref-match__meta">model: ' + escapeHtml(match.model) +
              (match.checked_at ? ' · ' + escapeHtml(match.checked_at.split('T')[0]) : '') + '</div>';
    }
    rmEl.innerHTML = html;
    rmEl.className = 'review-ref-match review-ref-match--' + kind;
    rmEl.style.display = 'block';

    var bibKey = ref.bib_key;
    rmEl.querySelectorAll('button[data-act]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var act = btn.dataset.act;
        if (act === 'rm-recheck')   refMatchRecheck(bibKey);
        else if (act === 'rm-mark-ok')  refMatchSetManual(bibKey, 'matched');
        else if (act === 'rm-mark-bad') refMatchSetManual(bibKey, 'not_matched');
        else if (act === 'rm-clear')    refMatchClear(bibKey);
      });
    });
  }

  function _updateRefMatchInState(bibKey, match) {
    for (var i = 0; i < allResults.length; i++) {
      if (allResults[i].bib_key === bibKey) {
        if (match === null) delete allResults[i].ref_match;
        else allResults[i].ref_match = match;
        break;
      }
    }
    // Re-render the dependent panels
    if (typeof renderReferencesPanel === 'function') renderReferencesPanel();
    var bibKeyOfCurrent = citations[currentCiteIndex] ? citations[currentCiteIndex].bib_key : null;
    if (bibKeyOfCurrent === bibKey && typeof showReferencePanel === 'function') {
      showReferencePanel(bibKey);
    }
  }

  function refMatchRecheck(bibKey) {
    if (!currentProjectSlug) return;
    var rmEl = el('review-ref-match');
    if (rmEl) rmEl.classList.add('review-ref-match--checking');
    fetch('/api/projects/' + currentProjectSlug + '/check-reference-match/' + encodeURIComponent(bibKey),
          { method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ force: true }) })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (rmEl) rmEl.classList.remove('review-ref-match--checking');
        if (data && data.ok) _updateRefMatchInState(bibKey, data.ref_match);
      })
      .catch(function () { if (rmEl) rmEl.classList.remove('review-ref-match--checking'); });
  }

  function refMatchSetManual(bibKey, verdict) {
    if (!currentProjectSlug) return;
    fetch('/api/projects/' + currentProjectSlug + '/set-ref-match/' + encodeURIComponent(bibKey),
          { method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ verdict: verdict }) })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data && data.ok) _updateRefMatchInState(bibKey, data.ref_match);
      });
  }

  function refMatchClear(bibKey) {
    if (!currentProjectSlug) return;
    fetch('/api/projects/' + currentProjectSlug + '/clear-ref-match/' + encodeURIComponent(bibKey),
          { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data && data.ok) _updateRefMatchInState(bibKey, null);
      });
  }

  function verdictRank(v) {
    if (!v) return -1;
    return v.verdict === 'not_supported' ? 3
         : v.verdict === 'partial' ? 2
         : v.verdict === 'unknown' ? 1 : 0;
  }

  function highlightSelectedRefCard(bibKey) {
    var listEl = el('review-refs-list');
    if (!listEl) return;
    var cards = listEl.querySelectorAll('.review-ref-card');
    cards.forEach(function (c) {
      var idx = parseInt(c.dataset.idx, 10);
      if (idx === currentCiteIndex) {
        c.classList.add('review-ref-card--selected');
        try { c.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); } catch (e) {}
      } else {
        c.classList.remove('review-ref-card--selected');
      }
    });
  }

  // Manual verdict picker (modal-less popover)
  function showVerdictPicker(idx, anchorEl) {
    // Remove existing picker if any
    var existing = document.getElementById('verdict-picker');
    if (existing) existing.remove();

    var picker = document.createElement('div');
    picker.id = 'verdict-picker';
    picker.className = 'verdict-picker';
    picker.innerHTML =
      '<div class="verdict-picker__title">Set verdict manually</div>' +
      '<button type="button" data-v="supported"     class="verdict-picker__opt verdict-picker__opt--supported">✓ Supported</button>' +
      '<button type="button" data-v="partial"       class="verdict-picker__opt verdict-picker__opt--partial">⚠ Partial</button>' +
      '<button type="button" data-v="not_supported" class="verdict-picker__opt verdict-picker__opt--not_supported">✗ Not supported</button>' +
      '<button type="button" data-v="unknown"       class="verdict-picker__opt verdict-picker__opt--unknown">? Unknown</button>' +
      '<button type="button" data-v="clear"         class="verdict-picker__opt verdict-picker__opt--clear">Clear (re-check on next run)</button>' +
      '<button type="button" data-v="cancel"        class="verdict-picker__opt verdict-picker__opt--cancel">Cancel</button>';

    document.body.appendChild(picker);
    var rect = anchorEl.getBoundingClientRect();
    picker.style.position = 'fixed';
    picker.style.left = Math.min(window.innerWidth - 220, rect.left) + 'px';
    picker.style.top = (rect.bottom + 4) + 'px';
    picker.style.zIndex = 1000;

    function close() { try { picker.remove(); } catch (e) {} document.removeEventListener('click', onDocClick, true); }
    function onDocClick(e) { if (!picker.contains(e.target)) close(); }
    setTimeout(function () { document.addEventListener('click', onDocClick, true); }, 0);

    picker.addEventListener('click', function (e) {
      var btn = e.target.closest('button[data-v]');
      if (!btn) return;
      var val = btn.dataset.v;
      close();
      if (val === 'cancel') return;
      if (val === 'clear') {
        clearManualVerdict(idx);
      } else {
        setManualVerdict(idx, val);
      }
    });
  }

  function setManualVerdict(idx, verdictValue) {
    if (!currentProjectSlug) return;
    fetch('/api/projects/' + currentProjectSlug + '/set-verdict/' + idx, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ verdict: verdictValue }),
    })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (res.ok && res.verdict) {
          claimChecks[res.cache_key] = res.verdict;
          citationCacheKeys[idx] = res.cache_key;
          renderReferencesPanel();
          if (idx === currentCiteIndex) renderVerdictHeader(idx);
          if (typeof window.verifyOnVerdict === 'function') {
            window.verifyOnVerdict(idx, res.verdict, res.cache_key);
          }
        }
      });
  }

  /* ---- Paste Content modal ---- */

  function openPasteContentModal(bibKey) {
    var modal = el('paste-content-modal');
    if (!modal) return;
    el('paste-content-key-label').textContent = bibKey;
    el('paste-content-text').value = '';
    el('paste-content-error').textContent = '';
    el('paste-content-submit').disabled = false;
    el('paste-content-submit').textContent = 'Save';
    modal.style.display = '';
    modal.dataset.bibKey = bibKey;
    setTimeout(function () { try { el('paste-content-text').focus(); } catch (e) {} }, 0);
  }

  function closePasteContentModal() {
    var modal = el('paste-content-modal');
    if (modal) modal.style.display = 'none';
  }

  function submitPasteContent() {
    if (!currentProjectSlug) return;
    var modal = el('paste-content-modal');
    var bibKey = modal.dataset.bibKey;
    var content = el('paste-content-text').value;
    var errEl = el('paste-content-error');
    errEl.textContent = '';
    if (!content || !content.trim()) {
      errEl.textContent = 'Paste some content first.';
      return;
    }
    var submitBtn = el('paste-content-submit');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Saving...';
    fetch('/api/projects/' + currentProjectSlug + '/paste-content/' + encodeURIComponent(bibKey), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: content }),
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        if (!res.ok) {
          errEl.textContent = (res.body && res.body.error) || 'Failed to save.';
          submitBtn.disabled = false;
          submitBtn.textContent = 'Save';
          return;
        }
        closePasteContentModal();
        if (typeof window._applySourceReplacementResponse === 'function') {
          window._applySourceReplacementResponse(bibKey, res.body);
        }
      })
      .catch(function (e) {
        errEl.textContent = 'Network error: ' + e.message;
        submitBtn.disabled = false;
        submitBtn.textContent = 'Save';
      });
  }

  /* ---- Add Reference modal (for citations with no .bib entry) ---- */

  function openAddRefModal(bibKey) {
    var modal = el('add-ref-modal');
    if (!modal) return;
    el('add-ref-key-label').textContent = bibKey;
    var label2 = el('add-ref-key-label-2');
    if (label2) label2.textContent = bibKey;
    el('add-ref-bib-text').value = '';
    el('add-ref-error').textContent = '';
    el('add-ref-submit').disabled = false;
    el('add-ref-submit').textContent = 'Add & Look Up';
    modal.style.display = '';
    modal.dataset.bibKey = bibKey;
    setTimeout(function () { try { el('add-ref-bib-text').focus(); } catch (e) {} }, 0);
  }

  function closeAddRefModal() {
    var modal = el('add-ref-modal');
    if (modal) modal.style.display = 'none';
  }

  function submitAddRef() {
    if (!currentProjectSlug) return;
    var modal = el('add-ref-modal');
    var bibKey = modal.dataset.bibKey;
    var bibText = el('add-ref-bib-text').value.trim();
    var errEl = el('add-ref-error');
    errEl.textContent = '';
    if (!bibText) { errEl.textContent = 'Paste a BibTeX entry.'; return; }
    // Quick client-side sanity check
    if (bibText.indexOf('@') === -1 || bibText.indexOf('{') === -1) {
      errEl.textContent = 'That does not look like a BibTeX entry (expected something like "@article{...}").';
      return;
    }

    var submitBtn = el('add-ref-submit');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Looking up...';

    fetch('/api/projects/' + currentProjectSlug + '/add-reference', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bib_key: bibKey, bib_text: bibText }),
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, status: r.status, body: j }; }); })
      .then(function (res) {
        if (!res.ok) {
          errEl.textContent = res.body.error || 'Failed to add reference.';
          submitBtn.disabled = false;
          submitBtn.textContent = 'Add & Look Up';
          return;
        }
        pollAddRefStatus(bibKey);
      })
      .catch(function (e) {
        errEl.textContent = 'Network error: ' + e.message;
        submitBtn.disabled = false;
        submitBtn.textContent = 'Add & Look Up';
      });
  }

  function pollAddRefStatus(bibKey) {
    var submitBtn = el('add-ref-submit');
    var pollCount = 0;
    var poll = setInterval(function () {
      pollCount++;
      fetch('/api/projects/' + currentProjectSlug + '/refresh-status/' + encodeURIComponent(bibKey))
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.status === 'done') {
            clearInterval(poll);
            if (data.result) {
              // Add to local cache and refresh UI
              var existing = false;
              for (var i = 0; i < allResults.length; i++) {
                if (allResults[i].bib_key === bibKey) { allResults[i] = data.result; existing = true; break; }
              }
              if (!existing) allResults.push(data.result);
              closeAddRefModal();
              showReferencePanel(bibKey);
              renderReferencesPanel();
              renderVerdictHeader(currentCiteIndex);
            } else {
              el('add-ref-error').textContent = 'Lookup failed: ' + (data.error || 'unknown error');
              submitBtn.disabled = false;
              submitBtn.textContent = 'Add & Look Up';
            }
          } else if (pollCount > 90) {  // ~3 minutes
            clearInterval(poll);
            el('add-ref-error').textContent = 'Lookup is taking longer than expected. Try refreshing later.';
            submitBtn.disabled = false;
            submitBtn.textContent = 'Add & Look Up';
          }
        })
        .catch(function () { /* ignore transient network errors mid-poll */ });
    }, 2000);
  }

  function clearManualVerdict(idx) {
    if (!currentProjectSlug) return;
    fetch('/api/projects/' + currentProjectSlug + '/clear-verdict/' + idx, { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function () {
        delete citationCacheKeys[idx];
        renderReferencesPanel();
        if (idx === currentCiteIndex) renderVerdictHeader(idx);
      });
  }

  // ---- Verdict header ----
  function renderVerdictHeader(idx) {
    var box = el('review-verdict');
    var emptyBox = el('review-verdict-empty');
    if (!box || !emptyBox) return;

    var verdict = getVerdictForCitation(idx);
    if (!verdict) {
      box.style.display = 'none';
      emptyBox.style.display = claimCheckEnabled ? 'flex' : 'none';
      return;
    }

    box.style.display = 'flex';
    emptyBox.style.display = 'none';

    var v = verdict.verdict || 'unknown';
    var badge = el('review-verdict-badge');
    badge.className = 'review-verdict__badge review-verdict__badge--' + v;
    badge.textContent = (v === 'supported' ? '✓ Supported'
                       : v === 'partial' ? '⚠ Partial'
                       : v === 'not_supported' ? '✗ Not supported'
                       : '? Unknown');
    var conf = verdict.confidence;
    el('review-verdict-conf').textContent = (conf != null) ? ('confidence ' + Math.round(conf * 100) + '%') : '';
    el('review-verdict-explanation').textContent = verdict.explanation || '';
    var ev = el('review-verdict-evidence');
    if (verdict.evidence_quote) {
      ev.textContent = '"' + verdict.evidence_quote + '"';
    } else {
      ev.textContent = '';
    }
  }

  // ---- Single-citation check ----
  function checkSingleCitation(idx, force) {
    if (!currentProjectSlug || idx == null) return Promise.resolve();
    var statusEl = el('review-check-status');
    if (statusEl) statusEl.textContent = 'Checking...';
    return fetch('/api/projects/' + currentProjectSlug + '/check-citation/' + idx, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force: !!force }),
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        if (!res.ok) {
          if (statusEl) statusEl.textContent = 'Error: ' + (res.body.error || 'check failed');
          return;
        }
        var ck = res.body.cache_key;
        claimChecks[ck] = res.body.verdict;
        citationCacheKeys[idx] = ck;
        if (statusEl) statusEl.textContent = '';
        if (idx === currentCiteIndex) renderVerdictHeader(idx);
        renderReferencesPanel();
      });
  }

  // ---- Batch check ----
  function checkAllCitations() {
    if (!currentProjectSlug) return;
    if (checkEventSource) { try { checkEventSource.close(); } catch (e) {} checkEventSource = null; }

    var statusEl = el('review-check-status');
    var stopBtn = el('review-stop-check-btn');
    var checkAllBtn = el('review-check-all-btn');

    if (statusEl) statusEl.textContent = 'Estimating...';

    fetch('/api/projects/' + currentProjectSlug + '/check-citations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, status: r.status, body: j }; }); })
      .then(function (res) {
        if (res.status === 409) {
          // Cost over the limit — confirm with the user
          var est = (res.body.estimate || {}).estimated_cost_usd;
          var estStr = (typeof est === 'number') ? est.toFixed(4) : '?';
          if (confirm('Estimated cost is $' + estStr + ', which exceeds the configured max_batch_usd. Run anyway?')) {
            return fetch('/api/projects/' + currentProjectSlug + '/check-citations', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ override_cost: true }),
            }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); });
          }
          if (statusEl) statusEl.textContent = '';
          return null;
        }
        return res;
      })
      .then(function (res) {
        if (!res) return;
        if (!res.ok) {
          if (statusEl) statusEl.textContent = 'Error: ' + (res.body.error || 'failed');
          return;
        }
        var sid = res.body.session_id;
        checkSessionId = sid;
        var total = res.body.n_citations || 0;
        if (statusEl) statusEl.textContent = '0 / ' + total + ' checked';
        if (stopBtn) stopBtn.style.display = '';
        if (checkAllBtn) checkAllBtn.disabled = true;

        checkEventSource = new EventSource(
          '/api/projects/' + currentProjectSlug + '/check-status/' + sid
        );
        checkEventSource.addEventListener('progress', function (ev) {
          var d;
          try { d = JSON.parse(ev.data); } catch (e) { return; }
          claimChecks[d.cache_key] = d.verdict;
          citationCacheKeys[d.index] = d.cache_key;
          if (statusEl) statusEl.textContent = d.progress + ' / ' + d.total + ' checked';
          // Update verdict header if user is on this citation
          if (d.index === currentCiteIndex) renderVerdictHeader(d.index);
          // Refresh refs panel periodically (cheap — small DOM)
          renderReferencesPanel();
          // Notify the verify table view if open
          if (typeof window.verifyOnVerdict === 'function') {
            window.verifyOnVerdict(d.index, d.verdict, d.cache_key);
          }
        });
        checkEventSource.addEventListener('complete', function () {
          finalizeBatch();
        });
        checkEventSource.onerror = function () {
          finalizeBatch('Connection lost.');
        };
      });
  }

  function finalizeBatch(errMsg) {
    var statusEl = el('review-check-status');
    var stopBtn = el('review-stop-check-btn');
    var checkAllBtn = el('review-check-all-btn');
    if (checkEventSource) { try { checkEventSource.close(); } catch (e) {} checkEventSource = null; }
    if (stopBtn) stopBtn.style.display = 'none';
    var verifyStopBtn = el('verify-stop-btn');
    if (verifyStopBtn) verifyStopBtn.style.display = 'none';
    if (checkAllBtn) checkAllBtn.disabled = !claimCheckEnabled || citations.length === 0;
    if (statusEl) statusEl.textContent = errMsg || 'All checked';
    checkSessionId = null;

    // Detect mass-failure: every verdict is unknown with the same explanation
    if (!errMsg) {
      var allVerdicts = Object.values(claimChecks);
      if (allVerdicts.length > 0) {
        var allUnknown = allVerdicts.every(function (v) { return v.verdict === 'unknown'; });
        var explanations = {};
        allVerdicts.forEach(function (v) {
          if (v.explanation) explanations[v.explanation] = (explanations[v.explanation] || 0) + 1;
        });
        var topExplanation = null, topCount = 0;
        for (var ex in explanations) { if (explanations[ex] > topCount) { topCount = explanations[ex]; topExplanation = ex; } }
        if (allUnknown && topCount === allVerdicts.length && topExplanation) {
          alert('All ' + allVerdicts.length + ' citations returned "unknown".\n\nReason: ' + topExplanation +
                '\n\nFix: install or configure the OpenAI client, then click Recheck.');
        }
      }
    }

    // After a successful batch, in the verify view default to showing only issues
    if (!errMsg && el('view-verify') && el('view-verify').classList.contains('view--active')) {
      verifyFilters.supported = false;
      // Reflect in checkboxes
      var fEl = el('verify-filters');
      if (fEl) fEl.querySelectorAll('input[data-filter]').forEach(function (cb) {
        cb.checked = !!verifyFilters[cb.dataset.filter];
      });
      renderVerifyTable();
    }
  }

  function stopBatch() {
    if (!currentProjectSlug || !checkSessionId) return;
    fetch('/api/projects/' + currentProjectSlug + '/check-citations/' + checkSessionId + '/stop',
      { method: 'POST' })
      .finally(function () {
        var statusEl = el('review-check-status');
        if (statusEl) statusEl.textContent = 'Stopping...';
      });
  }

  /* ==========================================================
     v4: Verification Table view (View 5)
     ========================================================== */

  var verifyRows = [];          // augmented rows from the API
  var verifyFilters = {
    supported: true, partial: true, not_supported: true, unknown: true, unchecked: true,
    search: '',
  };
  var verifySort = { col: 'index', dir: 'asc' };
  var verifyExpanded = {};      // citation_index -> bool

  function openVerifyView() {
    if (!currentProjectSlug) return;
    showView('verify');
    refreshVerifyData();
    // Wire SSE-driven row updates from in-flight batches (or future batches)
    window.verifyOnVerdict = function (idx, verdict, ck) {
      // Update local row if present and re-render that row
      for (var i = 0; i < verifyRows.length; i++) {
        if (verifyRows[i].index === idx) {
          verifyRows[i].verdict = verdict;
          break;
        }
      }
      renderVerifyTable();
      updateVerifyProgressBar();
    };
  }

  function refreshVerifyData() {
    return Promise.all([loadClaimCheckSettings(), fetchVerifyRows()]).then(function () {
      var runBtn = el('verify-run-all-btn');
      if (runBtn) runBtn.disabled = !claimCheckEnabled || verifyRows.length === 0;
      renderVerifyTable();
      updateVerifyProgressBar();
    });
  }

  function fetchVerifyRows() {
    return fetch('/api/projects/' + currentProjectSlug + '/citations-with-verdicts')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        verifyRows = data.citations || [];
        // Mirror into shared maps
        claimChecks = data.claim_checks || {};
        citationCacheKeys = {};
        verifyRows.forEach(function (c) {
          if (c.claim_check_key) citationCacheKeys[c.index] = c.claim_check_key;
        });
      });
  }

  function applyVerifyFiltersAndSort(rows) {
    var q = (verifyFilters.search || '').toLowerCase();
    var out = rows.filter(function (r) {
      var v = r.verdict;
      var bucket = v ? v.verdict : 'unchecked';
      if (!verifyFilters[bucket]) return false;
      if (q) {
        var hay = (r.bib_key + ' ' + (r.context_before || '') + ' ' + (r.context_after || '') +
                   ' ' + ((r.reference && r.reference.title) || '')).toLowerCase();
        if (hay.indexOf(q) === -1) return false;
      }
      return true;
    });
    var col = verifySort.col, dir = verifySort.dir === 'desc' ? -1 : 1;
    out.sort(function (a, b) {
      var av, bv;
      if (col === 'index') { av = a.index; bv = b.index; }
      else if (col === 'line') { av = a.line; bv = b.line; }
      else if (col === 'key') { av = a.bib_key || ''; bv = b.bib_key || ''; }
      else if (col === 'verdict') { av = (a.verdict && a.verdict.verdict) || 'zzz'; bv = (b.verdict && b.verdict.verdict) || 'zzz'; }
      else if (col === 'confidence') { av = (a.verdict && a.verdict.confidence) || -1; bv = (b.verdict && b.verdict.confidence) || -1; }
      else { av = a.index; bv = b.index; }
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return 0;
    });
    return out;
  }

  function renderVerifyTable() {
    var tbody = el('verify-tbody');
    if (!tbody) return;
    var rows = applyVerifyFiltersAndSort(verifyRows);
    tbody.innerHTML = '';
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="9" style="text-align:center; padding:2rem; color:#888;">No matching citations.</td></tr>';
      return;
    }
    rows.forEach(function (r) {
      var v = r.verdict;
      var bucket = v ? v.verdict : 'unchecked';
      var sentClean = '';
      // Build a quick claim-sentence preview by joining context_before/after; ideally would call extract on server
      var bef = (r.context_before || '').slice(-80);
      var aft = (r.context_after || '').slice(0, 80);
      sentClean = (bef + ' ' + (r.cite_command || ('\\cite{' + r.bib_key + '}')) + ' ' + aft).replace(/\s+/g, ' ').trim();

      var refTitle = r.reference && r.reference.title;
      var refDisplay = refTitle ? escapeHtml(refTitle) : '<span class="verify-cell-ref-missing">' + (r.reference && r.reference.has_md ? 'no title' : 'no .md content') + '</span>';

      var verdictBadge = v
        ? '<span class="verify-badge verify-badge--' + v.verdict + '">' + v.verdict + '</span>'
        : '<span style="color:#999">—</span>';

      var conf = (v && v.confidence != null) ? Math.round(v.confidence * 100) + '%' : '—';

      var evidence = (v && v.evidence_quote) ? ('"' + v.evidence_quote + '"') : '—';

      var tr = document.createElement('tr');
      tr.className = 'verify-row' + (verifyExpanded[r.index] ? ' verify-row--expanded' : '');
      tr.dataset.index = r.index;
      tr.innerHTML =
        '<td>' + r.index + '</td>' +
        '<td>' + (r.line || '') + '</td>' +
        '<td class="verify-cell-key">' + escapeHtml(r.bib_key || '') + '</td>' +
        '<td class="verify-cell-sentence" title="' + escapeHtml(sentClean) + '">' + escapeHtml(sentClean) + '</td>' +
        '<td>' + refDisplay + '</td>' +
        '<td>' + verdictBadge + '</td>' +
        '<td>' + conf + '</td>' +
        '<td class="verify-cell-evidence" title="' + escapeHtml(evidence) + '">' + escapeHtml(evidence) + '</td>' +
        '<td><div class="verify-actions">' +
          '<button data-act="check" title="Check / Recheck">↻</button>' +
          '<button data-act="open"  title="Open in Review">→</button>' +
          '<button data-act="expand" title="Show details">▾</button>' +
        '</div></td>';
      tbody.appendChild(tr);

      if (verifyExpanded[r.index]) {
        var detailTr = document.createElement('tr');
        detailTr.className = 'verify-detail-row';
        var detailHtml =
          '<td colspan="9" class="verify-detail">' +
            '<h4>Claim paragraph (raw)</h4>' +
            '<div class="verify-detail__paragraph">' + escapeHtml((r.context_before || '') + (r.cite_command || ('\\cite{' + r.bib_key + '}')) + (r.context_after || '')) + '</div>';
        if (v) {
          detailHtml += '<h4>Explanation</h4><div>' + escapeHtml(v.explanation || '') + '</div>';
          if (v.evidence_quote) detailHtml += '<h4>Evidence</h4><div class="verify-detail__evidence">' + escapeHtml(v.evidence_quote) + '</div>';
          var meta = [];
          if (v.model) meta.push('Model: ' + v.model);
          if (v.input_tokens) meta.push('In: ' + v.input_tokens);
          if (v.output_tokens) meta.push('Out: ' + v.output_tokens);
          if (v.checked_at) meta.push('Checked: ' + v.checked_at);
          if (meta.length) detailHtml += '<div class="verify-detail__meta">' + escapeHtml(meta.join('  ·  ')) + '</div>';
        }
        detailHtml += '</td>';
        detailTr.innerHTML = detailHtml;
        tbody.appendChild(detailTr);
      }
    });
  }

  function updateVerifyProgressBar() {
    var total = verifyRows.length;
    var done = verifyRows.filter(function (r) { return !!r.verdict; }).length;
    var pct = total ? Math.round((done / total) * 100) : 0;
    var fill = el('verify-progress-fill');
    var txt = el('verify-progress-text');
    if (fill) fill.style.width = pct + '%';
    if (txt) txt.textContent = done + ' / ' + total + ' checked';
  }

  function exportVerifyCsv() {
    var rows = applyVerifyFiltersAndSort(verifyRows);
    var lines = [['index','line','key','reference_title','verdict','confidence','explanation','evidence_quote'].join(',')];
    rows.forEach(function (r) {
      var v = r.verdict || {};
      lines.push([
        r.index,
        r.line || '',
        csvEscape(r.bib_key || ''),
        csvEscape((r.reference && r.reference.title) || ''),
        csvEscape(v.verdict || ''),
        v.confidence != null ? v.confidence : '',
        csvEscape(v.explanation || ''),
        csvEscape(v.evidence_quote || ''),
      ].join(','));
    });
    var csv = lines.join('\n');
    var blob = new Blob([csv], { type: 'text/csv' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'verification.csv';
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }

  function csvEscape(s) {
    s = String(s);
    if (s.indexOf(',') !== -1 || s.indexOf('"') !== -1 || s.indexOf('\n') !== -1) {
      return '"' + s.replace(/"/g, '""') + '"';
    }
    return s;
  }

  /* ----------------------------------------------------------
     Upload logic (project-scoped)
     ---------------------------------------------------------- */
  function validateFile(file) {
    if (!file) return 'No file selected.';
    if (!file.name.toLowerCase().endsWith('.bib')) return 'Please select a .bib file.';
    if (file.size > 2 * 1024 * 1024) return 'File is too large. Maximum size is 2 MB.';
    return null;
  }

  function setSelectedFile(file) {
    if (!file) { fileNameDisplay.textContent = ''; uploadBtn.disabled = true; return; }
    var err = validateFile(file);
    if (err) { uploadError.textContent = err; fileNameDisplay.textContent = ''; uploadBtn.disabled = true; return; }
    uploadError.textContent = '';
    fileNameDisplay.textContent = file.name + ' (' + (file.size / 1024).toFixed(1) + ' KB)';
    uploadBtn.disabled = false;
  }

  function handleUpload() {
    if (!currentProjectSlug) return;
    var file = fileInput.files[0];
    var err = validateFile(file);
    if (err) { uploadError.textContent = err; return; }
    uploadError.textContent = '';
    uploadBtn.disabled = true;
    uploadBtn.textContent = 'Uploading\u2026';

    var formData = new FormData();
    formData.append('file', file);

    fetch('/api/projects/' + currentProjectSlug + '/upload', { method: 'POST', body: formData })
      .then(function (resp) {
        if (!resp.ok) return resp.json().then(function (d) { throw new Error(d.error || 'Upload failed'); });
        return resp.json();
      })
      .then(function (data) {
        sessionId = data.session_id;
        totalRefs = data.total;
        processedCount = 0;
        allResults = [];
        allParsedRefs = [];
        if (data.warning) uploadWarning.textContent = data.warning;
        progressCount.textContent = '0 / ' + totalRefs;
        progressFill.style.width = '0%';
        progressFill.classList.add('progress-bar__fill--active');
        liveFeed.innerHTML = '';
        showView('processing');
        startSSE();
      })
      .catch(function (err) {
        uploadError.textContent = err.message || 'Upload failed.';
        uploadBtn.disabled = false;
        uploadBtn.innerHTML = SVG.upload + ' Upload';
      });
  }

  /* ----------------------------------------------------------
     SSE streaming
     ---------------------------------------------------------- */
  function startSSE() {
    if (eventSource) eventSource.close();
    eventSource = new EventSource('/stream/' + sessionId);

    eventSource.addEventListener('progress', function (e) {
      var data = JSON.parse(e.data);
      processedCount = data.index + 1;
      updateProgress();
      if (data.result) {
        allResults.push(data.result);
        var card = buildResultCard(data.result);
        liveFeed.appendChild(card);
        card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    });

    eventSource.addEventListener('error', function (e) {
      if (e.data) {
        var data = JSON.parse(e.data);
        processedCount = data.index + 1;
        updateProgress();
        allResults.push({
          bib_key: data.bib_key, title: data.bib_key, authors: [], year: null,
          journal: null, doi: null, abstract: null, pdf_url: null, url: null,
          citation_count: 0, sources: [], status: 'not_found', error: data.message
        });
        liveFeed.appendChild(buildErrorCard(data));
      }
    });

    eventSource.addEventListener('complete', function (e) {
      var data = JSON.parse(e.data);
      eventSource.close();
      eventSource = null;
      progressFill.classList.remove('progress-bar__fill--active');
      progressFill.style.width = '100%';
      // Go to dashboard which shows updated stats
      goToDashboard();
    });

    eventSource.addEventListener('heartbeat', function () {});
  }

  function updateProgress() {
    var pct = totalRefs > 0 ? Math.round((processedCount / totalRefs) * 100) : 0;
    progressCount.textContent = processedCount + ' / ' + totalRefs;
    progressFill.style.width = pct + '%';
  }

  /* ----------------------------------------------------------
     Results view
     ---------------------------------------------------------- */
  function updateStatsBar() {
    var s = { total: allResults.length, found_pdf: 0, found_abstract: 0, found_web_page: 0, not_found: 0 };
    allResults.forEach(function (r) {
      if (r.status === 'found_pdf') s.found_pdf++;
      else if (r.status === 'found_abstract') s.found_abstract++;
      else if (r.status === 'found_web_page') s.found_web_page++;
      else s.not_found++;
    });
    statTotal.textContent = s.total;
    statPdf.textContent = s.found_pdf;
    statAbstract.textContent = s.found_abstract;
    statWebPage.textContent = s.found_web_page;
    statNotFound.textContent = s.not_found;
  }

  function showResultsView(stats) {
    statTotal.textContent = stats.total;
    statPdf.textContent = stats.found_pdf;
    statAbstract.textContent = stats.found_abstract;
    statWebPage.textContent = stats.found_web_page;
    statNotFound.textContent = stats.not_found;

    resultsGrid.innerHTML = '';
    allResults.forEach(function (result) {
      resultsGrid.appendChild(buildResultCard(result));
    });

    searchInput.value = '';
    noResultsMsg.style.display = allResults.length === 0 ? 'block' : 'none';
    showView('results');
  }

  /* ----------------------------------------------------------
     Filter / search
     ---------------------------------------------------------- */
  function filterResults() {
    var query = searchInput.value.toLowerCase().trim();
    var cards = resultsGrid.querySelectorAll('.result-card');
    var visibleCount = 0;
    cards.forEach(function (card) {
      var t = card.dataset.title || '';
      var a = card.dataset.authors || '';
      if (!query || t.indexOf(query) !== -1 || a.indexOf(query) !== -1) { card.style.display = ''; visibleCount++; }
      else { card.style.display = 'none'; }
    });
    noResultsMsg.style.display = visibleCount === 0 ? 'block' : 'none';
  }

  /* ----------------------------------------------------------
     Downloads
     ---------------------------------------------------------- */
  function downloadCSV() {
    var id = currentProjectSlug || sessionId;
    if (id) window.location = '/download/' + id + '/csv';
  }
  function downloadPDF() {
    var id = currentProjectSlug || sessionId;
    if (id) window.location = '/download/' + id + '/pdf';
  }

  /* ----------------------------------------------------------
     Rebuild reference .md files (PDF/HTML/abstract -> .md)
     ---------------------------------------------------------- */
  var _buildMdES = null;

  function buildReferenceMd() {
    if (!currentProjectSlug) return;
    var btn = el('dash-build-md-btn');
    var status = el('dash-md-status');
    var progressBox = el('dash-md-progress');
    var progressFill = el('dash-md-progress-fill');
    var progressCount = el('dash-md-progress-count');
    var progressCurrent = el('dash-md-progress-current');
    if (!btn) return;

    var origLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Building...';
    if (status) status.textContent = 'Starting...';
    if (progressBox) progressBox.style.display = '';
    if (progressFill) progressFill.style.width = '0%';
    if (progressCount) progressCount.textContent = '0 / 0';
    if (progressCurrent) progressCurrent.textContent = '';

    var slug = currentProjectSlug;

    function finish(message, opts) {
      opts = opts || {};
      btn.disabled = false;
      btn.textContent = origLabel;
      if (status && message) status.textContent = message;
      if (progressBox && opts.hideProgress) progressBox.style.display = 'none';
      if (_buildMdES) { try { _buildMdES.close(); } catch (e) {} _buildMdES = null; }
      if (opts.refresh) {
        fetch('/api/projects/' + slug)
          .then(function (r) { return r.json(); })
          .then(function (proj) { showDashboard(proj); });
      }
    }

    fetch('/api/projects/' + slug + '/build-md', { method: 'POST' })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        if (!res.ok) {
          finish('Error: ' + ((res.body && res.body.error) || 'failed to start'), { hideProgress: true });
          return;
        }
        var sid = res.body.session_id;
        var total = res.body.total || 0;
        if (progressCount) progressCount.textContent = '0 / ' + total;
        if (status) status.textContent = 'Building 0 / ' + total + '...';

        if (_buildMdES) { try { _buildMdES.close(); } catch (e) {} }
        _buildMdES = new EventSource('/api/projects/' + slug + '/build-md-stream/' + sid);

        _buildMdES.addEventListener('progress', function (ev) {
          var d;
          try { d = JSON.parse(ev.data); } catch (e) { return; }
          var done = d.index + 1;
          var t = d.total || total;
          var pct = t > 0 ? Math.round((done / t) * 100) : 0;
          if (progressFill) progressFill.style.width = pct + '%';
          if (progressCount) progressCount.textContent = done + ' / ' + t;
          // Show the next reference being processed (current), or the last one done
          var label = d.current ? ('Converting ' + d.current) : (d.bib_key ? ('Done ' + d.bib_key) : '');
          if (progressCurrent) progressCurrent.textContent = label;
          if (status) status.textContent = 'Building ' + done + ' / ' + t + '...';
        });

        _buildMdES.addEventListener('complete', function (ev) {
          var d = {};
          try { d = JSON.parse(ev.data); } catch (e) {}
          if (progressFill) progressFill.style.width = '100%';
          if (progressFill) progressFill.classList.remove('progress-bar__fill--active');
          if (progressCurrent) progressCurrent.textContent = '';
          var msg = 'Built ' + (d.built || 0) + ' / ' + (d.total || 0) +
                    ' .md files (' + (d.skipped || 0) + ' skipped, ' + (d.errors || 0) + ' errors).';
          finish(msg, { refresh: true });
        });

        _buildMdES.onerror = function () {
          finish('Connection lost — check status by refreshing the page.', { hideProgress: true });
        };
      })
      .catch(function (e) {
        finish('Error: ' + e.message, { hideProgress: true });
      });
  }

  /* ----------------------------------------------------------
     Reference identity match — batch check from dashboard
     ---------------------------------------------------------- */
  var _refMatchES = null;

  function checkAllReferenceMatches() {
    if (!currentProjectSlug) return;
    var btn = el('dash-check-refs-match-btn');
    var progressBox = el('dash-rm-progress');
    var progressFill = el('dash-rm-progress-fill');
    var progressCount = el('dash-rm-progress-count');
    var progressCurrent = el('dash-rm-progress-current');
    if (!btn) return;

    var origLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Checking...';
    if (progressBox) progressBox.style.display = '';
    if (progressFill) {
      progressFill.style.width = '0%';
      progressFill.classList.add('progress-bar__fill--active');
    }
    if (progressCount) progressCount.textContent = '0 / 0';
    if (progressCurrent) progressCurrent.textContent = '';

    var slug = currentProjectSlug;

    function finish(message, opts) {
      opts = opts || {};
      btn.disabled = false;
      btn.textContent = origLabel;
      if (progressBox && opts.hideProgress) progressBox.style.display = 'none';
      if (_refMatchES) { try { _refMatchES.close(); } catch (e) {} _refMatchES = null; }
      if (opts.refresh) {
        fetch('/api/projects/' + slug)
          .then(function (r) { return r.json(); })
          .then(function (proj) { showDashboard(proj); });
      }
      if (message && progressCurrent) progressCurrent.textContent = message;
    }

    fetch('/api/projects/' + slug + '/check-references-match', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        if (!res.ok) {
          finish('Error: ' + ((res.body && res.body.error) || 'failed to start'), { hideProgress: true });
          return;
        }
        var sid = res.body.session_id;
        var total = res.body.n_references || 0;
        if (progressCount) progressCount.textContent = '0 / ' + total;

        if (_refMatchES) { try { _refMatchES.close(); } catch (e) {} }
        _refMatchES = new EventSource('/api/projects/' + slug + '/ref-match-status/' + sid);

        _refMatchES.addEventListener('progress', function (ev) {
          var d;
          try { d = JSON.parse(ev.data); } catch (e) { return; }
          var done = d.progress || 0;
          var t = d.total || total;
          var pct = t > 0 ? Math.round((done / t) * 100) : 0;
          if (progressFill) progressFill.style.width = pct + '%';
          if (progressCount) progressCount.textContent = done + ' / ' + t;
          if (progressCurrent && d.bib_key) progressCurrent.textContent = 'checked ' + d.bib_key;
          // Update local state so the dashboard breakdown / panels stay in sync
          if (d.bib_key) {
            for (var i = 0; i < allResults.length; i++) {
              if (allResults[i].bib_key === d.bib_key) {
                allResults[i].ref_match = d.ref_match;
                break;
              }
            }
          }
        });

        _refMatchES.addEventListener('complete', function (ev) {
          var d = {};
          try { d = JSON.parse(ev.data); } catch (e) {}
          if (progressFill) {
            progressFill.style.width = '100%';
            progressFill.classList.remove('progress-bar__fill--active');
          }
          var c = d.counts || {};
          var msg = (c.matched || 0) + ' matched, ' + (c.not_matched || 0) + ' NOT matched, ' +
                    ((c.unverifiable || 0) + (c.skipped_no_md || 0)) + ' unverifiable.';
          finish(msg, { refresh: true });
        });

        _refMatchES.onerror = function () {
          finish('Connection lost.', { hideProgress: true });
        };
      })
      .catch(function (e) { finish('Error: ' + e.message, { hideProgress: true }); });
  }

  /* ----------------------------------------------------------
     Validity Report — generate + open
     ---------------------------------------------------------- */
  function buildValidityReport() {
    if (!currentProjectSlug) return;
    var btn = el('dash-validity-report-btn');
    if (!btn) return;
    var origLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Building...';

    var slug = currentProjectSlug;
    fetch('/api/projects/' + slug + '/validity-report', { method: 'POST' })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        btn.disabled = false;
        btn.textContent = origLabel;
        if (!res.ok) {
          alert('Validity report failed: ' + ((res.body && res.body.error) || 'unknown error'));
          return;
        }
        // Open the report HTML in a new tab. The page also has a "Download
        // references bundle" link inline so the author can grab the zip.
        if (res.body.html_url) window.open(res.body.html_url, '_blank', 'noopener');
      })
      .catch(function (e) {
        btn.disabled = false;
        btn.textContent = origLabel;
        alert('Validity report failed: ' + e.message);
      });
  }

  function downloadReferencesZip() {
    if (!currentProjectSlug) return;
    // The endpoint auto-builds the report on demand if the zip isn't present yet,
    // so a user can grab the references bundle without first clicking Validity Report.
    // Plain navigation triggers the browser's attachment-download flow.
    var url = '/api/projects/' + currentProjectSlug + '/validity-report/references-zip';
    window.location.href = url;
  }

  /* ----------------------------------------------------------
     Upload new bib into same project
     ---------------------------------------------------------- */
  function newCheck() {
    if (!currentProjectSlug) { goToProjects(); return; }
    fileInput.value = '';
    fileNameDisplay.textContent = '';
    uploadError.textContent = '';
    uploadWarning.textContent = '';
    uploadBtn.disabled = true;
    uploadBtn.innerHTML = SVG.upload + ' Upload';
    uploadProjectName.textContent = currentProjectName || '';
    showView('upload');
  }

  /* ----------------------------------------------------------
     Drag-and-drop
     ---------------------------------------------------------- */
  function setupDragDrop() {
    var dragCounter = 0;
    dropZone.addEventListener('dragenter', function (e) { e.preventDefault(); e.stopPropagation(); dragCounter++; dropZone.classList.add('drop-zone--dragover'); });
    dropZone.addEventListener('dragleave', function (e) { e.preventDefault(); e.stopPropagation(); dragCounter--; if (dragCounter <= 0) { dragCounter = 0; dropZone.classList.remove('drop-zone--dragover'); } });
    dropZone.addEventListener('dragover', function (e) { e.preventDefault(); e.stopPropagation(); });
    dropZone.addEventListener('drop', function (e) {
      e.preventDefault(); e.stopPropagation(); dragCounter = 0; dropZone.classList.remove('drop-zone--dragover');
      if (e.dataTransfer.files.length > 0) { fileInput.files = e.dataTransfer.files; setSelectedFile(e.dataTransfer.files[0]); }
    });
    dropZone.addEventListener('click', function () { fileInput.click(); });
    dropZone.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); } });
  }

  /* ----------------------------------------------------------
     Initialisation
     ---------------------------------------------------------- */
  function init() {
    projectsView = el('view-projects');
    dashboardView = el('view-dashboard');
    uploadView = el('view-upload');
    processingView = el('view-processing');
    resultsView = el('view-results');
    reviewView = el('view-review');
    verifyView = el('view-verify');
    projectsGrid = el('projects-grid');
    noProjectsMsg = el('no-projects-msg');
    projectNameInput = el('project-name-input');
    createProjectBtn = el('create-project-btn');
    dropZone = el('drop-zone');
    fileInput = el('file-input');
    fileNameDisplay = el('file-name-display');
    uploadBtn = el('upload-btn');
    chooseBtn = el('choose-btn');
    uploadError = el('upload-error');
    uploadWarning = el('upload-warning');
    uploadProjectName = el('upload-project-name');
    progressLabel = el('progress-label');
    progressCount = el('progress-count');
    progressFill = el('progress-fill');
    liveFeed = el('live-feed');
    statTotal = el('stat-total');
    statPdf = el('stat-pdf');
    statAbstract = el('stat-abstract');
    statWebPage = el('stat-webpage');
    statNotFound = el('stat-notfound');
    searchInput = el('search-input');
    resultsGrid = el('results-grid');
    noResultsMsg = el('no-results-msg');
    downloadCsvBtn = el('download-csv');
    downloadPdfBtn = el('download-pdf');
    newCheckBtn = el('new-check-btn');
    resultsProjectName = el('results-project-name');

    // Review view DOM refs
    texFileInput = el('tex-file-input');
    reviewRefKey = el('review-ref-key');
    reviewRefTitle = el('review-ref-title');
    reviewRefMeta = el('review-ref-meta');
    reviewSetLinkBtn = el('review-set-link-btn');
    reviewRefreshBtn = el('review-refresh-btn');
    reviewRefContent = el('review-ref-content');
    reviewIframe = el('review-iframe');
    reviewAbstractText = el('review-abstract-text');
    reviewBibtexText = el('review-bibtex-text');
    reviewNoContent = el('review-no-content');
    reviewCounter = el('review-counter');
    reviewTexFilename = el('review-tex-filename');
    reviewTabPdf = el('review-tab-pdf');
    reviewTabHtml = el('review-tab-html');
    reviewTabAbstract = el('review-tab-abstract');
    reviewTabBibtex = el('review-tab-bibtex');
    reviewTabMd = el('review-tab-md');

    // Start with projects view
    loadProjects();
    showView('projects');

    // Event listeners
    createProjectBtn.addEventListener('click', createProject);
    projectNameInput.addEventListener('keydown', function (e) { if (e.key === 'Enter') createProject(); });
    fileInput.addEventListener('change', function () { setSelectedFile(fileInput.files[0]); });
    chooseBtn.addEventListener('click', function () { fileInput.click(); });
    uploadBtn.addEventListener('click', handleUpload);
    searchInput.addEventListener('input', filterResults);
    downloadCsvBtn.addEventListener('click', downloadCSV);
    downloadPdfBtn.addEventListener('click', downloadPDF);
    newCheckBtn.addEventListener('click', function () { goToDashboard(); });
    el('back-to-projects-upload').addEventListener('click', goToDashboard);
    el('back-to-projects-results').addEventListener('click', goToDashboard);

    // Dashboard events
    el('dash-back-btn').addEventListener('click', goToProjects);
    el('dash-bib-input').addEventListener('change', function () {
      if (this.files[0]) dashUploadBib(this.files[0]);
    });
    el('dash-tex-input').addEventListener('change', function () {
      if (this.files[0]) dashUploadTex(this.files[0]);
    });
    el('dash-view-refs-btn').addEventListener('click', function () {
      if (currentProjectSlug && allResults.length > 0) {
        resultsProjectName.textContent = currentProjectName || '';
        updateStatsBar();
        resultsGrid.innerHTML = '';
        allResults.forEach(function (result) { resultsGrid.appendChild(buildResultCard(result)); });
        searchInput.value = '';
        noResultsMsg.style.display = allResults.length === 0 ? 'block' : 'none';
        showView('results');
      }
    });
    el('dash-review-btn').addEventListener('click', function () {
      // Fetch last-viewed citation so we resume at the right position
      if (currentProjectSlug) {
        fetch('/api/projects/' + currentProjectSlug + '/last-viewed')
          .then(function (r) { return r.json(); })
          .then(function (d) {
            var resumeIdx = d.citation_index || 0;
            openReviewView();
            setTimeout(function () { navigateToCitation(resumeIdx); }, 500);
          })
          .catch(function () { openReviewView(); });
      } else {
        openReviewView();
      }
    });
    el('dash-csv-btn').addEventListener('click', downloadCSV);
    el('dash-pdf-btn').addEventListener('click', downloadPDF);
    var buildMdBtnEl = el('dash-build-md-btn');
    if (buildMdBtnEl) buildMdBtnEl.addEventListener('click', buildReferenceMd);
    var checkRmBtnEl = el('dash-check-refs-match-btn');
    if (checkRmBtnEl) checkRmBtnEl.addEventListener('click', checkAllReferenceMatches);
    var validityBtnEl = el('dash-validity-report-btn');
    if (validityBtnEl) validityBtnEl.addEventListener('click', buildValidityReport);
    var dlRefsBtnEl = el('dash-download-refs-btn');
    if (dlRefsBtnEl) dlRefsBtnEl.addEventListener('click', downloadReferencesZip);

    // Review view events
    texFileInput.addEventListener('change', function () { if (texFileInput.files[0]) uploadTex(); });
    el('review-back-btn').addEventListener('click', function () { goToDashboard(); });
    el('review-save-tex-btn').addEventListener('click', saveTexContent);

    // Review: set link + refresh for current citation
    // --- Source replacement: shared finalize after Set Link / Upload PDF / Paste Content ---
    function applySourceReplacementResponse(bibKey, data) {
      if (data.result) {
        for (var i = 0; i < allResults.length; i++) {
          if (allResults[i].bib_key === bibKey) { allResults[i] = data.result; break; }
        }
        showReferencePanel(bibKey);
      }
      if (data.verdicts_cleared) {
        citations.forEach(function (c, idx) {
          if (c.bib_key === bibKey) {
            var ck = citationCacheKeys[idx];
            var cached = ck ? claimChecks[ck] : null;
            if (!(cached && cached.manual)) {
              delete citationCacheKeys[idx];
            }
          }
        });
        renderVerdictHeader(currentCiteIndex);
      }
      // Always refresh the references panel so source-type labels (PDF/HTML/abstract/not found)
      // pick up the new files, regardless of whether any verdicts were invalidated.
      renderReferencesPanel();
    }

    reviewSetLinkBtn.addEventListener('click', function () {
      var cite = citations[currentCiteIndex];
      if (!cite || !currentProjectSlug) return;
      var url = prompt('Enter URL to the paper (PDF or web page):');
      if (!url || !url.trim()) return;
      reviewSetLinkBtn.disabled = true;
      fetch('/api/projects/' + currentProjectSlug + '/set-link/' + encodeURIComponent(cite.bib_key), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url.trim() }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) { applySourceReplacementResponse(cite.bib_key, data); })
        .finally(function () { reviewSetLinkBtn.disabled = false; });
    });

    // Upload PDF: hidden file input triggered by button
    var uploadPdfBtn = el('review-upload-pdf-btn');
    var uploadPdfInput = el('review-upload-pdf-input');
    if (uploadPdfBtn && uploadPdfInput) {
      uploadPdfBtn.addEventListener('click', function () { uploadPdfInput.click(); });
      uploadPdfInput.addEventListener('change', function () {
        var file = uploadPdfInput.files && uploadPdfInput.files[0];
        if (!file) return;
        var cite = citations[currentCiteIndex];
        if (!cite || !currentProjectSlug) return;
        var origLabel = uploadPdfBtn.textContent;
        uploadPdfBtn.disabled = true;
        uploadPdfBtn.textContent = 'Uploading...';
        var fd = new FormData();
        fd.append('file', file);
        fetch('/api/projects/' + currentProjectSlug + '/upload-pdf/' + encodeURIComponent(cite.bib_key), {
          method: 'POST',
          body: fd,
        })
          .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
          .then(function (res) {
            if (!res.ok) {
              alert('Upload failed: ' + (res.body && res.body.error || 'unknown'));
              return;
            }
            applySourceReplacementResponse(cite.bib_key, res.body);
          })
          .catch(function (e) { alert('Upload error: ' + e.message); })
          .finally(function () {
            uploadPdfBtn.disabled = false;
            uploadPdfBtn.textContent = origLabel;
            uploadPdfInput.value = '';
          });
      });
    }

    // Paste Content: open modal
    var pasteContentBtn = el('review-paste-content-btn');
    if (pasteContentBtn) pasteContentBtn.addEventListener('click', function () {
      var cite = citations[currentCiteIndex];
      if (!cite) return;
      openPasteContentModal(cite.bib_key);
    });
    var pasteCancelEl = el('paste-content-cancel');
    if (pasteCancelEl) pasteCancelEl.addEventListener('click', closePasteContentModal);
    var pasteSubmitEl = el('paste-content-submit');
    if (pasteSubmitEl) pasteSubmitEl.addEventListener('click', submitPasteContent);
    var pasteOverlay = document.querySelector('#paste-content-modal .modal__overlay');
    if (pasteOverlay) pasteOverlay.addEventListener('click', closePasteContentModal);
    // expose for inner scope
    window._applySourceReplacementResponse = applySourceReplacementResponse;

    reviewRefreshBtn.addEventListener('click', function () {
      var cite = citations[currentCiteIndex];
      if (!cite || !currentProjectSlug) return;
      reviewRefreshBtn.disabled = true;
      reviewRefreshBtn.textContent = 'Refreshing...';
      fetch('/api/projects/' + currentProjectSlug + '/refresh/' + encodeURIComponent(cite.bib_key), { method: 'POST' })
        .then(function (r) { return r.json(); })
        .then(function () {
          // Poll for completion
          var poll = setInterval(function () {
            fetch('/api/projects/' + currentProjectSlug + '/refresh-status/' + encodeURIComponent(cite.bib_key))
              .then(function (r) { return r.json(); })
              .then(function (data) {
                if (data.status === 'done') {
                  clearInterval(poll);
                  if (data.result) {
                    for (var i = 0; i < allResults.length; i++) {
                      if (allResults[i].bib_key === cite.bib_key) { allResults[i] = data.result; break; }
                    }
                    showReferencePanel(cite.bib_key);
                    renderReferencesPanel();  // update source label on the left
                  }
                  reviewRefreshBtn.disabled = false;
                  reviewRefreshBtn.textContent = 'Refresh';
                }
              });
          }, 2000);
        })
        .catch(function () { reviewRefreshBtn.disabled = false; reviewRefreshBtn.textContent = 'Refresh'; });
    });
    el('review-prev-btn').addEventListener('click', prevCitation);
    el('review-next-btn').addEventListener('click', nextCitation);

    // v4: claim-check toolbar
    var checkAllBtnEl = el('review-check-all-btn');
    if (checkAllBtnEl) checkAllBtnEl.addEventListener('click', checkAllCitations);
    var stopBtnEl = el('review-stop-check-btn');
    if (stopBtnEl) stopBtnEl.addEventListener('click', stopBatch);
    var recheckBtnEl = el('review-recheck-btn');
    if (recheckBtnEl) recheckBtnEl.addEventListener('click', function () { checkSingleCitation(currentCiteIndex, true); });
    var overrideBtnEl = el('review-override-btn');
    if (overrideBtnEl) overrideBtnEl.addEventListener('click', function () {
      showVerdictPicker(currentCiteIndex, overrideBtnEl);
    });

    // Add Reference button → open modal pre-filled with the missing bib_key
    var addRefBtnEl = el('review-add-ref-btn');
    if (addRefBtnEl) addRefBtnEl.addEventListener('click', function () {
      var key = addRefBtnEl.dataset.bibKey || (citations[currentCiteIndex] && citations[currentCiteIndex].bib_key);
      if (key) openAddRefModal(key);
    });
    // Modal events
    var addRefCancelEl = el('add-ref-cancel');
    if (addRefCancelEl) addRefCancelEl.addEventListener('click', closeAddRefModal);
    var addRefSubmitEl = el('add-ref-submit');
    if (addRefSubmitEl) addRefSubmitEl.addEventListener('click', submitAddRef);
    var addRefOverlay = document.querySelector('#add-ref-modal .modal__overlay');
    if (addRefOverlay) addRefOverlay.addEventListener('click', closeAddRefModal);
    var checkOneBtnEl = el('review-check-one-btn');
    if (checkOneBtnEl) checkOneBtnEl.addEventListener('click', function () { checkSingleCitation(currentCiteIndex, false); });

    // v4: references panel — card click (navigate) / verdict-button click (picker)
    var refsListEl = el('review-refs-list');
    if (refsListEl) refsListEl.addEventListener('click', function (e) {
      var verdictBtn = e.target.closest('button[data-act="set-verdict"]');
      if (verdictBtn) {
        e.stopPropagation();
        var vidx = parseInt(verdictBtn.dataset.idx, 10);
        if (!isNaN(vidx)) showVerdictPicker(vidx, verdictBtn);
        return;
      }
      var card = e.target.closest('.review-ref-card');
      if (card) {
        var idx = parseInt(card.dataset.idx, 10);
        if (!isNaN(idx)) navigateToCitation(idx);
      }
    });

    // v4: references panel filters
    var refsSearchEl = el('review-refs-search');
    if (refsSearchEl) refsSearchEl.addEventListener('input', function () {
      refsFilters.search = this.value; renderReferencesPanel();
    });
    var refsSortEl = el('review-refs-sort');
    if (refsSortEl) refsSortEl.addEventListener('change', function () {
      refsFilters.sort = this.value; renderReferencesPanel();
    });
    var refsIssuesEl = el('review-refs-issues-only');
    if (refsIssuesEl) refsIssuesEl.addEventListener('change', function () {
      refsFilters.issuesOnly = this.checked; renderReferencesPanel();
    });

    // v4: Dashboard "Verification Table" button
    var dashVerifyBtn = el('dash-verify-btn');
    if (dashVerifyBtn) dashVerifyBtn.addEventListener('click', function () {
      // Need tex/citations loaded first
      if (!currentProjectSlug) return;
      // Load tex content into module state if not already
      if (!citations.length) {
        fetch('/api/projects/' + currentProjectSlug + '/tex')
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.error) { alert(data.error); return; }
            texContent = data.tex_content;
            citations = data.citations || [];
            openVerifyView();
          });
      } else {
        openVerifyView();
      }
    });

    // v4: Verify view events
    var verifyBackBtn = el('verify-back-btn');
    if (verifyBackBtn) verifyBackBtn.addEventListener('click', goToDashboard);
    var verifyBackReviewBtn = el('verify-back-review-btn');
    if (verifyBackReviewBtn) verifyBackReviewBtn.addEventListener('click', function (e) {
      e.preventDefault(); openReviewView();
    });
    var verifyRunBtn = el('verify-run-all-btn');
    if (verifyRunBtn) verifyRunBtn.addEventListener('click', function () {
      var stopBtnV = el('verify-stop-btn');
      if (stopBtnV) stopBtnV.style.display = '';
      checkAllCitations();
    });
    var verifyStopBtn = el('verify-stop-btn');
    if (verifyStopBtn) verifyStopBtn.addEventListener('click', function () {
      this.style.display = 'none';
      stopBatch();
    });
    var verifySearchEl = el('verify-search');
    if (verifySearchEl) verifySearchEl.addEventListener('input', function () {
      verifyFilters.search = this.value; renderVerifyTable();
    });
    var verifyFiltersEl = el('verify-filters');
    if (verifyFiltersEl) verifyFiltersEl.addEventListener('change', function (e) {
      var t = e.target;
      if (t.dataset && t.dataset.filter) {
        verifyFilters[t.dataset.filter] = t.checked;
        renderVerifyTable();
      }
    });
    var verifyExportBtn = el('verify-export-csv-btn');
    if (verifyExportBtn) verifyExportBtn.addEventListener('click', exportVerifyCsv);

    // Header click → sort
    var verifyTable = el('verify-table');
    if (verifyTable) verifyTable.querySelectorAll('th[data-sort]').forEach(function (th) {
      th.addEventListener('click', function () {
        var col = th.dataset.sort;
        if (verifySort.col === col) verifySort.dir = verifySort.dir === 'asc' ? 'desc' : 'asc';
        else { verifySort.col = col; verifySort.dir = 'asc'; }
        renderVerifyTable();
      });
    });

    // Row clicks → action handlers
    var verifyTbody = el('verify-tbody');
    if (verifyTbody) verifyTbody.addEventListener('click', function (e) {
      var btn = e.target.closest('button[data-act]');
      if (btn) {
        var tr = btn.closest('tr');
        var idx = parseInt(tr.dataset.index, 10);
        if (isNaN(idx)) return;
        var act = btn.dataset.act;
        if (act === 'check') {
          checkSingleCitation(idx, true).then(function () { refreshVerifyData(); });
        } else if (act === 'open') {
          // Open the review view at this citation
          openReviewView();
          setTimeout(function () { navigateToCitation(idx); }, 400);
        } else if (act === 'expand') {
          verifyExpanded[idx] = !verifyExpanded[idx];
          renderVerifyTable();
        }
        e.stopPropagation();
        return;
      }
      // Row click also toggles expand
      var rowTr = e.target.closest('tr.verify-row');
      if (rowTr) {
        var ridx = parseInt(rowTr.dataset.index, 10);
        if (!isNaN(ridx)) { verifyExpanded[ridx] = !verifyExpanded[ridx]; renderVerifyTable(); }
      }
    });
    reviewTabPdf.addEventListener('click', function () { if (!this.disabled) switchTab('pdf'); });
    reviewTabHtml.addEventListener('click', function () { if (!this.disabled) switchTab('html'); });
    reviewTabAbstract.addEventListener('click', function () { if (!this.disabled) switchTab('abstract'); });
    if (reviewTabMd) reviewTabMd.addEventListener('click', function () { if (!this.disabled) switchTab('md'); });
    reviewTabBibtex.addEventListener('click', function () { if (!this.disabled) switchTab('bibtex'); });

    // Keyboard navigation for review view
    document.addEventListener('keydown', function (e) {
      if (!reviewView.classList.contains('view--active')) return;
      // Don't capture arrows when typing in inputs, textareas, or CodeMirror editor
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      if (e.target.closest('.cm-editor')) return;
      if (e.key === 'ArrowRight') { e.preventDefault(); nextCitation(); }
      if (e.key === 'ArrowLeft') { e.preventDefault(); prevCitation(); }
    });

    setupDragDrop();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
