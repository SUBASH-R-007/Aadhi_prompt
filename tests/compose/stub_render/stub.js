// Minimal stand-in for web/render.html used by the compose tests. Implements the render-mode
// contract (window.aadhiRender: ready, states, show, showIntro) with plain DOM + textContent.
(function () {
  'use strict';
  var params = new URLSearchParams(location.hash.slice(1));
  var token = params.get('token') || '';
  var STUB = window.STUB || { mode: 'ok' };
  var stage = document.getElementById('stage');
  var timeline = null;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }
  function clear() { while (stage.firstChild) stage.removeChild(stage.firstChild); }
  function rectOf(node) {
    var r = node.getBoundingClientRect();
    return { x: r.left, y: r.top, width: r.width, height: r.height };
  }
  function afterPaint() {
    return new Promise(function (resolve) {
      requestAnimationFrame(function () { requestAnimationFrame(function () { resolve(); }); });
    });
  }

  function revealTimes(scene) {
    var at = {};
    scene.beats.forEach(function (b) { if (b.board_item_id) at[b.board_item_id] = b.start; });
    return at;
  }

  function stateAt(scene, t) {
    var at = revealTimes(scene);
    var visible = scene.board.filter(function (i) { return !(i.id in at) || at[i.id] <= t + 1e-6; })
      .map(function (i) { return i.id; });
    var filled = [];
    var highlight = [];
    scene.beats.forEach(function (b) {
      if (b.fill_item_id && b.start <= t + 1e-6) filled.push(b.fill_item_id);
      if (b.start <= t + 1e-6 && t < b.end - 1e-6) highlight = highlight.concat(b.highlight_item_ids || []);
    });
    var countdown = null;
    var revealed = false;
    if (scene.quiz) {
      var q = scene.quiz;
      if (t >= q.countdown_start - 1e-6 && t < q.reveal_start - 1e-6) {
        countdown = Math.max(1, q.countdown_seconds - Math.floor(t - q.countdown_start + 1e-6));
      }
      revealed = t >= q.reveal_start - 1e-6;
    }
    var panel = !!scene.side_panel && t >= scene.side_panel.show_at - 1e-6;
    return { visible: visible, filled: filled, highlight: highlight, countdown: countdown, revealed: revealed,
             panel: panel };
  }

  function keyOf(st) {
    return JSON.stringify([st.visible, st.filled, st.highlight, st.countdown, st.revealed, st.panel]);
  }

  function states(i) {
    var scene = timeline.scenes[i];
    var times = [0];
    scene.beats.forEach(function (b) { times.push(b.start); if (b.highlight_item_ids.length) times.push(b.end); });
    if (scene.quiz) {
      for (var k = 0; k < scene.quiz.countdown_seconds; k++) times.push(scene.quiz.countdown_start + k);
      times.push(scene.quiz.reveal_start);
    }
    if (scene.side_panel) times.push(scene.side_panel.show_at);
    times = times.filter(function (t) { return t >= 0 && t < scene.duration; });
    times.sort(function (a, b) { return a - b; });
    var out = [];
    times.forEach(function (t) {
      var key = keyOf(stateAt(scene, t));
      if (!out.length || out[out.length - 1].key !== key) out.push({ t: t, key: key });
    });
    return out;
  }

  function show(arg) {
    var scene = timeline.scenes[arg.scene];
    var st = stateAt(scene, arg.t);
    clear();
    stage.appendChild(el('div', 'title', scene.title || scene.type));
    var mediaRect = null;
    var panelRect = null;
    var main = scene.media || scene.poster;
    if (main && scene.layout.fullscreen_media) {
      var box = el('div', 'media');
      stage.appendChild(box);
      mediaRect = rectOf(box);
    }
    if (scene.quiz) {
      var q = el('div', 'quiz');
      q.appendChild(el('div', 'question', scene.quiz.question));
      scene.quiz.options.forEach(function (o, n) {
        var cls = 'opt' + (st.revealed && n === scene.quiz.correct_index ? ' correct' : '');
        q.appendChild(el('div', cls, String.fromCharCode(65 + n) + '. ' + o));
      });
      stage.appendChild(q);
      if (st.countdown !== null) stage.appendChild(el('div', 'countdown', st.countdown));
    } else {
      var board = el('div', 'board');
      scene.board.forEach(function (item) {
        if (st.visible.indexOf(item.id) < 0) return;
        var text = item.blank && st.filled.indexOf(item.id) < 0 ? '______' : (item.text || item.latex || item.term || item.kind);
        board.appendChild(el('div', 'item' + (st.highlight.indexOf(item.id) >= 0 ? ' hl' : ''), text));
      });
      stage.appendChild(board);
    }
    if (st.panel) {
      stage.appendChild(el('div', 'panel', scene.side_panel.panel.title || scene.side_panel.panel.kind));
      var pm = scene.side_panel.media;
      if (pm && pm.render_in_mp4) {
        var pbox = el('div', 'panel-media');
        stage.appendChild(pbox);
        panelRect = rectOf(pbox);
      }
    }
    var settled = afterPaint();
    if (STUB.figure_url) {  // a page-drawn image from the configured (media) origin
      var img = el('img', 'fig');
      img.src = STUB.figure_url;
      stage.appendChild(img);
      settled = img.decode().catch(function () { /* blocked: drawn as nothing */ }).then(afterPaint);
    }
    if (STUB.mode === 'hang-show') return new Promise(function () { /* never settles */ });
    return settled.then(function () {
      return { state_key: keyOf(st), media_rect: mediaRect, panel_media_rect: panelRect,
               media_fit: main ? main.fit : null };
    });
  }

  function showIntro(arg) {
    clear();
    var cards = (timeline.intro && timeline.intro.cards) || [];
    var card = null;
    cards.forEach(function (c) { if (arg.t >= c.start && arg.t < c.start + c.duration) card = c; });
    if (card) {
      var box = el('div', 'card');
      box.appendChild(el('div', 'l1', card.line1));
      box.appendChild(el('div', 'l2', card.line2));
      stage.appendChild(box);
    }
    return afterPaint().then(function () { return { state_key: card ? card.line1 : 'logo' }; });
  }

  window.aadhiRender = { ready: false, error: null, states: states, show: show, showIntro: showIntro };

  if (STUB.mode === 'boot-error') {  // like render.js: the failure is recorded, never thrown to the page
    setTimeout(function () {
      window.aadhiRender.error = { code: 'player_api', message: 'Player is missing render-mode methods: renderState' };
      console.error('render page failed to boot: player_api');
    }, 50);
    return;
  }

  // An off-origin request: the renderer must block it (page.route allow-list).
  fetch('https://blocked.example.invalid/beacon').catch(function () { /* expected */ });

  var probes = [];
  if (STUB.ws_url) {  // WebSockets bypass page.route: the renderer must close them
    probes.push(new Promise(function (resolve) {
      try {
        var ws = new WebSocket(STUB.ws_url);
        ws.onopen = function () { ws.send('exfil'); };
        ws.onerror = ws.onclose = function () { resolve(); };
        setTimeout(resolve, 3000);
      } catch (e) { resolve(); }
    }));
  }
  if (STUB.redirect_to) {  // a same-origin URL redirecting off-origin (redirect hops bypass page.route)
    probes.push(fetch('/redirect?to=' + encodeURIComponent(STUB.redirect_to)).catch(function () { /* blocked */ }));
  }

  Promise.all(probes)
    .then(function () { return fetch('/api/render/timeline', { headers: { Authorization: 'Bearer ' + token } }); })
    .then(function (r) { if (!r.ok) throw new Error('timeline HTTP ' + r.status); return r.json(); })
    .then(function (tl) {
      timeline = tl;
      return document.fonts.ready;
    })
    .then(function () { window.aadhiRender.ready = true; })
    .catch(function (err) { window.aadhiRenderError = String(err); });
})();
