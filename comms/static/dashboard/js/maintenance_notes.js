/**
 * Vesta Dashboard — Weekly Maintenance Notes + Owner-Grain Send
 *
 * Fork of portfolio_notes.js adapted for maintenance (weekly periods, no financials).
 *
 * Part A: Portfolio-first authoring (PortfolioMaintenanceNote via /api/reports/maintenance-notes/)
 * Part B: Review & Send (assembled owner emails via /api/reports/maintenance-sends/)
 */
(function () {
  'use strict';

  // --- Helpers ---
  var $ = function (id) { return document.getElementById(id); };
  function esc(s) { return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function words(t) { t = (t || '').trim(); return t ? t.split(/\s+/).length : 0; }
  function initials(name) { var p = (name || '').trim().split(/\s+/); return ((p[0] || '')[0] || '') + ((p[1] || '')[0] || ''); }

  function toast(msg, kind) {
    var t = $('toast');
    t.textContent = msg;
    t.className = 'toast show' + (kind ? (' ' + kind) : '');
    clearTimeout(t._t);
    t._t = setTimeout(function () { t.className = 'toast'; }, 2800);
  }

  function setStatus(kind, msg) {
    $('statusbar').className = 'statusbar is-' + kind;
    $('status-text').textContent = msg;
  }

  // --- Period (weekly) — server-provided default, no client-side computation ---
  var appEl = document.querySelector('.app');
  function weekParam(dateStr) {
    // Full YYYY-MM-DD — no truncation (avoids the monthly period-mismatch bug)
    return dateStr;
  }
  function formatWeek(start, end) {
    var s = new Date(start + 'T00:00:00');
    var e = new Date(end + 'T00:00:00');
    return s.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
      ' – ' + e.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  }
  function formatWeekLabel(start) {
    var s = new Date(start + 'T00:00:00');
    return 'Week of ' + s.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
  }

  // Default week computed server-side (Python weekday math) and injected
  // via template data attributes — avoids JS getDay() Sunday divergence.
  var periodStart = appEl.getAttribute('data-week-start');
  var periodEnd = appEl.getAttribute('data-week-end');
  var prevWeek = appEl.getAttribute('data-prev-week');
  var nextWeek = appEl.getAttribute('data-next-week');

  function gotoWeek(iso) { if (iso) { window.location.search = '?week=' + iso; } }

  // --- State ---
  var currentTab = 'portfolios';     // 'portfolios' | 'send'
  var portfolioNotes = [];           // from /api/reports/maintenance-notes
  var recipients = [];               // from /api/reports/maintenance-sends/recipients
  var activePortfolioId = null;
  var activeRecipientEmail = null;
  var portfolioFilter = 'all';
  var sendFilter = 'all';
  var dirty = false;
  var customRange = null;  // {start, end} set by "Run filtered" only

  // --- Init period display ---
  function updatePeriodDisplay() {
    $('report-week').textContent = formatWeekLabel(periodStart);
    $('report-range').textContent = formatWeek(periodStart, periodEnd);
    $('start-date-input').value = periodStart;
    $('end-date-input').value = periodEnd;
    $('gen-modal-title').textContent = 'Generate maintenance notes for ' + formatWeekLabel(periodStart) + '?';
  }

  // --- Data fetch ---
  function loadPortfolioNotes() {
    var url = customRange
        ? '/reports/maintenance-notes?start_date=' + customRange.start + '&end_date=' + customRange.end
        : '/reports/maintenance-notes?week=' + weekParam(periodStart);
    return VestaAPI.get(url)
      .then(function (data) { portfolioNotes = data; })
      .catch(function (e) { console.error('loadPortfolioNotes', e); portfolioNotes = []; });
  }

  function loadRecipients() {
    return VestaAPI.get('/reports/maintenance-sends/recipients?week=' + weekParam(periodStart))
      .then(function (data) { recipients = data; })
      .catch(function (e) { console.error('loadRecipients', e); recipients = []; });
  }

  function loadAll() {
    return Promise.all([loadPortfolioNotes(), loadRecipients()]).then(renderCurrentTab);
  }

  // --- Portfolio list rendering ---
  function portfolioCounts() {
    var c = { all: portfolioNotes.length, draft: 0, approved: 0 };
    portfolioNotes.forEach(function (n) { if (c[n.status] != null) c[n.status]++; });
    $('n-all').textContent = c.all;
    $('n-draft').textContent = c.draft;
    $('n-approved').textContent = c.approved;
    $('draft-count').textContent = c.all + ' portfolio' + (c.all === 1 ? '' : 's');
  }

  function filteredPortfolios() {
    if (portfolioFilter === 'all') return portfolioNotes;
    return portfolioNotes.filter(function (n) { return n.status === portfolioFilter; });
  }

  function ownersLine(owners) {
    if (!owners || !owners.length) return 'No owners';
    var names = owners.map(function (o) { return o.name; });
    return names.length <= 2 ? names.join(', ') : names[0] + ', ' + names[1] + ' +' + (names.length - 2);
  }

  function renderPortfolioList() {
    portfolioCounts();
    var list = filteredPortfolios();
    var el = $('note-list');
    if (!list.length) { el.innerHTML = '<div class="list-empty">No maintenance notes match this filter.</div>'; return; }
    el.innerHTML = list.map(function (n) {
      var ownerCount = n.owners ? n.owners.length : 0;
      var actions = '';
      if (n.status === 'draft') actions += '<button class="btn btn-quiet btn-sm" data-act="approve" data-id="' + n.id + '">Approve</button>';
      return '<div class="row' + (n.id === activePortfolioId ? ' active' : '') + '" data-id="' + n.id + '">' +
        '<div class="row-top">' +
          '<div><div class="row-name">' + esc(n.portfolio_name) + '</div>' +
          '<div class="row-owners">' + esc(ownersLine(n.owners)) + '</div></div>' +
          '<span class="badge badge-' + n.status + '">' + n.status + '</span>' +
        '</div>' +
        '<div class="row-foot">' +
          '<span class="row-meta">' +
            '<span><svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/></svg>' + ownerCount + ' owner' + (ownerCount === 1 ? '' : 's') + '</span>' +
            '<span>&middot; ' + n.word_count + ' words</span>' +
          '</span>' +
          '<span class="row-actions">' + actions + '</span>' +
        '</div>' +
      '</div>';
    }).join('');
  }

  function byPortfolioId(id) {
    for (var i = 0; i < portfolioNotes.length; i++) {
      if (portfolioNotes[i].id === id) return portfolioNotes[i];
    }
    return null;
  }

  function renderPortfolioDetail(n) {
    $('detail-placeholder').style.display = 'none';
    $('recipient-detail').classList.remove('show');
    $('portfolio-detail').classList.add('show');

    $('note-title').textContent = n.portfolio_name;
    $('note-addr').textContent = n.property_count + ' propert' + (n.property_count === 1 ? 'y' : 'ies');
    var ownerCount = n.owners ? n.owners.length : 0;
    $('note-meta').textContent = formatWeek(periodStart, periodEnd) + ' \u00b7 ' + ownerCount + ' owner' + (ownerCount === 1 ? '' : 's');
    var st = $('note-status');
    st.className = 'badge badge-' + n.status;
    st.textContent = n.status;

    var displayNote = n.display_note || '';

    var ownersHtml = (n.owners || []).map(function (o) {
      return '<li class="recipient"><span class="av">' + esc(initials(o.name).toUpperCase()) + '</span>' +
        '<span><span class="who">' + esc(o.name) + '</span><br><span class="em">' + esc(o.email) + '</span></span></li>';
    }).join('');

    $('detail-scroll').innerHTML =
      '<div class="pf">' +
        '<div class="block">' +
          '<div class="note-toolbar">' +
            '<div class="block-label" style="margin:0">Notes</div>' +
            '<div style="display:flex;align-items:center;gap:12px">' +
              '<span class="note-words" data-words="note">' + words(displayNote) + ' words</span>' +
              '<div class="toggle" data-toggle="note"><button class="active" data-mode="edit">Edit</button><button data-mode="preview">Preview</button></div>' +
            '</div>' +
          '</div>' +
          '<div class="note-edit" data-edit="note"><textarea data-ta="note">' + esc(displayNote) + '</textarea></div>' +
          '<div class="note-preview" data-preview="note"><div class="em-h">' + esc(n.portfolio_name) + '</div><div class="em-body">' + (displayNote ? esc(displayNote).replace(/\n/g, '<br>') : '<em>No note generated.</em>') + '</div></div>' +
        '</div>' +
      '</div>' +
      '<div class="pf recip-card">' +
        '<div class="block">' +
          '<div class="block-label">Recipients \u00b7 ' + ownerCount + '</div>' +
          '<ul class="recipients">' + ownersHtml + '</ul>' +
          (ownerCount > 0 ? '<div class="recip-note"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4 20-7z"/></svg>' +
            'All ' + ownerCount + ' owner' + (ownerCount === 1 ? '' : 's') + ' receive this note. Edit once here and every recipient gets the same update.</div>' : '') +
        '</div>' +
      '</div>';

    // Wire toggle
    var toggles = $('detail-scroll').querySelectorAll('.toggle');
    Array.prototype.forEach.call(toggles, function (tg) {
      var idx = tg.getAttribute('data-toggle');
      tg.addEventListener('click', function (e) {
        var b = e.target.closest('button'); if (!b) return;
        var mode = b.getAttribute('data-mode');
        Array.prototype.forEach.call(tg.querySelectorAll('button'), function (x) { x.classList.remove('active'); });
        b.classList.add('active');
        $('detail-scroll').querySelector('[data-edit="' + idx + '"]').classList.toggle('hide', mode === 'preview');
        $('detail-scroll').querySelector('[data-preview="' + idx + '"]').classList.toggle('show', mode === 'preview');
      });
    });

    // Wire textarea word count + dirty
    var textareas = $('detail-scroll').querySelectorAll('textarea');
    Array.prototype.forEach.call(textareas, function (ta) {
      ta.addEventListener('input', function () {
        dirty = true;
        var idx = ta.getAttribute('data-ta');
        $('detail-scroll').querySelector('[data-words="' + idx + '"]').textContent = words(ta.value) + ' words';
      });
    });

    $('approve-btn').style.display = (n.status === 'draft') ? '' : 'none';
    $('delete-btn').style.display = (n.status === 'draft') ? '' : 'none';
    $('save-btn').disabled = false;
  }

  function selectPortfolio(id) {
    activePortfolioId = id;
    activeRecipientEmail = null;
    dirty = false;
    renderPortfolioList();
    var n = byPortfolioId(id);
    if (n) renderPortfolioDetail(n);
  }

  // --- Recipients list rendering (Part B) ---
  function sendCounts() {
    var c = { all: recipients.length, ready: 0, 'not-ready': 0, sent: 0 };
    recipients.forEach(function (r) {
      if (r.is_sent) c.sent++;
      else if (r.all_approved) c.ready++;
      else c['not-ready']++;
    });
    $('sn-all').textContent = c.all;
    $('sn-ready').textContent = c.ready;
    $('sn-not-ready').textContent = c['not-ready'];
    $('sn-sent').textContent = c.sent;
    $('draft-count').textContent = c.all + ' recipient' + (c.all === 1 ? '' : 's');
  }

  function filteredRecipients() {
    if (sendFilter === 'all') return recipients;
    if (sendFilter === 'ready') return recipients.filter(function (r) { return r.all_approved && !r.is_sent; });
    if (sendFilter === 'not-ready') return recipients.filter(function (r) { return !r.all_approved && !r.is_sent; });
    if (sendFilter === 'sent') return recipients.filter(function (r) { return r.is_sent; });
    return recipients;
  }

  function renderRecipientList() {
    sendCounts();
    var list = filteredRecipients();
    var el = $('note-list');
    if (!list.length) { el.innerHTML = '<div class="list-empty">No recipients match this filter.</div>'; return; }
    el.innerHTML = list.map(function (r) {
      var statusClass, statusText;
      if (r.is_sent) { statusClass = 'sent'; statusText = 'sent'; }
      else if (r.all_approved) { statusClass = 'ready'; statusText = 'ready'; }
      else { statusClass = 'not-ready'; statusText = 'not ready'; }

      var gating = r.portfolios.filter(function (p) { return p.status !== 'approved'; });
      var gatingText = gating.length > 0 ? gating.map(function (p) { return p.name; }).join(', ') : '';

      return '<div class="row' + (r.recipient_email === activeRecipientEmail ? ' active' : '') + '" data-email="' + esc(r.recipient_email) + '">' +
        '<div class="row-top">' +
          '<div><div class="row-name">' + esc(r.owner_name) + '</div>' +
          '<div class="row-owners">' + esc(r.recipient_email) + '</div></div>' +
          '<span class="badge badge-' + statusClass + '">' + statusText + '</span>' +
        '</div>' +
        '<div class="row-foot">' +
          '<span class="row-meta">' +
            '<span>' + r.portfolio_count + ' portfolio' + (r.portfolio_count === 1 ? '' : 's') + '</span>' +
            (gatingText ? '<span style="color:var(--orange)">&middot; waiting: ' + esc(gatingText) + '</span>' : '') +
          '</span>' +
          '<span class="row-actions">' +
            (r.all_approved && !r.is_sent ? '<button class="btn btn-send btn-sm" data-act="send-recip" data-email="' + esc(r.recipient_email) + '">Send</button>' : '') +
          '</span>' +
        '</div>' +
      '</div>';
    }).join('');
  }

  function byRecipientEmail(email) {
    for (var i = 0; i < recipients.length; i++) {
      if (recipients[i].recipient_email === email) return recipients[i];
    }
    return null;
  }

  function renderRecipientDetail(r) {
    $('detail-placeholder').style.display = 'none';
    $('portfolio-detail').classList.remove('show');
    $('recipient-detail').classList.add('show');

    $('recip-title').textContent = r.owner_names ? r.owner_names.join(', ') : r.owner_name;
    $('recip-email').textContent = r.recipient_email;

    var statusClass, statusText;
    if (r.is_sent) { statusClass = 'sent'; statusText = 'sent'; }
    else if (r.all_approved) { statusClass = 'ready'; statusText = 'ready'; }
    else { statusClass = 'not-ready'; statusText = 'not ready'; }

    var st = $('recip-status');
    st.className = 'badge badge-' + statusClass;
    st.textContent = statusText;

    $('recip-meta').textContent = formatWeek(periodStart, periodEnd) + ' \u00b7 ' +
      r.portfolio_count + ' portfolio' + (r.portfolio_count === 1 ? '' : 's');

    // Load assembled preview
    $('recip-scroll').innerHTML = '<div class="list-empty">Loading preview...</div>';
    $('recip-send-btn').disabled = !r.all_approved || r.is_sent;
    $('test-send-btn').disabled = false;

    VestaAPI.get('/reports/maintenance-sends/recipients/' + encodeURIComponent(r.recipient_email) + '/preview?week=' + weekParam(periodStart))
      .then(function (preview) {
        var gating = (preview.portfolios || []).filter(function (p) { return p.status !== 'approved'; });
        var gatingHtml = '';
        if (gating.length > 0) {
          gatingHtml = '<div class="pf" style="border-color:var(--orange)">' +
            '<div class="block"><div class="block-label" style="color:var(--orange)">Gating portfolios</div>' +
            '<ul class="gating-list">' +
            gating.map(function (p) { return '<li>' + esc(p.name) + ' <span class="badge badge-' + p.status + '">' + p.status + '</span></li>'; }).join('') +
            '</ul></div></div>';
        }

        $('recip-scroll').innerHTML =
          gatingHtml +
          '<div class="assembled-preview">' +
            '<h3>Email preview (as sent)</h3>' +
            '<div class="assembled-section">' + (preview.body_html || '<em>No content</em>') + '</div>' +
          '</div>';
      })
      .catch(function (e) {
        $('recip-scroll').innerHTML = '<div class="list-empty">Error loading preview: ' + esc(e.message) + '</div>';
      });
  }

  function selectRecipient(email) {
    activeRecipientEmail = email;
    activePortfolioId = null;
    renderRecipientList();
    var r = byRecipientEmail(email);
    if (r) renderRecipientDetail(r);
  }

  // --- Tab switching ---
  function renderCurrentTab() {
    if (currentTab === 'portfolios') {
      $('portfolio-controls').style.display = '';
      $('send-controls').style.display = 'none';
      $('left-title').textContent = 'Maintenance notes';
      renderPortfolioList();
      if (activePortfolioId) {
        var n = byPortfolioId(activePortfolioId);
        if (n) renderPortfolioDetail(n);
      }
    } else {
      $('portfolio-controls').style.display = 'none';
      $('send-controls').style.display = '';
      $('left-title').textContent = 'Review & Send';
      renderRecipientList();
      if (activeRecipientEmail) {
        var r = byRecipientEmail(activeRecipientEmail);
        if (r) renderRecipientDetail(r);
      }
    }
  }

  $('tab-bar').addEventListener('click', function (e) {
    var btn = e.target.closest('.tab-btn'); if (!btn) return;
    var tab = btn.getAttribute('data-tab');
    if (tab === currentTab) return;
    currentTab = tab;
    Array.prototype.forEach.call($('tab-bar').querySelectorAll('.tab-btn'), function (b) { b.classList.remove('active'); });
    btn.classList.add('active');
    // Reset selection and detail
    activePortfolioId = null;
    activeRecipientEmail = null;
    $('portfolio-detail').classList.remove('show');
    $('recipient-detail').classList.remove('show');
    $('detail-placeholder').style.display = 'flex';
    renderCurrentTab();
  });

  // --- Portfolio chip filters ---
  $('chips').addEventListener('click', function (e) {
    var c = e.target.closest('.chip'); if (!c) return;
    Array.prototype.forEach.call($('chips').querySelectorAll('.chip'), function (x) { x.classList.remove('active'); });
    c.classList.add('active');
    portfolioFilter = c.getAttribute('data-filter');
    renderPortfolioList();
  });

  // --- Send chip filters ---
  $('send-chips').addEventListener('click', function (e) {
    var c = e.target.closest('.chip'); if (!c) return;
    Array.prototype.forEach.call($('send-chips').querySelectorAll('.chip'), function (x) { x.classList.remove('active'); });
    c.classList.add('active');
    sendFilter = c.getAttribute('data-filter');
    renderRecipientList();
  });

  // --- List click handlers ---
  $('note-list').addEventListener('click', function (e) {
    // Inline action buttons
    var act = e.target.closest('[data-act]');
    if (act) {
      e.stopPropagation();
      var action = act.getAttribute('data-act');
      if (action === 'approve') {
        var id = parseInt(act.getAttribute('data-id'), 10);
        approveNote(id);
      }
      if (action === 'send-recip') {
        var email = act.getAttribute('data-email');
        sendRecipient(email);
      }
      return;
    }
    // Row selection
    var row = e.target.closest('.row');
    if (!row) return;
    if (currentTab === 'portfolios') {
      var id = parseInt(row.getAttribute('data-id'), 10);
      if (id) selectPortfolio(id);
    } else {
      var email = row.getAttribute('data-email');
      if (email) selectRecipient(email);
    }
  });

  // --- Portfolio actions ---
  function approveNote(id) {
    VestaAPI.post('/reports/maintenance-notes/' + id + '/approve', {})
      .then(function () { toast('Approved.', 'ok'); return loadAll(); })
      .catch(function (e) { toast('Approve failed: ' + e.message, 'warn'); });
  }

  $('approve-btn').addEventListener('click', function () {
    if (activePortfolioId) approveNote(activePortfolioId);
  });

  $('save-btn').addEventListener('click', function () {
    if (!activePortfolioId) return;
    var ta = $('detail-scroll').querySelector('textarea[data-ta="note"]');
    if (!ta) return;
    VestaAPI.put('/reports/maintenance-notes/' + activePortfolioId, { generated_note: ta.value })
      .then(function () { dirty = false; toast('Note saved.', 'ok'); return loadAll(); })
      .catch(function (e) { toast('Save failed: ' + e.message, 'warn'); });
  });

  $('delete-btn').addEventListener('click', function () {
    if (!activePortfolioId) return;
    if (!confirm('Delete this maintenance note?')) return;
    VestaAPI.delete('/reports/maintenance-notes/' + activePortfolioId)
      .then(function () {
        activePortfolioId = null;
        $('portfolio-detail').classList.remove('show');
        $('detail-placeholder').style.display = 'flex';
        toast('Deleted.', 'ok');
        return loadAll();
      })
      .catch(function (e) { toast('Delete failed: ' + e.message, 'warn'); });
  });

  $('copy-btn').addEventListener('click', function () {
    var ta = $('detail-scroll').querySelector('textarea[data-ta="note"]');
    if (ta) {
      navigator.clipboard.writeText(ta.value).then(function () { toast('Copied to clipboard.', 'ok'); });
    }
  });

  $('approve-all-btn').addEventListener('click', function () {
    VestaAPI.post('/reports/maintenance-notes/approve-all?week=' + weekParam(periodStart), {})
      .then(function (r) { toast((r.approved || 0) + ' note(s) approved.', 'ok'); return loadAll(); })
      .catch(function (e) { toast('Approve all failed: ' + e.message, 'warn'); });
  });

  $('delete-all-btn').addEventListener('click', function () {
    if (!confirm('Delete all draft maintenance notes for this week?')) return;
    VestaAPI.post('/reports/maintenance-notes/delete-all?week=' + weekParam(periodStart), {})
      .then(function (r) {
        activePortfolioId = null;
        $('portfolio-detail').classList.remove('show');
        $('detail-placeholder').style.display = 'flex';
        toast((r.deleted || 0) + ' note(s) deleted.', 'ok');
        return loadAll();
      })
      .catch(function (e) { toast('Delete all failed: ' + e.message, 'warn'); });
  });

  // --- Recipient send actions ---
  function sendRecipient(email) {
    setStatus('running', 'Sending to ' + email + '...');
    VestaAPI.post('/reports/maintenance-sends/recipients/' + encodeURIComponent(email) + '/send?week=' + weekParam(periodStart), {})
      .then(function (r) {
        setStatus('done', 'Sent to ' + email + '.');
        toast(r.message || 'Sent.', 'ok');
        return loadAll();
      })
      .catch(function (e) { setStatus('warn', 'Send failed.'); toast('Send failed: ' + e.message, 'warn'); });
  }

  $('recip-send-btn').addEventListener('click', function () {
    if (activeRecipientEmail) sendRecipient(activeRecipientEmail);
  });

  $('test-send-btn').addEventListener('click', function () {
    if (!activeRecipientEmail) return;
    setStatus('running', 'Sending test email...');
    VestaAPI.post('/reports/maintenance-sends/recipients/' + encodeURIComponent(activeRecipientEmail) + '/test-send?week=' + weekParam(periodStart), {})
      .then(function (r) {
        setStatus('done', r.message || 'Test sent.');
        toast(r.message || 'Test sent.', 'ok');
      })
      .catch(function (e) { setStatus('warn', 'Test send failed.'); toast('Test send failed: ' + e.message, 'warn'); });
  });

  $('send-all-ready-btn').addEventListener('click', function () {
    if (!confirm('Send to all ready recipients?')) return;
    setStatus('running', 'Sending to all ready recipients...');
    VestaAPI.post('/reports/maintenance-sends/send-all?week=' + weekParam(periodStart), {})
      .then(function (r) {
        setStatus('done', (r.sent || 0) + ' sent, ' + (r.failed || 0) + ' failed, ' + (r.skipped || 0) + ' skipped.');
        toast((r.sent || 0) + ' email(s) sent.', 'ok');
        return loadAll();
      })
      .catch(function (e) { setStatus('warn', 'Send all failed.'); toast('Send all failed: ' + e.message, 'warn'); });
  });

  // --- Generate ---
  function openGenerateConfirm() {
    var rebuild = 0, safe = 0;
    portfolioNotes.forEach(function (n) { if (n.status === 'approved') safe++; else rebuild++; });
    $('gen-rebuild-n').textContent = rebuild;
    $('gen-safe-n').textContent = safe;
    $('gen-modal').classList.add('show');
  }
  function closeGenerateConfirm() { $('gen-modal').classList.remove('show'); }

  function doGenerate(opts) {
    closeGenerateConfirm();
    setStatus('running', 'Starting generation...');
    $('generate').disabled = true;
    customRange = opts.custom ? { start: opts.start, end: opts.end } : null;
    VestaAPI.post('/reports/maintenance-notes/generate', {
      start_date: opts.start || periodStart,
      end_date: opts.end || periodEnd,
      dry_run: opts.dryRun || false,
      portfolio_name: opts.portfolioName || '',
    })
      .then(function () { pollProgress(); })
      .catch(function (e) {
        $('generate').disabled = false;
        customRange = null;
        setStatus('warn', 'Generation failed: ' + e.message);
        toast('Generation failed: ' + e.message, 'warn');
      });
  }

  function pollProgress() {
    var url = customRange
        ? '/reports/maintenance-notes/progress?start_date=' + customRange.start + '&end_date=' + customRange.end
        : '/reports/maintenance-notes/progress?week=' + weekParam(periodStart);
    var poll = setInterval(function () {
      VestaAPI.get(url)
        .then(function (d) {
          var pct = d.total > 0 ? Math.round(d.generated / d.total * 100) : 0;
          setStatus('running', d.generated + ' of ' + d.total + ' (' + pct + '%)');
          updateProgressBar(pct);
          if (!d.running || d.generated >= d.total) {
            clearInterval(poll);
            $('generate').disabled = false;
            setStatus('done', 'Generation complete — ' + d.generated + ' of ' + d.total + ' portfolios.');
            updateProgressBar(100);
            setTimeout(function () { hideProgressBar(); loadAll(); }, 800);
          }
        })
        .catch(function () { /* keep polling */ });
    }, 2500);
  }

  function updateProgressBar(pct) {
    var bar = $('gen-progress');
    if (!bar) return;
    bar.style.display = '';
    bar.querySelector('.progress-fill').style.width = pct + '%';
  }
  function hideProgressBar() {
    var bar = $('gen-progress');
    if (bar) bar.style.display = 'none';
  }

  $('generate').addEventListener('click', openGenerateConfirm);
  $('gen-cancel').addEventListener('click', closeGenerateConfirm);
  $('gen-confirm').addEventListener('click', function () { doGenerate({ start: periodStart, end: periodEnd }); });
  $('gen-modal').addEventListener('click', function (e) { if (e.target === this) closeGenerateConfirm(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeGenerateConfirm(); });

  $('run-btn').addEventListener('click', function () {
    $('filters').open = false;
    doGenerate({
      start: $('start-date-input').value,
      end: $('end-date-input').value,
      dryRun: $('dry-run-check').checked,
      portfolioName: $('portfolio-name-input').value,
      custom: true,
    });
  });
  $('run-all-btn').addEventListener('click', function () {
    $('filters').open = false;
    openGenerateConfirm();
  });

  // --- Period change ---
  $('edit-period').addEventListener('click', function () {
    var j = $('jump-week');
    j.value = periodStart;
    j.style.display = '';
    j.focus();
    if (j.showPicker) { try { j.showPicker(); } catch (e) {} }
  });
  $('jump-week').addEventListener('change', function () { gotoWeek(this.value); });
  $('prev-week').addEventListener('click', function () { gotoWeek(prevWeek); });
  $('next-week').addEventListener('click', function () { gotoWeek(nextWeek); });

  // --- Init ---
  updatePeriodDisplay();
  loadAll().then(function () { setStatus('idle', 'Ready.'); });

})();
