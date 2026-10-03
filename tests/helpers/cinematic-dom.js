'use strict';
// A small DOM stand-in for the cinematic and composer unit tests (cinematic.js, review.js): elements, simple selectors,
// animations (Web Animations), styles and a document.
// ---- a small DOM stand-in ------------------------------------------------------------------------------------------
class Anim {
    constructor(el, frames, opts) { this.el = el; this.frames = frames; this.opts = opts; this.playState = 'running'; }
    pause() { this.playState = 'paused'; }
    play() { this.playState = 'running'; }
    cancel() { this.playState = 'idle'; this.cancelled = true; }
}
class Node {
    constructor(doc, tag) {
        this.doc = doc; this.tag = tag; this.children = []; this.attrs = {}; this.listeners = {}; this._text = ''; this.parent = null;
        const props = {};
        this.style = new Proxy({ setProperty: (k, v) => { props[k] = String(v); }, getPropertyValue: k => props[k] || '', _props: props }, {
            get: (t, k) => (k in t ? t[k] : props[k] || ''), set: (t, k, v) => { props[k] = String(v); return true; } });
        const names = new Set();
        this.classList = { add: n => names.add(n), remove: n => names.delete(n), contains: n => names.has(n), toggle: (n, on) => (on ? names.add(n) : names.delete(n)) };
        Object.defineProperty(this, 'className', { get: () => [...names].join(' '), set: v => { names.clear(); String(v).split(/\s+/).filter(Boolean).forEach(n => names.add(n)); } });
        this.animations = [];
    }
    get id() { return this.attrs.id || ''; }
    set id(v) { this.attrs.id = String(v); }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    removeAttribute(k) { delete this.attrs[k]; }
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
    removeEventListener() {}
    appendChild(c) { this.children.push(c); c.parent = this; return c; }
    append(...cs) { cs.forEach(c => this.appendChild(typeof c === 'string' ? this.doc.createTextNode(c) : c)); }
    get firstElementChild() { return this.children.find(c => c.tag !== '#text') || null; }
    get childNodes() { return this.children; }
    get textContent() { return this.tag === '#text' ? this._text : this.children.map(c => c.textContent).join(''); }
    set textContent(v) { this.children = []; if (this.tag === '#text') this._text = String(v); else if (v !== '' && v !== null && v !== undefined) this.children.push(Object.assign(new Node(this.doc, '#text'), { _text: String(v) })); }
    set innerHTML(v) { this.children = []; this._html = String(v); }
    all() { return this.children.flatMap(c => [c, ...c.all()]); }
    matches(sel) { return sel.split(',').some(one => matchOne(this, one.trim())); }
    querySelector(sel) { return this.all().find(n => n.matches(sel)) || null; }
    querySelectorAll(sel) { return this.all().filter(n => n.matches(sel)); }
    animate(frames, opts) { const a = new Anim(this, frames, opts); this.animations.push(a); return a; }
    getBoundingClientRect() { return this.rect || { left: 0, top: 0, width: 0, height: 0 }; }
    fire(type) { (this.listeners[type] || []).forEach(fn => fn({ type, target: this })); }
}
// Selectors the modules use: tag, #id, .class, [attr="v"], descendant chains (last part checked, ancestors loosely)
function matchSimple(n, part) {
    if (n.tag === '#text') return false;
    const re = /([#.]?[\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]/g;
    let m; let ok = true;
    while ((m = re.exec(part))) {
        if (m[1]) {
            const t = m[1];
            if (t[0] === '#') ok = ok && n.id === t.slice(1);
            else if (t[0] === '.') ok = ok && n.classList.contains(t.slice(1));
            else ok = ok && n.tag === t;
        } else if (m[2]) {
            ok = ok && (m[3] === undefined ? m[2] in n.attrs : n.attrs[m[2]] === m[3]);
        }
    }
    return ok;
}
function matchOne(n, sel) {
    const parts = sel.split(/\s+/);
    if (!matchSimple(n, parts[parts.length - 1])) return false;
    let anc = n.parent;
    for (let i = parts.length - 2; i >= 0; i--) {
        while (anc && !matchSimple(anc, parts[i])) anc = anc.parent;
        if (!anc) return false;
        anc = anc.parent;
    }
    return true;
}
function fakeDoc() {
    const doc = { createElement: tag => new Node(doc, tag), createTextNode: t => Object.assign(new Node(doc, '#text'), { _text: t }),
        addEventListener() {}, removeEventListener() {}, defaultView: { innerWidth: 1280, innerHeight: 720 } };
    doc.documentElement = new Node(doc, 'html');
    doc.body = new Node(doc, 'body');
    doc.documentElement.appendChild(doc.body);
    doc.getElementById = id => doc.documentElement.all().find(n => n.id === id) || null;
    doc.querySelector = sel => doc.documentElement.querySelector(sel);
    doc.querySelectorAll = sel => doc.documentElement.querySelectorAll(sel);
    return doc;
}
module.exports = { Anim, Node, fakeDoc, matchOne, matchSimple };
