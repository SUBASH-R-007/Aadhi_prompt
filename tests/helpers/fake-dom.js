'use strict';
// Just enough DOM for mascot.js: elements with classes, attributes and events,
// a <video> whose loading and playback the tests drive by hand, and a document.

class FakeEventTarget {
    constructor() {
        this.listeners = {};
    }

    addEventListener(type, fn, opts) {
        (this.listeners[type] = this.listeners[type] || []).push({ fn, once: !!(opts && opts.once) });
    }

    removeEventListener(type, fn) {
        this.listeners[type] = (this.listeners[type] || []).filter(l => l.fn !== fn);
    }

    dispatch(type) {
        (this.listeners[type] || []).slice().forEach(l => {
            if (l.once) this.removeEventListener(type, l.fn);
            l.fn({ type, target: this });
        });
    }
}

class FakeClassList {
    constructor() {
        this.names = new Set();
    }

    add(...names) { names.forEach(n => this.names.add(n)); }
    remove(...names) { names.forEach(n => this.names.delete(n)); }
    contains(name) { return this.names.has(name); }

    toggle(name, force) {
        const on = force === undefined ? !this.names.has(name) : !!force;
        if (on) this.names.add(name);
        else this.names.delete(name);
        return on;
    }
}

class FakeElement extends FakeEventTarget {
    constructor(doc, tag) {
        super();
        this.ownerDocument = doc;
        this.tagName = tag.toUpperCase();
        this.attrs = new Map();
        this.classList = new FakeClassList();
        this.children = [];
        this.parentNode = null;
        this.innerHTML = '';
        this.textContent = '';
        this.clientWidth = 0;
        this.clientHeight = 0;
        const props = {};
        this.style = { props, setProperty: (k, v) => { props[k] = v; } };
    }

    set className(value) {
        this.classList = new FakeClassList();
        String(value).split(/\s+/).filter(Boolean).forEach(n => this.classList.add(n));
    }

    get className() {
        return Array.from(this.classList.names).join(' ');
    }

    setAttribute(k, v) { this.attrs.set(k, String(v)); }
    getAttribute(k) { return this.attrs.has(k) ? this.attrs.get(k) : null; }
    hasAttribute(k) { return this.attrs.has(k); }

    appendChild(child) {
        child.remove();
        child.parentNode = this;
        this.children.push(child);
        return child;
    }

    remove() {
        if (!this.parentNode) return;
        this.parentNode.children = this.parentNode.children.filter(c => c !== this);
        this.parentNode = null;
    }

    descendants() {
        return this.children.flatMap(c => [c, ...c.descendants()]);
    }

    querySelectorAll(selector) {
        return this.descendants().filter(el => matches(el, selector));
    }
}

function matches(el, selector) {
    if (selector.startsWith('.')) return el.classList.contains(selector.slice(1));
    const attr = selector.match(/^\[([\w-]+)\]$/);
    if (attr) return el.hasAttribute(attr[1]);
    throw new Error('fake-dom: unsupported selector ' + selector);
}

class FakeVideo extends FakeElement {
    constructor(doc) {
        super(doc, 'video');
        this.readyState = 0;
        this.paused = true;
        this.ended = false;
        this.currentTime = 0;
        this.error = null;
        this.muted = false;
        this.preload = 'metadata';
        this.srcHistory = [];
        this.playCalls = 0;
        this.loadCalls = 0;
        this.playResult = null; // null = play() succeeds; otherwise the error name play() rejects with
    }

    get src() {
        return this.srcHistory[this.srcHistory.length - 1] || '';
    }

    // Assigning src runs the media load algorithm: back to an empty, paused element
    set src(value) {
        this.srcHistory.push(value);
        this.readyState = 0;
        this.error = null;
        this.paused = true;
        this.ended = false;
        this.currentTime = 0;
    }

    load() {
        this.loadCalls++;
        this.readyState = 0;
        this.paused = true;
    }

    play() {
        this.playCalls++;
        if (this.playResult) {
            const err = new Error('play() rejected');
            err.name = this.playResult;
            return Promise.reject(err);
        }
        const wasPaused = this.paused;
        this.paused = false;
        this.ended = false;
        if (wasPaused) {
            this.dispatch('play');
            if (this.readyState >= 3) this.dispatch('playing');
        }
        return Promise.resolve();
    }

    pause() {
        if (this.paused) return;
        this.paused = true;
        this.dispatch('pause');
    }

    // --- test drivers ---

    finishLoading() {
        this.readyState = 4;
        ['loadedmetadata', 'loadeddata', 'canplay', 'canplaythrough'].forEach(t => this.dispatch(t));
        if (!this.paused) this.dispatch('playing');
    }

    fail(code = 2) {
        this.error = { code, message: 'test failure' };
        this.dispatch('error');
    }

    advance(seconds) {
        this.currentTime += seconds;
        this.dispatch('timeupdate');
    }

    end() {
        this.paused = true;
        this.ended = true;
        this.dispatch('pause');
        this.dispatch('ended');
    }
}

class FakeDocument extends FakeEventTarget {
    constructor() {
        super();
        this.hidden = false;
        this.defaultView = null;
        this.body = new FakeElement(this, 'body');
    }

    createElement(tag) {
        return tag === 'video' ? new FakeVideo(this) : new FakeElement(this, tag);
    }

    querySelectorAll(selector) {
        return this.body.querySelectorAll(selector);
    }
}

const CLIPS = { left: 'aadhi_left', right: 'aadhi_right', center: 'aadhi_center', popup: 'aadhi_popup', none: 'no_aadhi' };

// The #mascot-bg markup from index.html. extraLayers adds state-specific clips,
// e.g. [{ asset: 'left', state: 'talking' }].
function buildMascotDom({ extraLayers = [] } = {}) {
    const doc = new FakeDocument();
    const container = doc.createElement('div');
    container.setAttribute('data-fallback-src', 'video_template/static_background.png');
    doc.body.appendChild(container);

    const addLayer = (asset, state) => {
        const file = CLIPS[asset] + (state ? '_' + state : '');
        const video = doc.createElement('video');
        video.className = 'mascot-layer';
        video.setAttribute('data-asset', asset);
        if (state) video.setAttribute('data-state', state);
        video.setAttribute('data-src', 'video_template/' + file + '.mp4');
        video.setAttribute('poster', 'video_template/posters/' + file + '.jpg');
        container.appendChild(video);
        return video;
    };

    const layers = {};
    Object.keys(CLIPS).forEach(asset => { layers[asset] = addLayer(asset); });
    extraLayers.forEach(({ asset, state }) => { layers[asset + '/' + state] = addLayer(asset, state); });
    return { doc, container, layers };
}

module.exports = { buildMascotDom, FakeDocument };
