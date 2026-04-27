/**
 * Owner Notes — split-panel review workflow.
 * Left: owner list with word counts.
 * Right: editable note textarea, copy + save buttons.
 * Auto-refreshes every 60s (edit guard prevents re-render while typing).
 */
document.addEventListener('DOMContentLoaded', function () {

  // ── State ──────────────────────────────────────────────────────────────

  var notes = [];
  var _activeNoteId = null;
  var _currentMonth = null;
  var _dirty = false;

  // ── Edit Guard ──────────────────────────────────────────────────────────

  function isEditing() {
    var a = document.activeElement;
    if (!a) return false;
    var t = a.tagName.toLowerCase();
    return t === 'textarea' || t === 'input';
  }

  // ── Month selector ─────────────────────────────────────────────────────

  function loadMonths() {
    VestaAPI.get('/reports/owner-notes/months').then(function (data) {
      var months = Array.isArray(data) ? data : [];
      var sel = document.getElementById('on-month-select');
      if (!sel) return;
      if (!months.length) {
        sel.innerHTML = '<option value="">No notes available</option>';
        return;
      }
      var html = '<option value="">Select a month...</option>';
      for (var i = 0; i < months.length; i++) {
        html += '<option value="' + esc(months[i].value) + '">' + esc(months[i].label) + '</option>';
      }
      sel.innerHTML = html;
      // Auto-select most recent month
      sel.value = months[0].value;
      _currentMonth = months[0].value;
      loadNotes(_currentMonth);
    }).catch(function (err) {
      console.error('Owner notes: load months error', err);
    });
  }

  var monthSel = document.getElementById('on-month-select');
  if (monthSel) {
    monthSel.addEventListener('change', function () {
      _currentMonth = this.value || null;
      _activeNoteId = null;
      _dirty = false;
      if (_currentMonth) {
        loadNotes(_currentMonth);
      } else {
        notes = [];
        renderList();
        renderRight();
      }
    });
  }

  // ── Load notes ─────────────────────────────────────────────────────────

  function loadNotes(month) {
    VestaAPI.get('/reports/owner-notes?month=' + month).then(function (data) {
      var items = Array.isArray(data) ? data : [];
      notes = items;
      if (!isEditing()) {
        renderList();
        if (_activeNoteId !== null) renderRight();
      }
    }).catch(function (err) {
      console.error('Owner notes: load notes error', err);
    });
  }

  // ── Render: left panel ─────────────────────────────────────────────────

  function renderList() {
    var list = document.getElementById('on-owner-list');
    if (!list) return;
    if (!notes.length) {
      list.innerHTML = '<div style="padding:1rem;color:#9ca3af;font-size:.85rem;">No notes generated for this month yet.</div>';
      return;
    }
    var html = '';
    for (var i = 0; i < notes.length; i++) {
      var n = notes[i];
      var active = (_activeNoteId === n.id) ? ' active' : '';
      html += '<div class="me-owner-row' + active + '" data-id="' + n.id + '">';
      html += '<div class="me-owner-name">' + esc(n.owner_name) + '</div>';
      html += '<div class="me-owner-meta">' + esc(n.portfolio_name) + ' &middot; ' + n.word_count + ' words</div>';
      html += '</div>';
    }
    list.innerHTML = html;
    var rows = list.querySelectorAll('.me-owner-row');
    for (var j = 0; j < rows.length; j++) {
      rows[j].addEventListener('click', onRowClick);
    }
  }

  // ── Render: right panel ────────────────────────────────────────────────

  function renderRight() {
    var panel = document.getElementById('on-right-panel');
    if (!panel) return;

    var note = _activeNoteId !== null ? getNoteById(_activeNoteId) : null;
    if (!note) {
      panel.innerHTML = '<div class="me-right-empty">Select an owner to view or edit their note.</div>';
      return;
    }

    var html = '';

    // Heading
    html += '<div style="margin-bottom:1rem;">';
    html += '<strong style="font-size:1rem;">' + esc(note.owner_name) + '</strong>';
    html += '<span style="font-size:.8rem;color:#6b7280;margin-left:.6rem;">' + esc(note.portfolio_name) + '</span>';
    html += '<span style="font-size:.75rem;color:#9ca3af;margin-left:.6rem;">' + esc(note.report_month) + '</span>';
    html += '</div>';

    // Note textarea
    html += '<label class="me-body-label">Note <span style="font-weight:400;color:#9ca3af;">(AI-drafted, editable)</span></label>';
    html += '<textarea class="me-body-textarea" id="on-note-textarea">' + esc(note.generated_note) + '</textarea>';

    // Actions
    html += '<div class="me-actions">';
    html += '<button class="btn btn-primary btn-sm" id="on-copy-btn">Copy</button>';
    html += '<button class="btn btn-secondary btn-sm" id="on-save-btn">Save</button>';
    html += '<span class="me-save-status" id="on-save-status"></span>';
    html += '</div>';

    panel.innerHTML = html;

    var ta = document.getElementById('on-note-textarea');
    if (ta) {
      ta.addEventListener('input', function () { _dirty = true; });
      ta.addEventListener('blur', function () { if (_dirty) saveNote(); });
    }

    var saveBtn = document.getElementById('on-save-btn');
    if (saveBtn) saveBtn.addEventListener('click', saveNote);

    var copyBtn = document.getElementById('on-copy-btn');
    if (copyBtn) copyBtn.addEventListener('click', copyNote);
  }

  // ── Events ─────────────────────────────────────────────────────────────

  function onRowClick(e) {
    var id = parseInt(e.currentTarget.getAttribute('data-id'), 10);
    _activeNoteId = id;
    _dirty = false;
    renderList();
    renderRight();
  }

  // ── Save ───────────────────────────────────────────────────────────────

  function saveNote() {
    var note = getNoteById(_activeNoteId);
    if (!note) return;
    var ta = document.getElementById('on-note-textarea');
    var newText = ta ? ta.value : note.generated_note;

    VestaAPI.put('/reports/owner-notes/' + note.id, { generated_note: newText }).then(function (updated) {
      updateNoteInState(updated);
      _dirty = false;
      var status = document.getElementById('on-save-status');
      if (status) {
        status.textContent = 'Saved';
        setTimeout(function () { if (status) status.textContent = ''; }, 2000);
      }
    }).catch(function (err) {
      console.error('Owner notes: save error', err);
    });
  }

  // ── Copy ───────────────────────────────────────────────────────────────

  function copyNote() {
    var ta = document.getElementById('on-note-textarea');
    if (!ta) return;
    var btn = document.getElementById('on-copy-btn');
    navigator.clipboard.writeText(ta.value).then(function () {
      if (btn) {
        btn.textContent = 'Copied!';
        setTimeout(function () { if (btn) btn.textContent = 'Copy'; }, 2000);
      }
    }).catch(function (err) {
      console.error('Owner notes: clipboard error', err);
    });
  }

  // ── Helpers ─────────────────────────────────────────────────────────────

  function getNoteById(id) {
    for (var i = 0; i < notes.length; i++) {
      if (notes[i].id === id) return notes[i];
    }
    return null;
  }

  function updateNoteInState(updated) {
    for (var i = 0; i < notes.length; i++) {
      if (notes[i].id === updated.id) {
        notes[i] = updated;
        return;
      }
    }
    notes.push(updated);
  }

  function esc(text) {
    if (text == null) return '';
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(String(text)));
    return div.innerHTML;
  }

  // ── Auto-refresh ────────────────────────────────────────────────────────

  setInterval(function () {
    if (!isEditing() && _currentMonth) loadNotes(_currentMonth);
  }, 60000);

  // ── Init ────────────────────────────────────────────────────────────────

  loadMonths();

});
