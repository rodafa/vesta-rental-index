/**
 * Maintenance Emails — review dashboard.
 * Mirrors the weekly_reports page pattern: date range, owner sections,
 * inline editing, preview, test-send, batch-send, send history.
 */
var ME = (function () {

  var owners = [];  // [{owner_id, owner_name, open_melds, closed_melds, canceled_melds, total_spend}]
  var ALL_OWNERS = [];

  // ── Init ──────────────────────────────────────────────────────────────────

  document.addEventListener('DOMContentLoaded', function () {
    _setDefaultDates();
  });

  function _setDefaultDates() {
    var today = new Date();
    var dow = today.getDay();
    // Default: last Monday through today
    var daysSinceMon = (dow - 1 + 7) % 7;
    if (daysSinceMon === 0) daysSinceMon = 7;
    var start = new Date(today);
    start.setDate(today.getDate() - daysSinceMon);
    var end = new Date(today);
    document.getElementById('me-start').value = _iso(start);
    document.getElementById('me-end').value = _iso(end);
  }

  function _iso(d) {
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }

  function _fmtDate(s) {
    if (!s) return '';
    var d = new Date(s);
    var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return months[d.getMonth()] + ' ' + d.getDate();
  }

  // ── Load ──────────────────────────────────────────────────────────────────

  function load() {
    var s = document.getElementById('me-start').value;
    var e = document.getElementById('me-end').value;
    if (!s || !e) { VestaAPI.toast('Select a date range', 'error'); return; }

    _setStatus('Loading...');
    document.getElementById('me-owners-container').innerHTML =
      '<div class="card" style="text-align:center;color:var(--text-muted);padding:2rem;">Loading...</div>';

    // Fetch all owners with active portfolios
    VestaAPI.get('/maintenance/owner-email/send-history?start=' + s + '&end=' + e).then(function () {});

    // We need to iterate owners — get the list from the melds endpoint for each
    // Strategy: first fetch owner list, then fetch melds per owner
    _fetchAllOwners(s, e);
  }

  function _fetchAllOwners(start, end) {
    // Get all active owners from the properties API
    VestaAPI.get('/dashboard/owner-list').then(function (data) {
      var ownerList = data.owners || data || [];
      _loadOwnerMelds(ownerList, start, end);
    }).catch(function () {
      // Fallback: try to load from send history to get owner list
      _loadFromKnownOwners(start, end);
    });
  }

  function _loadFromKnownOwners(start, end) {
    // Use a single owner_id=0 call which might fail, then fallback
    _setStatus('Loading owner data...');
    VestaAPI.get('/maintenance/owner-email/melds?owner_id=0&start=' + start + '&end=' + end).catch(function () {
      _setStatus('Could not load owners — use owner filter');
      document.getElementById('me-owners-container').innerHTML =
        '<div class="card" style="text-align:center;color:var(--text-muted);padding:2rem;">Enter an owner ID in the filter or check API connectivity.</div>';
    });
  }

  function _loadOwnerMelds(ownerList, start, end) {
    if (!ownerList.length) {
      _setStatus('No active owners found');
      return;
    }

    var loaded = 0;
    var total = ownerList.length;
    owners = [];

    ownerList.forEach(function (owner) {
      var oid = owner.id || owner.owner_id;
      VestaAPI.get('/maintenance/owner-email/melds?owner_id=' + oid + '&start=' + start + '&end=' + end).then(function (data) {
        if (data.open_melds && data.open_melds.length || data.closed_melds && data.closed_melds.length || data.canceled_melds && data.canceled_melds.length) {
          owners.push(data);
        }
        loaded++;
        if (loaded === total) _onAllLoaded();
      }).catch(function () {
        loaded++;
        if (loaded === total) _onAllLoaded();
      });
    });
  }

  function _onAllLoaded() {
    ALL_OWNERS = owners.slice();
    _populateOwnerFilter();
    _renderStats();
    applyFilters();
    _loadSendLog();
    _setStatus('Loaded ' + owners.length + ' owners with activity');
  }

  function _populateOwnerFilter() {
    var select = document.getElementById('me-filter-owner');
    select.innerHTML = '<option value="all">All Owners (' + owners.length + ')</option>';
    owners.sort(function (a, b) { return (a.owner_name || '').localeCompare(b.owner_name || ''); });
    owners.forEach(function (o) {
      var count = (o.open_melds || []).length + (o.closed_melds || []).length + (o.canceled_melds || []).length;
      select.innerHTML += '<option value="' + o.owner_id + '">' + _esc(o.owner_name) + ' (' + count + ')</option>';
    });
  }

  function _renderStats() {
    var totalOpen = 0, totalClosed = 0, totalCanceled = 0, totalSpend = 0;
    owners.forEach(function (o) {
      totalOpen += (o.open_melds || []).length;
      totalClosed += (o.closed_melds || []).length;
      totalCanceled += (o.canceled_melds || []).length;
      totalSpend += o.total_spend || 0;
    });

    var bar = document.getElementById('me-stats');
    if (!owners.length) { bar.style.display = 'none'; return; }
    bar.style.display = 'flex';
    bar.innerHTML =
      _bmCard('Owners', owners.length) +
      _bmCard('Open', totalOpen) +
      _bmCard('Closed', totalClosed) +
      _bmCard('Canceled', totalCanceled) +
      _bmCard('Total Spend', '$' + totalSpend.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2}));
  }

  function _bmCard(label, value) {
    return '<div class="benchmark-card"><div class="bm-label">' + label + '</div><div class="bm-value">' + value + '</div></div>';
  }

  // ── Filters ───────────────────────────────────────────────────────────────

  function applyFilters() {
    var ownerFilter = document.getElementById('me-filter-owner').value;
    var search = (document.getElementById('me-filter-search').value || '').toLowerCase();

    var filtered = ALL_OWNERS.filter(function (o) {
      if (ownerFilter !== 'all' && String(o.owner_id) !== ownerFilter) return false;
      if (search) {
        var haystack = (o.owner_name + ' ' + _allMeldText(o)).toLowerCase();
        if (haystack.indexOf(search) === -1) return false;
      }
      return true;
    });

    document.getElementById('me-count').textContent = filtered.length + ' of ' + ALL_OWNERS.length + ' owners';
    _renderOwners(filtered);
  }

  function _allMeldText(o) {
    var parts = [];
    [].concat(o.open_melds || [], o.closed_melds || [], o.canceled_melds || []).forEach(function (m) {
      parts.push(m.brief_description || '');
      parts.push(m.unit_address || '');
      parts.push(m.reference_id || '');
    });
    return parts.join(' ');
  }

  // ── Render owners ─────────────────────────────────────────────────────────

  function _renderOwners(filtered) {
    var container = document.getElementById('me-owners-container');
    if (!filtered.length) {
      container.innerHTML = '<div class="card" style="text-align:center;color:var(--text-muted);padding:2rem;">No owners with maintenance activity in this period.</div>';
      return;
    }

    container.innerHTML = filtered.map(function (o) {
      var html = '<div class="card" style="margin-bottom:1rem;">';

      // Owner header
      html += '<div style="display:flex;align-items:center;gap:.75rem;margin-bottom:.75rem;">';
      html += '<strong style="font-size:1rem;">' + _esc(o.owner_name) + '</strong>';
      html += '<span style="font-size:.78rem;color:var(--text-muted);">';
      html += (o.open_melds || []).length + ' open, ' + (o.closed_melds || []).length + ' closed';
      if (o.total_spend) html += ', $' + o.total_spend.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2}) + ' spend';
      html += '</span>';
      html += '<div style="margin-left:auto;display:flex;gap:.4rem;">';
      html += '<button class="btn btn-secondary btn-xs" onclick="ME.preview(' + o.owner_id + ')">Preview</button>';
      if (typeof USER_EMAIL !== 'undefined' && USER_EMAIL) {
        html += '<button class="btn btn-secondary btn-xs" onclick="ME.testSend(' + o.owner_id + ', \'' + _attr(o.owner_name) + '\')">Test Send</button>';
      }
      html += '</div></div>';

      // Open melds
      if (o.open_melds && o.open_melds.length) {
        html += _sectionHeader('Open Work Orders', o.open_melds.length, '#3b82f6');
        html += o.open_melds.map(function (m) { return _meldCard(m, 'open'); }).join('');
      }

      // Closed melds
      if (o.closed_melds && o.closed_melds.length) {
        html += _sectionHeader('Completed This Week', o.closed_melds.length, '#16a34a');
        html += o.closed_melds.map(function (m) { return _meldCard(m, 'closed'); }).join('');
      }

      // Canceled melds
      if (o.canceled_melds && o.canceled_melds.length) {
        html += _sectionHeader('Canceled', o.canceled_melds.length, '#9ca3af');
        html += o.canceled_melds.map(function (m) { return _meldCard(m, 'canceled'); }).join('');
      }

      html += '</div>';
      return html;
    }).join('');
  }

  function _sectionHeader(title, count, color) {
    return '<div style="font-size:.8rem;font-weight:700;color:' + color + ';text-transform:uppercase;letter-spacing:.04em;margin:1rem 0 .4rem;border-bottom:2px solid ' + color + ';padding-bottom:.25rem;">' +
      title + ' (' + count + ')</div>';
  }

  function _meldCard(m, section) {
    var needsManual = m.summary_status === 'needs_manual';
    var borderColor = needsManual ? '#dc2626' : (section === 'closed' ? '#16a34a' : section === 'canceled' ? '#d1d5db' : '#3b82f6');

    var html = '<div style="background:#f9fafb;border-radius:6px;border-left:4px solid ' + borderColor + ';padding:.75rem 1rem;margin-bottom:.5rem;">';

    // Top row: ref + description + priority/cost
    html += '<div style="display:flex;align-items:flex-start;gap:.5rem;">';
    html += '<div style="flex:1;">';
    html += '<div style="font-size:.72rem;color:#9ca3af;">' + _esc(m.unit_address || '') + '</div>';
    html += '<span style="font-size:.75rem;color:#6b7280;">';
    if (m.reference_id) html += '#' + _esc(m.reference_id) + ' &bull; ';
    html += _esc(m.category || '') + '</span>';
    html += '<div style="font-size:.875rem;font-weight:600;color:#1f2937;margin-top:2px;">' + _esc(m.brief_description || '') + '</div>';
    html += '</div>';

    // Right side: priority badge or cost
    if (section === 'open' && m.priority === 'EMERGENCY') {
      html += '<span class="badge" style="background:#dc2626;color:#fff;font-size:.65rem;">EMERGENCY</span>';
    } else if (section === 'open' && m.priority === 'HIGH') {
      html += '<span class="badge" style="background:#d97706;color:#fff;font-size:.65rem;">HIGH</span>';
    } else if (section === 'closed' && m.cost) {
      html += '<span style="font-weight:700;font-size:.875rem;color:#1f2937;">$' + Number(m.cost).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2}) + '</span>';
    }
    html += '</div>';

    // Summary row — editable
    html += '<div style="margin-top:.5rem;">';
    if (needsManual) {
      html += '<div style="background:#fef2f2;border:1px solid #fecaca;border-radius:4px;padding:.4rem .6rem;font-size:.78rem;color:#991b1b;margin-bottom:.3rem;">Needs manual summary — AI could not generate</div>';
    }
    html += '<textarea id="me-summary-' + m.id + '" style="width:100%;box-sizing:border-box;font-size:.82rem;padding:.4rem .5rem;border:1px solid #e5e7eb;border-radius:4px;resize:vertical;min-height:50px;line-height:1.4;" onblur="ME.saveSummary(' + m.id + ')">' + _esc(m.summary || '') + '</textarea>';
    html += '</div>';

    // Meta row
    html += '<div style="display:flex;align-items:center;gap:.75rem;margin-top:.3rem;font-size:.72rem;color:#9ca3af;">';
    if (m.assigned_vendor_name) html += '<span>' + _esc(m.assigned_vendor_name) + '</span>';
    if (m.completion_date) html += '<span>&bull; ' + _fmtDate(m.completion_date) + '</span>';
    if (m.tenant_rating) html += '<span>&bull; Rating: ' + m.tenant_rating + '/5</span>';
    if (m.owner_approval_status === 'Requested') {
      html += '<span class="badge" style="background:#f59e0b;color:#fff;font-size:.6rem;">APPROVAL REQUESTED</span>';
    }
    html += '</div>';

    html += '</div>';
    return html;
  }

  // ── Actions ───────────────────────────────────────────────────────────────

  function saveSummary(meldPk) {
    var ta = document.getElementById('me-summary-' + meldPk);
    if (!ta) return;
    var text = ta.value;

    VestaAPI.post('/maintenance/owner-email/summary/' + meldPk + '/edit', {
      summary: text
    }).then(function () {
      // Update local state
      _updateMeldSummary(meldPk, text);
      VestaAPI.toast('Summary saved');
    }).catch(function () {
      VestaAPI.toast('Failed to save summary', 'error');
    });
  }

  function _updateMeldSummary(meldPk, text) {
    ALL_OWNERS.forEach(function (o) {
      ['open_melds', 'closed_melds', 'canceled_melds'].forEach(function (key) {
        (o[key] || []).forEach(function (m) {
          if (m.id === meldPk) {
            m.summary = text;
            m.summary_status = 'edited';
          }
        });
      });
    });
  }

  function generateSummaries() {
    var CHUNK_SIZE = 3;
    var btn = document.getElementById('me-gen-btn');
    btn.disabled = true;

    // Collect all meld IDs that need summaries
    var meldIds = [];
    ALL_OWNERS.forEach(function (o) {
      ['open_melds', 'closed_melds', 'canceled_melds'].forEach(function (key) {
        (o[key] || []).forEach(function (m) {
          if (!m.summary || m.summary_status === 'needs_manual') {
            meldIds.push(m.id);
          }
        });
      });
    });

    if (!meldIds.length) {
      btn.disabled = false;
      _setStatus('No melds need summaries');
      return;
    }

    // Split into chunks
    var chunks = [];
    for (var i = 0; i < meldIds.length; i += CHUNK_SIZE) {
      chunks.push(meldIds.slice(i, i + CHUNK_SIZE));
    }

    var totalSummarized = 0;
    var totalSkipped = 0;
    var totalFailed = 0;
    var failedChunks = 0;
    var completed = 0;
    var totalMelds = meldIds.length;

    _setStatus('Generating summaries... 0 of ' + totalMelds);

    // Process chunks sequentially
    function processNext(index) {
      if (index >= chunks.length) {
        btn.disabled = false;
        var msg = 'Done: ' + totalSummarized + ' summarized, ' + totalFailed + ' failed';
        if (failedChunks > 0) msg += ' (' + failedChunks + ' chunk errors)';
        _setStatus(msg);
        VestaAPI.toast('Summaries generated');
        load();
        return;
      }

      VestaAPI.post('/maintenance/owner-email/generate-summaries', {
        meld_ids: chunks[index],
        force: false
      }).then(function (result) {
        totalSummarized += result.summarized || 0;
        totalSkipped += result.skipped || 0;
        totalFailed += result.failed || 0;
        completed += chunks[index].length;
        _setStatus('Generating summaries... ' + completed + ' of ' + totalMelds);
        processNext(index + 1);
      }).catch(function (err) {
        failedChunks++;
        var errMsg = (err && err.message) ? err.message : 'Unknown error';
        console.error('Chunk ' + (index + 1) + ' failed:', errMsg);
        totalFailed += chunks[index].length;
        completed += chunks[index].length;
        _setStatus('Generating summaries... ' + completed + ' of ' + totalMelds + ' (chunk error: ' + errMsg + ')');
        processNext(index + 1);
      });
    }

    processNext(0);
  }

  function preview(ownerId) {
    var s = document.getElementById('me-start').value;
    var e = document.getElementById('me-end').value;
    window.open('/maintenance/owner-email/preview?owner_id=' + ownerId + '&start=' + s + '&end=' + e, '_blank');
  }

  function testSend(ownerId, ownerName) {
    if (!confirm('Send a test copy of ' + ownerName + "'s maintenance email to " + USER_EMAIL + '?')) return;
    var s = document.getElementById('me-start').value;
    var e = document.getElementById('me-end').value;

    VestaAPI.post('/maintenance/owner-email/test-send', {
      start: s,
      end: e,
      owner_id: ownerId,
      recipient: USER_EMAIL
    }).then(function (resp) {
      VestaAPI.toast('Test email sent to ' + USER_EMAIL);
    }).catch(function () {
      VestaAPI.toast('Failed to send test email', 'error');
    });
  }

  function confirmSend() {
    if (!ALL_OWNERS.length) { VestaAPI.toast('No owners loaded', 'error'); return; }

    var overlay = document.createElement('div');
    overlay.className = 'dialog-overlay';
    overlay.innerHTML =
      '<div class="dialog-box">' +
        '<h3>Send Maintenance Emails</h3>' +
        '<p>This will email <strong>' + ALL_OWNERS.length + '</strong> owner' + (ALL_OWNERS.length !== 1 ? 's' : '') +
          ' their weekly maintenance summary.</p>' +
        '<p style="font-size:.82rem;color:var(--text-muted);">Owners already sent for this week will be skipped.</p>' +
        '<div class="dialog-actions">' +
          '<button class="btn btn-secondary" id="me-send-cancel">Cancel</button>' +
          '<button class="btn btn-success" id="me-send-confirm">Send Emails</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    document.getElementById('me-send-cancel').onclick = function () { overlay.remove(); };
    document.getElementById('me-send-confirm').onclick = function () {
      overlay.remove();
      _doSend();
    };
  }

  function _doSend() {
    var btn = document.getElementById('me-send-btn');
    btn.disabled = true;
    _setStatus('Sending emails...');

    var s = document.getElementById('me-start').value;
    var e = document.getElementById('me-end').value;

    VestaAPI.post('/maintenance/owner-email/send', {
      start: s,
      end: e
    }).then(function (resp) {
      btn.disabled = false;
      _setStatus('Sent: ' + (resp.sent || 0) + ', Skipped: ' + (resp.skipped || 0) + ', Failed: ' + (resp.failed || 0));
      VestaAPI.toast('Emails sent: ' + (resp.sent || 0));
      _loadSendLog();
    }).catch(function () {
      btn.disabled = false;
      _setStatus('Error sending emails');
      VestaAPI.toast('Failed to send emails', 'error');
    });
  }

  // ── Send Log ──────────────────────────────────────────────────────────────

  function _loadSendLog() {
    var s = document.getElementById('me-start').value;
    var e = document.getElementById('me-end').value;
    var url = '/maintenance/owner-email/send-history';
    if (s && e) url += '?start=' + s + '&end=' + e;

    VestaAPI.get(url).then(function (sends) {
      var logSection = document.getElementById('me-send-log');
      var logBody = document.getElementById('me-send-log-body');
      if (!sends || !sends.length) {
        logSection.style.display = 'none';
        return;
      }
      logSection.style.display = 'block';
      logBody.innerHTML = sends.map(function (s) {
        var badge = s.status === 'sent' ? 'badge-green' : s.status === 'failed' ? 'badge-red' : 'badge-yellow';
        return '<tr>' +
          '<td>' + _esc(s.owner_name) + '</td>' +
          '<td>' + s.week_date + '</td>' +
          '<td><span class="badge ' + badge + '">' + s.status + '</span></td>' +
          '<td>' + (s.melds_count || 0) + '</td>' +
          '<td>' + (s.sent_at ? new Date(s.sent_at).toLocaleString() : '\u2014') + '</td>' +
          '<td><button class="btn btn-secondary btn-xs" onclick="ME.viewSnapshot(' + s.id + ')">View</button></td>' +
        '</tr>';
      }).join('');
    }).catch(function () {});
  }

  function viewSnapshot(sendId) {
    VestaAPI.get('/maintenance/owner-email/send-history/' + sendId + '/snapshots').then(function (data) {
      var modal = document.getElementById('me-snapshot-modal');
      var content = document.getElementById('me-snapshot-content');

      if (!data || !data.length) {
        content.innerHTML = '<p style="color:var(--text-muted);">No snapshot data available for this send.</p>';
      } else {
        var html = '';
        ['open', 'closed', 'canceled'].forEach(function (section) {
          var melds = data.filter(function (m) { return m.section === section; });
          if (!melds.length) return;
          var color = section === 'open' ? '#3b82f6' : section === 'closed' ? '#16a34a' : '#9ca3af';
          html += '<div style="font-size:.75rem;font-weight:700;color:' + color + ';text-transform:uppercase;margin:1rem 0 .3rem;">' + section + ' (' + melds.length + ')</div>';
          melds.forEach(function (m) {
            html += '<div style="background:#f9fafb;border-left:3px solid ' + color + ';padding:.5rem .75rem;margin-bottom:.4rem;border-radius:4px;">';
            html += '<div style="font-size:.72rem;color:#9ca3af;">' + _esc(m.unit_label) + '</div>';
            html += '<div style="font-size:.78rem;color:#6b7280;">' + (m.meld_reference_id ? '#' + _esc(m.meld_reference_id) : '') + '</div>';
            html += '<div style="font-size:.85rem;margin-top:.2rem;">' + _esc(m.summary_text) + '</div>';
            if (m.cost) html += '<div style="font-size:.78rem;color:#1f2937;font-weight:600;margin-top:.2rem;">$' + Number(m.cost).toFixed(2) + '</div>';
            html += '</div>';
          });
        });
        content.innerHTML = html;
      }
      modal.style.display = 'block';
    }).catch(function () {
      VestaAPI.toast('Failed to load snapshot', 'error');
    });
  }

  function closeSnapshot() {
    document.getElementById('me-snapshot-modal').style.display = 'none';
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  function _setStatus(msg) {
    var el = document.getElementById('me-status');
    if (el) el.textContent = msg;
  }

  function _esc(s) {
    if (!s) return '';
    var el = document.createElement('span');
    el.textContent = String(s);
    return el.innerHTML;
  }

  function _attr(s) {
    return (s || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
  }

  // ── Public API ────────────────────────────────────────────────────────────

  return {
    load: load,
    applyFilters: applyFilters,
    saveSummary: saveSummary,
    generateSummaries: generateSummaries,
    preview: preview,
    testSend: testSend,
    confirmSend: confirmSend,
    viewSnapshot: viewSnapshot,
    closeSnapshot: closeSnapshot
  };

})();
