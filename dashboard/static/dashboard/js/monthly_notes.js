/**
 * Monthly Owner Notes — generate, review, approve, and send AI-drafted notes.
 * Left panel: owner/portfolio list with status badges and approve/send actions.
 * Right panel: editable note textarea, financial summary, copy/save/approve/send.
 * Auto-refreshes every 60s (edit guard prevents re-render while typing).
 */
document.addEventListener('DOMContentLoaded', function () {

  // ── State ──────────────────────────────────────────────────────────────────
  var notes = [];
  var _activeNoteId = null;
  var _dirty = false;
  var _pollInterval = null;

  // ── DOM refs ───────────────────────────────────────────────────────────────
  var startDateInput      = document.getElementById('start-date-input');
  var endDateInput        = document.getElementById('end-date-input');
  var portfolioNameInput  = document.getElementById('portfolio-name-input');
  var ownerIdInput        = document.getElementById('owner-id-input');
  var propertyIdInput   = document.getElementById('property-id-input');
  var dryRunCheck       = document.getElementById('dry-run-check');
  var runBtn            = document.getElementById('run-btn');
  var sendAllBtn        = document.getElementById('send-all-btn');
  var statusBar         = document.getElementById('status-bar');
  var noteList          = document.getElementById('note-list');
  var noteDetail        = document.getElementById('note-detail');
  var detailPlaceholder = document.getElementById('detail-placeholder');
  var noteTitle         = document.getElementById('note-title');
  var noteTextarea      = document.getElementById('note-textarea');
  var copyBtn           = document.getElementById('copy-btn');
  var saveBtn           = document.getElementById('save-btn');
  var approveBtn        = document.getElementById('approve-btn');
  var noteSendBtn       = document.getElementById('note-send-btn');
  var wordCountEl       = document.getElementById('word-count');
  var noteFinancials    = document.getElementById('note-financials');
  var finPeriod         = document.getElementById('fin-period');
  var finIncome         = document.getElementById('fin-income');
  var finExpenses       = document.getElementById('fin-expenses');
  var finDistribution   = document.getElementById('fin-distribution');
  var finEnding         = document.getElementById('fin-ending');

  // ── Helpers ────────────────────────────────────────────────────────────────
  function isEditing() {
    var a = document.activeElement;
    return a && (a.tagName === 'TEXTAREA' || a.tagName === 'INPUT');
  }

  // Return YYYY-MM-DD for first day of last month
  function lastMonthStart() {
    var d = new Date();
    var y = d.getFullYear();
    var m = d.getMonth(); // 0-indexed; equals 1-indexed previous month number
    if (m === 0) { y -= 1; m = 12; }
    return y + '-' + (m < 10 ? '0' + m : '' + m) + '-01';
  }

  // Return YYYY-MM-DD for last day of last month
  function lastMonthEnd() {
    var d = new Date();
    // new Date(y, m, 0) = last day of month m-1 (0-indexed)
    var last = new Date(d.getFullYear(), d.getMonth(), 0);
    var y = last.getFullYear();
    var m = last.getMonth() + 1;
    var day = last.getDate();
    return y + '-' + (m < 10 ? '0' + m : '' + m) + '-' + (day < 10 ? '0' + day : '' + day);
  }

  // Derive YYYY-MM from start date for DB queries and polling key
  function derivedMonth() {
    var s = startDateInput.value;
    return s ? s.substring(0, 7) : '';
  }

  function fmtCurrency(val) {
    var n = parseFloat(val) || 0;
    return '$' + n.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  function statusBadge(status) {
    if (status === 'success'  || status === 'sent')     return '<span class="mn-badge mn-badge-sent">sent</span>';
    if (status === 'pending')  return '<span class="mn-badge mn-badge-pending">pending</span>';
    if (status === 'approved') return '<span class="mn-badge mn-badge-approved">approved</span>';
    if (status === 'skipped')  return '<span class="mn-badge mn-badge-skipped">skipped</span>';
    if (status === 'failed')   return '<span class="mn-badge mn-badge-failed">failed</span>';
    return '';
  }

  function setStatus(type, msg) {
    var icons  = { running: '⟳', done: '✓', error: '✗', warn: '⚠', idle: '●' };
    var colors = { running: '#3b82f6', done: '#10b981', error: '#ef4444', warn: '#f59e0b', idle: '#9ca3af' };
    statusBar.innerHTML = '<span style="color:' + (colors[type] || '#9ca3af') + ';margin-right:.4rem">' + (icons[type] || '●') + '</span>' + msg;
  }

  function updateWordCount() {
    var text = noteTextarea.value || '';
    var count = text.trim() ? text.trim().split(/\s+/).length : 0;
    wordCountEl.textContent = count + ' words';
  }

  function _noteById(id) {
    for (var i = 0; i < notes.length; i++) {
      if (notes[i].id === id) return notes[i];
    }
    return null;
  }

  // ── Load notes for selected period ─────────────────────────────────────────
  function loadNotes() {
    var month = derivedMonth();
    if (!month) return;

    VestaAPI.get('/reports/owner-notes?month=' + month).then(function (data) {
      notes = Array.isArray(data) ? data : (data.items || []);
      if (!isEditing()) renderList();
    }).catch(function () {
      setStatus('error', 'Failed to load notes.');
    });
  }

  // ── Render left panel list ─────────────────────────────────────────────────
  function renderList() {
    if (!notes.length) {
      noteList.innerHTML = '<div class="mn-empty">No notes for this month. Run generation to create them.</div>';
      return;
    }

    var html = '';
    for (var i = 0; i < notes.length; i++) {
      var n = notes[i];
      var activeClass = (n.id === _activeNoteId) ? ' active' : '';
      html += '<div class="mn-row' + activeClass + '" data-id="' + n.id + '">';
      html += '<div class="mn-row-name">' + n.owner_name + '</div>';
      html += '<div class="mn-row-meta">' + n.portfolio_name + ' ' + statusBadge(n.status) + '</div>';
      if (n.status === 'pending' || n.status === 'approved' || n.status === 'sent' || n.status === 'success') {
        html += '<div class="mn-row-words">' + n.word_count + ' words</div>';
      }
      // Row-level action buttons
      var hasApprove = (n.status === 'pending' || n.status === 'success');
      var hasSend    = (n.status === 'approved');
      if (hasApprove || hasSend) {
        html += '<div class="mn-row-actions">';
        if (hasApprove) {
          html += '<button class="mn-action-btn mn-action-approve" data-id="' + n.id + '">Approve</button>';
        }
        if (hasSend) {
          html += '<button class="mn-action-btn mn-action-send" data-id="' + n.id + '">Send</button>';
        }
        html += '</div>';
      }
      html += '</div>';
    }
    noteList.innerHTML = html;

    // Row click → select note
    var rows = noteList.querySelectorAll('.mn-row');
    for (var j = 0; j < rows.length; j++) {
      (function (row) {
        row.addEventListener('click', function () {
          selectNote(parseInt(row.getAttribute('data-id'), 10));
        });
      })(rows[j]);
    }

    // Approve buttons
    var approveBtns = noteList.querySelectorAll('.mn-action-approve');
    for (var k = 0; k < approveBtns.length; k++) {
      (function (btn) {
        btn.addEventListener('click', function (e) {
          e.stopPropagation();
          approveNote(parseInt(btn.getAttribute('data-id'), 10));
        });
      })(approveBtns[k]);
    }

    // Send buttons
    var sendBtns = noteList.querySelectorAll('.mn-action-send');
    for (var m = 0; m < sendBtns.length; m++) {
      (function (btn) {
        btn.addEventListener('click', function (e) {
          e.stopPropagation();
          sendNote(parseInt(btn.getAttribute('data-id'), 10));
        });
      })(sendBtns[m]);
    }

    // Restore active selection if still valid
    if (_activeNoteId) {
      var stillExists = false;
      for (var p = 0; p < notes.length; p++) {
        if (notes[p].id === _activeNoteId) { stillExists = true; break; }
      }
      if (!stillExists) {
        _activeNoteId = null;
        noteDetail.style.display = 'none';
        detailPlaceholder.style.display = 'flex';
      }
    }
  }

  // ── Select a note ──────────────────────────────────────────────────────────
  function selectNote(id) {
    if (_dirty && _activeNoteId !== id) {
      if (!confirm('You have unsaved changes. Discard?')) return;
    }
    _activeNoteId = id;
    _dirty = false;

    var note = _noteById(id);
    if (!note) return;

    // Highlight row
    var rows = noteList.querySelectorAll('.mn-row');
    for (var j = 0; j < rows.length; j++) {
      rows[j].classList.toggle('active', parseInt(rows[j].getAttribute('data-id'), 10) === id);
    }

    // Populate right panel
    noteTitle.textContent = note.owner_name + ' — ' + note.portfolio_name;
    detailPlaceholder.style.display = 'none';
    noteDetail.style.display = 'flex';

    // Financial summary
    var hasFinancials = note.statement_period || note.total_income || note.total_distribution;
    if (hasFinancials) {
      finPeriod.textContent = note.statement_period ? 'Period: ' + note.statement_period : '';
      finIncome.textContent       = fmtCurrency(note.total_income);
      finExpenses.textContent     = fmtCurrency(note.total_expenses);
      finDistribution.textContent = fmtCurrency(note.total_distribution);
      finEnding.textContent       = fmtCurrency(note.ending_balance);
      noteFinancials.style.display = 'block';
    } else {
      noteFinancials.style.display = 'none';
    }

    var isEditable = (note.status === 'pending' || note.status === 'approved' || note.status === 'success');
    var isSent     = (note.status === 'sent');

    if (isEditable || isSent) {
      noteTextarea.value    = note.generated_note || '';
      noteTextarea.disabled = isSent;
      saveBtn.disabled      = isSent;
      copyBtn.disabled      = false;
    } else if (note.status === 'skipped') {
      noteTextarea.value    = '(Skipped — no data available for this portfolio during the period.)';
      noteTextarea.disabled = true;
      saveBtn.disabled      = true;
      copyBtn.disabled      = true;
    } else {
      noteTextarea.value    = 'Generation failed: ' + (note.error_message || 'unknown error');
      noteTextarea.disabled = true;
      saveBtn.disabled      = true;
      copyBtn.disabled      = true;
    }

    // Approve button — show for pending/success notes
    if (note.status === 'pending' || note.status === 'success') {
      approveBtn.style.display = '';
      approveBtn.disabled = false;
    } else {
      approveBtn.style.display = 'none';
    }

    // Send button — show for approved notes
    if (note.status === 'approved') {
      noteSendBtn.style.display = '';
      noteSendBtn.disabled = false;
    } else {
      noteSendBtn.style.display = 'none';
    }

    updateWordCount();
  }

  // ── Approve note ───────────────────────────────────────────────────────────
  function approveNote(id) {
    VestaAPI.post('/reports/owner-notes/' + id + '/approve', {}).then(function (updated) {
      _updateNote(updated);
      VestaAPI.toast('Approved.', 'success');
      if (_activeNoteId === id) selectNote(id);
    }).catch(function () {
      VestaAPI.toast('Approve failed.', 'error');
    });
  }

  // ── Send single note ───────────────────────────────────────────────────────
  function sendNote(id) {
    setStatus('running', 'Sending email…');
    VestaAPI.post('/reports/owner-notes/' + id + '/send', {}).then(function (resp) {
      _updateNote(resp);
      if (resp.ok) {
        setStatus('done', 'Email sent.');
        VestaAPI.toast('Sent.', 'success');
      } else {
        setStatus('error', 'Send failed: ' + (resp.message || 'unknown error'));
        VestaAPI.toast('Send failed.', 'error');
      }
      if (_activeNoteId === id) selectNote(id);
    }).catch(function () {
      setStatus('error', 'Send request failed.');
      VestaAPI.toast('Send failed.', 'error');
    });
  }

  // ── Send all approved ──────────────────────────────────────────────────────
  function sendAllApproved() {
    var month = derivedMonth();
    if (!month) { VestaAPI.toast('Select a date range first.', 'error'); return; }

    var approvedCount = 0;
    for (var i = 0; i < notes.length; i++) {
      if (notes[i].status === 'approved') approvedCount++;
    }
    if (approvedCount === 0) {
      VestaAPI.toast('No approved notes to send for this month.', 'warn');
      return;
    }

    sendAllBtn.disabled = true;
    setStatus('running', 'Sending all approved notes…');

    VestaAPI.post('/reports/owner-notes/send-all?month=' + month, {}).then(function (resp) {
      sendAllBtn.disabled = false;
      var msg = 'Done — ' + resp.sent + ' sent, ' + resp.failed + ' failed.';
      setStatus(resp.failed > 0 ? 'warn' : 'done', msg);
      loadNotes();
    }).catch(function () {
      sendAllBtn.disabled = false;
      setStatus('error', 'Send-all request failed.');
    });
  }

  // ── Run generation ─────────────────────────────────────────────────────────
  function runGeneration() {
    var startDate = startDateInput.value;
    var endDate   = endDateInput.value;
    if (!startDate || !endDate) { VestaAPI.toast('Select a date range first.', 'error'); return; }
    if (endDate < startDate) { VestaAPI.toast('End date must be on or after start date.', 'error'); return; }

    var month         = derivedMonth(); // YYYY-MM key for polling and DB
    var portfolioName = portfolioNameInput.value.trim() || null;
    var ownerId       = ownerIdInput.value.trim() || null;
    var propIdRaw     = propertyIdInput.value.trim();
    var propId        = propIdRaw ? parseInt(propIdRaw, 10) : null;
    var isDryRun      = dryRunCheck.checked;

    runBtn.disabled = true;
    var dryLabel = isDryRun ? ' (dry run — no DB writes)' : '';
    setStatus('running', 'Generating notes for ' + startDate + ' – ' + endDate + dryLabel + ' — this may take a minute…');

    VestaAPI.post('/reports/owner-notes/generate', {
      month: month,
      dry_run: isDryRun,
      owner_id: ownerId,
      portfolio_name: portfolioName,
      property_id: propId,
      start_date: startDate,
      end_date: endDate
    }).then(function (resp) {
      if (!resp.ok) {
        setStatus('error', resp.error || 'Could not start generation.');
        runBtn.disabled = false;
        return;
      }
      if (isDryRun) {
        // Dry run writes nothing to DB — output is in Railway / server logs only
        setStatus('done', 'Dry run started in background. Check Railway logs for output. No notes will be saved here.');
        runBtn.disabled = false;
      } else {
        startPolling(month);
      }
    }).catch(function () {
      setStatus('error', 'Request failed — check server logs.');
      runBtn.disabled = false;
    });
  }

  // ── Poll DB directly until notes appear (works across multiple gunicorn workers) ──
  function startPolling(month) {
    if (_pollInterval) clearInterval(_pollInterval);
    var attempts = 0;
    var maxAttempts = 36; // 3 minutes at 5s intervals
    var lastCount = -1;
    var stableChecks = 0;

    _pollInterval = setInterval(function () {
      attempts++;
      VestaAPI.get('/reports/owner-notes?month=' + month).then(function (data) {
        var fresh = Array.isArray(data) ? data : (data.items || []);
        var currentCount = fresh.length;

        if (currentCount > 0) {
          notes = fresh;
          if (!isEditing()) renderList();
          setStatus('running', 'Generating\u2026 ' + currentCount + ' note(s) saved so far.');
        }

        if (currentCount > 0 && currentCount === lastCount) {
          stableChecks++;
        } else {
          stableChecks = 0;
        }
        lastCount = currentCount;

        var done = stableChecks >= 3 || attempts >= maxAttempts;
        if (done) {
          clearInterval(_pollInterval);
          _pollInterval = null;
          runBtn.disabled = false;
          if (currentCount === 0) {
            setStatus('warn', 'Generation may still be running \u2014 check back in a moment or watch Railway logs.');
          } else {
            setStatus('done', 'Done \u2014 ' + currentCount + ' note(s) generated.');
            notes = fresh;
            if (!isEditing()) renderList();
          }
        }
      });
    }, 5000);
  }

  // ── Save note ──────────────────────────────────────────────────────────────
  function saveNote() {
    if (!_activeNoteId) return;
    saveBtn.disabled = true;

    VestaAPI.put('/reports/owner-notes/' + _activeNoteId, {
      generated_note: noteTextarea.value
    }).then(function (updated) {
      _dirty = false;
      saveBtn.disabled = false;
      _updateNote(updated);
      renderList();
      VestaAPI.toast('Note saved.', 'success');
    }).catch(function () {
      saveBtn.disabled = false;
      VestaAPI.toast('Save failed.', 'error');
    });
  }

  // ── Copy to clipboard ──────────────────────────────────────────────────────
  function copyNote() {
    var text = noteTextarea.value;
    if (!text) return;
    navigator.clipboard.writeText(text).then(function () {
      VestaAPI.toast('Copied to clipboard.', 'success');
    }).catch(function () {
      VestaAPI.toast('Copy failed — select all and copy manually.', 'error');
    });
  }

  // ── Update local note cache and re-render ──────────────────────────────────
  function _updateNote(updated) {
    for (var i = 0; i < notes.length; i++) {
      if (notes[i].id === updated.id) {
        notes[i] = updated;
        break;
      }
    }
    renderList();
  }

  // ── Event bindings ─────────────────────────────────────────────────────────
  runBtn.addEventListener('click', runGeneration);
  saveBtn.addEventListener('click', saveNote);
  copyBtn.addEventListener('click', copyNote);
  approveBtn.addEventListener('click', function () { if (_activeNoteId) approveNote(_activeNoteId); });
  noteSendBtn.addEventListener('click', function () { if (_activeNoteId) sendNote(_activeNoteId); });
  sendAllBtn.addEventListener('click', sendAllApproved);

  noteTextarea.addEventListener('input', function () {
    _dirty = true;
    updateWordCount();
  });

  // Changing start date resets the panel and reloads the note list
  startDateInput.addEventListener('change', function () {
    _activeNoteId = null;
    _dirty = false;
    noteDetail.style.display = 'none';
    detailPlaceholder.style.display = '';
    setStatus('idle', 'Ready.');
    loadNotes();
  });

  // Changing end date just reloads — keeps current selection
  endDateInput.addEventListener('change', loadNotes);

  // ── Auto-refresh every 60s (skip if a poll is running) ────────────────────
  setInterval(function () {
    if (_pollInterval) return;
    loadNotes();
  }, 60000);

  // ── Init ───────────────────────────────────────────────────────────────────
  startDateInput.value = lastMonthStart();
  endDateInput.value   = lastMonthEnd();
  setStatus('idle', 'Ready.');
  loadNotes();
});
