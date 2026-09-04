// pstore AI field autofill.
//
// Any input/textarea carrying `data-ai-fill="<field>"` gets a small "✨ AI"
// button beside it. Clicking it asks the backend (POST /api/ai/fill) to write
// professional, field-specific copy for that field (the field name selects the
// exact persuasion brief), then fills the field.
//
// Optional attrs on the same element:
//   data-ai-niche    override the niche keyword (defaults to the page niche)
//   data-ai-hint     extra direction for the AI (besides the field brief)
//   data-ai-append   if set, the generated copy is appended instead of replaced
(function () {
  'use strict';

  var existing = document.getElementById('ai-fill-style');
  if (!existing) {
    var st = document.createElement('style');
    st.id = 'ai-fill-style';
    st.textContent =
      '.ai-fill-wrap{display:flex;align-items:flex-end;gap:8px;flex-wrap:wrap}' +
      '.ai-fill-wrap>input,.ai-fill-wrap>select{flex:1 1 200px;min-width:160px}' +
      '.ai-fill-wrap>textarea{flex:1 1 100%;width:100%}' +
      'button.ai-fill-btn{flex:0 0 auto;min-height:32px;padding:0 12px;font-size:12px;' +
      'background:#fff;color:var(--accent2,#7c5cff);border:1px solid var(--accent2,#7c5cff);' +
      'box-shadow:none;white-space:nowrap}' +
      'button.ai-fill-btn:hover{box-shadow:none;filter:brightness(1.05)}' +
      'button.ai-fill-btn.loading{opacity:.6;pointer-events:none}' +
      'button.ai-fill-btn.error{color:#b42318;border-color:#b42318}' +
      '.ai-fill-note{width:100%;font-size:12px;color:var(--muted,#887b94);margin:2px 0 0}';
    document.head.appendChild(st);
  }

  // Default niche: from a hidden input or the page title/dataset.
  function pageNiche() {
    var el = document.querySelector('[data-page-niche]');
    if (el) return el.getAttribute('data-page-niche') || '';
    var meta = document.querySelector('meta[name="pstore-niche"]');
    return meta ? (meta.getAttribute('content') || '') : '';
  }

  async function post(path, body) {
    var r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      credentials: 'same-origin',
    });
    return r.json();
  }

  function setVal(el, text) {
    var v = text || '';
    if (el.getAttribute('data-ai-append') !== null &&
        (el.value || '').trim() && v) {
      el.value = (el.value.trimEnd() + '\n' + v).trim();
    } else {
      el.value = v;
    }
    // Notify any framework listeners so "Save all" sees the change.
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function showNote(box, msg, isErr) {
    var note = box.parentNode.querySelector('.' + 'ai-fill-note');
    if (!note) {
      note = document.createElement('span');
      note.className = 'ai-fill-note';
      note.textContent = msg;
      box.insertAdjacentElement('afterend', note);
    } else {
      note.textContent = msg;
    }
    note.style.color = isErr ? '#b42318' : '';
  }

  function wrap(el) {
    if (el.dataset && el.dataset.aiWrapped) return;
    var box = document.createElement('div');
    box.className = 'ai-fill-wrap';
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'ai-fill-btn';
    btn.textContent = '✨ AI';
    btn.title = 'Auto-write this field with AI';
    el.parentNode.insertBefore(box, el);
    box.appendChild(btn);
    box.appendChild(el);
    if (el.dataset) el.dataset.aiWrapped = '1';
    btn.addEventListener('click', function () {
      var field = el.getAttribute('data-ai-fill') || 'generic';
      var niche = el.getAttribute('data-ai-niche') || pageNiche() || '';
      var hint = el.getAttribute('data-ai-hint') || '';
      btn.classList.add('loading');
      showNote(btn, 'Writing…');
      post('/api/ai/fill', {
        field: field,
        niche: niche,
        current: el.value || '',
        hint: hint,
      }).then(function (d) {
        btn.classList.remove('loading');
        if (d && d.ok && d.text !== undefined) {
          setVal(el, d.text);
          showNote(btn, '✓ Filled');
        } else if (d && d.error) {
          btn.classList.add('error');
          showNote(btn, d.error, true);
        } else {
          btn.classList.add('error');
          showNote(btn, 'The AI didn\'t return copy — try again.', true);
        }
      }).catch(function (e) {
        btn.classList.remove('loading');
        btn.classList.add('error');
        showNote(btn, 'Request failed: ' + e, true);
      });
    });
  }

  function init() {
    document.querySelectorAll('input[data-ai-fill], textarea[data-ai-fill]')
      .forEach(wrap);
  }

  // Expose for programmatic wiring on dynamic pages.
  window.aiFill = { init: init, wrap: wrap };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
