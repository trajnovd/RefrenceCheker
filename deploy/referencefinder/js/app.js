/* ============================================================
   References Checker — Client-Side Application (No Backend)
   All API calls happen via browser fetch().
   ============================================================ */

(function () {
  'use strict';

  /* ----------------------------------------------------------
     Constants
     ---------------------------------------------------------- */
  var UNPAYWALL_EMAIL = 'references-checker@trajanov.com';
  var S2_FIELDS = 'paperId,title,abstract,year,citationCount,isOpenAccess,openAccessPdf,authors,externalIds';
  var CROSSREF_DELAY = 300;   // ms between CrossRef/Unpaywall calls
  var S2_DELAY = 1500;        // ms between Semantic Scholar calls
  var RETRY_WAIT = 15000;     // ms to wait on 429
  var MAX_RETRIES = 3;        // retry count on 429

  /* ----------------------------------------------------------
     State
     ---------------------------------------------------------- */
  var totalRefs = 0;
  var processedCount = 0;
  var allResults = [];
  var abortController = null;
  var s2Disabled = false;
  var s2Consecutive429 = 0;
  var activeFilter = 'all';

  /* ----------------------------------------------------------
     DOM references
     ---------------------------------------------------------- */
  var uploadView, processingView, resultsView;
  var dropZone, fileInput, fileNameDisplay, uploadBtn, chooseBtn;
  var uploadError, uploadWarning;
  var progressLabel, progressCount, progressFill, progressStatus;
  var liveFeed;
  var statTotal, statPdf, statAbstract, statNotFound;
  var searchInput, resultsGrid, noResultsMsg;
  var downloadCsvBtn;
  var newCheckBtn;
  var filterAllBtn, filterPdfBtn, filterAbstractBtn, filterNotfoundBtn;

  /* ----------------------------------------------------------
     SVG icon helpers (inline, no emoji, no CDN)
     ---------------------------------------------------------- */
  var SVG = {
    checkCircle: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M9 12l2 2 4-4"/></svg>',

    document: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',

    xCircle: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',

    alertTriangle: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',

    externalLink: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',

    upload: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>',

    search: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',

    download: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',

    chevronDown: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>',

    fileText: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',

    citation: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9h6V6l-6 6zm12 0h-6V6l6 6zM6 18h6v-3l-6 6zm12 0h-6v-3l6 6z"/></svg>',
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

  function delay(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  /* ----------------------------------------------------------
     BibTeX Parser (regex-based, runs in browser)
     ---------------------------------------------------------- */
  function cleanLatex(text) {
    if (!text) return text;
    // Remove LaTeX accent commands: \"{o}, \'{e}, \c{c}, etc. -> keep the letter
    text = text.replace(/\\["'^`~Hcvudtb]\{(\w)\}/g, '$1');
    text = text.replace(/\\["'^`~Hcvudtb](\w)/g, '$1');
    // Remove other LaTeX commands like \textbf{...} -> keep content
    text = text.replace(/\\[a-zA-Z]+\{([^}]*)\}/g, '$1');
    // Remove remaining braces
    text = text.replace(/\{/g, '').replace(/\}/g, '');
    // Remove backslashes
    text = text.replace(/\\/g, '');
    // Clean up multiple spaces
    text = text.replace(/\s+/g, ' ').trim();
    return text;
  }

  function parseBibFile(bibString) {
    if (!bibString || !bibString.trim()) return [];

    var entries = [];
    var seenDois = {};
    var seenTitles = {};

    // Match BibTeX entries: @type{key, ... }
    // We need to handle nested braces for field values
    var entryRegex = /@(\w+)\s*\{\s*([^,]*?)\s*,([\s\S]*?)(?=\n\s*@\w+\s*\{|\s*$)/g;
    var match;

    while ((match = entryRegex.exec(bibString)) !== null) {
      var entryType = match[1].toLowerCase();
      var bibKey = match[2].trim();
      var body = match[3];

      // Skip @string, @comment, @preamble
      if (entryType === 'string' || entryType === 'comment' || entryType === 'preamble') {
        continue;
      }

      var fields = parseFields(body);

      var title = cleanLatex((fields.title || '').trim());
      var doi = (fields.doi || '').trim();
      var authors = cleanLatex(fields.author || '');
      var year = (fields.year || '').trim();
      var journal = cleanLatex(fields.journal || fields.booktitle || '');
      var url = (fields.url || '').trim();

      // Clean DOI - remove URL prefix if present
      if (doi) {
        doi = doi.replace(/^https?:\/\/doi\.org\//i, '');
        doi = doi.replace(/^https?:\/\/dx\.doi\.org\//i, '');
      }

      // Deduplicate by DOI
      if (doi) {
        var doiLower = doi.toLowerCase();
        if (seenDois[doiLower]) continue;
        seenDois[doiLower] = true;
      } else if (title) {
        var normTitle = title.toLowerCase().trim();
        if (seenTitles[normTitle]) continue;
        seenTitles[normTitle] = true;
      }

      var status = null;
      if (!title && !doi) {
        status = 'insufficient_data';
      }

      entries.push({
        bib_key: bibKey,
        title: title || null,
        authors: authors,
        year: year || null,
        journal: journal || null,
        doi: doi || null,
        url: url || null,
        status: status
      });
    }

    return entries;
  }

  function parseFields(body) {
    var fields = {};
    // Match field = value pairs; value can be {braced}, "quoted", or number
    var pos = 0;
    var len = body.length;

    while (pos < len) {
      // Skip whitespace and commas
      while (pos < len && /[\s,]/.test(body[pos])) pos++;
      if (pos >= len) break;

      // Check for closing brace (end of entry)
      if (body[pos] === '}') break;

      // Read field name
      var nameStart = pos;
      while (pos < len && /[\w-]/.test(body[pos])) pos++;
      var fieldName = body.substring(nameStart, pos).toLowerCase();
      if (!fieldName) { pos++; continue; }

      // Skip whitespace
      while (pos < len && /\s/.test(body[pos])) pos++;

      // Expect =
      if (pos >= len || body[pos] !== '=') continue;
      pos++; // skip =

      // Skip whitespace
      while (pos < len && /\s/.test(body[pos])) pos++;
      if (pos >= len) break;

      // Read value
      var value = '';
      if (body[pos] === '{') {
        value = readBraced(body, pos);
        pos += value.length + 2; // +2 for outer braces
      } else if (body[pos] === '"') {
        pos++; // skip opening quote
        var qStart = pos;
        var depth = 0;
        while (pos < len) {
          if (body[pos] === '{') depth++;
          else if (body[pos] === '}') depth--;
          else if (body[pos] === '"' && depth === 0) break;
          pos++;
        }
        value = body.substring(qStart, pos);
        if (pos < len) pos++; // skip closing quote
      } else {
        // Bare value (number or string name)
        var vStart = pos;
        while (pos < len && /[^\s,}]/.test(body[pos])) pos++;
        value = body.substring(vStart, pos);
      }

      // Handle # concatenation
      var fullValue = value;
      while (pos < len) {
        var tmpPos = pos;
        while (tmpPos < len && /\s/.test(body[tmpPos])) tmpPos++;
        if (tmpPos < len && body[tmpPos] === '#') {
          tmpPos++;
          while (tmpPos < len && /\s/.test(body[tmpPos])) tmpPos++;
          pos = tmpPos;
          if (pos < len && body[pos] === '{') {
            var extra = readBraced(body, pos);
            pos += extra.length + 2;
            fullValue += extra;
          } else if (pos < len && body[pos] === '"') {
            pos++;
            var qqStart = pos;
            while (pos < len && body[pos] !== '"') pos++;
            fullValue += body.substring(qqStart, pos);
            if (pos < len) pos++;
          } else {
            var evStart = pos;
            while (pos < len && /[^\s,}]/.test(body[pos])) pos++;
            fullValue += body.substring(evStart, pos);
          }
        } else {
          break;
        }
      }

      fields[fieldName] = fullValue.trim();
    }

    return fields;
  }

  function readBraced(str, start) {
    // start points at opening {
    var depth = 0;
    var pos = start;
    var len = str.length;
    pos++; // skip opening brace
    var contentStart = pos;
    while (pos < len) {
      if (str[pos] === '{') depth++;
      else if (str[pos] === '}') {
        if (depth === 0) {
          return str.substring(contentStart, pos);
        }
        depth--;
      }
      pos++;
    }
    // Unbalanced braces - return what we have
    return str.substring(contentStart, pos);
  }

  /* ----------------------------------------------------------
     API Clients (browser fetch, all CORS-friendly)
     ---------------------------------------------------------- */

  /**
   * Fetch with retry on 429.
   * Returns the Response object or null on failure.
   */
  function fetchWithRetry(url, options, maxRetries, retryWait, signal) {
    maxRetries = maxRetries || MAX_RETRIES;
    retryWait = retryWait || RETRY_WAIT;

    return (function attempt(tries) {
      return fetch(url, Object.assign({ signal: signal }, options))
        .then(function (resp) {
          if (resp.status === 429 && tries < maxRetries) {
            return delay(retryWait).then(function () {
              return attempt(tries + 1);
            });
          }
          return resp;
        })
        .catch(function (err) {
          if (err.name === 'AbortError') throw err;
          if (tries < maxRetries) {
            return delay(2000).then(function () {
              return attempt(tries + 1);
            });
          }
          return null;
        });
    })(0);
  }

  /* --- CrossRef --- */
  function lookupCrossRef(doi, signal) {
    if (!doi) return Promise.resolve(null);
    var url = 'https://api.crossref.org/works/' + doi;
    return fetchWithRetry(url, {}, MAX_RETRIES, RETRY_WAIT, signal)
      .then(function (resp) {
        if (!resp || resp.status !== 200) return null;
        return resp.json();
      })
      .then(function (data) {
        if (!data) return null;
        var msg = data.message || {};
        var titles = msg.title || [];
        var authorsRaw = msg.author || [];
        var authors = authorsRaw.map(function (a) {
          return ((a.given || '') + ' ' + (a.family || '')).trim();
        });
        var container = msg['container-title'] || [];
        var pub = msg['published-print'] || msg['published-online'] || {};
        var dateParts = pub['date-parts'] || [[]];
        var year = (dateParts[0] && dateParts[0][0]) ? String(dateParts[0][0]) : null;
        return {
          title: titles[0] || null,
          authors: authors,
          journal: container[0] || null,
          year: year,
          url: msg.URL || null
        };
      })
      .catch(function (err) {
        if (err.name === 'AbortError') throw err;
        return null;
      });
  }

  /* --- Unpaywall --- */
  function lookupUnpaywall(doi, signal) {
    if (!doi) return Promise.resolve(null);
    var url = 'https://api.unpaywall.org/v2/' + doi + '?email=' + encodeURIComponent(UNPAYWALL_EMAIL);
    return fetchWithRetry(url, {}, MAX_RETRIES, RETRY_WAIT, signal)
      .then(function (resp) {
        if (!resp || resp.status !== 200) return null;
        return resp.json();
      })
      .then(function (data) {
        if (!data) return null;
        var best = data.best_oa_location || {};
        return {
          is_oa: data.is_oa || false,
          pdf_url: best.url_for_pdf || null
        };
      })
      .catch(function (err) {
        if (err.name === 'AbortError') throw err;
        return null;
      });
  }

  /* --- Semantic Scholar --- */
  function normalizeTitle(text) {
    return (text || '').toLowerCase().replace(/[^\w\s]/g, '').trim();
  }

  function parseS2Paper(data) {
    if (!data) return null;
    var oaPdf = data.openAccessPdf || {};
    var authors = (data.authors || []).map(function (a) { return a.name || ''; });
    var extIds = data.externalIds || {};
    return {
      title: data.title || null,
      abstract: data.abstract || null,
      year: data.year ? String(data.year) : null,
      citation_count: data.citationCount || null,
      pdf_url: oaPdf.url || null,
      authors: authors,
      doi: extIds.DOI || null
    };
  }

  function lookupS2ByDoi(doi, signal) {
    if (s2Disabled || !doi) return Promise.resolve(null);
    var url = 'https://api.semanticscholar.org/graph/v1/paper/DOI:' + doi + '?fields=' + S2_FIELDS;

    return fetchWithRetry(url, {}, MAX_RETRIES, RETRY_WAIT, signal)
      .then(function (resp) {
        if (!resp) { return null; }
        if (resp.status === 429) {
          s2Consecutive429++;
          if (s2Consecutive429 >= 5) {
            s2Disabled = true;
          }
          return null;
        }
        s2Consecutive429 = 0;
        if (resp.status !== 200) return null;
        return resp.json();
      })
      .then(function (data) {
        return parseS2Paper(data);
      })
      .catch(function (err) {
        if (err.name === 'AbortError') throw err;
        return null;
      });
  }

  function lookupS2ByTitle(title, signal) {
    if (s2Disabled || !title) return Promise.resolve(null);
    var url = 'https://api.semanticscholar.org/graph/v1/paper/search/match?query=' + encodeURIComponent(title) + '&fields=' + S2_FIELDS;

    return fetchWithRetry(url, {}, MAX_RETRIES, RETRY_WAIT, signal)
      .then(function (resp) {
        if (!resp) { return null; }
        if (resp.status === 429) {
          s2Consecutive429++;
          if (s2Consecutive429 >= 5) {
            s2Disabled = true;
          }
          return null;
        }
        s2Consecutive429 = 0;
        if (resp.status === 404) return null; // no match
        if (resp.status !== 200) return null;
        return resp.json();
      })
      .then(function (data) {
        if (!data) return null;
        var results = data.data || [];
        if (!results.length) return null;
        var best = results[0];
        // Verify title match
        var normQuery = normalizeTitle(title);
        var normResult = normalizeTitle(best.title || '');
        if (normQuery === normResult) return parseS2Paper(best);
        if (normQuery.indexOf(normResult) !== -1 || normResult.indexOf(normQuery) !== -1) {
          return parseS2Paper(best);
        }
        // Fuzzy: >60% word overlap
        var queryWords = normQuery.split(/\s+/).filter(Boolean);
        var resultWords = normResult.split(/\s+/).filter(Boolean);
        if (queryWords.length && resultWords.length) {
          var querySet = {};
          queryWords.forEach(function (w) { querySet[w] = true; });
          var overlap = 0;
          resultWords.forEach(function (w) { if (querySet[w]) overlap++; });
          var ratio = overlap / Math.max(queryWords.length, resultWords.length);
          if (ratio > 0.6) return parseS2Paper(best);
        }
        return null;
      })
      .catch(function (err) {
        if (err.name === 'AbortError') throw err;
        return null;
      });
  }

  /* ----------------------------------------------------------
     Lookup Engine — processes a single reference
     ---------------------------------------------------------- */
  function processReference(ref, signal) {
    if (ref.status === 'insufficient_data') {
      return Promise.resolve({
        bib_key: ref.bib_key,
        title: ref.title,
        authors: [],
        year: null,
        journal: null,
        doi: null,
        abstract: null,
        pdf_url: null,
        url: null,
        citation_count: null,
        sources: [],
        status: 'insufficient_data',
        error: 'No title or DOI in .bib entry'
      });
    }

    var title = ref.title;
    var doi = ref.doi;
    var authors = ref.authors;
    var year = ref.year;

    var result = {
      bib_key: ref.bib_key,
      title: title,
      authors: authors ? (Array.isArray(authors) ? authors : [authors]) : [],
      year: year,
      journal: ref.journal,
      doi: doi,
      abstract: null,
      pdf_url: null,
      url: ref.url,
      citation_count: null,
      sources: [],
      status: 'not_found',
      error: null
    };

    // Step 1: If DOI exists, fetch CrossRef + Unpaywall in parallel
    var step1;
    if (doi) {
      step1 = Promise.all([
        lookupCrossRef(doi, signal),
        lookupUnpaywall(doi, signal)
      ]).then(function (results) {
        var cr = results[0];
        var uw = results[1];

        if (cr) {
          result.sources.push('crossref');
          result.title = result.title || cr.title;
          result.authors = (cr.authors && cr.authors.length) ? cr.authors : result.authors;
          result.journal = result.journal || cr.journal;
          result.year = result.year || cr.year;
          result.url = result.url || cr.url;
        }

        if (uw) {
          result.sources.push('unpaywall');
          if (uw.pdf_url) {
            result.pdf_url = uw.pdf_url;
          }
        }
      });
    } else {
      step1 = Promise.resolve();
    }

    // Step 2: Semantic Scholar (after CrossRef delay)
    return step1.then(function () {
      if (s2Disabled) return null;
      return delay(S2_DELAY).then(function () {
        // Try DOI first, then title
        if (doi) {
          return lookupS2ByDoi(doi, signal).then(function (s2) {
            if (s2) return s2;
            // If DOI lookup failed, try title match
            if (title) {
              return delay(S2_DELAY).then(function () {
                return lookupS2ByTitle(title, signal);
              });
            }
            return null;
          });
        } else if (title) {
          return lookupS2ByTitle(title, signal);
        }
        return null;
      });
    }).then(function (s2) {
      if (s2) {
        result.sources.push('semantic_scholar');
        result.abstract = result.abstract || s2.abstract;
        result.citation_count = s2.citation_count;
        result.doi = result.doi || s2.doi;
        if (!result.pdf_url && s2.pdf_url) {
          result.pdf_url = s2.pdf_url;
        }

        // If S2 gave us a DOI we didn't have, try Unpaywall
        if (result.doi && !doi && !result.pdf_url) {
          return delay(CROSSREF_DELAY).then(function () {
            return lookupUnpaywall(result.doi, signal);
          }).then(function (uw) {
            if (uw && uw.pdf_url) {
              result.pdf_url = uw.pdf_url;
              if (result.sources.indexOf('unpaywall') === -1) {
                result.sources.push('unpaywall');
              }
            }
          });
        }
      }
    }).then(function () {
      // Determine final status
      if (result.pdf_url) {
        result.status = 'found_pdf';
      } else if (result.abstract) {
        result.status = 'found_abstract';
      } else {
        result.status = 'not_found';
      }
      return result;
    }).catch(function (err) {
      if (err.name === 'AbortError') throw err;
      result.status = 'not_found';
      result.error = err.message || 'Lookup failed';
      return result;
    });
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
      case 'insufficient_data': return 'Insufficient Data';
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
    card.dataset.authors = (Array.isArray(result.authors) ? result.authors.join(' ') : (result.authors || '')).toLowerCase();
    card.dataset.status = result.status || 'error';

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
    var authorsArray = Array.isArray(result.authors) ? result.authors : (result.authors ? [result.authors] : []);
    if (authorsArray.length && authorsArray[0]) {
      metaParts.push('<span>' + escapeHtml(authorsArray.join(', ')) + '</span>');
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

    // Badges row: status + sources + citation count
    html += '<div class="result-card__badges">';
    html += statusBadge(result.status);
    if (result.sources && result.sources.length) {
      result.sources.forEach(function (src) {
        html += '<span class="source-badge">' + escapeHtml(src) + '</span>';
      });
    }
    if (result.citation_count != null && result.citation_count > 0) {
      html += '<span class="citation-badge">' + SVG.citation + ' ' + escapeHtml(String(result.citation_count)) + ' cited</span>';
    }
    html += '</div>';

    // Actions: abstract toggle, PDF link, view online link
    html += '<div class="result-card__actions">';

    if (result.abstract) {
      var abstractId = 'abstract-' + (result.bib_key || Math.random().toString(36).substr(2));
      html += '<button type="button" class="result-card__abstract-toggle" aria-expanded="false" aria-controls="' + escapeHtml(abstractId) + '">';
      html += SVG.chevronDown + ' Show abstract';
      html += '</button>';
    }

    if (result.pdf_url) {
      html += '<a class="result-card__pdf-link" href="' + escapeHtml(result.pdf_url) + '" target="_blank" rel="noopener noreferrer">';
      html += SVG.externalLink + ' Open PDF';
      html += '</a>';
    }

    if (result.url) {
      html += '<a class="result-card__view-link" href="' + escapeHtml(result.url) + '" target="_blank" rel="noopener noreferrer">';
      html += SVG.externalLink + ' View online';
      html += '</a>';
    }

    html += '</div>';

    // Abstract (hidden by default)
    if (result.abstract) {
      var absId = 'abstract-' + (result.bib_key || Math.random().toString(36).substr(2));
      html += '<div class="result-card__abstract" id="' + escapeHtml(absId) + '" role="region" aria-label="Abstract">';
      html += escapeHtml(result.abstract);
      html += '</div>';
    }

    // Error message
    if (result.error) {
      html += '<p class="result-card__error-msg">' + escapeHtml(result.error) + '</p>';
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

  /* ----------------------------------------------------------
     Upload + Processing Logic
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
    uploadWarning.textContent = '';
    uploadBtn.disabled = true;
    uploadBtn.innerHTML = SVG.search + ' Parsing\u2026';

    var reader = new FileReader();
    reader.onload = function (e) {
      var bibString = e.target.result;
      var refs;
      try {
        refs = parseBibFile(bibString);
      } catch (parseErr) {
        uploadError.textContent = 'Failed to parse .bib file: ' + parseErr.message;
        uploadBtn.disabled = false;
        uploadBtn.innerHTML = SVG.search + ' Check References';
        return;
      }

      if (!refs || refs.length === 0) {
        uploadError.textContent = 'No valid references found in file.';
        uploadBtn.disabled = false;
        uploadBtn.innerHTML = SVG.search + ' Check References';
        return;
      }

      if (refs.length > 500) {
        uploadWarning.textContent = 'Large file with ' + refs.length + ' references. This may take several minutes.';
      }

      // Switch to processing view
      totalRefs = refs.length;
      processedCount = 0;
      allResults = [];
      s2Disabled = false;
      s2Consecutive429 = 0;

      progressCount.textContent = '0 / ' + totalRefs;
      progressFill.style.width = '0%';
      progressFill.classList.add('progress-bar__fill--active');
      progressStatus.textContent = '';
      liveFeed.innerHTML = '';
      showView('processing');

      // Start processing
      startProcessing(refs);
    };

    reader.onerror = function () {
      uploadError.textContent = 'Failed to read file. Please try again.';
      uploadBtn.disabled = false;
      uploadBtn.innerHTML = SVG.search + ' Check References';
    };

    reader.readAsText(file, 'UTF-8');
  }

  function startProcessing(refs) {
    abortController = new AbortController();
    var signal = abortController.signal;

    processSequentially(refs, 0, signal).then(function () {
      progressFill.classList.remove('progress-bar__fill--active');
      progressFill.style.width = '100%';
      progressStatus.textContent = 'Complete!';
      showResultsView();
    }).catch(function (err) {
      if (err.name === 'AbortError') {
        progressStatus.textContent = 'Cancelled.';
      } else {
        progressStatus.textContent = 'An error occurred: ' + err.message;
      }
      progressFill.classList.remove('progress-bar__fill--active');
    });
  }

  function processSequentially(refs, index, signal) {
    if (index >= refs.length) return Promise.resolve();
    if (signal.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'));

    var ref = refs[index];
    var statusMsg = 'Looking up: ' + (ref.title || ref.bib_key || 'reference ' + (index + 1));
    if (s2Disabled) {
      statusMsg += ' (Semantic Scholar disabled due to rate limits)';
    }
    progressStatus.textContent = statusMsg;

    return processReference(ref, signal).then(function (result) {
      if (signal.aborted) throw new DOMException('Aborted', 'AbortError');

      allResults.push(result);
      processedCount = index + 1;
      updateProgress();

      // Add card to live feed
      var card = buildResultCard(result);
      liveFeed.appendChild(card);
      card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

      // Delay between references — enough for S2 rate limits
      var interDelay = S2_DELAY;
      return delay(interDelay).then(function () {
        return processSequentially(refs, index + 1, signal);
      });
    }).catch(function (err) {
      if (err.name === 'AbortError') throw err;

      // On error, still record the reference and continue
      var errorResult = {
        bib_key: ref.bib_key,
        title: ref.title || ref.bib_key,
        authors: [],
        year: null,
        journal: null,
        doi: null,
        abstract: null,
        pdf_url: null,
        url: null,
        citation_count: null,
        sources: [],
        status: 'not_found',
        error: err.message || 'Lookup failed'
      };
      allResults.push(errorResult);
      processedCount = index + 1;
      updateProgress();

      var card = buildResultCard(errorResult);
      liveFeed.appendChild(card);

      return delay(CROSSREF_DELAY).then(function () {
        return processSequentially(refs, index + 1, signal);
      });
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
  function showResultsView() {
    // Compute stats
    var total = allResults.length;
    var foundPdf = 0;
    var foundAbstract = 0;
    var notFound = 0;

    allResults.forEach(function (r) {
      if (r.status === 'found_pdf') foundPdf++;
      else if (r.status === 'found_abstract') foundAbstract++;
      else notFound++;
    });

    statTotal.textContent = total;
    statPdf.textContent = foundPdf;
    statAbstract.textContent = foundAbstract;
    statNotFound.textContent = notFound;

    // Populate results grid
    resultsGrid.innerHTML = '';
    allResults.forEach(function (result) {
      var card = buildResultCard(result);
      resultsGrid.appendChild(card);
    });

    searchInput.value = '';
    activeFilter = 'all';
    updateFilterButtons();
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
      var status = card.dataset.status || '';

      var matchesSearch = !query || title.indexOf(query) !== -1 || authors.indexOf(query) !== -1;
      var matchesFilter = activeFilter === 'all' ||
        (activeFilter === 'found_pdf' && status === 'found_pdf') ||
        (activeFilter === 'found_abstract' && status === 'found_abstract') ||
        (activeFilter === 'not_found' && (status === 'not_found' || status === 'insufficient_data' || status === 'error'));

      if (matchesSearch && matchesFilter) {
        card.style.display = '';
        visibleCount++;
      } else {
        card.style.display = 'none';
      }
    });

    noResultsMsg.style.display = visibleCount === 0 ? 'block' : 'none';
  }

  function setFilter(filter) {
    activeFilter = filter;
    updateFilterButtons();
    filterResults();
  }

  function updateFilterButtons() {
    [filterAllBtn, filterPdfBtn, filterAbstractBtn, filterNotfoundBtn].forEach(function (btn) {
      btn.classList.remove('btn--filter--active');
      btn.setAttribute('aria-pressed', 'false');
    });
    var activeBtn;
    switch (activeFilter) {
      case 'all': activeBtn = filterAllBtn; break;
      case 'found_pdf': activeBtn = filterPdfBtn; break;
      case 'found_abstract': activeBtn = filterAbstractBtn; break;
      case 'not_found': activeBtn = filterNotfoundBtn; break;
    }
    if (activeBtn) {
      activeBtn.classList.add('btn--filter--active');
      activeBtn.setAttribute('aria-pressed', 'true');
    }
  }

  /* ----------------------------------------------------------
     CSV Download (client-side)
     ---------------------------------------------------------- */
  function downloadCSV() {
    if (!allResults.length) return;

    var csvFields = ['bib_key', 'title', 'authors', 'year', 'journal', 'doi', 'abstract', 'pdf_url', 'url', 'citation_count', 'sources', 'status'];

    var csvRows = [csvFields.join(',')];

    allResults.forEach(function (r) {
      var row = csvFields.map(function (field) {
        var val = r[field];
        if (field === 'authors') {
          val = Array.isArray(val) ? val.join('; ') : (val || '');
        } else if (field === 'sources') {
          val = Array.isArray(val) ? val.join(', ') : (val || '');
        } else {
          val = val != null ? String(val) : '';
        }
        // Escape CSV: wrap in quotes if contains comma, quote, or newline
        if (val.indexOf(',') !== -1 || val.indexOf('"') !== -1 || val.indexOf('\n') !== -1) {
          val = '"' + val.replace(/"/g, '""') + '"';
        }
        return val;
      });
      csvRows.push(row.join(','));
    });

    var csvString = csvRows.join('\n');
    var blob = new Blob([csvString], { type: 'text/csv;charset=utf-8;' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'references_report.csv';
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  /* ----------------------------------------------------------
     Reset for new check
     ---------------------------------------------------------- */
  function resetApp() {
    if (abortController) {
      abortController.abort();
      abortController = null;
    }
    totalRefs = 0;
    processedCount = 0;
    allResults = [];
    s2Disabled = false;
    s2Consecutive429 = 0;
    activeFilter = 'all';
    fileInput.value = '';
    fileNameDisplay.textContent = '';
    uploadError.textContent = '';
    uploadWarning.textContent = '';
    uploadBtn.disabled = true;
    uploadBtn.innerHTML = SVG.search + ' Check References';
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
    progressStatus = el('progress-status');
    liveFeed = el('live-feed');
    statTotal = el('stat-total');
    statPdf = el('stat-pdf');
    statAbstract = el('stat-abstract');
    statNotFound = el('stat-notfound');
    searchInput = el('search-input');
    resultsGrid = el('results-grid');
    noResultsMsg = el('no-results-msg');
    downloadCsvBtn = el('download-csv');
    newCheckBtn = el('new-check-btn');
    filterAllBtn = el('filter-all');
    filterPdfBtn = el('filter-pdf');
    filterAbstractBtn = el('filter-abstract');
    filterNotfoundBtn = el('filter-notfound');

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

    newCheckBtn.addEventListener('click', resetApp);

    // Filter buttons
    filterAllBtn.addEventListener('click', function () { setFilter('all'); });
    filterPdfBtn.addEventListener('click', function () { setFilter('found_pdf'); });
    filterAbstractBtn.addEventListener('click', function () { setFilter('found_abstract'); });
    filterNotfoundBtn.addEventListener('click', function () { setFilter('not_found'); });

    setupDragDrop();
  }

  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
