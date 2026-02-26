/**
 * Owner Reports — weekly vacancy report builder for property owners.
 * Rich per-property cards with leasing metrics, notes, and email workflow.
 * Auto-refreshes every 60s (skips detail while editing).
 */
document.addEventListener('DOMContentLoaded', async () => {
  // State
  var owners = [];
  var currentOwnerIndex = -1;
  var currentReport = null;  // rich report data for selected owner
  var noteMap = {};
  var lastSentMap = {};

  // Week date: Monday of the current week
  function weekMonday() {
    var d = new Date();
    var day = d.getDay();
    var diff = d.getDate() - day + (day === 0 ? -6 : 1);
    var monday = new Date(d.setDate(diff));
    return monday.toISOString().split('T')[0];
  }

  var weekDate = weekMonday();
  var dateEl = document.getElementById('report-date');
  if (dateEl) dateEl.textContent = VestaAPI.dateStr(weekDate);

  // Wire up buttons
  document.getElementById('send-all-btn').addEventListener('click', sendAllReviewed);
  document.getElementById('close-preview-btn').addEventListener('click', closePreview);

  // ── Load ────────────────────────────────────────────────────────────────

  async function loadAll() {
    try {
      var [ownerData, weekNotes, sentNotes] = await Promise.all([
        VestaAPI.get('/analytics/owners-with-vacancies'),
        VestaAPI.get('/dashboard/owner-notes?report_date=' + weekDate),
        VestaAPI.get('/dashboard/owner-notes?status=sent'),
      ]);

      owners = ownerData.items || ownerData;

      noteMap = {};
      var notes = weekNotes.items || weekNotes;
      for (var i = 0; i < notes.length; i++) {
        noteMap[notes[i].owner_id] = notes[i];
      }

      lastSentMap = {};
      var sent = sentNotes.items || sentNotes;
      for (var j = 0; j < sent.length; j++) {
        var s = sent[j];
        if (s.sent_at && (!lastSentMap[s.owner_id] || s.sent_at > lastSentMap[s.owner_id])) {
          lastSentMap[s.owner_id] = s.sent_at;
        }
      }

      if (!owners.length) {
        VestaAPI.render('owner-list', '<div class="empty-state">No owners with vacancies</div>');
        return;
      }

      renderOwnerList();
      if (currentOwnerIndex >= 0 && !isEditing()) {
        loadOwnerDetail(owners[currentOwnerIndex]);
      }
    } catch (err) {
      console.error('Owner Reports load error:', err);
      VestaAPI.render('owner-list', '<div class="loading">Error loading owners</div>');
    }
  }

  await loadAll();
  setInterval(loadAll, 60000);

  // ── Edit Guard ──────────────────────────────────────────────────────────

  function isEditing() {
    var a = document.activeElement;
    if (!a) return false;
    var t = a.tagName.toLowerCase();
    return t === 'textarea' || t === 'input';
  }

  // ── Owner List ──────────────────────────────────────────────────────────

  function renderOwnerList() {
    var html = '';
    for (var i = 0; i < owners.length; i++) {
      var o = owners[i];
      var note = noteMap[o.owner_id];
      var statusClass = note ? note.status : 'none';
      var activeClass = i === currentOwnerIndex ? ' active' : '';

      var lastSentHtml = '';
      var sentAt = lastSentMap[o.owner_id];
      if (sentAt) {
        lastSentHtml = '<div class="owner-last-sent">Sent ' +
          VestaAPI.dateStr(sentAt.split('T')[0]) + '</div>';
      }

      html +=
        '<div class="owner-list-item' + activeClass + '" data-index="' + i + '">' +
          '<div>' +
            '<span class="status-dot ' + esc(statusClass) + '"></span>' +
            esc(o.owner_name) +
            lastSentHtml +
          '</div>' +
          '<span class="count-badge">' + o.vacant_unit_count + '</span>' +
        '</div>';
    }
    VestaAPI.render('owner-list', html);

    var items = document.querySelectorAll('#owner-list .owner-list-item');
    for (var j = 0; j < items.length; j++) {
      items[j].addEventListener('click', onOwnerClick);
    }
  }

  function onOwnerClick(e) {
    var idx = parseInt(e.currentTarget.getAttribute('data-index'), 10);
    selectOwner(idx);
  }

  function selectOwner(idx) {
    currentOwnerIndex = idx;
    renderOwnerList();
    loadOwnerDetail(owners[idx]);
  }

  // ── Owner Detail ────────────────────────────────────────────────────────

  async function loadOwnerDetail(owner) {
    VestaAPI.render('owner-detail', '<div class="loading">Loading report data...</div>');

    try {
      var report = await VestaAPI.get('/analytics/owner-report-detail/' + owner.owner_id + '?week_date=' + weekDate);
      currentReport = report;

      var note = noteMap[owner.owner_id] || null;
      if (!note) {
        var notesData = await VestaAPI.get('/dashboard/owner-notes?owner_id=' + owner.owner_id + '&report_date=' + weekDate);
        var notes = notesData.items || notesData;
        note = notes.length ? notes[0] : null;
        if (note) {
          noteMap[owner.owner_id] = note;
          renderOwnerList();
        }
      }

      renderOwnerDetail(owner, report, note);
    } catch (err) {
      console.error('Owner detail load error:', err);
      VestaAPI.render('owner-detail', '<div class="loading">Error loading report</div>');
    }
  }

  function renderOwnerDetail(owner, report, note) {
    var notesText = note ? (note.notes_text || '') : '';
    var emailBody = note ? (note.email_body || '') : '';
    var emailSubject = note ? (note.email_subject || '') : '';
    var noteId = note ? note.id : '';
    var statusLabel = note ? note.status : 'new';

    var html =
      '<h2 class="section-title">' + esc(owner.owner_name) + '</h2>' +
      '<p style="font-size:.85rem;color:var(--text-muted);margin-bottom:1rem;">' +
        esc(owner.owner_email || 'No email on file') +
        ' &middot; Status: <strong>' + esc(statusLabel) + '</strong>' +
        (note && note.opened_at ? ' &middot; <span style="color:#28a745;">Opened</span>' : '') +
      '</p>';

    // Email subject + summary fields
    html +=
      '<div class="section-title" style="font-size:.95rem;">Email Subject</div>' +
      '<input type="text" id="email-subject" value="' + escAttr(emailSubject || 'Weekly Vacancy Update \u2014 ' + VestaAPI.dateStr(weekDate)) + '" ' +
        'style="width:100%;padding:.5rem .75rem;border:1px solid var(--border-medium);border-radius:6px;font-size:.85rem;margin-bottom:.75rem;">' +
      '<div class="section-title" style="font-size:.95rem;">Summary for Owner <span style="font-weight:400;color:var(--text-muted);">(appears in email)</span></div>' +
      '<textarea id="email-body" placeholder="Weekly summary message for the owner...">' +
        esc(emailBody) + '</textarea>';

    // Per-property cards
    html += '<div class="section-title" style="font-size:.95rem;">Properties (' + report.units.length + ')</div>';

    for (var i = 0; i < report.units.length; i++) {
      html += renderUnitCard(report.units[i]);
    }

    // Internal notes
    html +=
      '<div class="section-title" style="font-size:.95rem;margin-top:1rem;">Internal Notes <span style="font-weight:400;color:var(--text-muted);">(not in email)</span></div>' +
      '<textarea id="notes-text" placeholder="Internal notes...">' + esc(notesText) + '</textarea>' +
      '<input type="hidden" id="note-id" value="' + (noteId || '') + '">';

    // Action buttons
    html +=
      '<div style="display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.5rem;">' +
        '<button class="btn btn-secondary" id="btn-save-draft">Save Draft</button>' +
        '<button class="btn btn-primary" id="btn-mark-reviewed">Mark Reviewed</button>' +
        '<button class="btn btn-secondary" id="btn-preview-email">Preview Email</button>' +
        '<button class="btn btn-success" id="btn-send-email">Send Email</button>' +
        '<button class="btn btn-primary" id="btn-save-next">Save &amp; Next</button>' +
      '</div>';

    VestaAPI.render('owner-detail', html);

    // Bind buttons
    document.getElementById('btn-save-draft').addEventListener('click', function () { saveNote(owner, 'draft'); });
    document.getElementById('btn-mark-reviewed').addEventListener('click', function () { saveNote(owner, 'reviewed'); });
    document.getElementById('btn-preview-email').addEventListener('click', function () { previewEmail(owner); });
    document.getElementById('btn-send-email').addEventListener('click', function () { sendEmail(owner); });
    document.getElementById('btn-save-next').addEventListener('click', function () { saveNoteAndNext(owner); });

    // Bind property note save buttons
    var noteButtons = document.querySelectorAll('.btn-save-prop-note');
    for (var k = 0; k < noteButtons.length; k++) {
      noteButtons[k].addEventListener('click', onSavePropNote);
    }
  }

  // ── Unit Card ───────────────────────────────────────────────────────────

  function renderUnitCard(u) {
    var flagged = u.leads_per_active_day < 0.5;
    var borderColor = flagged ? 'var(--red-accent)' : 'var(--accent)';

    var html = '<div class="card" style="border-left:3px solid ' + borderColor + ';margin-bottom:1rem;padding:1rem;">';

    // Header
    html +=
      '<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:.75rem;">' +
        '<div>' +
          '<strong style="font-size:.95rem;">' + esc(u.address) + '</strong>' +
          '<div style="font-size:.78rem;color:var(--text-muted);">' +
            esc(u.city) + ', ' + esc(u.state) + ' ' + esc(u.zip_code) +
            (u.bedrooms ? ' &middot; ' + u.bedrooms + 'BR' : '') +
          '</div>' +
        '</div>' +
        '<div style="text-align:right;">' +
          (u.days_on_market != null ? '<span class="dom-badge ' + domClass(u.days_on_market) + '">' + u.days_on_market + ' DOM</span>' : '') +
          '<div style="font-size:.78rem;color:var(--text-muted);margin-top:2px;">' + VestaAPI.num(u.leads_per_active_day) + ' leads/day</div>' +
        '</div>' +
      '</div>';

    // Metrics grid
    html += '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:.5rem;margin-bottom:.75rem;">';
    html += metricsBox('This Week', u.weekly, u.prev_weekly);
    html += metricsBox('Last Week', u.prev_weekly, null);
    html += metricsBox('All-Time', u.all_time, null);
    html += '</div>';

    // Market context row
    html += '<div style="display:flex;gap:1rem;flex-wrap:wrap;font-size:.8rem;color:var(--text-muted);margin-bottom:.75rem;padding:.5rem;background:#f8f9ff;border-radius:4px;">';
    html += '<span>List: <strong style="color:var(--text-primary);">' + VestaAPI.$(u.current_list_price) + '</strong></span>';
    html += '<span>Target: <strong style="color:var(--text-primary);">' + VestaAPI.$(u.target_rent) + '</strong></span>';
    if (u.market_context.zip_avg_list_price) {
      html += '<span>Zip Avg: <strong style="color:var(--text-primary);">' + VestaAPI.$(u.market_context.zip_avg_list_price) + '</strong></span>';
    }
    if (u.market_context.zip_avg_dom != null) {
      html += '<span>Zip DOM: <strong style="color:var(--text-primary);">' + u.market_context.zip_avg_dom + '</strong></span>';
    }
    html += '</div>';

    // Showing feedback
    if (u.showing_feedback && u.showing_feedback.length) {
      html += '<div style="margin-bottom:.75rem;">' +
        '<div style="font-size:.75rem;text-transform:uppercase;color:var(--text-muted);letter-spacing:.3px;margin-bottom:.25rem;font-weight:600;">Showing Feedback</div>';
      for (var f = 0; f < u.showing_feedback.length; f++) {
        var fb = u.showing_feedback[f];
        html += '<div style="font-size:.82rem;padding-left:.5rem;border-left:2px solid var(--accent);margin-bottom:.35rem;line-height:1.4;">';
        if (fb.prospect_name) html += '<strong>' + esc(fb.prospect_name) + '</strong>: ';
        html += esc(fb.feedback_summary);
        html += '</div>';
      }
      html += '</div>';
    }

    // Upcoming showings
    if (u.upcoming_showings && u.upcoming_showings.length) {
      html += '<div style="margin-bottom:.75rem;">' +
        '<div style="font-size:.75rem;text-transform:uppercase;color:var(--text-muted);letter-spacing:.3px;margin-bottom:.25rem;font-weight:600;">Upcoming Showings</div>';
      for (var s = 0; s < u.upcoming_showings.length; s++) {
        var sh = u.upcoming_showings[s];
        html += '<div style="font-size:.82rem;">' + VestaAPI.dateStr(sh.date) + ' at ' + esc(sh.time) +
          (sh.prospect_name ? ' \u2014 ' + esc(sh.prospect_name) : '') + '</div>';
      }
      html += '</div>';
    }

    // Price recommendation
    if (u.price_recommendation && u.price_recommendation.recommended) {
      html += '<div style="background:#fff3cd;border-radius:4px;padding:.5rem .75rem;font-size:.82rem;color:#856404;margin-bottom:.75rem;border-left:3px solid #856404;">' +
        esc(u.price_recommendation.message) + '</div>';
    }

    // Property note
    html += '<div style="margin-bottom:.5rem;">' +
      '<div style="font-size:.75rem;text-transform:uppercase;color:var(--text-muted);letter-spacing:.3px;margin-bottom:.25rem;font-weight:600;">Property Note <span style="font-weight:400;">(goes in email)</span></div>' +
      '<div style="display:flex;gap:.5rem;">' +
        '<textarea class="prop-note-text" data-unit-id="' + u.unit_id + '" style="min-height:60px;flex:1;margin-bottom:0;" placeholder="Note for this property...">' +
          esc(u.property_note) + '</textarea>' +
        '<button class="btn btn-secondary btn-sm btn-save-prop-note" data-unit-id="' + u.unit_id + '" style="align-self:flex-start;">Save</button>' +
      '</div>';

    // Previous notes
    if (u.prev_notes && u.prev_notes.length) {
      html += '<details style="margin-top:.35rem;font-size:.78rem;"><summary style="cursor:pointer;color:var(--text-muted);">Previous notes (' + u.prev_notes.length + ')</summary>';
      for (var p = 0; p < u.prev_notes.length; p++) {
        var pn = u.prev_notes[p];
        html += '<div style="padding:.35rem 0;border-bottom:1px solid var(--border);color:var(--text-muted);">' +
          '<strong>' + VestaAPI.dateStr(pn.week_date) + '</strong> (' + esc(pn.author) + '): ' + esc(pn.note_text) + '</div>';
      }
      html += '</details>';
    }
    html += '</div>';

    // Zillow link
    if (u.zillow_url) {
      html += '<a href="' + esc(u.zillow_url) + '" target="_blank" style="font-size:.8rem;color:var(--accent);text-decoration:none;">View on Zillow &rarr;</a>';
    }

    html += '</div>';
    return html;
  }

  function metricsBox(title, data, prev) {
    var html = '<div style="background:#fafafa;border-radius:4px;padding:.5rem .6rem;font-size:.78rem;">' +
      '<div style="font-weight:600;margin-bottom:.25rem;font-size:.7rem;text-transform:uppercase;color:var(--text-muted);letter-spacing:.3px;">' + title + '</div>';

    var rows = [
      ['Leads', data.leads, prev ? prev.leads : null],
      ['Show', data.showings, prev ? prev.showings : null],
      ['Miss', data.missed, prev ? prev.missed : null],
      ['Apps', data.apps, prev ? prev.apps : null],
    ];

    for (var i = 0; i < rows.length; i++) {
      var label = rows[i][0];
      var val = rows[i][1];
      var pval = rows[i][2];
      var wow = '';
      if (pval != null && pval !== val) {
        var diff = val - pval;
        var cls = diff > 0 ? 'positive' : 'negative';
        // For missed, positive diff is bad
        if (label === 'Miss') cls = diff > 0 ? 'negative' : 'positive';
        wow = ' <span class="change ' + cls + '" style="font-size:.7rem;">' + (diff > 0 ? '+' : '') + diff + '</span>';
      }
      html += '<div style="display:flex;justify-content:space-between;"><span>' + label + '</span><span style="font-weight:600;">' + val + wow + '</span></div>';
    }
    html += '</div>';
    return html;
  }

  function domClass(dom) {
    if (dom >= 30) return 'dom-danger';
    if (dom >= 14) return 'dom-warn';
    return 'dom-ok';
  }

  // ── Property Notes ──────────────────────────────────────────────────────

  async function onSavePropNote(e) {
    var unitId = e.currentTarget.getAttribute('data-unit-id');
    var textarea = document.querySelector('.prop-note-text[data-unit-id="' + unitId + '"]');
    if (!textarea) return;

    var text = textarea.value.trim();
    if (!text) return;

    try {
      await VestaAPI.post('/dashboard/property-notes', {
        unit_id: parseInt(unitId, 10),
        week_date: weekDate,
        author: 'Staff',
        note_text: text,
      });
      VestaAPI.toast('Note saved', 'success');
    } catch (err) {
      console.error('Save property note error:', err);
      VestaAPI.toast('Error saving note', 'error');
    }
  }

  // ── Save Logic ──────────────────────────────────────────────────────────

  async function saveNote(owner, status) {
    var noteId = document.getElementById('note-id').value;
    var payload = {
      notes_text: document.getElementById('notes-text').value,
      email_subject: document.getElementById('email-subject').value,
      email_body: document.getElementById('email-body').value,
    };

    try {
      var saved;
      if (noteId) {
        payload.status = status;
        saved = await VestaAPI.put('/dashboard/owner-notes/' + noteId, payload);
      } else {
        payload.owner_id = owner.owner_id;
        payload.report_date = weekDate;
        payload.status = status;
        saved = await VestaAPI.post('/dashboard/owner-notes', payload);
      }

      noteMap[owner.owner_id] = saved;
      document.getElementById('note-id').value = saved.id || '';
      renderOwnerList();
      VestaAPI.toast('Note saved (' + status + ')', 'success');
      return saved;
    } catch (err) {
      console.error('Save note error:', err);
      VestaAPI.toast('Error: ' + err.message, 'error');
      return null;
    }
  }

  async function previewEmail(owner) {
    var noteId = document.getElementById('note-id').value;
    if (!noteId) {
      var saved = await saveNote(owner, 'draft');
      if (!saved) return;
      noteId = saved.id;
    } else {
      // Save latest content before preview
      await saveNote(owner, noteMap[owner.owner_id] ? noteMap[owner.owner_id].status : 'draft');
    }

    try {
      var result = await VestaAPI.post('/dashboard/owner-notes/' + noteId + '/preview', {});
      var modal = document.getElementById('email-preview-modal');
      var iframe = document.getElementById('preview-iframe');
      modal.style.display = 'block';
      iframe.srcdoc = result.html;
    } catch (err) {
      console.error('Preview error:', err);
      VestaAPI.toast('Error loading preview', 'error');
    }
  }

  function closePreview() {
    document.getElementById('email-preview-modal').style.display = 'none';
  }

  async function sendEmail(owner) {
    var noteId = document.getElementById('note-id').value;

    try {
      if (!noteId) {
        var saved = await saveNote(owner, 'reviewed');
        if (!saved) return;
        noteId = saved.id;
      }

      var result = await VestaAPI.post('/dashboard/owner-notes/' + noteId + '/send', {});
      if (result.status === 'error') {
        VestaAPI.toast('Error: ' + (result.detail || 'Send failed'), 'error');
        return;
      }

      noteMap[owner.owner_id] = result;
      if (result.sent_at) {
        lastSentMap[owner.owner_id] = result.sent_at;
      }

      renderOwnerList();
      loadOwnerDetail(owner);
      VestaAPI.toast('Email sent to ' + owner.owner_name, 'success');
    } catch (err) {
      console.error('Send email error:', err);
      VestaAPI.toast('Error: ' + err.message, 'error');
    }
  }

  async function saveNoteAndNext(owner) {
    var saved = await saveNote(owner, 'draft');
    if (!saved) return;

    var nextIndex = currentOwnerIndex + 1;
    if (nextIndex < owners.length) {
      selectOwner(nextIndex);
    } else {
      VestaAPI.toast('All owners reviewed', 'success');
    }
  }

  // ── Send All Reviewed ───────────────────────────────────────────────────

  async function sendAllReviewed() {
    var reviewedNotes = [];
    var keys = Object.keys(noteMap);
    for (var i = 0; i < keys.length; i++) {
      if (noteMap[keys[i]].status === 'reviewed') {
        reviewedNotes.push(noteMap[keys[i]]);
      }
    }

    if (!reviewedNotes.length) {
      VestaAPI.toast('No reviewed notes to send', 'error');
      return;
    }

    var sentCount = 0;
    for (var j = 0; j < reviewedNotes.length; j++) {
      try {
        var result = await VestaAPI.post('/dashboard/owner-notes/' + reviewedNotes[j].id + '/send', {});
        if (result.status !== 'error') {
          noteMap[result.owner_id] = result;
          if (result.sent_at) lastSentMap[result.owner_id] = result.sent_at;
          sentCount++;
        }
      } catch (err) {
        console.error('Send error for note ' + reviewedNotes[j].id + ':', err);
      }
    }

    renderOwnerList();
    VestaAPI.toast('Sent ' + sentCount + ' of ' + reviewedNotes.length + ' emails', 'success');

    if (currentOwnerIndex >= 0) {
      loadOwnerDetail(owners[currentOwnerIndex]);
    }
  }

  // ── Helpers ─────────────────────────────────────────────────────────────

  function esc(text) {
    if (text == null) return '';
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(String(text)));
    return div.innerHTML;
  }

  function escAttr(text) {
    if (text == null) return '';
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/"/g, '&quot;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }
});
