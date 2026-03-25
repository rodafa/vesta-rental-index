/**
 * Maintenance Emails — split-panel draft workflow.
 * Left: owner list with status badges.
 * Right: editable subject + body, send button.
 * Auto-refreshes every 60s (edit guard prevents re-render while typing).
 */
document.addEventListener('DOMContentLoaded', function () {

  // ── State ──────────────────────────────────────────────────────────────

  var drafts = [];
  var _activeDraftId = null;
  var _dirtyBody = false;
  var _dirtySubject = false;

  // ── Week calculation (most recent Tuesday) ─────────────────────────────

  function currentTuesday() {
    var d = new Date();
    var day = d.getDay(); // 0=Sun,1=Mon,...,6=Sat — Tue=2
    var daysSinceTue = (day - 2 + 7) % 7;
    var tue = new Date(d);
    tue.setDate(d.getDate() - daysSinceTue);
    return tue.toISOString().split('T')[0];
  }

  var weekStart = currentTuesday();

  function weekLabel() {
    var start = new Date(weekStart + 'T00:00:00');
    var end = new Date(start);
    end.setDate(start.getDate() + 6);
    return 'Week: ' + fmtDate(start) + ' \u2013 ' + fmtDate(end);
  }

  function fmtDate(d) {
    var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return months[d.getMonth()] + ' ' + d.getDate();
  }

  var weekLabelEl = document.getElementById('me-week-label');
  if (weekLabelEl) weekLabelEl.textContent = weekLabel();

  // ── Edit Guard ──────────────────────────────────────────────────────────

  function isEditing() {
    var a = document.activeElement;
    if (!a) return false;
    var t = a.tagName.toLowerCase();
    return t === 'textarea' || t === 'input';
  }

  // ── Load ────────────────────────────────────────────────────────────────

  function loadAll() {
    VestaAPI.get('/dashboard/meld-drafts?week_start=' + weekStart).then(function (data) {
      var items = data.items || data;
      if (!Array.isArray(items)) items = [];
      drafts = items;
      if (!isEditing()) {
        renderOwnerList();
        if (_activeDraftId !== null) {
          renderRightPanel();
        }
      }
    }).catch(function (err) {
      console.error('Maintenance emails load error:', err);
    });
  }

  loadAll();
  setInterval(loadAll, 60000);

  // ── Generate ────────────────────────────────────────────────────────────

  document.getElementById('me-generate-btn').addEventListener('click', function () {
    var spinner = document.getElementById('me-spinner');
    var btn = document.getElementById('me-generate-btn');
    if (spinner) spinner.classList.add('visible');
    btn.disabled = true;

    VestaAPI.post('/dashboard/meld-drafts/generate', { week_start: weekStart }).then(function (result) {
      if (spinner) spinner.classList.remove('visible');
      btn.disabled = false;
      var newDrafts = (result.drafts || []);
      if (!newDrafts.length) {
        VestaAPI.toast('No matched melds for this week', 'error');
        return;
      }
      drafts = newDrafts;
      _activeDraftId = null;
      renderOwnerList();
      renderRightPanel();
      VestaAPI.toast('Generated ' + newDrafts.length + ' draft' + (newDrafts.length !== 1 ? 's' : ''), 'success');
    }).catch(function (err) {
      if (spinner) spinner.classList.remove('visible');
      btn.disabled = false;
      console.error('Generate error:', err);
      VestaAPI.toast('Error generating drafts', 'error');
    });
  });

  // ── Render: left panel ──────────────────────────────────────────────────

  function renderOwnerList() {
    var list = document.getElementById('me-owner-list');
    if (!list) return;
    if (!drafts.length) {
      list.innerHTML = '<div style="padding:1rem;color:#9ca3af;font-size:.85rem;">No drafts yet \u2014 click Generate.</div>';
      return;
    }
    var html = '';
    for (var i = 0; i < drafts.length; i++) {
      var d = drafts[i];
      var active = (_activeDraftId === d.id) ? ' active' : '';
      html += '<div class="me-owner-row' + active + '" data-id="' + d.id + '">';
      html += '<div class="me-owner-name">' + esc(d.owner_name) + '</div>';
      html += '<div class="me-owner-meta">' + d.meld_count + ' meld' + (d.meld_count !== 1 ? 's' : '') +
        ' &middot; ' + statusBadge(d) + '</div>';
      html += '</div>';
    }
    list.innerHTML = html;
    var rows = list.querySelectorAll('.me-owner-row');
    for (var j = 0; j < rows.length; j++) {
      rows[j].addEventListener('click', onOwnerRowClick);
    }
  }

  function statusBadge(d) {
    if (d.status === 'sent') {
      var sentStr = d.sent_at ? ' (' + fmtDate(new Date(d.sent_at)) + ')' : '';
      return '<span class="me-badge me-badge-sent">SENT' + esc(sentStr) + '</span>';
    }
    return '<span class="me-badge me-badge-draft">DRAFT</span>';
  }

  // ── Render: right panel ─────────────────────────────────────────────────

  function renderRightPanel() {
    var panel = document.getElementById('me-right-panel');
    if (!panel) return;

    var draft = _activeDraftId !== null ? getDraftById(_activeDraftId) : null;
    if (!draft) {
      panel.innerHTML = '<div class="me-right-empty">Select an owner to view or edit their draft.</div>';
      return;
    }

    var isSent = draft.status === 'sent';
    var roAttr = isSent ? ' readonly' : '';

    var html = '';

    // Owner + status
    html += '<div style="display:flex;align-items:center;gap:.75rem;margin-bottom:1rem;">';
    html += '<strong style="font-size:1rem;">' + esc(draft.owner_name) + '</strong>';
    html += statusBadge(draft);
    if (isSent && draft.sent_at) {
      html += '<span style="font-size:.78rem;color:#6b7280;">Sent ' + fmtDate(new Date(draft.sent_at)) + '</span>';
    }
    html += '</div>';

    // Subject
    html += '<div class="me-subject-row">';
    html += '<label class="me-body-label">Subject</label>';
    html += '<input type="text" class="me-subject-input" id="me-subject-input" value="' + esc(draft.email_subject) + '"' + roAttr + '>';
    html += '</div>';

    // Body
    html += '<label class="me-body-label">Email Body <span style="font-weight:400;color:#9ca3af;">(AI-drafted, editable)</span></label>';
    html += '<textarea class="me-body-textarea" id="me-body-textarea"' + roAttr + '>' + esc(draft.email_body) + '</textarea>';

    // Properties included
    if (draft.properties_included && draft.properties_included.length) {
      html += '<div style="font-size:.75rem;color:#9ca3af;margin-top:.4rem;">Covers: ' + esc(draft.properties_included.join(', ')) + '</div>';
    }

    // Actions
    html += '<div class="me-actions">';
    if (!isSent) {
      html += '<button class="btn btn-success btn-sm" id="me-send-btn">Send Email</button>';
      html += '<button class="btn btn-secondary btn-sm" id="me-save-btn">Save Draft</button>';
    }
    html += '<span class="me-save-status" id="me-save-status"></span>';
    html += '</div>';

    panel.innerHTML = html;

    // Bind events
    var bodyTA = document.getElementById('me-body-textarea');
    var subjectIn = document.getElementById('me-subject-input');

    if (bodyTA) {
      bodyTA.addEventListener('input', function () { _dirtyBody = true; });
      bodyTA.addEventListener('blur', onBodyBlur);
    }
    if (subjectIn) {
      subjectIn.addEventListener('input', function () { _dirtySubject = true; });
      subjectIn.addEventListener('blur', onSubjectBlur);
    }

    var sendBtn = document.getElementById('me-send-btn');
    if (sendBtn) sendBtn.addEventListener('click', onSend);

    var saveBtn = document.getElementById('me-save-btn');
    if (saveBtn) saveBtn.addEventListener('click', onSave);
  }

  // ── Events ──────────────────────────────────────────────────────────────

  function onOwnerRowClick(e) {
    var id = parseInt(e.currentTarget.getAttribute('data-id'), 10);
    _activeDraftId = id;
    _dirtyBody = false;
    _dirtySubject = false;
    renderOwnerList();
    renderRightPanel();
  }

  function onBodyBlur() {
    if (_dirtyBody) saveDraft();
  }

  function onSubjectBlur() {
    if (_dirtySubject) saveDraft();
  }

  function onSave() {
    saveDraft();
  }

  function saveDraft() {
    var draft = getDraftById(_activeDraftId);
    if (!draft) return;

    var bodyTA = document.getElementById('me-body-textarea');
    var subjectIn = document.getElementById('me-subject-input');
    var newBody = bodyTA ? bodyTA.value : draft.email_body;
    var newSubject = subjectIn ? subjectIn.value : draft.email_subject;

    VestaAPI.put('/dashboard/meld-drafts/' + draft.id, {
      email_body: newBody,
      email_subject: newSubject
    }).then(function (updated) {
      updateDraftInState(updated);
      _dirtyBody = false;
      _dirtySubject = false;
      var status = document.getElementById('me-save-status');
      if (status) {
        status.textContent = 'Saved';
        setTimeout(function () { if (status) status.textContent = ''; }, 2000);
      }
    }).catch(function (err) {
      console.error('Save draft error:', err);
      VestaAPI.toast('Error saving draft', 'error');
    });
  }

  function onSend() {
    var draft = getDraftById(_activeDraftId);
    if (!draft) return;

    var btn = document.getElementById('me-send-btn');
    if (btn) btn.disabled = true;

    // Save any pending edits first
    var savePromise;
    if (_dirtyBody || _dirtySubject) {
      var bodyTA = document.getElementById('me-body-textarea');
      var subjectIn = document.getElementById('me-subject-input');
      var newBody = bodyTA ? bodyTA.value : draft.email_body;
      var newSubject = subjectIn ? subjectIn.value : draft.email_subject;
      savePromise = VestaAPI.put('/dashboard/meld-drafts/' + draft.id, {
        email_body: newBody,
        email_subject: newSubject
      }).then(function (updated) {
        updateDraftInState(updated);
        _dirtyBody = false;
        _dirtySubject = false;
      });
    } else {
      savePromise = Promise.resolve();
    }

    savePromise.then(function () {
      return VestaAPI.post('/dashboard/meld-drafts/' + draft.id + '/send', {});
    }).then(function (result) {
      if (result.error) {
        VestaAPI.toast('Error: ' + result.error, 'error');
        if (btn) btn.disabled = false;
        return;
      }
      updateDraftInState(result);
      renderOwnerList();
      renderRightPanel();
      VestaAPI.toast('Email sent to ' + draft.owner_name, 'success');
    }).catch(function (err) {
      console.error('Send error:', err);
      VestaAPI.toast('Error sending email', 'error');
      if (btn) btn.disabled = false;
    });
  }

  // ── Helpers ─────────────────────────────────────────────────────────────

  function getDraftById(id) {
    for (var i = 0; i < drafts.length; i++) {
      if (drafts[i].id === id) return drafts[i];
    }
    return null;
  }

  function updateDraftInState(updated) {
    for (var i = 0; i < drafts.length; i++) {
      if (drafts[i].id === updated.id) {
        drafts[i] = updated;
        return;
      }
    }
    drafts.push(updated);
  }

  function esc(text) {
    if (text == null) return '';
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(String(text)));
    return div.innerHTML;
  }

});
