/**
 * Owner Reports — weekly vacancy report builder for property owners.
 * Loads owners with vacancies, lets staff write notes/emails per owner,
 * then send individually or in bulk.
 */
document.addEventListener('DOMContentLoaded', async () => {
  // State
  var owners = [];
  var currentOwnerIndex = -1;
  var noteMap = {};      // owner_id -> today's note object
  var lastSentMap = {};  // owner_id -> most recent sent_at ISO string

  // Show today's date in subtitle
  var todayStr = VestaAPI.today();
  var dateEl = document.getElementById('report-date');
  if (dateEl) dateEl.textContent = VestaAPI.dateStr(todayStr);

  // Wire up Send All Reviewed button
  document.getElementById('send-all-btn').addEventListener('click', sendAllReviewed);

  try {
    // Parallel fetch: owners, today's notes, and all sent notes (for "last sent" dates)
    var [ownerData, todayNotes, sentNotes] = await Promise.all([
      VestaAPI.get('/analytics/owners-with-vacancies'),
      VestaAPI.get('/dashboard/owner-notes?report_date=' + todayStr),
      VestaAPI.get('/dashboard/owner-notes?status=sent'),
    ]);

    owners = ownerData.items || ownerData;

    // Build noteMap from today's notes
    var notes = todayNotes.items || todayNotes;
    for (var i = 0; i < notes.length; i++) {
      noteMap[notes[i].owner_id] = notes[i];
    }

    // Build lastSentMap from all sent notes (most recent sent_at per owner)
    var sent = sentNotes.items || sentNotes;
    for (var j = 0; j < sent.length; j++) {
      var s = sent[j];
      if (s.sent_at) {
        if (!lastSentMap[s.owner_id] || s.sent_at > lastSentMap[s.owner_id]) {
          lastSentMap[s.owner_id] = s.sent_at;
        }
      }
    }

    if (!owners.length) {
      VestaAPI.render('owner-list', '<div class="empty-state">No owners with vacancies</div>');
      return;
    }

    renderOwnerList();
  } catch (err) {
    console.error('Owner Reports load error:', err);
    VestaAPI.render('owner-list', '<div class="loading">Error loading owners</div>');
  }

  // ── Owner List ────────────────────────────────────────────────────────────

  function renderOwnerList() {
    var html = '';
    for (var i = 0; i < owners.length; i++) {
      var o = owners[i];
      var note = noteMap[o.owner_id];
      var statusClass = note ? note.status : 'none';
      var activeClass = i === currentOwnerIndex ? ' active' : '';

      // "Last sent" date line
      var lastSentHtml = '';
      var sentAt = lastSentMap[o.owner_id];
      if (sentAt) {
        lastSentHtml =
          '<div class="owner-last-sent">Sent ' +
          VestaAPI.dateStr(sentAt.split('T')[0]) +
          '</div>';
      }

      html +=
        '<div class="owner-list-item' + activeClass + '" data-index="' + i + '">' +
          '<div>' +
            '<span class="status-dot ' + escapeHtml(statusClass) + '"></span>' +
            escapeHtml(o.owner_name) +
            lastSentHtml +
          '</div>' +
          '<span class="count-badge">' + o.vacant_unit_count + '</span>' +
        '</div>';
    }
    VestaAPI.render('owner-list', html);

    // Bind click handlers
    var items = document.querySelectorAll('#owner-list .owner-list-item');
    for (var j = 0; j < items.length; j++) {
      items[j].addEventListener('click', onOwnerClick);
    }
  }

  function onOwnerClick(e) {
    var el = e.currentTarget;
    var idx = parseInt(el.getAttribute('data-index'), 10);
    selectOwner(idx);
  }

  function selectOwner(idx) {
    currentOwnerIndex = idx;
    renderOwnerList();
    loadOwnerDetail(owners[idx]);
  }

  // ── Owner Detail ──────────────────────────────────────────────────────────

  async function loadOwnerDetail(owner) {
    VestaAPI.render('owner-detail', '<div class="loading">Loading owner details...</div>');

    try {
      // If we already have a note from the preload, use it
      var note = noteMap[owner.owner_id] || null;

      // If not preloaded, fetch fresh
      if (!note) {
        var notesData = await VestaAPI.get(
          '/dashboard/owner-notes?owner_id=' + owner.owner_id +
          '&report_date=' + todayStr
        );
        var notes = notesData.items || notesData;
        note = notes.length ? notes[0] : null;
        if (note) {
          noteMap[owner.owner_id] = note;
          renderOwnerList();
        }
      }

      renderOwnerDetail(owner, note);
    } catch (err) {
      console.error('Owner detail load error:', err);
      VestaAPI.render('owner-detail', '<div class="loading">Error loading owner details</div>');
    }
  }

  function renderOwnerDetail(owner, note) {
    var notesText = note ? (note.notes_text || '') : '';
    var emailSubject = note ? (note.email_subject || '') : '';
    var emailBody = note ? (note.email_body || '') : '';
    var noteId = note ? note.id : '';
    var statusLabel = note ? note.status : 'new';

    // Vacant units mini table
    var units = owner.vacant_units || [];
    var unitsHtml = '';
    if (units.length) {
      unitsHtml =
        '<table>' +
          '<thead><tr>' +
            '<th>Address</th><th>City</th><th>Beds</th><th>Target Rent</th>' +
          '</tr></thead>' +
          '<tbody>';
      for (var i = 0; i < units.length; i++) {
        var u = units[i];
        unitsHtml +=
          '<tr>' +
            '<td>' + escapeHtml(u.address || '\u2014') + '</td>' +
            '<td>' + escapeHtml(u.city || '\u2014') + '</td>' +
            '<td>' + (u.bedrooms != null ? u.bedrooms : '\u2014') + '</td>' +
            '<td>' + VestaAPI.$(u.target_rent) + '</td>' +
          '</tr>';
      }
      unitsHtml += '</tbody></table>';
    } else {
      unitsHtml = '<div class="empty-state">No vacant units</div>';
    }

    var html =
      '<h2 class="section-title">' + escapeHtml(owner.owner_name) + '</h2>' +
      '<p style="font-size:.85rem;color:var(--text-muted);margin-bottom:1rem;">' +
        escapeHtml(owner.owner_email || 'No email on file') +
        ' &middot; Status: <strong>' + escapeHtml(statusLabel) + '</strong>' +
      '</p>' +

      '<div class="section-title" style="font-size:.95rem;">Vacant Units</div>' +
      unitsHtml +

      '<div class="section-title" style="font-size:.95rem;margin-top:1rem;">Internal Notes</div>' +
      '<textarea id="notes-text" placeholder="Internal notes about this owner&#39;s vacancies...">' +
        escapeHtml(notesText) +
      '</textarea>' +

      '<div class="section-title" style="font-size:.95rem;">Email Subject</div>' +
      '<input type="text" id="email-subject" value="' + escapeAttr(emailSubject) + '" ' +
        'placeholder="Weekly vacancy update" ' +
        'style="width:100%;padding:.5rem .75rem;border:1px solid var(--border-medium);border-radius:6px;font-size:.85rem;margin-bottom:.75rem;">' +

      '<div class="section-title" style="font-size:.95rem;">Email Body</div>' +
      '<textarea id="email-body" placeholder="Email body to send to owner...">' +
        escapeHtml(emailBody) +
      '</textarea>' +

      '<input type="hidden" id="note-id" value="' + (noteId || '') + '">' +

      '<div style="display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.5rem;">' +
        '<button class="btn btn-secondary" id="btn-save-draft">Save Draft</button>' +
        '<button class="btn btn-primary" id="btn-mark-reviewed">Mark Reviewed</button>' +
        '<button class="btn btn-success" id="btn-send-email">Send Email</button>' +
        '<button class="btn btn-primary" id="btn-save-next">Save &amp; Next</button>' +
      '</div>';

    VestaAPI.render('owner-detail', html);

    // Bind button handlers
    document.getElementById('btn-save-draft').addEventListener('click', function () {
      saveNote(owner, 'draft');
    });
    document.getElementById('btn-mark-reviewed').addEventListener('click', function () {
      saveNote(owner, 'reviewed');
    });
    document.getElementById('btn-send-email').addEventListener('click', function () {
      sendEmail(owner);
    });
    document.getElementById('btn-save-next').addEventListener('click', function () {
      saveNoteAndNext(owner);
    });
  }

  // ── Save Logic ────────────────────────────────────────────────────────────

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
        payload.report_date = todayStr;
        payload.status = status;
        saved = await VestaAPI.post('/dashboard/owner-notes', payload);
      }

      // Update local state
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

  async function sendEmail(owner) {
    var noteId = document.getElementById('note-id').value;

    try {
      // Save first if no note exists
      if (!noteId) {
        var saved = await saveNote(owner, 'reviewed');
        if (!saved) return;
        noteId = saved.id;
      }

      var result = await VestaAPI.post('/dashboard/owner-notes/' + noteId + '/send', {});
      noteMap[owner.owner_id] = result;

      // Update lastSentMap
      if (result.sent_at) {
        lastSentMap[owner.owner_id] = result.sent_at;
      }

      renderOwnerList();
      // Refresh detail to show updated status
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

    // Move to next owner in list
    var nextIndex = currentOwnerIndex + 1;
    if (nextIndex < owners.length) {
      selectOwner(nextIndex);
    } else {
      VestaAPI.toast('All owners reviewed', 'success');
    }
  }

  // ── Send All Reviewed ─────────────────────────────────────────────────────

  async function sendAllReviewed() {
    // Collect all reviewed notes from noteMap
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
        var result = await VestaAPI.post(
          '/dashboard/owner-notes/' + reviewedNotes[j].id + '/send', {}
        );
        noteMap[result.owner_id] = result;
        if (result.sent_at) {
          lastSentMap[result.owner_id] = result.sent_at;
        }
        sentCount++;
      } catch (err) {
        console.error('Send error for note ' + reviewedNotes[j].id + ':', err);
      }
    }

    renderOwnerList();
    VestaAPI.toast('Sent ' + sentCount + ' of ' + reviewedNotes.length + ' emails', 'success');

    // Reload current detail if one is selected
    if (currentOwnerIndex >= 0) {
      loadOwnerDetail(owners[currentOwnerIndex]);
    }
  }

  // ── Helpers ─────────────────────────────────────────────────────────────

  function escapeHtml(text) {
    if (text == null) return '';
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(String(text)));
    return div.innerHTML;
  }

  function escapeAttr(text) {
    if (text == null) return '';
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/"/g, '&quot;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }
});
