/*
 * Aadhi app shell (Phase 21): the top bar (Home · Create · Library · Videos · Settings), the Settings dialog (the start
 * screen's controls, moved into it and grouped), the player bar's More menu, plain notices, and keyboard focus kept inside the
 * panel that is open (the page behind it is made inert). The page's own functions do the work: goHome / openStudioHome (the
 * Studio), assetLibrary.open() (Library), exportFlow.open() (Videos). Nothing here draws on the recorded stage; the shell is
 * hidden while a video is recorded. Loaded last (defer), after the page's own scripts.
 */
(function () {
    'use strict';
    const doc = document;
    const $ = id => doc.getElementById(id);
    if (!$('app-bar')) return; // an exported lesson page (↓ HTML) has no shell

    const debug = () => /[?&]visualDebug(=|&|$)/.test(window.location.search);
    const recording = () => doc.body.hasAttribute('data-recording') || doc.body.dataset.mode === 'exporting';
    const pageFn = name => (typeof window[name] === 'function' ? window[name] : null);

    // ---- notices: what happened and what to do, in plain words (the raw detail only in the debug view) -----------------------
    const noticeRoot = $('app-notice');
    function notice(text, { kind = 'info', detail = '', action = null, timeout } = {}) {
        const item = doc.createElement('div');
        item.className = 'app-notice-item';
        item.dataset.kind = kind;
        item.setAttribute('role', kind === 'error' ? 'alert' : 'status');
        const words = doc.createElement('p');
        words.className = 'app-notice-text';
        words.textContent = `${{ error: '⚠', busy: '⟳', success: '✓' }[kind] || 'ⓘ'} ${text}`;
        item.appendChild(words);
        let timer = null;
        const close = () => { clearTimeout(timer); item.remove(); };
        const actions = doc.createElement('div');
        actions.className = 'app-notice-actions';
        if (action && typeof action.run === 'function') {
            const run = doc.createElement('button');
            run.type = 'button';
            run.className = 'ui-btn ui-btn-sm ui-btn-primary';
            run.textContent = action.label || 'OK';
            run.addEventListener('click', () => { close(); action.run(); });
            actions.appendChild(run);
        }
        if (kind !== 'busy') {
            const dismiss = doc.createElement('button');
            dismiss.type = 'button';
            dismiss.className = 'ui-btn ui-btn-sm';
            dismiss.setAttribute('aria-label', 'Dismiss this message');
            dismiss.title = 'Dismiss';
            dismiss.textContent = '✕';
            dismiss.addEventListener('click', close);
            actions.appendChild(dismiss);
        }
        item.appendChild(actions);
        if (detail && debug()) {
            const raw = doc.createElement('pre');
            raw.className = 'shell-debug';
            raw.textContent = String(detail).slice(0, 2000);
            item.appendChild(raw);
        }
        noticeRoot.appendChild(item);
        while (noticeRoot.children.length > 3) noticeRoot.firstElementChild.remove();
        // errors stay until dismissed; a status while work runs is closed by its caller
        const life = timeout !== undefined ? timeout : (kind === 'info' || kind === 'success' ? 7000 : 0);
        if (life > 0) timer = setTimeout(close, life);
        return { close, element: item };
    }

    // ---- the panel on top: the rest of the page is inert (keyboard focus and clicks stay in the panel) ----------------------
    // Panels: the Studio, the editor, Visual Review / the Library / the Document Assistant / the export panel, Settings,
    // the lesson file editor and the scene list, sign in. Only the `inert` this shell set is ever removed.
    const OVERLAYS = '.studio-root, .editor-root, .asset-overlay.open, .export-overlay.open:not(.recording-hidden), '
        + '#script-editor-modal.active, #info-modal.active, #settings-overlay:not([hidden]), #loading-overlay.active';
    // (any panel root, open or not: where a focused element belongs)
    const OVERLAY_ROOTS = '.studio-root, .editor-root, .asset-overlay, .export-overlay, #script-editor-modal, #info-modal, '
        + '#settings-overlay, #loading-overlay, #auth-modal, #admin-modal';
    const MANAGED_FOCUS = ['script-editor-modal', 'info-modal', 'auth-modal', 'admin-modal'];
    let lastTop = null;
    let focusBefore = null;
    // Phase 21 review: the panels give focus back (or take it) synchronously, before this shell lifts `inert` (it reacts to
    // their class changes a moment later), so their focus() met an inert element and focus fell to <body>. The shell
    // remembers the last focused element inside each panel and on the page, and puts focus right once `inert` is updated.
    const lastFocusIn = new WeakMap();
    let lastFocusOutside = null;
    doc.addEventListener('focusin', e => {
        const t = e.target;
        if (!t || t.nodeType !== 1) return;
        const root = typeof t.closest === 'function' ? t.closest(OVERLAY_ROOTS) : null;
        if (root) lastFocusIn.set(root, t);
        else if (t !== doc.body) lastFocusOutside = t;
    }, true);
    function focusable(el) {
        return !!el && el.nodeType === 1 && doc.contains(el) && typeof el.focus === 'function' && !el.closest('[inert]')
            && !el.disabled && el.getClientRects().length > 0;
    }
    // the same control in a panel that drew itself again meanwhile (the Studio refreshes while Visual Review is open above it):
    // found by its id or by the attributes the panels name their controls with
    const CONTROL_KEYS = ['data-action', 'data-stage', 'data-item', 'data-project-id', 'data-focus', 'data-field'];
    function sameControl(top, el) {
        if (!el || el.nodeType !== 1) return null;
        if (el.id) { const byId = $(el.id); return byId && top.contains(byId) ? byId : null; }
        const keys = CONTROL_KEYS.filter(a => el.getAttribute(a)).map(a => `[${a}="${CSS.escape(el.getAttribute(a))}"]`).join('');
        if (!keys) return null;
        try { return top.querySelector(el.tagName.toLowerCase() + keys); } catch (e) { return null; }
    }
    function focusPanel(top) {
        let remembered = lastFocusIn.get(top);
        if (remembered && !doc.contains(remembered)) remembered = sameControl(top, remembered);
        if (focusable(remembered)) { remembered.focus(); return; }
        const heading = top.querySelector('h1[tabindex="-1"], h2[tabindex="-1"], h3[tabindex="-1"], [role="dialog"] [tabindex="-1"]');
        const target = focusable(heading) ? heading : firstFocusable(top);
        if (focusable(target)) target.focus();
    }
    function topOverlay() {
        const open = [...doc.querySelectorAll(OVERLAYS)];
        ['auth-modal', 'admin-modal'].forEach(id => { const m = $(id); if (m && m.style.display === 'flex') open.push(m); });
        let top = null;
        let topZ = -Infinity;
        open.forEach(el => {
            const z = parseInt(getComputedStyle(el).zIndex, 10) || 0;
            if (z >= topZ) { top = el; topZ = z; }
        });
        return top;
    }
    function firstFocusable(root) {
        return root.querySelector('input:not([type="hidden"]):not([disabled]), textarea:not([disabled]), select:not([disabled]), button:not([disabled]), [tabindex="0"]');
    }
    function updateInert() {
        const top = recording() ? null : topOverlay();
        [...doc.body.children].forEach(el => {
            // (the notices and mascot.js's "Enable playback" button stay usable above any panel)
            if (['SCRIPT', 'STYLE', 'LINK', 'TEMPLATE'].includes(el.tagName) || el.id === 'app-notice' || el.classList.contains('playback-gate')) return;
            const want = !!top && el !== top && !el.contains(top);
            if (want && !el.hasAttribute('inert')) {
                el.setAttribute('inert', '');
                el.dataset.shellInert = '1';
            } else if (!want && el.dataset.shellInert === '1') {
                el.removeAttribute('inert');
                delete el.dataset.shellInert;
            }
        });
        if (top !== lastTop) {
            // the page's own dialogs: focus goes in, and back where it was when they close
            if (top && MANAGED_FOCUS.includes(top.id) && !top.contains(doc.activeElement)) {
                if (!lastTop) focusBefore = doc.activeElement;
                const first = firstFocusable(top);
                if (first) setTimeout(() => first.focus(), 0);
            } else if (!top && lastTop && MANAGED_FOCUS.includes(lastTop.id) && focusBefore && doc.contains(focusBefore)) {
                const back = focusBefore;
                setTimeout(() => { if (!doc.activeElement || doc.activeElement === doc.body) back.focus(); }, 0);
                focusBefore = null;
            } else if (top && !MANAGED_FOCUS.includes(top.id)) {
                // a panel opened (or came back on top): if its own focus() met `inert`, focus goes in now
                const active = doc.activeElement;
                if (!active || active === doc.body || !top.contains(active)) focusPanel(top);
            } else if (!top && lastTop) {
                // every panel closed: if its own focus-back met `inert`, focus returns to where it was on the page
                const active = doc.activeElement;
                if ((!active || active === doc.body || (typeof active.closest === 'function' && active.closest(OVERLAY_ROOTS)))
                    && focusable(lastFocusOutside)) lastFocusOutside.focus();
            }
            lastTop = top;
        }
    }
    const watchAttrs = new MutationObserver(() => updateInert());
    const watched = new WeakSet();
    function watch(el) {
        if (!el || el.nodeType !== 1 || watched.has(el)) return;
        watched.add(el);
        watchAttrs.observe(el, { attributes: true, attributeFilter: ['class', 'style', 'hidden'] });
    }
    [...doc.body.children].forEach(watch);
    watch($('loading-overlay')); // (inside the start screen: the classic generation's overlay is a panel too while active)
    new MutationObserver(records => {
        records.forEach(r => r.addedNodes.forEach(watch));
        updateInert();
    }).observe(doc.body, { childList: true, attributes: true, attributeFilter: ['data-recording', 'data-mode'] });

    // ---- Settings ----------------------------------------------------------------------------------------------------------
    const settings = $('settings-overlay');
    const settingsDialog = $('settings-dialog');
    const settingsBody = $('settings-body');
    const settingsNav = [...doc.querySelectorAll('.settings-nav-btn')];
    let settingsReturn = null;
    function lessonOpen() {
        return (typeof currentProjectId !== 'undefined' && !!currentProjectId) || doc.body.classList.contains('presentation-active');
    }
    function refreshSettings() {
        const open = lessonOpen();
        settingsDialog.querySelectorAll('[data-needs-lesson]').forEach(b => {
            b.disabled = !open;
            b.title = open ? '' : 'Open a lesson first';
        });
        settingsDialog.querySelectorAll('[data-debug-only]').forEach(b => { b.hidden = !debug(); });
    }
    function markSection(key) {
        settingsNav.forEach(b => {
            if (b.dataset.section === key) b.setAttribute('aria-current', 'true');
            else b.removeAttribute('aria-current');
        });
    }
    function showSection(key) {
        const section = settingsBody.querySelector(`.settings-section[data-section="${key}"]`);
        if (!section) return;
        settingsBody.scrollTop += section.getBoundingClientRect().top - settingsBody.getBoundingClientRect().top;
        markSection(key);
    }
    function openSettings(section) {
        if (recording()) return;
        settingsReturn = menu.contains(doc.activeElement) ? moreBtn : doc.activeElement; // (from the More menu: back to its button)
        closeMenu(false);
        refreshSettings();
        settings.hidden = false;
        updateInert();
        if (section) showSection(section);
        else { settingsBody.scrollTop = 0; markSection('general'); }
        $('settings-close').focus();
    }
    function closeSettings() {
        if (settings.hidden) return;
        settings.hidden = true;
        updateInert();
        const back = settingsReturn;
        settingsReturn = null;
        if (back && doc.contains(back) && typeof back.focus === 'function' && !back.closest('[inert]')) back.focus();
    }
    window.openSettings = openSettings;
    window.closeSettings = closeSettings;
    $('settings-close').addEventListener('click', closeSettings);
    settings.addEventListener('click', e => { if (e.target === settings) closeSettings(); });
    settingsNav.forEach(b => b.addEventListener('click', () => showSection(b.dataset.section)));
    settingsBody.addEventListener('scroll', () => {
        const edge = settingsBody.getBoundingClientRect().top + 48;
        let current = 'general';
        settingsBody.querySelectorAll('.settings-section').forEach(sec => { if (sec.getBoundingClientRect().top <= edge) current = sec.dataset.section; });
        markSection(current);
    }, { passive: true });
    // the scene list and the lesson file editor open above the stage: Settings steps aside first
    ['edit-script-btn', 'info-btn'].forEach(id => { const b = $(id); if (b) b.addEventListener('click', () => closeSettings(), true); });
    // keys typed in Settings never reach the lesson's shortcuts; Tab stays inside the dialog (browser check finding: after its last
    // control focus left the page) — unless a dialog above it (sign in) made it inert
    settings.addEventListener('keydown', e => {
        if (e.key === 'Tab' && !settings.hidden && !settings.closest('[inert]')) {
            const items = [...settingsDialog.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), '
                + 'select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])')]
                .filter(el => el.getClientRects().length > 0 && !el.closest('[hidden]') && !el.closest('[inert]'));
            if (items.length) {
                const first = items[0];
                const last = items[items.length - 1];
                const inside = settingsDialog.contains(doc.activeElement);
                if (e.shiftKey && (!inside || doc.activeElement === first)) { e.preventDefault(); last.focus(); }
                else if (!e.shiftKey && (!inside || doc.activeElement === last)) { e.preventDefault(); first.focus(); }
            }
        }
        if (e.key !== 'Escape') e.stopPropagation();
    });

    // the one-step generation (classic), kept for compatibility (Advanced)
    const classic = $('classic-generation');
    const CLASSIC_KEY = 'aadhi.classicGeneration';
    try { classic.checked = localStorage.getItem(CLASSIC_KEY) === '1'; } catch (e) { classic.checked = false; }
    classic.addEventListener('change', () => {
        try {
            if (classic.checked) localStorage.setItem(CLASSIC_KEY, '1');
            else localStorage.removeItem(CLASSIC_KEY);
        } catch (e) { /* storage blocked: the setting lasts for this page only */ }
        const update = pageFn('updateStartActions');
        if (update) update();
    });

    // ---- the top bar ---------------------------------------------------------------------------------------------------------
    function closeAll() {
        closeMenu(false);
        closeSettings();
    }
    $('nav-home').addEventListener('click', () => { closeAll(); const home = pageFn('goHome'); if (home) home(); });
    $('nav-create').addEventListener('click', () => { closeAll(); const create = pageFn('openStudioHome'); if (create) create('create'); });
    $('open-assets-btn').addEventListener('click', () => { closeAll(); if (typeof assetLibrary !== 'undefined') assetLibrary.open(); });
    $('open-videos-btn').addEventListener('click', () => { closeAll(); if (typeof exportFlow !== 'undefined') exportFlow.open(); });
    $('nav-settings').addEventListener('click', () => (settings.hidden ? openSettings() : closeSettings()));
    // Home is the current place while the start screen shows
    const startScreen = $('upload-screen');
    const markHome = () => {
        const home = $('nav-home');
        if (startScreen && !startScreen.classList.contains('hidden')) home.setAttribute('aria-current', 'page');
        else home.removeAttribute('aria-current');
    };
    if (startScreen) new MutationObserver(markHome).observe(startScreen, { attributes: true, attributeFilter: ['class'] });
    markHome();

    // ---- the player bar's More menu (keyboard: arrows, Home / End, Esc closes) --------------------------------------------------
    const moreBtn = $('player-more-btn');
    const menu = $('player-more-menu');
    const items = () => [...menu.querySelectorAll('[role="menuitem"]')].filter(i => !i.hidden && !i.disabled);
    function openMenu(focus) {
        menu.hidden = false;
        moreBtn.setAttribute('aria-expanded', 'true');
        const list = items();
        if (focus === 'first' && list[0]) list[0].focus();
        if (focus === 'last' && list.length) list[list.length - 1].focus();
    }
    function closeMenu(returnFocus) {
        if (menu.hidden) return;
        menu.hidden = true;
        moreBtn.setAttribute('aria-expanded', 'false');
        if (returnFocus) moreBtn.focus();
    }
    moreBtn.addEventListener('click', e => (menu.hidden ? openMenu(e.detail === 0 ? 'first' : null) : closeMenu(false)));
    moreBtn.addEventListener('keydown', e => {
        if (e.key === 'ArrowDown') { e.preventDefault(); openMenu('first'); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); openMenu('last'); }
        else if (e.key === 'Escape' && !menu.hidden) { e.preventDefault(); closeMenu(true); } // (the bar keeps its keys to itself)
    });
    menu.addEventListener('keydown', e => {
        const list = items();
        const at = list.indexOf(doc.activeElement);
        if (e.key === 'ArrowDown') { e.preventDefault(); (list[(at + 1) % list.length] || list[0]).focus(); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); (list[(at - 1 + list.length) % list.length] || list[0]).focus(); }
        else if (e.key === 'Home') { e.preventDefault(); list[0].focus(); }
        else if (e.key === 'End') { e.preventDefault(); list[list.length - 1].focus(); }
        else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeMenu(true); }
        else if (e.key === 'Tab') closeMenu(false);
    });
    // an item does its own work (the page's listeners), then the menu closes
    menu.addEventListener('click', e => { if (e.target.closest('[role="menuitem"]')) setTimeout(() => closeMenu(false), 0); });
    doc.addEventListener('click', e => { if (!menu.hidden && !e.target.closest('.control-more')) closeMenu(false); });
    $('player-dev-btn').addEventListener('click', () => openSettings('advanced'));
    // "Developer tools…" only with ?visualDebug (everyone finds them in Settings → Advanced)
    menu.querySelectorAll('[data-debug-only]').forEach(el => { el.hidden = !debug(); });

    // ---- the caption above the player bar while the bar shows (the preview only) -----------------------------------------------
    // The bar covers the caption band (two rows on tablets): how far the caption must rise to sit 8 px above the bar (from the
    // caption's own bottom: the style's box and the caption fit move it) is kept in --ui-caption-lift, and index.html's stage
    // styles lift it only while the bar shows and no export records. Set on <html>, never on the caption (the export's caption
    // log watches the caption)
    const playerBar = $('voice-control-bar');
    const captionTrack = $('subtitle-track');
    if (playerBar && captionTrack && typeof window.ResizeObserver === 'function') {
        const placeCaption = () => {
            const bottom = parseFloat(window.getComputedStyle(captionTrack).bottom);
            const lift = Math.max(5, Math.round(playerBar.offsetHeight + 8 - (isFinite(bottom) ? bottom : 60)));
            doc.documentElement.style.setProperty('--ui-caption-lift', `${lift}px`);
        };
        new window.ResizeObserver(placeCaption).observe(playerBar);
        new MutationObserver(placeCaption).observe(captionTrack, { attributes: true, attributeFilter: ['style'] });
        new MutationObserver(placeCaption).observe(doc.body, { attributes: true, attributeFilter: ['data-cinematic', 'data-cine-caption'] });
        window.addEventListener('resize', placeCaption);
    }

    // ---- the player bar while a lesson plays: shown when the pointer moves, on a tap, a key or a focus move, and hidden again
    // 3 s later, like a video player's controls — it covers the bottom of the stage, where the caption is (the page showed it
    // whenever the pointer was on the page, and always on touch screens). Kept while the pointer is on it, the keyboard is in it
    // (keyboard focus only: a mouse click leaves the focus on ▶, browser check finding) or its More menu is open. Always shown
    // while the lesson is paused (product.css)
    let playerIdle = null;
    const barInUse = () => !!playerBar && (playerBar.matches(':hover') || !!playerBar.querySelector(':focus-visible')
        || !!playerBar.querySelector('.control-menu:not([hidden])'));
    function showPlayer() {
        if (!doc.body.classList.contains('lesson-playing')) return;
        doc.body.classList.add('player-active');
        clearTimeout(playerIdle);
        playerIdle = setTimeout(function idle() {
            if (barInUse()) { playerIdle = setTimeout(idle, 3000); return; }
            doc.body.classList.remove('player-active');
        }, 3000);
    }
    ['pointermove', 'pointerdown', 'keydown', 'focusin'].forEach(type => doc.addEventListener(type, showPlayer, true));

    // ---- Esc: the panel on top closes (other panels close themselves) --------------------------------------------------------------
    doc.addEventListener('keydown', e => {
        if (e.key !== 'Escape' || e.defaultPrevented) return;
        const top = topOverlay();
        if (!menu.hidden && (!top || top === settings)) { closeMenu(true); return; }
        if (top === settings) { e.preventDefault(); closeSettings(); }
        else if (top && top.id === 'script-editor-modal') { e.preventDefault(); $('script-editor-cancel').click(); }
        else if (top && top.id === 'info-modal') { e.preventDefault(); $('info-modal-close').click(); }
        else if (top && top.id === 'admin-modal' && typeof closeAdminModal === 'function') { e.preventDefault(); closeAdminModal(); }
    });

    window.appShell = { notice, openSettings, closeSettings, closeAll, updateInert };
    updateInert();
})();
