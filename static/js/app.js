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
  let eventSource = null;

  // Review state
  let texContent = null;
  let citations = [];
  let currentCiteIndex = 0;

  /* ----------------------------------------------------------
     DOM references (set in init)
     ---------------------------------------------------------- */
  let projectsView, dashboardView, uploadView, processingView, resultsView, reviewView;
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
  let reviewTabPdf, reviewTabHtml, reviewTabAbstract, reviewTabBibtex;
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
    if (name === 'projects') projectsView.classList.add('view--active');
    if (name === 'dashboard') dashboardView.classList.add('view--active');
    if (name === 'upload') uploadView.classList.add('view--active');
    if (name === 'processing') processingView.classList.add('view--active');
    if (name === 'results') resultsView.classList.add('view--active');
    if (name === 'review') reviewView.classList.add('view--active');
    // Hide header in review mode to maximize space
    var header = document.getElementById('app-header');
    header.style.display = (name === 'review') ? 'none' : '';
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
        showDashboard(proj);
      });
  }

  function showDashboard(proj) {
    el('dash-project-name').textContent = proj.name;

    // Bib status
    var bibStatus = el('dash-bib-status');
    var viewRefsBtn = el('dash-view-refs-btn');
    var hasResults = proj.results && proj.results.length > 0;
    if (proj.bib_filename && hasResults) {
      bibStatus.textContent = proj.bib_filename + ' — ' + proj.total + ' references (' + proj.status + ')';
      viewRefsBtn.disabled = false;
    } else if (proj.bib_filename) {
      bibStatus.textContent = proj.bib_filename + ' — ' + (proj.status || 'uploaded');
      viewRefsBtn.disabled = true;
    } else {
      bibStatus.textContent = 'No .bib file uploaded yet';
      viewRefsBtn.disabled = true;
    }

    // Tex status
    var texStatus = el('dash-tex-status');
    var reviewBtn = el('dash-review-btn');
    if (proj.tex_filename) {
      var citCount = (proj.citations || []).length;
      texStatus.textContent = proj.tex_filename + ' — ' + citCount + ' citations';
      reviewBtn.disabled = false;
    } else {
      texStatus.textContent = 'No .tex file uploaded yet';
      reviewBtn.disabled = true;
    }

    // Export buttons
    el('dash-csv-btn').disabled = !hasResults;
    el('dash-pdf-btn').disabled = !hasResults;

    // Statistics
    var statsDiv = el('dash-stats');
    if (hasResults) {
      statsDiv.style.display = '';
      var s = { total: 0, pdf: 0, abs: 0, web: 0, nf: 0 };
      proj.results.forEach(function (r) {
        s.total++;
        if (r.status === 'found_pdf') s.pdf++;
        else if (r.status === 'found_abstract') s.abs++;
        else if (r.status === 'found_web_page') s.web++;
        else s.nf++;
      });
      el('dash-stat-total').textContent = s.total;
      el('dash-stat-pdf').textContent = s.pdf;
      el('dash-stat-abstract').textContent = s.abs;
      el('dash-stat-webpage').textContent = s.web;
      el('dash-stat-notfound').textContent = s.nf;
    } else {
      statsDiv.style.display = 'none';
    }

    showView('dashboard');
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
    if (result.sources && result.sources.length) {
      result.sources.forEach(function (src) {
        html += '<span class="source-badge">' + escapeHtml(src) + '</span>';
      });
    }
    if (result.citation_count != null && result.citation_count > 0) {
      html += '<span class="source-badge">Cited: ' + escapeHtml(String(result.citation_count)) + '</span>';
    }
    html += '</div>';

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

        // Show view FIRST so CodeMirror can measure dimensions
        showView('review');

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

  function showReferencePanel(bibKey) {
    reviewIframe.style.display = 'none';
    reviewIframe.src = '';
    reviewAbstractText.style.display = 'none';
    reviewBibtexText.style.display = 'none';
    reviewNoContent.style.display = 'none';

    // Reset tabs
    reviewTabPdf.classList.remove('review-tab--active');
    reviewTabHtml.classList.remove('review-tab--active');
    reviewTabAbstract.classList.remove('review-tab--active');
    reviewTabBibtex.classList.remove('review-tab--active');
    reviewTabPdf.disabled = true;
    reviewTabHtml.disabled = true;
    reviewTabAbstract.disabled = true;
    reviewTabBibtex.disabled = true;
    reviewSetLinkBtn.disabled = false;
    reviewRefreshBtn.disabled = false;

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

    if (!ref) {
      reviewRefKey.textContent = bibKey;
      reviewRefTitle.textContent = 'Reference not found in project results';
      reviewRefMeta.innerHTML = '<span style="color:var(--color-error);">This citation key does not match any reference in the .bib file.</span>';
      reviewSetLinkBtn.disabled = true;
      reviewRefreshBtn.disabled = true;
      reviewNoContent.style.display = 'block';
      return;
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

    // Enable available tabs
    var hasPdf = !!(files.pdf || ref.pdf_url);
    var hasHtml = !!ref.url;
    var hasAbstract = !!ref.abstract;
    var hasBibtex = !!ref.raw_bib;

    reviewTabPdf.disabled = !hasPdf;
    reviewTabHtml.disabled = !hasHtml;
    reviewTabAbstract.disabled = !hasAbstract;
    reviewTabBibtex.disabled = !hasBibtex;

    // Auto-select best tab
    if (hasPdf) switchTab('pdf', ref);
    else if (hasHtml) switchTab('html', ref);
    else if (hasAbstract) switchTab('abstract', ref);
    else if (hasBibtex) switchTab('bibtex', ref);
    else {
      reviewNoContent.style.display = 'block';
      reviewNoContent.textContent = 'No content available. Use "Set Link" to add a URL.';
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
    reviewNoContent.style.display = 'none';
    reviewTabPdf.classList.remove('review-tab--active');
    reviewTabHtml.classList.remove('review-tab--active');
    reviewTabAbstract.classList.remove('review-tab--active');
    reviewTabBibtex.classList.remove('review-tab--active');

    var pdfSrc = files.pdf
      ? '/api/projects/' + currentProjectSlug + '/files/' + encodeURIComponent(files.pdf)
      : ref.pdf_url;

    if (tab === 'pdf' && pdfSrc) {
      reviewTabPdf.classList.add('review-tab--active');
      reviewIframe.src = pdfSrc;
      reviewIframe.style.display = 'block';
    } else if (tab === 'html' && ref.url) {
      reviewTabHtml.classList.add('review-tab--active');
      // Check if we have a locally saved page — use that (no iframe blocking)
      if (files.page && currentProjectSlug) {
        reviewIframe.src = '/api/projects/' + currentProjectSlug + '/files/' + encodeURIComponent(files.page);
        reviewIframe.style.display = 'block';
      } else {
        // Try iframe but show prominent fallback link since many sites block framing
        reviewIframe.src = ref.url;
        reviewIframe.style.display = 'block';
        // Detect blocked iframe: if iframe loads about:blank or throws, hide it
        reviewIframe.onerror = function () {
          reviewIframe.style.display = 'none';
        };
      }
      reviewNoContent.innerHTML = '<div style="padding:1rem;text-align:center;">' +
        '<p style="margin-bottom:0.5rem;color:var(--color-muted);">If the page is blank, the site blocks embedded frames.</p>' +
        '<a href="' + escapeHtml(ref.url) + '" target="_blank" rel="noopener noreferrer" ' +
        'style="color:var(--color-primary);font-weight:700;font-size:1rem;">Open in new tab &rarr;</a></div>';
      reviewNoContent.style.display = 'block';
    } else if (tab === 'abstract' && ref.abstract) {
      reviewTabAbstract.classList.add('review-tab--active');
      reviewAbstractText.textContent = ref.abstract;
      reviewAbstractText.style.display = 'block';
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
    el('dash-review-btn').addEventListener('click', function () { openReviewView(); });
    el('dash-csv-btn').addEventListener('click', downloadCSV);
    el('dash-pdf-btn').addEventListener('click', downloadPDF);

    // Review view events
    texFileInput.addEventListener('change', function () { if (texFileInput.files[0]) uploadTex(); });
    el('review-back-btn').addEventListener('click', function () { goToDashboard(); });
    el('review-save-tex-btn').addEventListener('click', saveTexContent);

    // Review: set link + refresh for current citation
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
        .then(function (data) {
          if (data.result) {
            for (var i = 0; i < allResults.length; i++) {
              if (allResults[i].bib_key === cite.bib_key) { allResults[i] = data.result; break; }
            }
            showReferencePanel(cite.bib_key);
          }
        })
        .finally(function () { reviewSetLinkBtn.disabled = false; });
    });

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
    reviewTabPdf.addEventListener('click', function () { if (!this.disabled) switchTab('pdf'); });
    reviewTabHtml.addEventListener('click', function () { if (!this.disabled) switchTab('html'); });
    reviewTabAbstract.addEventListener('click', function () { if (!this.disabled) switchTab('abstract'); });
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
