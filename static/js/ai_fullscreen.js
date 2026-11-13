/* ==========================================================================
   Smart Study — Full-screen AI Assistant (Gemini-style)
   Vanilla JS, no build step. Talks to the /ai/api/* endpoints in app.py.
   ========================================================================== */
(function () {
  'use strict';

  var CFG = window.AIFS || {};
  var URLS = CFG.urls || {};

  // ------------------------------------------------------------------ utils
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function urlFor(tpl, id) { return tpl.replace('987654321', String(id)); }

  var ICON = {
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    up: '<path d="M7 11v9H4v-9h3zM7 11l4-8a2 2 0 0 1 2 2v4h5.5a2 2 0 0 1 2 2.3l-1.2 7a2 2 0 0 1-2 1.7H7"/>',
    down: '<path d="M17 13V4h3v9h-3zM17 13l-4 8a2 2 0 0 1-2-2v-4H5.5a2 2 0 0 1-2-2.3l1.2-7a2 2 0 0 1 2-1.7H17"/>',
    refresh: '<path d="M20 11a8 8 0 0 0-14.5-4M4 4v4h4M4 13a8 8 0 0 0 14.5 4M20 20v-4h-4"/>',
    speaker: '<path d="M11 5 6 9H3v6h3l5 4V5zM15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"/>',
    pencil: '<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16v4zM13.5 6.5l4 4"/>',
    file: '<path d="M6 3.5h9l4.5 4.5V20a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1Z"/><path d="M15 3.5V8h4.5"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    more: '<circle cx="12" cy="5" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="12" cy="19" r="1.4"/>',
    pin: '<path d="M9 4h6l-1 6 3 3H7l3-3-1-6zM12 13v7"/>',
    rename: '<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16v4z"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-12M9 7V4h6v3"/>',
    sparkle: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15z"/>',
    book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15Z"/>',
    quiz: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 17h.01"/>',
    list: '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
    cards: '<rect x="3" y="6" width="14" height="12" rx="2"/><path d="M7 3h12a2 2 0 0 1 2 2v10"/>',
    calendar: '<rect x="3.5" y="5" width="17" height="16" rx="2.5"/><path d="M8 3v4M16 3v4M3.5 10h17"/>',
    calc: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 7h8M8 12h.01M12 12h.01M16 12h.01M8 16h.01M12 16h.01M16 16h.01"/>',
    pen: '<path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>',
    code: '<path d="M8 8l-5 4 5 4M16 8l5 4-5 4M14 5l-4 14"/>'
  };
  function svg(name, size) {
    size = size || 16;
    return '<svg width="' + size + '" height="' + size + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (ICON[name] || '') + '</svg>';
  }

  // ------------------------------------------------------------------ state
  var S = {
    convId: null,
    messages: [],       // [{id, role, content, attachments}]
    title: 'New chat',
    sending: false,
    abort: null,
    revealStop: null,
    mode: null,
    files: [],
    noteId: '',
    stick: true,
    conversations: [],
    speakingBtn: null,
    recognizing: false
  };

  var MODES = {
    explain:    { label: 'Explain simply',   icon: 'book',     desc: 'Beginner-friendly, then deeper' },
    quiz:       { label: 'Quiz me',          icon: 'quiz',     desc: 'One question at a time' },
    summarize:  { label: 'Summarize',        icon: 'list',     desc: 'Headings + key takeaways' },
    flashcards: { label: 'Flashcards',       icon: 'cards',    desc: 'Question / answer table' },
    plan:       { label: 'Study plan',       icon: 'calendar', desc: 'Time blocks and revision' },
    solve:      { label: 'Solve step by step', icon: 'calc',   desc: 'Show every step' },
    essay:      { label: 'Writing help',     icon: 'pen',      desc: 'Outline, then a draft' },
    code:       { label: 'Code help',        icon: 'code',     desc: 'Code with explanations' }
  };

  // ------------------------------------------------------------- DOM refs
  var el = {};
  function grabDom() {
    ['aifs', 'aifsSide', 'aifsScrim', 'sideToggle', 'sideToggleInner', 'newChatBtn', 'chatSearch', 'chatList',
     'clearAllBtn', 'noteSelect', 'exportBtn', 'fsBtn', 'themeBtn', 'thread', 'emptyState', 'helloLine',
     'suggestGrid', 'messages', 'scrollDown', 'composer', 'fileStrip', 'prompt', 'attachBtn', 'toolsBtn',
     'toolsPop', 'modeChip', 'micBtn', 'sendBtn', 'fileInput', 'dlg', 'dlgForm', 'dlgTitle', 'dlgText',
     'dlgInput', 'dlgCancel', 'dlgOk', 'ctxMenu', 'toast'
    ].forEach(function (id) { el[id] = $(id); });
  }

  // ------------------------------------------------------------- toast
  var toastTimer = null;
  function toast(msg) {
    el.toast.textContent = msg;
    el.toast.classList.add('is-show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.toast.classList.remove('is-show'); }, 2800);
  }

  // ------------------------------------------------------------- markdown
  // Math is lifted out before Markdown runs (so _ and * inside formulas survive)
  // and put back afterwards for KaTeX. Written without regex look-behind so it
  // also works on older Safari versions.
  var MATH_BLOCK_RE = /\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)/g;
  var MATH_INLINE_RE = /(^|[^\\$])\$([^\s$](?:[^$\n]*?[^\s$\\])?)\$(?!\d)/g;

  function renderMarkdown(text) {
    text = text || '';
    if (!window.marked) return '<p>' + esc(text).replace(/\n/g, '<br>') + '</p>';
    var stash = [];
    var protectedText = text.replace(MATH_BLOCK_RE, function (m) {
      stash.push(m);
      return '@@M' + (stash.length - 1) + '@@';
    }).replace(MATH_INLINE_RE, function (m, pre, body) {
      stash.push('$' + body + '$');
      return pre + '@@M' + (stash.length - 1) + '@@';
    });
    var html;
    try {
      html = window.marked.parse(protectedText, { gfm: true, breaks: true });
    } catch (e) {
      html = '<p>' + esc(text).replace(/\n/g, '<br>') + '</p>';
    }
    html = html.replace(/@@M(\d+)@@/g, function (_, i) { return esc(stash[Number(i)]); });
    if (window.DOMPurify) {
      html = window.DOMPurify.sanitize(html, { ADD_ATTR: ['target'] });
    } else {
      // No sanitizer available (CDN blocked): fall back to safe plain text.
      html = '<p>' + esc(text).replace(/\n/g, '<br>') + '</p>';
    }
    return html;
  }

  function enhance(root) {
    root.querySelectorAll('pre').forEach(function (pre) {
      if (pre.parentElement && pre.parentElement.classList.contains('code-wrap')) return;
      var code = pre.querySelector('code');
      var m = code && code.className.match(/language-([\w+-]+)/);
      var lang = m ? m[1] : 'code';
      var wrap = document.createElement('div');
      wrap.className = 'code-wrap';
      wrap.innerHTML = '<div class="code-head"><span>' + esc(lang) + '</span>' +
        '<button type="button" class="code-copy">' + svg('copy', 14) + '<em>Copy</em></button></div>';
      pre.parentNode.replaceChild(wrap, pre);
      wrap.appendChild(pre);
      if (window.hljs && code) { try { window.hljs.highlightElement(code); } catch (e) { /* ignore */ } }
    });
    root.querySelectorAll('table').forEach(function (t) {
      if (t.parentElement && t.parentElement.classList.contains('tbl-wrap')) return;
      var w = document.createElement('div');
      w.className = 'tbl-wrap';
      t.parentNode.replaceChild(w, t);
      w.appendChild(t);
    });
    root.querySelectorAll('a[href]').forEach(function (a) {
      a.setAttribute('target', '_blank');
      a.setAttribute('rel', 'noopener noreferrer');
    });
    if (window.renderMathInElement) {
      try {
        window.renderMathInElement(root, {
          delimiters: [
            { left: '$$', right: '$$', display: true },
            { left: '\\[', right: '\\]', display: true },
            { left: '\\(', right: '\\)', display: false },
            { left: '$', right: '$', display: false }
          ],
          throwOnError: false
        });
      } catch (e) { /* ignore */ }
    }
  }

  // ------------------------------------------------------------- scrolling
  function nearBottom() {
    var t = el.thread;
    return t.scrollHeight - t.scrollTop - t.clientHeight < 90;
  }
  function scrollToBottom(force) {
    if (force || S.stick) el.thread.scrollTop = el.thread.scrollHeight;
  }

  // ------------------------------------------------------------- messages UI
  function attachmentsHTML(atts) {
    if (!atts || !atts.length) return '';
    return '<div class="u-atts">' + atts.map(function (a) {
      if (a.kind === 'image' && a.url) {
        return '<a class="u-att-img" href="' + esc(a.url) + '" target="_blank" rel="noopener"><img src="' + esc(a.url) + '" alt="' + esc(a.name) + '"></a>';
      }
      return '<span class="u-att-doc">' + svg('file', 15) + '<span>' + esc(a.name || 'file') + '</span></span>';
    }).join('') + '</div>';
  }

  function userNode(msg) {
    var n = document.createElement('div');
    n.className = 'msg msg-user';
    if (msg.id) n.dataset.id = msg.id;
    n.innerHTML =
      '<div class="u-wrap">' + attachmentsHTML(msg.attachments) +
      '<div class="u-bubble"></div>' +
      '<div class="msg-actions u-actions">' +
        '<button type="button" class="act" data-act="edit" title="Edit">' + svg('pencil', 15) + '</button>' +
        '<button type="button" class="act" data-act="copy" title="Copy">' + svg('copy', 15) + '</button>' +
      '</div></div>';
    n.querySelector('.u-bubble').textContent = msg.content;
    return n;
  }

  function aiNode(msg) {
    var n = document.createElement('div');
    n.className = 'msg msg-ai';
    if (msg && msg.id) n.dataset.id = msg.id;
    n.innerHTML =
      '<div class="ai-avatar">' + svg('sparkle', 18) + '</div>' +
      '<div class="ai-body"><div class="md"></div>' +
      '<div class="msg-actions ai-actions" hidden>' +
        '<button type="button" class="act" data-act="like" title="Good response">' + svg('up', 16) + '</button>' +
        '<button type="button" class="act" data-act="dislike" title="Bad response">' + svg('down', 16) + '</button>' +
        '<button type="button" class="act" data-act="copy" title="Copy">' + svg('copy', 16) + '</button>' +
        '<button type="button" class="act" data-act="listen" title="Read aloud">' + svg('speaker', 16) + '</button>' +
        '<button type="button" class="act" data-act="regen" title="Regenerate">' + svg('refresh', 16) + '</button>' +
      '</div></div>';
    if (msg && msg.content != null) {
      var md = n.querySelector('.md');
      md.innerHTML = renderMarkdown(msg.content);
      enhance(md);
      n.querySelector('.ai-actions').hidden = false;
      n._text = msg.content;
      applyFeedbackState(n);
    }
    return n;
  }

  function skeletonNode() {
    var n = document.createElement('div');
    n.className = 'msg msg-ai is-pending';
    n.innerHTML = '<div class="ai-avatar is-spin">' + svg('sparkle', 18) + '</div>' +
      '<div class="ai-body"><div class="skeleton"><i></i><i></i><i></i></div></div>';
    return n;
  }

  function updateRegenVisibility() {
    var all = el.messages.querySelectorAll('.msg-ai');
    all.forEach(function (n, i) {
      var b = n.querySelector('[data-act="regen"]');
      if (b) b.style.display = (i === all.length - 1) ? '' : 'none';
    });
    var users = el.messages.querySelectorAll('.msg-user');
    users.forEach(function (n, i) {
      var b = n.querySelector('[data-act="edit"]');
      if (b) b.style.display = (i === users.length - 1 && n.dataset.id) ? '' : 'none';
    });
  }

  function showEmpty(show) {
    el.emptyState.style.display = show ? '' : 'none';
    el.aifs.classList.toggle('is-empty', show);
  }

  function renderThread() {
    el.messages.innerHTML = '';
    S.messages.forEach(function (m) {
      el.messages.appendChild(m.role === 'user' ? userNode(m) : aiNode(m));
    });
    showEmpty(S.messages.length === 0);
    updateRegenVisibility();
    S.stick = true;
    scrollToBottom(true);
  }

  // feedback (👍/👎) is remembered per message in this browser
  function fbKey(node) { return 'aifs-fb-' + (node.dataset.id || ''); }
  function applyFeedbackState(node) {
    if (!node.dataset.id) return;
    var v = null;
    try { v = localStorage.getItem(fbKey(node)); } catch (e) { /* ignore */ }
    node.querySelectorAll('[data-act="like"],[data-act="dislike"]').forEach(function (b) {
      b.classList.toggle('is-on', b.dataset.act === v);
    });
  }

  // ------------------------------------------------------------- typewriter
  function reveal(node, fullText, done) {
    var md = node.querySelector('.md');
    var total = fullText.length;
    var step = Math.max(3, Math.ceil(total / 140));
    var shown = 0;
    var cancelled = false;
    var raf = 0;

    node.classList.add('is-typing');
    S.revealStop = function () { cancelled = true; };

    function frame() {
      shown = Math.min(total, shown + step);
      md.innerHTML = renderMarkdown(fullText.slice(0, shown)) + '<span class="caret"></span>';
      scrollToBottom(false);
      if (shown < total && !cancelled) {
        raf = requestAnimationFrame(frame);
      } else {
        S.revealStop = null;
        node.classList.remove('is-typing');
        var finalText = cancelled ? fullText.slice(0, shown) : fullText;
        md.innerHTML = renderMarkdown(finalText);
        enhance(md);
        node._text = finalText;
        node.querySelector('.ai-actions').hidden = false;
        scrollToBottom(false);
        if (done) done(cancelled);
      }
    }
    raf = requestAnimationFrame(frame);
    return function cancel() { cancelled = true; };
  }

  // ------------------------------------------------------------- send / stop
  function setSending(on) {
    S.sending = on;
    el.aifs.classList.toggle('is-sending', on);
    el.sendBtn.disabled = on ? false : !canSend();
    el.sendBtn.setAttribute('aria-label', on ? 'Stop' : 'Send');
  }
  function canSend() {
    return el.prompt.value.trim().length > 0 || S.files.length > 0;
  }
  function refreshSendBtn() { if (!S.sending) el.sendBtn.disabled = !canSend(); }

  function stopGenerating() {
    if (S.abort) { try { S.abort.abort(); } catch (e) { /* ignore */ } }
    if (S.revealStop) S.revealStop();
  }

  function removeNodesAfter(node, inclusive) {
    var cur = inclusive ? node : node.nextElementSibling;
    while (cur) {
      var next = cur.nextElementSibling;
      cur.remove();
      cur = next;
    }
  }

  async function send(opts) {
    opts = opts || {};
    if (S.sending) return;
    var text = (opts.text !== undefined ? opts.text : el.prompt.value).trim();
    var regenerate = !!opts.regenerate;
    var editId = opts.editId || null;
    var files = regenerate ? [] : (opts.files || S.files.slice());

    if (!regenerate && !text && !files.length) return;

    // ---- optimistic UI -----------------------------------------------
    showEmpty(false);
    var userEl = null;

    if (regenerate) {
      var aiNodes = el.messages.querySelectorAll('.msg-ai');
      if (aiNodes.length) aiNodes[aiNodes.length - 1].remove();
    } else {
      if (editId) {
        var oldEl = el.messages.querySelector('.msg-user[data-id="' + editId + '"]');
        if (oldEl) removeNodesAfter(oldEl, true);
      }
      var localAtts = files.map(function (f) {
        var isImg = /^image\//.test(f.type);
        return { name: f.name, kind: isImg ? 'image' : 'doc', url: isImg ? URL.createObjectURL(f) : null };
      });
      userEl = userNode({ content: text || 'Please look at the attached file(s).', attachments: localAtts });
      el.messages.appendChild(userEl);
    }
    var pending = skeletonNode();
    el.messages.appendChild(pending);
    S.stick = true;
    scrollToBottom(true);

    var modeUsed = S.mode;
    if (!regenerate) {
      if (opts.text === undefined) {
        el.prompt.value = '';
        autosize();
        S.files = [];
        renderFiles();
      }
      setMode(null);
      el.prompt.placeholder = 'Ask Smart Study AI anything…';
    }
    setSending(true);

    var fd = new FormData();
    if (!regenerate) {
      fd.append('message', text);
      files.forEach(function (f) { fd.append('files', f, f.name); });
      if (modeUsed) fd.append('mode', modeUsed);
    } else {
      fd.append('regenerate', '1');
    }
    if (S.convId) fd.append('conversation_id', S.convId);
    if (editId) fd.append('edit_message_id', editId);
    fd.append('note_id', S.noteId || '');

    S.abort = new AbortController();
    var data = null;
    try {
      var res = await fetch(URLS.chat, { method: 'POST', body: fd, signal: S.abort.signal });
      data = await res.json().catch(function () { return null; });
      if (!res.ok || !data || data.error) {
        throw new Error((data && data.error) || 'Something went wrong. Please try again.');
      }
    } catch (err) {
      S.abort = null;
      pending.remove();
      if (err && err.name === 'AbortError') {
        toast('Stopped');
      } else {
        var errNode = aiNode({ content: '**Sorry —** ' + (err && err.message ? err.message : 'something went wrong.') });
        errNode.classList.add('is-error');
        errNode.querySelector('.ai-actions').hidden = true;
        el.messages.appendChild(errNode);
      }
      setSending(false);
      updateRegenVisibility();
      return;
    }
    S.abort = null;

    // ---- success: sync state ------------------------------------------
    S.convId = data.conversation.id;
    S.title = data.conversation.title;
    if (history.replaceState) history.replaceState(null, '', URLS.page + '?c=' + S.convId);

    if (regenerate) {
      // drop the old assistant message from local state
      for (var i = S.messages.length - 1; i >= 0; i--) {
        if (S.messages[i].role === 'assistant') { S.messages.splice(i, 1); break; }
      }
    } else {
      if (editId) {
        var idx = S.messages.findIndex(function (m) { return String(m.id) === String(editId); });
        if (idx >= 0) S.messages.splice(idx);
      }
      S.messages.push(data.user_message);
      if (userEl) {
        userEl.dataset.id = data.user_message.id;
        var attHtml = attachmentsHTML(data.user_message.attachments);
        var oldAtts = userEl.querySelector('.u-atts');
        if (oldAtts) oldAtts.outerHTML = attHtml;
        else if (attHtml) userEl.querySelector('.u-bubble').insertAdjacentHTML('beforebegin', attHtml);
      }
    }
    S.messages.push(data.assistant_message);

    // swap skeleton for the real assistant node and type the answer out
    var aiEl = aiNode({ id: data.assistant_message.id });
    pending.replaceWith(aiEl);
    updateRegenVisibility();
    reveal(aiEl, data.assistant_message.content, function () {
      setSending(false);
      updateRegenVisibility();
      applyFeedbackState(aiEl);
      el.prompt.focus();
    });

    loadConversations();
  }

  // ------------------------------------------------------------- composer
  function autosize() {
    var t = el.prompt;
    t.style.height = 'auto';
    t.style.height = Math.min(t.scrollHeight, 200) + 'px';
  }

  function renderFiles() {
    el.fileStrip.innerHTML = '';
    el.fileStrip.classList.toggle('has-files', S.files.length > 0);
    S.files.forEach(function (f, i) {
      var chip = document.createElement('div');
      chip.className = 'file-chip';
      var isImg = /^image\//.test(f.type);
      chip.innerHTML = (isImg
        ? '<img src="' + URL.createObjectURL(f) + '" alt="">'
        : '<span class="file-ic">' + svg('file', 16) + '</span>') +
        '<span class="file-name">' + esc(f.name) + '</span>' +
        '<button type="button" class="file-x" aria-label="Remove">' + svg('x', 13) + '</button>';
      chip.querySelector('.file-x').addEventListener('click', function () {
        S.files.splice(i, 1);
        renderFiles();
        refreshSendBtn();
      });
      el.fileStrip.appendChild(chip);
    });
    refreshSendBtn();
  }

  var ALLOWED_RE = /\.(png|jpe?g|webp|gif|pdf|docx|pptx|txt)$/i;
  function addFiles(list) {
    Array.prototype.forEach.call(list || [], function (f) {
      if (!f) return;
      if (!(/^image\//.test(f.type) || ALLOWED_RE.test(f.name))) {
        toast('"' + f.name + '" isn\'t supported.');
        return;
      }
      if (f.size > 10 * 1024 * 1024) { toast('"' + f.name + '" is larger than 10 MB.'); return; }
      if (S.files.length >= 4) { toast('You can attach up to 4 files.'); return; }
      S.files.push(f);
    });
    renderFiles();
    el.prompt.focus();
  }

  function setMode(key) {
    S.mode = key && MODES[key] ? key : null;
    if (S.mode) {
      el.modeChip.hidden = false;
      el.modeChip.innerHTML = svg(MODES[S.mode].icon, 14) + '<span>' + esc(MODES[S.mode].label) + '</span>' +
        '<button type="button" aria-label="Remove tool">' + svg('x', 12) + '</button>';
      el.modeChip.querySelector('button').addEventListener('click', function () { setMode(null); });
    } else {
      el.modeChip.hidden = true;
      el.modeChip.innerHTML = '';
    }
  }

  function buildToolsMenu() {
    el.toolsPop.innerHTML = '';
    (CFG.modes || Object.keys(MODES)).forEach(function (key) {
      var m = MODES[key];
      if (!m) return;
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'pop-item';
      b.setAttribute('role', 'menuitem');
      b.innerHTML = '<span class="pop-ic">' + svg(m.icon, 17) + '</span>' +
        '<span class="pop-txt"><b>' + esc(m.label) + '</b><small>' + esc(m.desc) + '</small></span>';
      b.addEventListener('click', function () {
        setMode(S.mode === key ? null : key);
        closePopups();
        el.prompt.focus();
      });
      el.toolsPop.appendChild(b);
    });
  }

  function closePopups() {
    el.toolsPop.classList.remove('is-open');
    el.toolsBtn.setAttribute('aria-expanded', 'false');
    el.ctxMenu.classList.remove('is-open');
  }

  // ------------------------------------------------------------- suggestions
  function buildSuggestions() {
    var subs = CFG.subjects || [];
    var a = subs[0], b = subs[1] || subs[0];
    var items = [
      { icon: 'book', title: 'Explain a concept',
        text: a ? 'Explain a tricky topic from ' + a + ' in simple words, with an example.' : 'Explain how photosynthesis works in simple words, with an example.',
        mode: 'explain' },
      { icon: 'quiz', title: 'Quiz me',
        text: b ? 'Quiz me on ' + b + ' — one question at a time.' : 'Quiz me on basic algebra — one question at a time.',
        mode: 'quiz' },
      { icon: 'calendar', title: 'Plan my study week',
        text: 'Make a realistic 7-day study plan for my upcoming exams, with breaks.',
        mode: 'plan' },
      { icon: 'calc', title: 'Solve step by step', text: '', mode: 'solve',
        placeholder: 'Type or paste the problem you want solved…' }
    ];
    el.suggestGrid.innerHTML = '';
    items.forEach(function (it) {
      var c = document.createElement('button');
      c.type = 'button';
      c.className = 'suggest-card';
      c.innerHTML = '<span class="suggest-ic">' + svg(it.icon, 20) + '</span>' +
        '<span class="suggest-title">' + esc(it.title) + '</span>' +
        '<span class="suggest-text">' + esc(it.text || it.placeholder) + '</span>';
      c.addEventListener('click', function () {
        if (it.text) {
          setMode(it.mode);
          send({ text: it.text, files: [] });
        } else {
          setMode(it.mode);
          el.prompt.placeholder = it.placeholder;
          el.prompt.focus();
        }
      });
      el.suggestGrid.appendChild(c);
    });
  }

  function buildGreeting() {
    var h = new Date().getHours();
    var part = h < 5 ? 'Hello' : h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : h < 22 ? 'Good evening' : 'Hello';
    el.helloLine.textContent = part + ', ' + (CFG.userName || 'there');
  }

  // ------------------------------------------------------------- sidebar list
  function groupOf(iso) {
    var d = new Date(iso), now = new Date();
    var a = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var b = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    var days = Math.round((a - b) / 86400000);
    if (days <= 0) return 'Today';
    if (days === 1) return 'Yesterday';
    if (days <= 7) return 'Previous 7 days';
    if (days <= 30) return 'Previous 30 days';
    return 'Older';
  }

  function renderList() {
    var list = el.chatList;
    list.innerHTML = '';
    if (!S.conversations.length) {
      list.innerHTML = '<p class="list-empty">' + (el.chatSearch.value ? 'No chats match your search.' : 'Your chats will appear here.') + '</p>';
      return;
    }
    var groups = {};
    var order = [];
    S.conversations.forEach(function (c) {
      var g = c.pinned ? 'Pinned' : groupOf(c.updated_at);
      if (!groups[g]) { groups[g] = []; order.push(g); }
      groups[g].push(c);
    });
    order.forEach(function (g) {
      var h = document.createElement('div');
      h.className = 'list-group';
      h.textContent = g;
      list.appendChild(h);
      groups[g].forEach(function (c) {
        var row = document.createElement('div');
        row.className = 'chat-item' + (c.id === S.convId ? ' is-active' : '');
        row.setAttribute('role', 'listitem');
        row.innerHTML = '<button type="button" class="chat-open" title="' + esc(c.title) + '">' +
          (c.pinned ? '<span class="pin-ic">' + svg('pin', 13) + '</span>' : '') +
          '<span class="chat-title">' + esc(c.title) + '</span></button>' +
          '<button type="button" class="chat-more" aria-label="Chat options">' + svg('more', 16) + '</button>';
        row.querySelector('.chat-open').addEventListener('click', function () { openConversation(c.id); closeSidebarOnMobile(); });
        row.querySelector('.chat-more').addEventListener('click', function (ev) { ev.stopPropagation(); openCtx(c, ev.currentTarget); });
        list.appendChild(row);
      });
    });
  }

  var searchTimer = null;
  async function loadConversations() {
    try {
      var q = el.chatSearch.value.trim();
      var res = await fetch(URLS.list + (q ? '?q=' + encodeURIComponent(q) : ''));
      var data = await res.json();
      S.conversations = data.conversations || [];
      renderList();
    } catch (e) { /* keep old list */ }
  }

  function openCtx(c, anchor) {
    var m = el.ctxMenu;
    m.innerHTML = '';
    [
      { k: 'pin', label: c.pinned ? 'Unpin' : 'Pin', icon: 'pin' },
      { k: 'rename', label: 'Rename', icon: 'rename' },
      { k: 'delete', label: 'Delete', icon: 'trash', danger: true }
    ].forEach(function (it) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'pop-item' + (it.danger ? ' is-danger' : '');
      b.innerHTML = '<span class="pop-ic">' + svg(it.icon, 16) + '</span><span class="pop-txt"><b>' + it.label + '</b></span>';
      b.addEventListener('click', function () { closePopups(); chatAction(it.k, c); });
      m.appendChild(b);
    });
    var r = anchor.getBoundingClientRect();
    m.classList.add('is-open');
    var top = Math.min(r.bottom + 4, window.innerHeight - m.offsetHeight - 8);
    var left = Math.min(r.left, window.innerWidth - m.offsetWidth - 8);
    m.style.top = Math.max(8, top) + 'px';
    m.style.left = Math.max(8, left) + 'px';
  }

  async function chatAction(kind, c) {
    try {
      if (kind === 'pin') {
        await fetch(urlFor(URLS.pin, c.id), { method: 'POST' });
        loadConversations();
      } else if (kind === 'rename') {
        var title = await askDialog({ title: 'Rename chat', input: c.title, ok: 'Save' });
        if (title) {
          var res = await fetch(urlFor(URLS.rename, c.id), {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: title })
          });
          if (res.ok && c.id === S.convId) S.title = title;
          loadConversations();
        }
      } else if (kind === 'delete') {
        var ok = await askDialog({ title: 'Delete chat?', text: '"' + c.title + '" will be permanently deleted.', ok: 'Delete', danger: true });
        if (ok) {
          await fetch(urlFor(URLS.del, c.id), { method: 'POST' });
          if (c.id === S.convId) newChat();
          loadConversations();
          toast('Chat deleted');
        }
      }
    } catch (e) { toast('Something went wrong. Please try again.'); }
  }

  // ------------------------------------------------------------- dialog
  function askDialog(o) {
    return new Promise(function (resolve) {
      var d = el.dlg;
      if (typeof d.showModal !== 'function') {
        if (o.input !== undefined) { resolve(window.prompt(o.title, o.input || '')); }
        else { resolve(window.confirm(o.title + (o.text ? '\n' + o.text : ''))); }
        return;
      }
      el.dlgTitle.textContent = o.title;
      el.dlgText.textContent = o.text || '';
      el.dlgText.style.display = o.text ? '' : 'none';
      el.dlgInput.style.display = o.input !== undefined ? '' : 'none';
      el.dlgInput.value = o.input || '';
      el.dlgOk.textContent = o.ok || 'OK';
      el.dlgOk.className = 'btn ' + (o.danger ? 'btn-danger' : 'btn-primary');

      function cleanup(result) {
        el.dlgForm.removeEventListener('submit', onSubmit);
        el.dlgCancel.removeEventListener('click', onCancel);
        d.removeEventListener('cancel', onCancel);
        if (d.open) d.close();
        resolve(result);
      }
      function onSubmit(ev) {
        ev.preventDefault();
        if (o.input !== undefined) {
          var v = el.dlgInput.value.trim();
          cleanup(v || null);
        } else { cleanup(true); }
      }
      function onCancel(ev) { if (ev) ev.preventDefault(); cleanup(null); }
      el.dlgForm.addEventListener('submit', onSubmit);
      el.dlgCancel.addEventListener('click', onCancel);
      d.addEventListener('cancel', onCancel);
      d.showModal();
      if (o.input !== undefined) { el.dlgInput.focus(); el.dlgInput.select(); }
    });
  }

  // ------------------------------------------------------------- conversations
  function newChat() {
    stopGenerating();
    S.convId = null;
    S.messages = [];
    S.title = 'New chat';
    el.messages.innerHTML = '';
    showEmpty(true);
    setMode(null);
    S.files = [];
    renderFiles();
    el.prompt.value = '';
    el.prompt.placeholder = 'Ask Smart Study AI anything…';
    autosize();
    refreshSendBtn();
    if (history.replaceState) history.replaceState(null, '', URLS.page);
    renderList();
    buildGreeting();
    el.prompt.focus();
  }

  async function openConversation(id) {
    if (S.sending) stopGenerating();
    try {
      var res = await fetch(urlFor(URLS.conv, id));
      if (!res.ok) throw new Error('not found');
      var data = await res.json();
      S.convId = data.conversation.id;
      S.title = data.conversation.title;
      S.messages = data.messages || [];
      if (el.noteSelect) {
        var want = data.conversation.note_id ? String(data.conversation.note_id) : '';
        el.noteSelect.value = want;
        if (el.noteSelect.value !== want) el.noteSelect.value = '';
        S.noteId = el.noteSelect.value;
      }
      renderThread();
      if (history.replaceState) history.replaceState(null, '', URLS.page + '?c=' + S.convId);
      renderList();
    } catch (e) {
      toast('Could not open that chat.');
      newChat();
    }
  }

  // ------------------------------------------------------------- message actions
  function plainTextOf(node) {
    if (node._text) return node._text;
    var md = node.querySelector('.md');
    return md ? md.innerText : '';
  }

  async function copyText(text, btn) {
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
      else {
        var ta = document.createElement('textarea');
        ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
      }
      if (btn) {
        var old = btn.innerHTML;
        btn.innerHTML = svg('check', 15) + (btn.classList.contains('code-copy') ? '<em>Copied</em>' : '');
        setTimeout(function () { btn.innerHTML = old; }, 1400);
      } else { toast('Copied'); }
    } catch (e) { toast('Could not copy'); }
  }

  function stopSpeaking() {
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    if (S.speakingBtn) { S.speakingBtn.classList.remove('is-on'); S.speakingBtn = null; }
  }
  function speak(node, btn) {
    if (!('speechSynthesis' in window)) { toast('Read aloud isn\'t supported in this browser.'); return; }
    if (S.speakingBtn === btn) { stopSpeaking(); return; }
    stopSpeaking();
    var md = node.querySelector('.md');
    var u = new SpeechSynthesisUtterance(md ? md.innerText : plainTextOf(node));
    u.onend = u.onerror = function () { if (S.speakingBtn === btn) { btn.classList.remove('is-on'); S.speakingBtn = null; } };
    S.speakingBtn = btn;
    btn.classList.add('is-on');
    window.speechSynthesis.speak(u);
  }

  function startEdit(node) {
    var id = node.dataset.id;
    if (!id || S.sending) return;
    var bubble = node.querySelector('.u-bubble');
    var original = bubble.textContent;
    var wrap = node.querySelector('.u-wrap');
    wrap.classList.add('is-editing');
    bubble.style.display = 'none';
    node.querySelector('.u-actions').style.display = 'none';
    var box = document.createElement('div');
    box.className = 'u-edit';
    box.innerHTML = '<textarea rows="2"></textarea><div class="u-edit-actions">' +
      '<button type="button" class="btn btn-sm btn-outline" data-e="cancel">Cancel</button>' +
      '<button type="button" class="btn btn-sm btn-primary" data-e="save">Update</button></div>';
    var ta = box.querySelector('textarea');
    ta.value = original;
    wrap.appendChild(box);
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 220) + 'px';
    ta.focus();
    function close() {
      box.remove();
      bubble.style.display = '';
      node.querySelector('.u-actions').style.display = '';
      wrap.classList.remove('is-editing');
    }
    box.querySelector('[data-e="cancel"]').addEventListener('click', close);
    box.querySelector('[data-e="save"]').addEventListener('click', function () {
      var v = ta.value.trim();
      if (!v) return;
      close();
      send({ text: v, editId: id, files: [] });
    });
    ta.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); box.querySelector('[data-e="save"]').click(); }
      if (e.key === 'Escape') close();
    });
  }

  function onThreadClick(ev) {
    var cc = ev.target.closest('.code-copy');
    if (cc) {
      var code = cc.closest('.code-wrap').querySelector('code');
      copyText(code ? code.innerText : '', cc);
      return;
    }
    var btn = ev.target.closest('.act');
    if (!btn) return;
    var node = btn.closest('.msg');
    var act = btn.dataset.act;
    if (act === 'copy') {
      var t = node.classList.contains('msg-user') ? node.querySelector('.u-bubble').textContent : plainTextOf(node);
      copyText(t, btn);
    } else if (act === 'regen') {
      if (!S.sending) send({ regenerate: true });
    } else if (act === 'edit') {
      startEdit(node);
    } else if (act === 'listen') {
      speak(node, btn);
    } else if (act === 'like' || act === 'dislike') {
      var on = !btn.classList.contains('is-on');
      try {
        if (on) localStorage.setItem(fbKey(node), act); else localStorage.removeItem(fbKey(node));
      } catch (e) { /* ignore */ }
      node.querySelectorAll('[data-act="like"],[data-act="dislike"]').forEach(function (b) {
        b.classList.toggle('is-on', on && b === btn);
      });
      if (on) toast('Thanks for the feedback');
    }
  }

  function exportChat() {
    if (!S.messages.length) { toast('Nothing to download yet.'); return; }
    var md = '# ' + S.title + '\n\n' + S.messages.map(function (m) {
      return (m.role === 'user' ? '**You**\n\n' : '**Smart Study AI**\n\n') + m.content;
    }).join('\n\n---\n\n') + '\n';
    var blob = new Blob([md], { type: 'text/markdown' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = (S.title || 'chat').replace(/[^\w\- ]+/g, '').trim().replace(/\s+/g, '_').slice(0, 50) + '.md';
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  // ------------------------------------------------------------- voice input
  function setupMic() {
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { el.micBtn.style.display = 'none'; return; }
    var rec = null;
    el.micBtn.addEventListener('click', function () {
      if (S.recognizing && rec) { rec.stop(); return; }
      rec = new SR();
      rec.lang = navigator.language || 'en-US';
      rec.interimResults = true;
      rec.continuous = false;
      var base = el.prompt.value ? el.prompt.value.replace(/\s*$/, ' ') : '';
      rec.onstart = function () { S.recognizing = true; el.micBtn.classList.add('is-on'); };
      rec.onresult = function (e) {
        var t = '';
        for (var i = 0; i < e.results.length; i++) t += e.results[i][0].transcript;
        el.prompt.value = base + t;
        autosize();
        refreshSendBtn();
      };
      rec.onerror = function () { toast('Microphone unavailable or blocked.'); };
      rec.onend = function () { S.recognizing = false; el.micBtn.classList.remove('is-on'); };
      try { rec.start(); } catch (e) { /* already started */ }
    });
  }

  // ------------------------------------------------------------- layout helpers
  function isMobile() { return window.innerWidth <= 860; }
  function closeSidebarOnMobile() { if (isMobile()) el.aifs.classList.remove('side-open'); }
  function toggleSidebar() {
    if (isMobile()) {
      el.aifs.classList.toggle('side-open');
    } else {
      var c = el.aifs.classList.toggle('side-collapsed');
      try { localStorage.setItem('aifs-collapsed', c ? '1' : '0'); } catch (e) { /* ignore */ }
    }
  }

  function toggleFullscreen() {
    var d = document;
    if (!d.fullscreenEnabled) { toast('Full screen isn\'t available in this browser.'); return; }
    if (d.fullscreenElement) d.exitFullscreen(); else d.documentElement.requestFullscreen().catch(function () {});
  }

  function toggleTheme() {
    var html = document.documentElement;
    var next = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    try { localStorage.setItem('sss-theme', next); } catch (e) { /* ignore */ }
  }

  // ------------------------------------------------------------- init
  function init() {
    grabDom();
    buildGreeting();
    buildSuggestions();
    buildToolsMenu();
    setupMic();

    try { if (localStorage.getItem('aifs-collapsed') === '1' && !isMobile()) el.aifs.classList.add('side-collapsed'); } catch (e) { /* ignore */ }

    // sidebar
    el.sideToggle.addEventListener('click', toggleSidebar);
    el.sideToggleInner.addEventListener('click', toggleSidebar);
    el.aifsScrim.addEventListener('click', function () { el.aifs.classList.remove('side-open'); });
    el.newChatBtn.addEventListener('click', function () { newChat(); closeSidebarOnMobile(); });
    el.clearAllBtn.addEventListener('click', async function () {
      if (!S.conversations.length) { toast('No chats to delete.'); return; }
      var ok = await askDialog({ title: 'Delete all chats?', text: 'Every conversation will be permanently deleted.', ok: 'Delete all', danger: true });
      if (!ok) return;
      await fetch(URLS.delAll, { method: 'POST' });
      newChat();
      loadConversations();
      toast('All chats deleted');
    });
    el.chatSearch.addEventListener('input', function () {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(loadConversations, 220);
    });

    // header
    if (el.noteSelect) el.noteSelect.addEventListener('change', function () { S.noteId = el.noteSelect.value; });
    el.exportBtn.addEventListener('click', exportChat);
    el.fsBtn.addEventListener('click', toggleFullscreen);
    el.themeBtn.addEventListener('click', toggleTheme);
    document.addEventListener('fullscreenchange', function () {
      el.aifs.classList.toggle('is-browser-fs', !!document.fullscreenElement);
    });

    // thread
    el.thread.addEventListener('click', onThreadClick);
    el.thread.addEventListener('scroll', function () {
      S.stick = nearBottom();
      el.scrollDown.classList.toggle('is-show', !S.stick && S.messages.length > 0);
    });
    el.scrollDown.addEventListener('click', function () { S.stick = true; scrollToBottom(true); });

    // composer
    el.prompt.addEventListener('input', function () { autosize(); refreshSendBtn(); });
    el.prompt.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        if (!S.sending) send();
      }
    });
    el.prompt.addEventListener('paste', function (e) {
      var items = (e.clipboardData && e.clipboardData.files) || [];
      if (items.length) { e.preventDefault(); addFiles(items); }
    });
    el.sendBtn.addEventListener('click', function () {
      if (S.sending) stopGenerating(); else send();
    });
    el.attachBtn.addEventListener('click', function () { el.fileInput.click(); });
    el.fileInput.addEventListener('change', function () { addFiles(el.fileInput.files); el.fileInput.value = ''; });
    el.toolsBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      var open = !el.toolsPop.classList.contains('is-open');
      closePopups();
      el.toolsPop.classList.toggle('is-open', open);
      el.toolsBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });

    // drag & drop files anywhere
    var dragDepth = 0;
    window.addEventListener('dragenter', function (e) {
      if (e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], 'Files') >= 0) {
        dragDepth++; el.aifs.classList.add('is-dragging');
      }
    });
    window.addEventListener('dragleave', function () {
      dragDepth = Math.max(0, dragDepth - 1);
      if (!dragDepth) el.aifs.classList.remove('is-dragging');
    });
    window.addEventListener('dragover', function (e) { e.preventDefault(); });
    window.addEventListener('drop', function (e) {
      e.preventDefault(); dragDepth = 0; el.aifs.classList.remove('is-dragging');
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
    });

    // global
    document.addEventListener('click', function (e) {
      if (!e.target.closest('.aifs-pop') && !e.target.closest('#toolsBtn') && !e.target.closest('.chat-more')) closePopups();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { closePopups(); el.aifs.classList.remove('side-open'); }
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'o' || e.key === 'O')) { e.preventDefault(); newChat(); }
      if (e.key === '/' && !/^(INPUT|TEXTAREA|SELECT)$/.test((document.activeElement || {}).tagName || '')) {
        e.preventDefault(); el.prompt.focus();
      }
    });
    window.addEventListener('resize', function () { if (!isMobile()) el.aifs.classList.remove('side-open'); });
    window.addEventListener('beforeunload', function () { if ('speechSynthesis' in window) window.speechSynthesis.cancel(); });

    // initial data
    loadConversations();
    var qs = new URLSearchParams(window.location.search);
    var cid = parseInt(qs.get('c'), 10);
    if (cid) openConversation(cid); else { showEmpty(true); el.prompt.focus(); }
    refreshSendBtn();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
