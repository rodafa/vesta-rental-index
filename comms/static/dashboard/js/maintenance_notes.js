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

  // --- Period helpers ---
  var appEl = document.querySelector('.app');
  function formatWeek(start, end) {
    var s = new Date(start + 'T00:00:00');
    var e = new Date(end + 'T00:00:00');
    return s.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
      ' \u2013 ' + e.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  }

  function addDays(iso, n) {
    var d = new Date(iso + 'T00:00:00');
    d.setDate(d.getDate() + n);
    var mm = String(d.getMonth() + 1).padStart(2, '0');
    var dd = String(d.getDate()).padStart(2, '0');
    return d.getFullYear() + '-' + mm + '-' + dd;
  }

  // Returns periodStart if it falls on a Monday, null otherwise.
  // Used by the 7 mutation/send endpoints that require ?week= (Monday token).
  function weekMonday() {
    if (!periodStart) return null;
    var d = new Date(periodStart + 'T00:00:00');
    return d.getDay() === 1 ? periodStart : null;  // getDay(): 1 = Monday
  }

  // True when the header inputs describe a standard Mon-Sun week.
  function isStandardWeek() {
    if (!periodStart || !periodEnd) return false;
    var d = new Date(periodStart + 'T00:00:00');
    if (d.getDay() !== 1) return false;
    return periodEnd === addDays(periodStart, 6);
  }

  // Single source of truth — always in sync with header inputs
  var periodStart = appEl.getAttribute('data-week-start');
  var periodEnd = appEl.getAttribute('data-week-end');

  // --- State ---
  var currentTab = 'portfolios';     // 'portfolios' | 'send'
  var portfolioNotes = [];           // from /api/reports/maintenance-notes
  var recipients = [];               // from /api/reports/maintenance-sends/recipients
  var activePortfolioId = null;
  var activeRecipientEmail = null;
  var portfolioFilter = 'all';
  var sendFilter = 'all';
  var dirty = false;

  // --- Period sync ---
  function syncInputs() {
    $('period-start').value = periodStart;
    $('period-end').value = periodEnd;
  }

  function syncGenerateButton() {
    $('generate').disabled = !periodStart || !periodEnd;
  }

  // Disable approve-all / delete-all / send actions when the period
  // is not a standard Mon-Sun week.  Generate stays enabled throughout.
  var WEEK_HINT = 'Sending is available for standard Mon\u2013Sun weeks. Use the arrows to pick a week.';
  function syncWeekActions() {
    var ok = isStandardWeek();
    $('approve-all-btn').disabled = !ok;
    $('delete-all-btn').disabled = !ok;
    $('send-all-ready-btn').disabled = !ok;
    $('approve-all-btn').title = ok ? '' : WEEK_HINT;
    $('delete-all-btn').title = ok ? '' : WEEK_HINT;
    $('send-all-ready-btn').title = ok ? '' : WEEK_HINT;
  }

  function shiftPeriod(days) {
    periodStart = addDays(periodStart, days);
    periodEnd = addDays(periodEnd, days);
    syncInputs();
    syncGenerateButton();
    syncWeekActions();
    loadAll();
  }

  // --- Data fetch ---
  function loadPortfolioNotes() {
    var url = '/reports/maintenance-notes?start_date=' + periodStart + '&end_date=' + periodEnd;
    return VestaAPI.get(url)
      .then(function (data) { portfolioNotes = data; })
      .catch(function (e) { console.error('loadPortfolioNotes', e); portfolioNotes = []; });
  }

  function loadRecipients() {
    var wk = weekMonday();
    if (!wk) { recipients = []; return Promise.resolve(); }
    return VestaAPI.get('/reports/maintenance-sends/recipients?week=' + wk)
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

    var snap = n.work_order_snapshot || {};
    var introText = snap.intro || '';

    // Build work-order fields by bucket
    var BUCKETS = [
      { key: 'open', label: 'Open' },
      { key: 'scheduled', label: 'Scheduled' },
      { key: 'completed', label: 'Completed' },
      { key: 'cancelled', label: 'Cancelled' }
    ];
    var woHtml = '';
    var hasAnyWo = false;

    BUCKETS.forEach(function (b) {
      var groups = snap[b.key];
      if (!groups || !groups.length) return;
      var woCount = 0;
      groups.forEach(function (g) { woCount += (g.work_orders || []).length; });
      if (woCount === 0) return;
      hasAnyWo = true;

      woHtml += '<div class="wo-bucket">' +
        '<div class="wo-bucket-head">' + esc(b.label) + ' (' + woCount + ')</div>';

      groups.forEach(function (g) {
        woHtml += '<div class="wo-prop-head">' + esc(g.property_address) + '</div>';
        (g.work_orders || []).forEach(function (wo) {
          var labelParts = [];
          if (wo.work_order_number) labelParts.push('#' + esc(String(wo.work_order_number)));
          if (wo.title) labelParts.push(esc(wo.title));
          var metaParts = [];
          if (g.show_unit && wo.unit_label) metaParts.push(esc(wo.unit_label));
          if (wo.vendor_name) metaParts.push(esc(wo.vendor_name));

          woHtml += '<div class="wo-card">' +
            '<div class="wo-label">' + labelParts.join(' \u2014 ') + '</div>' +
            (metaParts.length ? '<div class="wo-meta">' + metaParts.join(' &middot; ') + '</div>' : '') +
            '<textarea class="wo-summary" data-bucket="' + b.key + '" data-wo="' + esc(String(wo.work_order_number || '')) + '">' + esc(wo.ai_summary || '') + '</textarea>' +
          '</div>';
        });
      });

      woHtml += '</div>';
    });

    if (!hasAnyWo) {
      woHtml = '<div class="wo-empty">No work orders for this period.</div>';
    }

    var ownersHtml = (n.owners || []).map(function (o) {
      return '<li class="recipient"><span class="av">' + esc(initials(o.name).toUpperCase()) + '</span>' +
        '<span><span class="who">' + esc(o.name) + '</span><br><span class="em">' + esc(o.email) + '</span></span></li>';
    }).join('');

    $('detail-scroll').innerHTML =
      '<div class="pf">' +
        '<div class="block">' +
          '<div class="block-label">Intro</div>' +
          '<div class="note-edit"><textarea data-ta="intro">' + esc(introText) + '</textarea></div>' +
        '</div>' +
        '<div class="block">' +
          '<div class="block-label">Work Orders</div>' +
          woHtml +
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

    // Wire all textareas: set dirty on input
    var textareas = $('detail-scroll').querySelectorAll('textarea');
    Array.prototype.forEach.call(textareas, function (ta) {
      ta.addEventListener('input', function () {
        dirty = true;
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
            (r.all_approved && !r.is_sent && isStandardWeek() ? '<button class="btn btn-send btn-sm" data-act="send-recip" data-email="' + esc(r.recipient_email) + '">Send</button>' : '') +
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
    var stdWeek = isStandardWeek();
    $('recip-send-btn').disabled = !stdWeek || !r.all_approved || r.is_sent;
    $('test-send-btn').disabled = !stdWeek;
    $('recip-send-btn').title = stdWeek ? '' : WEEK_HINT;
    $('test-send-btn').title = stdWeek ? '' : WEEK_HINT;

    VestaAPI.get('/reports/maintenance-sends/recipients/' + encodeURIComponent(r.recipient_email) + '/preview?week=' + weekMonday())
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
    var introEl = $('detail-scroll').querySelector('textarea[data-ta="intro"]');
    var intro = introEl ? introEl.value : '';
    var edits = [];
    var woEls = $('detail-scroll').querySelectorAll('textarea.wo-summary');
    Array.prototype.forEach.call(woEls, function (el) {
      edits.push({
        bucket: el.getAttribute('data-bucket'),
        work_order_number: el.getAttribute('data-wo'),
        ai_summary: el.value
      });
    });
    VestaAPI.put('/reports/maintenance-notes/' + activePortfolioId, { intro: intro, edits: edits })
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
    var parts = [];
    var introEl = $('detail-scroll').querySelector('textarea[data-ta="intro"]');
    if (introEl && introEl.value.trim()) parts.push(introEl.value.trim());
    var woEls = $('detail-scroll').querySelectorAll('textarea.wo-summary');
    Array.prototype.forEach.call(woEls, function (el) {
      var wo = el.getAttribute('data-wo');
      var summary = el.value.trim();
      if (summary) parts.push((wo ? '#' + wo + ' ' : '') + summary);
    });
    if (parts.length) {
      navigator.clipboard.writeText(parts.join('\n\n')).then(function () { toast('Copied to clipboard.', 'ok'); });
    }
  });

  $('approve-all-btn').addEventListener('click', function () {
    var wk = weekMonday();
    if (!wk) { toast(WEEK_HINT); return; }
    VestaAPI.post('/reports/maintenance-notes/approve-all?week=' + wk, {})
      .then(function (r) {
        var msg = (r.approved || 0) + ' note(s) approved.';
        if (r.skipped_empty) msg += ' ' + r.skipped_empty + ' skipped (no content).';
        toast(msg, r.skipped_empty ? 'warn' : 'ok');
        return loadAll();
      })
      .catch(function (e) { toast('Approve all failed: ' + e.message, 'warn'); });
  });

  $('delete-all-btn').addEventListener('click', function () {
    var wk = weekMonday();
    if (!wk) { toast(WEEK_HINT); return; }
    if (!confirm('Delete all draft maintenance notes for this week?')) return;
    VestaAPI.post('/reports/maintenance-notes/delete-all?week=' + wk, {})
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
    var wk = weekMonday();
    if (!wk) { toast(WEEK_HINT); return; }
    setStatus('running', 'Sending to ' + email + '...');
    VestaAPI.post('/reports/maintenance-sends/recipients/' + encodeURIComponent(email) + '/send?week=' + wk, {})
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
    var wk = weekMonday();
    if (!wk) { toast(WEEK_HINT); return; }
    setStatus('running', 'Sending test email...');
    VestaAPI.post('/reports/maintenance-sends/recipients/' + encodeURIComponent(activeRecipientEmail) + '/test-send?week=' + wk, {})
      .then(function (r) {
        setStatus('done', r.message || 'Test sent.');
        toast(r.message || 'Test sent.', 'ok');
      })
      .catch(function (e) { setStatus('warn', 'Test send failed.'); toast('Test send failed: ' + e.message, 'warn'); });
  });

  $('send-all-ready-btn').addEventListener('click', function () {
    var wk = weekMonday();
    if (!wk) { toast(WEEK_HINT); return; }
    if (!confirm('Send to all ready recipients?')) return;
    setStatus('running', 'Sending to all ready recipients...');
    VestaAPI.post('/reports/maintenance-sends/send-all?week=' + wk, {})
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
    $('gen-modal-title').textContent = 'Generate maintenance notes for ' + formatWeek(periodStart, periodEnd) + '?';
    $('gen-modal').classList.add('show');
  }
  function closeGenerateConfirm() { $('gen-modal').classList.remove('show'); }

  function doGenerate() {
    if (!periodStart || !periodEnd) {
      toast('Set both start and end dates before generating.', 'warn');
      return;
    }
    closeGenerateConfirm();
    setStatus('running', 'Starting generation...');
    $('generate').disabled = true;
    VestaAPI.post('/reports/maintenance-notes/generate', {
      start_date: periodStart,
      end_date: periodEnd,
      dry_run: $('dry-run-check').checked,
      portfolio_name: $('portfolio-name-input').value,
    })
      .then(function () { pollProgress(); })
      .catch(function (e) {
        syncGenerateButton();
        setStatus('warn', 'Generation failed: ' + e.message);
        toast('Generation failed: ' + e.message, 'warn');
      });
  }

  function pollProgress() {
    var pName = $('portfolio-name-input').value;
    var url = '/reports/maintenance-notes/progress?start_date=' + periodStart + '&end_date=' + periodEnd;
    if (pName) url += '&portfolio_name=' + encodeURIComponent(pName);
    var poll = setInterval(function () {
      VestaAPI.get(url)
        .then(function (d) {
          var pct = d.total > 0 ? Math.round(d.generated / d.total * 100) : 0;
          setStatus('running', d.generated + ' of ' + d.total + ' (' + pct + '%)');
          updateProgressBar(pct);
          if (!d.running || d.generated >= d.total) {
            clearInterval(poll);
            syncGenerateButton();
            setStatus('done', d.generated > 0
              ? 'Generation complete \u2014 ' + d.generated + ' of ' + d.total + ' portfolios.'
              : 'Done \u2014 no portfolios had maintenance activity this period.');
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
  $('gen-confirm').addEventListener('click', doGenerate);
  $('gen-modal').addEventListener('click', function (e) { if (e.target === this) closeGenerateConfirm(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeGenerateConfirm(); });

  // --- Period controls ---
  $('period-start').addEventListener('change', function () {
    periodStart = this.value;
    syncGenerateButton();
    syncWeekActions();
    if (periodStart && periodEnd) loadAll();
  });
  $('period-end').addEventListener('change', function () {
    periodEnd = this.value;
    syncGenerateButton();
    syncWeekActions();
    if (periodStart && periodEnd) loadAll();
  });
  $('prev-week').addEventListener('click', function () { shiftPeriod(-7); });
  $('next-week').addEventListener('click', function () { shiftPeriod(7); });

  // --- Init ---
  syncInputs();
  syncGenerateButton();
  syncWeekActions();
  loadAll().then(function () { setStatus('idle', 'Ready.'); });

})();
