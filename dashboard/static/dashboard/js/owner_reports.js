/**
 * Leasing Updates — accordion layout grouped by portfolio.
 * Each portfolio is a collapsible section; each listing has property basics,
 * lead insights (compact grid with WoW pills), and an editable message.
 * Auto-refreshes every 60s (skips re-render while editing).
 */
document.addEventListener('DOMContentLoaded', function () {

  // ── State ──────────────────────────────────────────────────────────────

  var owners = [];
  var ownerReports = {};      // keyed by owner_id
  var noteMap = {};            // OwnerReportNote per owner_id
  var propNoteMap = {};        // keyed by unit_id, textarea content (in-memory)
  var expandedSections = {};   // keyed by portfolio name
  var lastSentMap = {};        // last sent_at per owner
  var dirtyUnits = {};         // modified textareas
  var portfolioGroups = {};    // keyed by portfolio name → {owners, units, medians}
  var unitOwnerMap = {};       // unit_id → owner_id (for draft/save)

  // Week date
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

  // Wire up page-level buttons
  document.getElementById('save-all-btn').addEventListener('click', saveAllDrafts);
  document.getElementById('send-all-btn').addEventListener('click', sendAllReviewed);
  document.getElementById('close-preview-btn').addEventListener('click', closePreview);

  // ── Load ────────────────────────────────────────────────────────────────

  function loadAll() {
    Promise.all([
      VestaAPI.get('/analytics/owners-with-active-listings'),
      VestaAPI.get('/dashboard/owner-notes?report_date=' + weekDate),
      VestaAPI.get('/dashboard/owner-notes?status=sent'),
    ]).then(function (results) {
      var ownerData = results[0];
      var weekNotes = results[1];
      var sentNotes = results[2];

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
        VestaAPI.render('owner-accordion', '<div class="empty-state">No leasing updates this week</div>');
        updateReadyCount();
        return;
      }

      // Fetch all owner reports in parallel
      var fetches = [];
      for (var k = 0; k < owners.length; k++) {
        fetches.push(fetchOwnerReport(owners[k].owner_id));
      }

      Promise.all(fetches).then(function () {
        buildPortfolioGroups();
        if (!isEditing()) {
          renderPage();
        }
        updateReadyCount();
      });
    }).catch(function (err) {
      console.error('Leasing Updates load error:', err);
      VestaAPI.render('owner-accordion', '<div class="loading">Error loading owners</div>');
    });
  }

  function fetchOwnerReport(ownerId) {
    return VestaAPI.get('/analytics/owner-report-detail/' + ownerId + '?week_date=' + weekDate)
      .then(function (report) {
        ownerReports[ownerId] = report;
        // Seed propNoteMap from API data (only if user hasn't already edited)
        for (var i = 0; i < report.units.length; i++) {
          var u = report.units[i];
          if (propNoteMap[u.unit_id] === undefined) {
            if (u.property_note) {
              propNoteMap[u.unit_id] = u.property_note;
            }
            // else leave undefined so generateDraft runs on first render
          }
        }
      }).catch(function (err) {
        console.error('Error fetching report for owner ' + ownerId + ':', err);
      });
  }

  loadAll();
  setInterval(loadAll, 60000);

  // ── Portfolio Grouping ────────────────────────────────────────────────

  function buildPortfolioGroups() {
    portfolioGroups = {};
    unitOwnerMap = {};
    var seenUnits = {};

    for (var i = 0; i < owners.length; i++) {
      var owner = owners[i];
      var report = ownerReports[owner.owner_id];
      if (!report || !report.units) continue;

      for (var j = 0; j < report.units.length; j++) {
        var u = report.units[j];
        var pName = u.portfolio_name || 'Other';

        // Deduplicate units by unit_id (M2M safety)
        if (seenUnits[u.unit_id]) continue;
        seenUnits[u.unit_id] = true;

        if (!portfolioGroups[pName]) {
          portfolioGroups[pName] = {
            owners: {},
            units: [],
            medians: report.portfolio_medians || {}
          };
        }

        portfolioGroups[pName].owners[owner.owner_id] = owner;
        portfolioGroups[pName].units.push(u);
        unitOwnerMap[u.unit_id] = owner.owner_id;
      }
    }
  }

  // ── Edit Guard ──────────────────────────────────────────────────────────

  function isEditing() {
    var a = document.activeElement;
    if (!a) return false;
    var t = a.tagName.toLowerCase();
    return t === 'textarea' || t === 'input';
  }

  // ── Rendering ───────────────────────────────────────────────────────────

  function renderPage() {
    var html = '';
    var names = Object.keys(portfolioGroups).sort();
    for (var i = 0; i < names.length; i++) {
      html += renderPortfolioSection(names[i], portfolioGroups[names[i]]);
    }
    VestaAPI.render('owner-accordion', html);
    bindAccordionEvents();
  }

  function renderPortfolioSection(name, group) {
    var expanded = expandedSections[name] ? ' expanded' : '';
    var listingCount = group.units.length;

    // Find first owner for this portfolio (for preview/send)
    var ownerIds = Object.keys(group.owners);
    var firstOwnerId = ownerIds.length ? ownerIds[0] : '';

    var html = '<div class="portfolio-section' + expanded + '" data-portfolio="' + esc(name) + '">';

    // Header
    html +=
      '<div class="portfolio-section-header" data-portfolio="' + esc(name) + '">' +
        '<div class="hdr-left">' +
          esc(name) +
        '</div>' +
        '<div class="hdr-right">' +
          '<span>' + listingCount + ' listing' + (listingCount !== 1 ? 's' : '') + '</span>' +
          '<span class="chevron">&#9654;</span>' +
        '</div>' +
      '</div>';

    // Body
    html += '<div class="portfolio-section-body">';

    for (var i = 0; i < group.units.length; i++) {
      var u = group.units[i];
      var ownerId = unitOwnerMap[u.unit_id];
      var owner = group.owners[ownerId] || {};
      html += renderListingRow(u, owner, group.medians);
      html += renderListingRowFooter(u);
    }

    html += '</div>'; // end body

    // Footer — preview/send per owner in this portfolio
    html += '<div class="portfolio-section-footer">';
    for (var o = 0; o < ownerIds.length; o++) {
      var oid = ownerIds[o];
      html +=
        '<button class="btn btn-secondary btn-sm btn-preview-owner" data-owner-id="' + oid + '">Preview Email</button>' +
        '<button class="btn btn-success btn-sm btn-send-owner" data-owner-id="' + oid + '">Send</button>';
    }
    html += '</div>';

    html += '</div>'; // end section
    return html;
  }

  // ── Listing Row (3 columns) ─────────────────────────────────────────────

  function renderListingRow(u, owner, medians) {
    var html = '<div class="listing-row" data-unit-id="' + u.unit_id + '">';
    html += renderCol1(u);
    html += renderCol2(u, medians);
    html += renderCol3(u, owner);
    html += '</div>';
    return html;
  }

  // Column 1 — Property Basics
  function renderCol1(u) {
    var html = '<div class="listing-col">';

    // Address with Zillow link
    if (u.zillow_url) {
      html += '<div class="address-line"><a href="' + esc(u.zillow_url) + '" target="_blank">' + esc(u.address) + '</a></div>';
    } else {
      html += '<div class="address-line">' + esc(u.address) + '</div>';
    }

    // Date listed
    if (u.date_listed) {
      html += '<div class="detail-line">Listed ' + VestaAPI.dateStr(u.date_listed) + '</div>';
    }

    // DOM badge
    if (u.days_on_market != null) {
      html += '<div class="detail-line"><span class="dom-badge ' + domClass(u.days_on_market) + '">' +
        u.days_on_market + ' DOM</span></div>';
    }

    // List price + price history
    if (u.current_list_price) {
      html += '<div class="detail-line"><strong>' + VestaAPI.$(u.current_list_price) + '</strong></div>';
    }
    if (u.price_history && u.price_history.length) {
      for (var ph = 0; ph < u.price_history.length; ph++) {
        var drop = u.price_history[ph];
        html += '<div class="detail-line price-drop-line">' +
          '<span class="price-drop-badge">\u2193 $' + VestaAPI.num(parseFloat(drop.drop_amount)) +
          ' (' + parseFloat(drop.drop_percent).toFixed(1) + '%) on ' + VestaAPI.dateStr(drop.date) + '</span>' +
          '</div>';
      }
    }

    // Beds / baths / sqft
    var specs = [];
    if (u.bedrooms) specs.push(u.bedrooms + ' bed');
    if (u.bathrooms) specs.push(u.bathrooms + ' bath');
    if (u.square_feet) specs.push(VestaAPI.num(u.square_feet) + ' sqft');
    if (specs.length) {
      html += '<div class="detail-line">' + specs.join(' / ') + '</div>';
    }

    html += '</div>';
    return html;
  }

  // Column 2 — Lead Insights (compact blocks with WoW pills)
  function renderCol2(u, medians) {
    var html = '<div class="listing-col">';

    // ── This Week block
    html += '<div class="metric-block">';
    html += '<div class="metric-block-title">This Week</div>';
    html += '<div class="metric-mini-grid">';
    html += metricRowPill('Leads', u.weekly.leads, u.prev_weekly.leads);
    html += metricRowPill('Showings', u.weekly.showings, u.prev_weekly.showings);
    html += metricRowPill('Missed', u.weekly.missed, u.prev_weekly.missed, true);
    html += metricRowPill('Apps', u.weekly.apps, u.prev_weekly.apps);
    html += '</div>'; // end mini-grid
    html += '</div>'; // end metric-block

    // ── All-Time block
    html += '<div class="metric-block">';
    html += '<div class="metric-block-title">All-Time</div>';
    html += '<div class="metric-mini-grid">';
    html += metricRowSimple('Leads', u.all_time.leads);
    html += metricRowSimple('Showings', u.all_time.showings);
    html += metricRowSimple('Apps', u.all_time.apps);
    var dom = u.days_on_market || 0;
    var weeks = Math.max(dom / 7, 1);
    var avgLeadsWk = (u.all_time.leads / weeks).toFixed(1);
    html += metricRowSimple('Avg leads/wk', avgLeadsWk);
    html += '</div>'; // end mini-grid
    html += '</div>'; // end metric-block

    // ── Leads/day callout
    var lpd = u.leads_per_active_day;
    var lpdCls = lpd < 0.5 ? 'low' : 'ok';
    html += '<div class="lpd-callout ' + lpdCls + '">Leads/day: ' + VestaAPI.num(lpd) + '</div>';

    // ── Showing notes (from PropertyWeeklyNote or Showing feedback)
    if (u.showing_feedback && u.showing_feedback.length) {
      html += '<div class="showing-notes-block">';
      html += '<div class="metric-block-title">Showing Notes</div>';
      for (var sn = 0; sn < u.showing_feedback.length; sn++) {
        var sf = u.showing_feedback[sn];
        html += '<div class="showing-note-item">';
        if (sf.date) html += '<span class="sn-date">' + VestaAPI.dateStr(sf.date) + '</span> ';
        if (sf.prospect_name) html += '<strong>' + esc(sf.prospect_name) + '</strong>: ';
        html += esc(sf.feedback_summary);
        html += '</div>';
      }
      html += '</div>';
    }

    // ── Market context (single line with median)
    if (medians && medians.median_dom != null) {
      html += '<div class="market-ctx-line">Portfolio median DOM: ' + medians.median_dom +
        ' &middot; This unit: ' + dom + '</div>';
    }

    html += '</div>';
    return html;
  }

  function metricRowPill(label, val, prevVal, invertColor) {
    var pill = '';
    if (prevVal != null && prevVal !== val) {
      var diff = val - prevVal;
      var cls;
      if (invertColor) {
        cls = diff > 0 ? 'up-bad' : 'down-good';
      } else {
        cls = diff > 0 ? 'up' : 'down';
      }
      pill = '<span class="wow-pill ' + cls + '">' + (diff > 0 ? '+' : '') + diff + '</span>';
    }
    return '<div class="metric-row"><span>' + label + '</span><span class="m-val">' + val + pill + '</span></div>';
  }

  function metricRowSimple(label, val) {
    return '<div class="metric-row"><span>' + label + '</span><span class="m-val">' + val + '</span></div>';
  }

  // Column 3 — Message to Owner
  function renderCol3(u, owner) {
    var html = '<div class="listing-col">';

    // Get or generate draft text
    var text = propNoteMap[u.unit_id];
    if (text === undefined) {
      text = generateDraft(u, owner);
      propNoteMap[u.unit_id] = text;
    }

    html += '<textarea class="draft-textarea" data-unit-id="' + u.unit_id + '">' + esc(text) + '</textarea>';

    // Past notes
    if (u.prev_notes && u.prev_notes.length) {
      html += '<div class="past-notes-toggle" data-unit-id="' + u.unit_id + '">View past notes (' + u.prev_notes.length + ')</div>';
      html += '<div class="past-notes-content" data-unit-id="' + u.unit_id + '">';
      for (var p = 0; p < u.prev_notes.length; p++) {
        var pn = u.prev_notes[p];
        html += '<div class="past-note-item"><strong>' + VestaAPI.dateStr(pn.week_date) + '</strong> (' +
          esc(pn.author) + '): ' + esc(pn.note_text) + '</div>';
      }
      html += '</div>';
    }

    html += '</div>';
    return html;
  }

  // ── Listing Row Footer ──────────────────────────────────────────────────

  function renderListingRowFooter(u) {
    var saved = dirtyUnits[u.unit_id] === false ? '<span class="save-status" style="color:#28a745;">Saved</span>' : '';
    return '<div class="listing-row-footer" data-unit-id="' + u.unit_id + '">' +
      '<button class="btn btn-secondary btn-sm btn-save-row" data-unit-id="' + u.unit_id + '">Save Draft</button>' +
      saved +
    '</div>';
  }

  // ── Generate Draft ──────────────────────────────────────────────────────

  function generateDraft(u, owner) {
    var firstName = (owner.owner_name || '').split(' ')[0] || 'there';
    var address = shortAddress(u.address);
    var parts = [];

    // 1. Greeting + activity
    var leads = u.weekly.leads || 0;
    var showings = u.weekly.showings || 0;
    if (leads > 0 || showings > 0) {
      var bits = [];
      if (leads > 0) bits.push(numWord(leads) + ' new lead' + (leads !== 1 ? 's' : ''));
      if (showings > 0) bits.push(numWord(showings) + ' showing' + (showings !== 1 ? 's' : ''));
      parts.push('Hi ' + firstName + ' \u2014 this week we had ' + bits.join(' and ') + ' on ' + address + '.');
    } else {
      parts.push('Hi ' + firstName + ' \u2014 no new activity on ' + address + ' this week.');
    }

    // 2. Showing feedback (narrative)
    var feedback = u.showing_feedback || [];
    for (var f = 0; f < Math.min(feedback.length, 3); f++) {
      var fb = feedback[f];
      if (!fb.feedback_summary) continue;
      if (fb.prospect_name) {
        parts.push(fb.prospect_name + ': ' + lowerFirst(fb.feedback_summary) + '.');
      } else {
        // Note from staff (no individual prospect) — include as-is
        parts.push(fb.feedback_summary);
      }
    }

    // 3. Applications
    var apps = u.weekly.apps || 0;
    if (apps > 0) {
      parts.push('We received ' + numWord(apps) + ' new application' + (apps !== 1 ? 's' : '') + ' this week.');
    }

    // 4. Price recommendation
    if (u.price_recommendation && u.price_recommendation.recommended && u.price_recommendation.suggested_price) {
      var suggested = parseFloat(u.price_recommendation.suggested_price);
      var current = parseFloat(u.current_list_price || 0);
      var diff = Math.abs(current - suggested);
      parts.push('Activity is below our target, so we\'d recommend a $' + Math.round(diff) +
        ' price adjustment to $' + formatNumber(Math.round(suggested)) + ' to increase showing traffic.');
    }

    // 5. Upcoming showings
    var upcoming = u.upcoming_showings || [];
    if (upcoming.length > 0) {
      parts.push('We have ' + numWord(upcoming.length) + ' showing' +
        (upcoming.length !== 1 ? 's' : '') + ' coming up this week.');
    }

    // 6. Closing
    parts.push('We\'ll keep you posted.');

    return parts.join(' ');
  }

  function shortAddress(addr) {
    if (!addr) return 'this property';
    // Remove city/state/zip if present after a comma
    var idx = addr.indexOf(',');
    return idx > 0 ? addr.substring(0, idx).trim() : addr;
  }

  function numWord(n) {
    if (n === 1) return 'one';
    if (n === 2) return 'two';
    return String(n);
  }

  function lowerFirst(s) {
    if (!s) return '';
    return s.charAt(0).toLowerCase() + s.slice(1);
  }

  function formatNumber(n) {
    return n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  // ── Accordion Events ───────────────────────────────────────────────────

  function bindAccordionEvents() {
    // Headers (toggle)
    var headers = document.querySelectorAll('.portfolio-section-header');
    for (var i = 0; i < headers.length; i++) {
      headers[i].addEventListener('click', onToggleSection);
    }

    // Save Draft per row
    var saveRowBtns = document.querySelectorAll('.btn-save-row');
    for (var j = 0; j < saveRowBtns.length; j++) {
      saveRowBtns[j].addEventListener('click', onSaveRow);
    }

    // Textarea input tracking
    var textareas = document.querySelectorAll('.draft-textarea');
    for (var k = 0; k < textareas.length; k++) {
      textareas[k].addEventListener('input', onTextareaInput);
    }

    // Past notes toggles
    var toggles = document.querySelectorAll('.past-notes-toggle');
    for (var t = 0; t < toggles.length; t++) {
      toggles[t].addEventListener('click', onTogglePastNotes);
    }

    // Preview per owner
    var previewBtns = document.querySelectorAll('.btn-preview-owner');
    for (var p = 0; p < previewBtns.length; p++) {
      previewBtns[p].addEventListener('click', onPreviewOwner);
    }

    // Send per owner
    var sendBtns = document.querySelectorAll('.btn-send-owner');
    for (var s = 0; s < sendBtns.length; s++) {
      sendBtns[s].addEventListener('click', onSendOwner);
    }
  }

  function onToggleSection(e) {
    var name = e.currentTarget.getAttribute('data-portfolio');
    expandedSections[name] = !expandedSections[name];

    // Toggle via CSS class (no full re-render)
    var section = e.currentTarget.closest('.portfolio-section');
    if (section) {
      if (expandedSections[name]) {
        section.classList.add('expanded');
      } else {
        section.classList.remove('expanded');
      }
    }
  }

  function onTextareaInput(e) {
    var unitId = parseInt(e.target.getAttribute('data-unit-id'), 10);
    propNoteMap[unitId] = e.target.value;
    dirtyUnits[unitId] = true;
  }

  function onTogglePastNotes(e) {
    var unitId = e.currentTarget.getAttribute('data-unit-id');
    var content = document.querySelector('.past-notes-content[data-unit-id="' + unitId + '"]');
    if (content) {
      content.classList.toggle('open');
    }
  }

  // ── Save Per Row ────────────────────────────────────────────────────────

  function onSaveRow(e) {
    var unitId = parseInt(e.currentTarget.getAttribute('data-unit-id'), 10);
    saveSingleUnit(unitId);
  }

  function saveSingleUnit(unitId) {
    var textarea = document.querySelector('.draft-textarea[data-unit-id="' + unitId + '"]');
    var text = textarea ? textarea.value.trim() : (propNoteMap[unitId] || '');

    // Find which owner this unit belongs to
    var ownerId = unitOwnerMap[unitId] || findOwnerForUnit(unitId);

    return VestaAPI.post('/dashboard/property-notes', {
      unit_id: unitId,
      week_date: weekDate,
      author: 'Staff',
      note_text: text,
    }).then(function () {
      dirtyUnits[unitId] = false;
      propNoteMap[unitId] = text;

      // Ensure owner note exists as draft
      if (ownerId) {
        return ensureOwnerNote(ownerId);
      }
    }).then(function () {
      // Update footer status
      var footer = document.querySelector('.listing-row-footer[data-unit-id="' + unitId + '"]');
      if (footer) {
        var existing = footer.querySelector('.save-status');
        if (existing) {
          existing.textContent = 'Saved';
          existing.style.color = '#28a745';
        } else {
          var span = document.createElement('span');
          span.className = 'save-status';
          span.style.color = '#28a745';
          span.textContent = 'Saved';
          footer.appendChild(span);
        }
      }
      VestaAPI.toast('Draft saved', 'success');
    }).catch(function (err) {
      console.error('Save error:', err);
      VestaAPI.toast('Error saving draft', 'error');
    });
  }

  function findOwnerForUnit(unitId) {
    for (var i = 0; i < owners.length; i++) {
      var report = ownerReports[owners[i].owner_id];
      if (report && report.units) {
        for (var j = 0; j < report.units.length; j++) {
          if (report.units[j].unit_id === unitId) return owners[i].owner_id;
        }
      }
    }
    return null;
  }

  function ensureOwnerNote(ownerId) {
    if (noteMap[ownerId]) return Promise.resolve(noteMap[ownerId]);

    return VestaAPI.post('/dashboard/owner-notes', {
      owner_id: ownerId,
      report_date: weekDate,
      status: 'draft',
      notes_text: '',
      email_subject: 'Weekly Listing Update \u2014 ' + VestaAPI.dateStr(weekDate),
      email_body: '',
    }).then(function (saved) {
      noteMap[ownerId] = saved;
      updateReadyCount();
      return saved;
    });
  }

  // ── Save All Drafts ─────────────────────────────────────────────────────

  function saveAllDrafts() {
    var unitIds = Object.keys(dirtyUnits);
    var toSave = [];
    for (var i = 0; i < unitIds.length; i++) {
      if (dirtyUnits[unitIds[i]]) {
        toSave.push(parseInt(unitIds[i], 10));
      }
    }

    if (!toSave.length) {
      VestaAPI.toast('No unsaved changes', 'success');
      return;
    }

    var promises = [];
    for (var j = 0; j < toSave.length; j++) {
      promises.push(saveSingleUnit(toSave[j]));
    }

    Promise.all(promises).then(function () {
      VestaAPI.toast('All drafts saved', 'success');
    }).catch(function (err) {
      console.error('Save all error:', err);
      VestaAPI.toast('Some drafts failed to save', 'error');
    });
  }

  // ── Send Per Owner ──────────────────────────────────────────────────────

  function onPreviewOwner(e) {
    var ownerId = parseInt(e.currentTarget.getAttribute('data-owner-id'), 10);
    previewOwner(ownerId);
  }

  function onSendOwner(e) {
    var ownerId = parseInt(e.currentTarget.getAttribute('data-owner-id'), 10);
    sendOwner(ownerId);
  }

  function saveAllUnitsForOwner(ownerId) {
    var report = ownerReports[ownerId];
    if (!report || !report.units) return Promise.resolve();

    var promises = [];
    for (var i = 0; i < report.units.length; i++) {
      var unitId = report.units[i].unit_id;
      var text = propNoteMap[unitId] || '';
      promises.push(
        VestaAPI.post('/dashboard/property-notes', {
          unit_id: unitId,
          week_date: weekDate,
          author: 'Staff',
          note_text: text,
        })
      );
      dirtyUnits[unitId] = false;
    }

    return Promise.all(promises);
  }

  function previewOwner(ownerId) {
    // Save all unit notes first, ensure owner note, then preview
    saveAllUnitsForOwner(ownerId).then(function () {
      return ensureOwnerNote(ownerId);
    }).then(function () {
      var note = noteMap[ownerId];
      if (!note || !note.id) {
        VestaAPI.toast('Error: no note found', 'error');
        return;
      }
      return VestaAPI.post('/dashboard/owner-notes/' + note.id + '/preview', {});
    }).then(function (result) {
      if (!result) return;
      var modal = document.getElementById('email-preview-modal');
      var iframe = document.getElementById('preview-iframe');
      modal.style.display = 'block';
      iframe.srcdoc = result.html;
    }).catch(function (err) {
      console.error('Preview error:', err);
      VestaAPI.toast('Error loading preview', 'error');
    });
  }

  function sendOwner(ownerId) {
    saveAllUnitsForOwner(ownerId).then(function () {
      return ensureOwnerNote(ownerId);
    }).then(function () {
      var note = noteMap[ownerId];
      if (!note || !note.id) {
        VestaAPI.toast('Error: no note found', 'error');
        return Promise.reject(new Error('no note'));
      }
      // Mark as reviewed then send
      return VestaAPI.put('/dashboard/owner-notes/' + note.id, { status: 'reviewed' });
    }).then(function (updated) {
      noteMap[ownerId] = updated;
      return VestaAPI.post('/dashboard/owner-notes/' + updated.id + '/send', {});
    }).then(function (result) {
      if (result.status === 'error') {
        VestaAPI.toast('Error: ' + (result.detail || 'Send failed'), 'error');
        return;
      }
      noteMap[ownerId] = result;
      if (result.sent_at) {
        lastSentMap[ownerId] = result.sent_at;
      }
      updateReadyCount();
      var ownerName = '';
      for (var i = 0; i < owners.length; i++) {
        if (owners[i].owner_id === ownerId) { ownerName = owners[i].owner_name; break; }
      }
      VestaAPI.toast('Email sent to ' + ownerName, 'success');
    }).catch(function (err) {
      if (err && err.message === 'no note') return;
      console.error('Send error:', err);
      VestaAPI.toast('Error sending email', 'error');
    });
  }

  function closePreview() {
    document.getElementById('email-preview-modal').style.display = 'none';
  }

  // ── Send All Reviewed ──────────────────────────────────────────────────

  function sendAllReviewed() {
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
    var chain = Promise.resolve();
    for (var j = 0; j < reviewedNotes.length; j++) {
      (function (note) {
        chain = chain.then(function () {
          return VestaAPI.post('/dashboard/owner-notes/' + note.id + '/send', {}).then(function (result) {
            if (result.status !== 'error') {
              noteMap[result.owner_id] = result;
              if (result.sent_at) lastSentMap[result.owner_id] = result.sent_at;
              sentCount++;
            }
          }).catch(function (err) {
            console.error('Send error for note ' + note.id + ':', err);
          });
        });
      })(reviewedNotes[j]);
    }

    chain.then(function () {
      updateReadyCount();
      renderPage();
      VestaAPI.toast('Sent ' + sentCount + ' of ' + reviewedNotes.length + ' emails', 'success');
    });
  }

  // ── Ready Count ────────────────────────────────────────────────────────

  function updateReadyCount() {
    var reviewed = 0;
    var total = owners.length;
    var keys = Object.keys(noteMap);
    for (var i = 0; i < keys.length; i++) {
      if (noteMap[keys[i]].status === 'reviewed') reviewed++;
    }
    var el = document.getElementById('ready-count');
    if (el) {
      el.textContent = reviewed + ' of ' + total + ' owner' + (total !== 1 ? 's' : '') + ' ready to send';
    }
  }

  // ── DOM Badge ──────────────────────────────────────────────────────────

  function domClass(dom) {
    if (dom >= 60) return 'dom-danger';
    if (dom >= 30) return 'dom-warn';
    return 'dom-ok';
  }

  // ── Helpers ────────────────────────────────────────────────────────────

  function esc(text) {
    if (text == null) return '';
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(String(text)));
    return div.innerHTML;
  }

});
