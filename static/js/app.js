/* ============================================================
   References Checker — Frontend Application Logic
   ============================================================ */

(function () {
  'use strict';

  /* ----------------------------------------------------------
     State
     ---------------------------------------------------------- */
  let sessionId = null;
  let totalRefs = 0;
  let processedCount = 0;
  let allResults = [];    // collected during SSE
  let eventSource = null;

  /* ----------------------------------------------------------
     DOM references (set in init)
     ---------------------------------------------------------- */
  let uploadView, processingView, resultsView;
  let dropZone, fileInput, fileNameDisplay, uploadBtn, chooseBtn;
  let uploadError, uploadWarning;
  let progressLabel, progressCount, progressFill;
  let liveFeed;
  let statTotal, statPdf, statAbstract, statNotFound;
  let searchInput, resultsGrid, noResultsMsg;
  let downloadCsvBtn, downloadPdfBtn;
  let newCheckBtn;

  /* ----------------------------------------------------------
     SVG icon helpers (inline, no emoji, no CDN)
     ---------------------------------------------------------- */
  const SVG = {
    checkCircle: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M9 12l2 2 4-4"/></svg>`,

    document: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>`,

    xCircle: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>`,

    alertTriangle: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,

    externalLink: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>`,

    upload: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>`,

    search: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`,

    download: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>`,

    chevronDown: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>`,

    fileText: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`,

    list: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>`,
  };

  /* ----------------------------------------------------------
     Utility
     ---------------------------------------------------------- */
  function el(id) {
    return document.getElementById(id);
  }

  function escapeHtml(str) {
    if (!str) return '';
    var d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
  }

  function showView(name) {
    uploadView.classList.remove('view--active');
    processingView.classList.remove('view--active');
    resultsView.classList.remove('view--active');
    if (name === 'upload') uploadView.classList.add('view--active');
    if (name === 'processing') processingView.classList.add('view--active');
    if (name === 'results') resultsView.classList.add('view--active');
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
      default: icon = SVG.xCircle; break;
    }
    return '<span class="' + cssClass + '">' + icon + ' ' + escapeHtml(statusLabel(status)) + '</span>';
  }

  /* ----------------------------------------------------------
     Card rendering
     ---------------------------------------------------------- */
  function buildResultCard(result) {
    var card = document.createElement('article');
    card.className = 'result-card result-card--' + (result.status || 'error');
    card.setAttribute('role', 'region');
    card.setAttribute('aria-label', 'Reference: ' + (result.title || result.bib_key || 'Unknown'));

    // Store data for filtering
    card.dataset.title = (result.title || '').toLowerCase();
    card.dataset.authors = (result.authors || []).join(' ').toLowerCase();

    var html = '';

    // Bib key label
    if (result.bib_key) {
      html += '<span class="result-card__bib-key">' + escapeHtml(result.bib_key) + '</span>';
    }

    // Header row: icon + title
    html += '<div class="result-card__header">';
    html += statusIcon(result.status);
    html += '<h3 class="result-card__title">' + escapeHtml(result.title || result.bib_key || 'Untitled') + '</h3>';
    html += '</div>';

    // Meta: authors, year, journal
    var metaParts = [];
    if (result.authors && result.authors.length) {
      metaParts.push('<span>' + escapeHtml(result.authors.join(', ')) + '</span>');
    }
    if (result.year) {
      metaParts.push('<span>' + escapeHtml(String(result.year)) + '</span>');
    }
    if (result.journal) {
      metaParts.push('<span>' + escapeHtml(result.journal) + '</span>');
    }
    if (metaParts.length) {
      html += '<p class="result-card__meta">' + metaParts.join('') + '</p>';
    }

    // Badges row: status + sources
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

    // Actions: abstract toggle, PDF link
    html += '<div class="result-card__actions">';

    if (result.abstract) {
      var abstractId = 'abstract-' + (result.bib_key || Math.random().toString(36).substr(2));
      html += '<button type="button" class="result-card__abstract-toggle" aria-expanded="false" aria-controls="' + abstractId + '">';
      html += SVG.chevronDown + ' Show abstract';
      html += '</button>';
    }

    if (result.pdf_url) {
      html += '<a class="result-card__pdf-link" href="' + escapeHtml(result.pdf_url) + '" target="_blank" rel="noopener noreferrer">';
      html += SVG.externalLink + ' Open PDF';
      html += '</a>';
    } else if (result.url) {
      html += '<a class="result-card__pdf-link" href="' + escapeHtml(result.url) + '" target="_blank" rel="noopener noreferrer">';
      html += SVG.externalLink + ' View online';
      html += '</a>';
    }

    html += '</div>';

    // Abstract (hidden by default)
    if (result.abstract) {
      var absId = 'abstract-' + (result.bib_key || Math.random().toString(36).substr(2));
      html += '<div class="result-card__abstract" id="' + absId + '" role="region" aria-label="Abstract">';
      html += escapeHtml(result.abstract);
      html += '</div>';
    }

    card.innerHTML = html;

    // Wire up abstract toggle
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

    return card;
  }

  function buildErrorCard(data) {
    var card = document.createElement('article');
    card.className = 'result-card result-card--error';
    card.setAttribute('role', 'region');
    card.setAttribute('aria-label', 'Error for reference: ' + (data.bib_key || 'Unknown'));
    card.dataset.title = (data.bib_key || '').toLowerCase();
    card.dataset.authors = '';

    var html = '';
    html += '<div class="result-card__header">';
    html += '<span class="result-card__status-icon result-card__status-icon--error" aria-label="Error">' + SVG.xCircle + '</span>';
    html += '<h3 class="result-card__title">' + escapeHtml(data.bib_key || 'Unknown reference') + '</h3>';
    html += '</div>';
    html += '<p class="result-card__error-msg">' + escapeHtml(data.message || 'An error occurred') + '</p>';
    html += '<div class="result-card__badges">' + statusBadge('not_found') + '</div>';

    card.innerHTML = html;
    return card;
  }

  /* ----------------------------------------------------------
     Upload logic
     ---------------------------------------------------------- */
  function validateFile(file) {
    if (!file) return 'No file selected.';
    if (!file.name.toLowerCase().endsWith('.bib')) return 'Please select a .bib file.';
    if (file.size > 2 * 1024 * 1024) return 'File is too large. Maximum size is 2 MB.';
    return null;
  }

  function setSelectedFile(file) {
    if (!file) {
      fileNameDisplay.textContent = '';
      uploadBtn.disabled = true;
      return;
    }
    var err = validateFile(file);
    if (err) {
      uploadError.textContent = err;
      fileNameDisplay.textContent = '';
      uploadBtn.disabled = true;
      return;
    }
    uploadError.textContent = '';
    fileNameDisplay.textContent = file.name + ' (' + (file.size / 1024).toFixed(1) + ' KB)';
    uploadBtn.disabled = false;
  }

  function handleUpload() {
    var file = fileInput.files[0];
    var err = validateFile(file);
    if (err) {
      uploadError.textContent = err;
      return;
    }
    uploadError.textContent = '';
    uploadBtn.disabled = true;
    uploadBtn.textContent = 'Uploading\u2026';

    var formData = new FormData();
    formData.append('file', file);

    fetch('/upload', { method: 'POST', body: formData })
      .then(function (resp) {
        if (!resp.ok) {
          return resp.json().then(function (d) {
            throw new Error(d.error || 'Upload failed');
          });
        }
        return resp.json();
      })
      .then(function (data) {
        sessionId = data.session_id;
        totalRefs = data.total;
        processedCount = 0;
        allResults = [];

        if (data.warning) {
          uploadWarning.textContent = data.warning;
        }

        // Switch to processing view
        progressCount.textContent = '0 / ' + totalRefs;
        progressFill.style.width = '0%';
        progressFill.classList.add('progress-bar__fill--active');
        liveFeed.innerHTML = '';
        showView('processing');

        // Start SSE
        startSSE();
      })
      .catch(function (err) {
        uploadError.textContent = err.message || 'Upload failed. Please try again.';
        uploadBtn.disabled = false;
        uploadBtn.innerHTML = SVG.upload + ' Upload';
      });
  }

  /* ----------------------------------------------------------
     SSE streaming
     ---------------------------------------------------------- */
  function startSSE() {
    if (eventSource) {
      eventSource.close();
    }

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
      // SSE connection error vs application error event
      if (e.data) {
        var data = JSON.parse(e.data);
        processedCount = data.index + 1;
        updateProgress();

        // Store as a not_found result for stats
        allResults.push({
          bib_key: data.bib_key,
          title: data.bib_key,
          authors: [],
          year: null,
          journal: null,
          doi: null,
          abstract: null,
          pdf_url: null,
          url: null,
          citation_count: 0,
          sources: [],
          status: 'not_found',
          error: data.message
        });

        var card = buildErrorCard(data);
        liveFeed.appendChild(card);
      }
      // If e.data is undefined, it's a connection error; EventSource auto-reconnects.
    });

    eventSource.addEventListener('complete', function (e) {
      var data = JSON.parse(e.data);
      eventSource.close();
      eventSource = null;

      progressFill.classList.remove('progress-bar__fill--active');
      progressFill.style.width = '100%';

      // Populate results view
      showResultsView(data);
    });

    eventSource.addEventListener('heartbeat', function () {
      // Keep-alive, nothing to do
    });
  }

  function updateProgress() {
    var pct = totalRefs > 0 ? Math.round((processedCount / totalRefs) * 100) : 0;
    progressCount.textContent = processedCount + ' / ' + totalRefs;
    progressFill.style.width = pct + '%';
  }

  /* ----------------------------------------------------------
     Results view
     ---------------------------------------------------------- */
  function showResultsView(stats) {
    // Stats
    statTotal.textContent = stats.total;
    statPdf.textContent = stats.found_pdf;
    statAbstract.textContent = stats.found_abstract;
    statNotFound.textContent = stats.not_found;

    // Populate results grid
    resultsGrid.innerHTML = '';
    allResults.forEach(function (result) {
      var card = buildResultCard(result);
      resultsGrid.appendChild(card);
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
      var title = card.dataset.title || '';
      var authors = card.dataset.authors || '';
      if (!query || title.indexOf(query) !== -1 || authors.indexOf(query) !== -1) {
        card.style.display = '';
        visibleCount++;
      } else {
        card.style.display = 'none';
      }
    });

    noResultsMsg.style.display = visibleCount === 0 ? 'block' : 'none';
  }

  /* ----------------------------------------------------------
     Downloads
     ---------------------------------------------------------- */
  function downloadCSV() {
    if (sessionId) {
      window.location = '/download/' + sessionId + '/csv';
    }
  }

  function downloadPDF() {
    if (sessionId) {
      window.location = '/download/' + sessionId + '/pdf';
    }
  }

  /* ----------------------------------------------------------
     Reset for new check
     ---------------------------------------------------------- */
  function resetApp() {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    sessionId = null;
    totalRefs = 0;
    processedCount = 0;
    allResults = [];
    fileInput.value = '';
    fileNameDisplay.textContent = '';
    uploadError.textContent = '';
    uploadWarning.textContent = '';
    uploadBtn.disabled = true;
    uploadBtn.innerHTML = SVG.upload + ' Upload';
    showView('upload');
  }

  /* ----------------------------------------------------------
     Drag-and-drop
     ---------------------------------------------------------- */
  function setupDragDrop() {
    var dragCounter = 0;

    dropZone.addEventListener('dragenter', function (e) {
      e.preventDefault();
      e.stopPropagation();
      dragCounter++;
      dropZone.classList.add('drop-zone--dragover');
    });

    dropZone.addEventListener('dragleave', function (e) {
      e.preventDefault();
      e.stopPropagation();
      dragCounter--;
      if (dragCounter <= 0) {
        dragCounter = 0;
        dropZone.classList.remove('drop-zone--dragover');
      }
    });

    dropZone.addEventListener('dragover', function (e) {
      e.preventDefault();
      e.stopPropagation();
    });

    dropZone.addEventListener('drop', function (e) {
      e.preventDefault();
      e.stopPropagation();
      dragCounter = 0;
      dropZone.classList.remove('drop-zone--dragover');

      var files = e.dataTransfer.files;
      if (files.length > 0) {
        fileInput.files = files;
        setSelectedFile(files[0]);
      }
    });

    // Click on drop zone opens file picker
    dropZone.addEventListener('click', function () {
      fileInput.click();
    });

    // Keyboard: Enter/Space on drop zone opens file picker
    dropZone.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        fileInput.click();
      }
    });
  }

  /* ----------------------------------------------------------
     Initialisation
     ---------------------------------------------------------- */
  function init() {
    // Get DOM refs
    uploadView = el('view-upload');
    processingView = el('view-processing');
    resultsView = el('view-results');
    dropZone = el('drop-zone');
    fileInput = el('file-input');
    fileNameDisplay = el('file-name-display');
    uploadBtn = el('upload-btn');
    chooseBtn = el('choose-btn');
    uploadError = el('upload-error');
    uploadWarning = el('upload-warning');
    progressLabel = el('progress-label');
    progressCount = el('progress-count');
    progressFill = el('progress-fill');
    liveFeed = el('live-feed');
    statTotal = el('stat-total');
    statPdf = el('stat-pdf');
    statAbstract = el('stat-abstract');
    statNotFound = el('stat-notfound');
    searchInput = el('search-input');
    resultsGrid = el('results-grid');
    noResultsMsg = el('no-results-msg');
    downloadCsvBtn = el('download-csv');
    downloadPdfBtn = el('download-pdf');
    newCheckBtn = el('new-check-btn');

    // Initial view
    showView('upload');
    uploadBtn.disabled = true;

    // Event listeners
    fileInput.addEventListener('change', function () {
      setSelectedFile(fileInput.files[0]);
    });

    chooseBtn.addEventListener('click', function () {
      fileInput.click();
    });

    uploadBtn.addEventListener('click', handleUpload);

    searchInput.addEventListener('input', filterResults);

    downloadCsvBtn.addEventListener('click', downloadCSV);
    downloadPdfBtn.addEventListener('click', downloadPDF);

    newCheckBtn.addEventListener('click', resetApp);

    setupDragDrop();
  }

  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
