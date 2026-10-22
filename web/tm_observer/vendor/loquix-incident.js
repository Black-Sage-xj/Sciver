var __getOwnPropNames = Object.getOwnPropertyNames;
var __esm = (fn, res) => function __init() {
  return fn && (res = (0, fn[__getOwnPropNames(fn)[0]])(fn = 0)), res;
};
var __commonJS = (cb, mod) => function __require() {
  return mod || (0, cb[__getOwnPropNames(cb)[0]])((mod = { exports: {} }).exports, mod), mod.exports;
};

// node_modules/@lit/reactive-element/css-tag.js
var t, e, s, o, n, r, i, S, c;
var init_css_tag = __esm({
  "node_modules/@lit/reactive-element/css-tag.js"() {
    t = globalThis;
    e = t.ShadowRoot && (void 0 === t.ShadyCSS || t.ShadyCSS.nativeShadow) && "adoptedStyleSheets" in Document.prototype && "replace" in CSSStyleSheet.prototype;
    s = Symbol();
    o = /* @__PURE__ */ new WeakMap();
    n = class {
      constructor(t3, e6, o6) {
        if (this._$cssResult$ = true, o6 !== s) throw Error("CSSResult is not constructable. Use `unsafeCSS` or `css` instead.");
        this.cssText = t3, this.t = e6;
      }
      get styleSheet() {
        let t3 = this.o;
        const s4 = this.t;
        if (e && void 0 === t3) {
          const e6 = void 0 !== s4 && 1 === s4.length;
          e6 && (t3 = o.get(s4)), void 0 === t3 && ((this.o = t3 = new CSSStyleSheet()).replaceSync(this.cssText), e6 && o.set(s4, t3));
        }
        return t3;
      }
      toString() {
        return this.cssText;
      }
    };
    r = (t3) => new n("string" == typeof t3 ? t3 : t3 + "", void 0, s);
    i = (t3, ...e6) => {
      const o6 = 1 === t3.length ? t3[0] : e6.reduce((e7, s4, o7) => e7 + ((t4) => {
        if (true === t4._$cssResult$) return t4.cssText;
        if ("number" == typeof t4) return t4;
        throw Error("Value passed to 'css' function must be a 'css' function result: " + t4 + ". Use 'unsafeCSS' to pass non-literal values, but take care to ensure page security.");
      })(s4) + t3[o7 + 1], t3[0]);
      return new n(o6, t3, s);
    };
    S = (s4, o6) => {
      if (e) s4.adoptedStyleSheets = o6.map((t3) => t3 instanceof CSSStyleSheet ? t3 : t3.styleSheet);
      else for (const e6 of o6) {
        const o7 = document.createElement("style"), n5 = t.litNonce;
        void 0 !== n5 && o7.setAttribute("nonce", n5), o7.textContent = e6.cssText, s4.appendChild(o7);
      }
    };
    c = e ? (t3) => t3 : (t3) => t3 instanceof CSSStyleSheet ? ((t4) => {
      let e6 = "";
      for (const s4 of t4.cssRules) e6 += s4.cssText;
      return r(e6);
    })(t3) : t3;
  }
});

// node_modules/@lit/reactive-element/reactive-element.js
var i2, e2, h, r2, o2, n2, a, c2, l, p, d, u, f, b, y;
var init_reactive_element = __esm({
  "node_modules/@lit/reactive-element/reactive-element.js"() {
    init_css_tag();
    init_css_tag();
    ({ is: i2, defineProperty: e2, getOwnPropertyDescriptor: h, getOwnPropertyNames: r2, getOwnPropertySymbols: o2, getPrototypeOf: n2 } = Object);
    a = globalThis;
    c2 = a.trustedTypes;
    l = c2 ? c2.emptyScript : "";
    p = a.reactiveElementPolyfillSupport;
    d = (t3, s4) => t3;
    u = { toAttribute(t3, s4) {
      switch (s4) {
        case Boolean:
          t3 = t3 ? l : null;
          break;
        case Object:
        case Array:
          t3 = null == t3 ? t3 : JSON.stringify(t3);
      }
      return t3;
    }, fromAttribute(t3, s4) {
      let i5 = t3;
      switch (s4) {
        case Boolean:
          i5 = null !== t3;
          break;
        case Number:
          i5 = null === t3 ? null : Number(t3);
          break;
        case Object:
        case Array:
          try {
            i5 = JSON.parse(t3);
          } catch (t4) {
            i5 = null;
          }
      }
      return i5;
    } };
    f = (t3, s4) => !i2(t3, s4);
    b = { attribute: true, type: String, converter: u, reflect: false, useDefault: false, hasChanged: f };
    Symbol.metadata ??= Symbol("metadata"), a.litPropertyMetadata ??= /* @__PURE__ */ new WeakMap();
    y = class extends HTMLElement {
      static addInitializer(t3) {
        this._$Ei(), (this.l ??= []).push(t3);
      }
      static get observedAttributes() {
        return this.finalize(), this._$Eh && [...this._$Eh.keys()];
      }
      static createProperty(t3, s4 = b) {
        if (s4.state && (s4.attribute = false), this._$Ei(), this.prototype.hasOwnProperty(t3) && ((s4 = Object.create(s4)).wrapped = true), this.elementProperties.set(t3, s4), !s4.noAccessor) {
          const i5 = Symbol(), h3 = this.getPropertyDescriptor(t3, i5, s4);
          void 0 !== h3 && e2(this.prototype, t3, h3);
        }
      }
      static getPropertyDescriptor(t3, s4, i5) {
        const { get: e6, set: r6 } = h(this.prototype, t3) ?? { get() {
          return this[s4];
        }, set(t4) {
          this[s4] = t4;
        } };
        return { get: e6, set(s5) {
          const h3 = e6?.call(this);
          r6?.call(this, s5), this.requestUpdate(t3, h3, i5);
        }, configurable: true, enumerable: true };
      }
      static getPropertyOptions(t3) {
        return this.elementProperties.get(t3) ?? b;
      }
      static _$Ei() {
        if (this.hasOwnProperty(d("elementProperties"))) return;
        const t3 = n2(this);
        t3.finalize(), void 0 !== t3.l && (this.l = [...t3.l]), this.elementProperties = new Map(t3.elementProperties);
      }
      static finalize() {
        if (this.hasOwnProperty(d("finalized"))) return;
        if (this.finalized = true, this._$Ei(), this.hasOwnProperty(d("properties"))) {
          const t4 = this.properties, s4 = [...r2(t4), ...o2(t4)];
          for (const i5 of s4) this.createProperty(i5, t4[i5]);
        }
        const t3 = this[Symbol.metadata];
        if (null !== t3) {
          const s4 = litPropertyMetadata.get(t3);
          if (void 0 !== s4) for (const [t4, i5] of s4) this.elementProperties.set(t4, i5);
        }
        this._$Eh = /* @__PURE__ */ new Map();
        for (const [t4, s4] of this.elementProperties) {
          const i5 = this._$Eu(t4, s4);
          void 0 !== i5 && this._$Eh.set(i5, t4);
        }
        this.elementStyles = this.finalizeStyles(this.styles);
      }
      static finalizeStyles(s4) {
        const i5 = [];
        if (Array.isArray(s4)) {
          const e6 = new Set(s4.flat(1 / 0).reverse());
          for (const s5 of e6) i5.unshift(c(s5));
        } else void 0 !== s4 && i5.push(c(s4));
        return i5;
      }
      static _$Eu(t3, s4) {
        const i5 = s4.attribute;
        return false === i5 ? void 0 : "string" == typeof i5 ? i5 : "string" == typeof t3 ? t3.toLowerCase() : void 0;
      }
      constructor() {
        super(), this._$Ep = void 0, this.isUpdatePending = false, this.hasUpdated = false, this._$Em = null, this._$Ev();
      }
      _$Ev() {
        this._$ES = new Promise((t3) => this.enableUpdating = t3), this._$AL = /* @__PURE__ */ new Map(), this._$E_(), this.requestUpdate(), this.constructor.l?.forEach((t3) => t3(this));
      }
      addController(t3) {
        (this._$EO ??= /* @__PURE__ */ new Set()).add(t3), void 0 !== this.renderRoot && this.isConnected && t3.hostConnected?.();
      }
      removeController(t3) {
        this._$EO?.delete(t3);
      }
      _$E_() {
        const t3 = /* @__PURE__ */ new Map(), s4 = this.constructor.elementProperties;
        for (const i5 of s4.keys()) this.hasOwnProperty(i5) && (t3.set(i5, this[i5]), delete this[i5]);
        t3.size > 0 && (this._$Ep = t3);
      }
      createRenderRoot() {
        const t3 = this.shadowRoot ?? this.attachShadow(this.constructor.shadowRootOptions);
        return S(t3, this.constructor.elementStyles), t3;
      }
      connectedCallback() {
        this.renderRoot ??= this.createRenderRoot(), this.enableUpdating(true), this._$EO?.forEach((t3) => t3.hostConnected?.());
      }
      enableUpdating(t3) {
      }
      disconnectedCallback() {
        this._$EO?.forEach((t3) => t3.hostDisconnected?.());
      }
      attributeChangedCallback(t3, s4, i5) {
        this._$AK(t3, i5);
      }
      _$ET(t3, s4) {
        const i5 = this.constructor.elementProperties.get(t3), e6 = this.constructor._$Eu(t3, i5);
        if (void 0 !== e6 && true === i5.reflect) {
          const h3 = (void 0 !== i5.converter?.toAttribute ? i5.converter : u).toAttribute(s4, i5.type);
          this._$Em = t3, null == h3 ? this.removeAttribute(e6) : this.setAttribute(e6, h3), this._$Em = null;
        }
      }
      _$AK(t3, s4) {
        const i5 = this.constructor, e6 = i5._$Eh.get(t3);
        if (void 0 !== e6 && this._$Em !== e6) {
          const t4 = i5.getPropertyOptions(e6), h3 = "function" == typeof t4.converter ? { fromAttribute: t4.converter } : void 0 !== t4.converter?.fromAttribute ? t4.converter : u;
          this._$Em = e6;
          const r6 = h3.fromAttribute(s4, t4.type);
          this[e6] = r6 ?? this._$Ej?.get(e6) ?? r6, this._$Em = null;
        }
      }
      requestUpdate(t3, s4, i5, e6 = false, h3) {
        if (void 0 !== t3) {
          const r6 = this.constructor;
          if (false === e6 && (h3 = this[t3]), i5 ??= r6.getPropertyOptions(t3), !((i5.hasChanged ?? f)(h3, s4) || i5.useDefault && i5.reflect && h3 === this._$Ej?.get(t3) && !this.hasAttribute(r6._$Eu(t3, i5)))) return;
          this.C(t3, s4, i5);
        }
        false === this.isUpdatePending && (this._$ES = this._$EP());
      }
      C(t3, s4, { useDefault: i5, reflect: e6, wrapped: h3 }, r6) {
        i5 && !(this._$Ej ??= /* @__PURE__ */ new Map()).has(t3) && (this._$Ej.set(t3, r6 ?? s4 ?? this[t3]), true !== h3 || void 0 !== r6) || (this._$AL.has(t3) || (this.hasUpdated || i5 || (s4 = void 0), this._$AL.set(t3, s4)), true === e6 && this._$Em !== t3 && (this._$Eq ??= /* @__PURE__ */ new Set()).add(t3));
      }
      async _$EP() {
        this.isUpdatePending = true;
        try {
          await this._$ES;
        } catch (t4) {
          Promise.reject(t4);
        }
        const t3 = this.scheduleUpdate();
        return null != t3 && await t3, !this.isUpdatePending;
      }
      scheduleUpdate() {
        return this.performUpdate();
      }
      performUpdate() {
        if (!this.isUpdatePending) return;
        if (!this.hasUpdated) {
          if (this.renderRoot ??= this.createRenderRoot(), this._$Ep) {
            for (const [t5, s5] of this._$Ep) this[t5] = s5;
            this._$Ep = void 0;
          }
          const t4 = this.constructor.elementProperties;
          if (t4.size > 0) for (const [s5, i5] of t4) {
            const { wrapped: t5 } = i5, e6 = this[s5];
            true !== t5 || this._$AL.has(s5) || void 0 === e6 || this.C(s5, void 0, i5, e6);
          }
        }
        let t3 = false;
        const s4 = this._$AL;
        try {
          t3 = this.shouldUpdate(s4), t3 ? (this.willUpdate(s4), this._$EO?.forEach((t4) => t4.hostUpdate?.()), this.update(s4)) : this._$EM();
        } catch (s5) {
          throw t3 = false, this._$EM(), s5;
        }
        t3 && this._$AE(s4);
      }
      willUpdate(t3) {
      }
      _$AE(t3) {
        this._$EO?.forEach((t4) => t4.hostUpdated?.()), this.hasUpdated || (this.hasUpdated = true, this.firstUpdated(t3)), this.updated(t3);
      }
      _$EM() {
        this._$AL = /* @__PURE__ */ new Map(), this.isUpdatePending = false;
      }
      get updateComplete() {
        return this.getUpdateComplete();
      }
      getUpdateComplete() {
        return this._$ES;
      }
      shouldUpdate(t3) {
        return true;
      }
      update(t3) {
        this._$Eq &&= this._$Eq.forEach((t4) => this._$ET(t4, this[t4])), this._$EM();
      }
      updated(t3) {
      }
      firstUpdated(t3) {
      }
    };
    y.elementStyles = [], y.shadowRootOptions = { mode: "open" }, y[d("elementProperties")] = /* @__PURE__ */ new Map(), y[d("finalized")] = /* @__PURE__ */ new Map(), p?.({ ReactiveElement: y }), (a.reactiveElementVersions ??= []).push("2.1.2");
  }
});

// node_modules/lit-html/lit-html.js
function V(t3, i5) {
  if (!u2(t3) || !t3.hasOwnProperty("raw")) throw Error("invalid template strings array");
  return void 0 !== e3 ? e3.createHTML(i5) : i5;
}
function M(t3, i5, s4 = t3, e6) {
  if (i5 === E) return i5;
  let h3 = void 0 !== e6 ? s4._$Co?.[e6] : s4._$Cl;
  const o6 = a2(i5) ? void 0 : i5._$litDirective$;
  return h3?.constructor !== o6 && (h3?._$AO?.(false), void 0 === o6 ? h3 = void 0 : (h3 = new o6(t3), h3._$AT(t3, s4, e6)), void 0 !== e6 ? (s4._$Co ??= [])[e6] = h3 : s4._$Cl = h3), void 0 !== h3 && (i5 = M(t3, h3._$AS(t3, i5.values), h3, e6)), i5;
}
var t2, i3, s2, e3, h2, o3, n3, r3, l2, c3, a2, u2, d2, f2, v, _, m, p2, g, $, y2, x, b2, w, T, E, A, C, P, N, S2, R, k, H, I, L, z, Z, B, D;
var init_lit_html = __esm({
  "node_modules/lit-html/lit-html.js"() {
    t2 = globalThis;
    i3 = (t3) => t3;
    s2 = t2.trustedTypes;
    e3 = s2 ? s2.createPolicy("lit-html", { createHTML: (t3) => t3 }) : void 0;
    h2 = "$lit$";
    o3 = `lit$${Math.random().toFixed(9).slice(2)}$`;
    n3 = "?" + o3;
    r3 = `<${n3}>`;
    l2 = document;
    c3 = () => l2.createComment("");
    a2 = (t3) => null === t3 || "object" != typeof t3 && "function" != typeof t3;
    u2 = Array.isArray;
    d2 = (t3) => u2(t3) || "function" == typeof t3?.[Symbol.iterator];
    f2 = "[ 	\n\f\r]";
    v = /<(?:(!--|\/[^a-zA-Z])|(\/?[a-zA-Z][^>\s]*)|(\/?$))/g;
    _ = /-->/g;
    m = />/g;
    p2 = RegExp(`>|${f2}(?:([^\\s"'>=/]+)(${f2}*=${f2}*(?:[^ 	
\f\r"'\`<>=]|("|')|))|$)`, "g");
    g = /'/g;
    $ = /"/g;
    y2 = /^(?:script|style|textarea|title)$/i;
    x = (t3) => (i5, ...s4) => ({ _$litType$: t3, strings: i5, values: s4 });
    b2 = x(1);
    w = x(2);
    T = x(3);
    E = Symbol.for("lit-noChange");
    A = Symbol.for("lit-nothing");
    C = /* @__PURE__ */ new WeakMap();
    P = l2.createTreeWalker(l2, 129);
    N = (t3, i5) => {
      const s4 = t3.length - 1, e6 = [];
      let n5, l3 = 2 === i5 ? "<svg>" : 3 === i5 ? "<math>" : "", c4 = v;
      for (let i6 = 0; i6 < s4; i6++) {
        const s5 = t3[i6];
        let a3, u3, d3 = -1, f3 = 0;
        for (; f3 < s5.length && (c4.lastIndex = f3, u3 = c4.exec(s5), null !== u3); ) f3 = c4.lastIndex, c4 === v ? "!--" === u3[1] ? c4 = _ : void 0 !== u3[1] ? c4 = m : void 0 !== u3[2] ? (y2.test(u3[2]) && (n5 = RegExp("</" + u3[2], "g")), c4 = p2) : void 0 !== u3[3] && (c4 = p2) : c4 === p2 ? ">" === u3[0] ? (c4 = n5 ?? v, d3 = -1) : void 0 === u3[1] ? d3 = -2 : (d3 = c4.lastIndex - u3[2].length, a3 = u3[1], c4 = void 0 === u3[3] ? p2 : '"' === u3[3] ? $ : g) : c4 === $ || c4 === g ? c4 = p2 : c4 === _ || c4 === m ? c4 = v : (c4 = p2, n5 = void 0);
        const x2 = c4 === p2 && t3[i6 + 1].startsWith("/>") ? " " : "";
        l3 += c4 === v ? s5 + r3 : d3 >= 0 ? (e6.push(a3), s5.slice(0, d3) + h2 + s5.slice(d3) + o3 + x2) : s5 + o3 + (-2 === d3 ? i6 : x2);
      }
      return [V(t3, l3 + (t3[s4] || "<?>") + (2 === i5 ? "</svg>" : 3 === i5 ? "</math>" : "")), e6];
    };
    S2 = class _S {
      constructor({ strings: t3, _$litType$: i5 }, e6) {
        let r6;
        this.parts = [];
        let l3 = 0, a3 = 0;
        const u3 = t3.length - 1, d3 = this.parts, [f3, v2] = N(t3, i5);
        if (this.el = _S.createElement(f3, e6), P.currentNode = this.el.content, 2 === i5 || 3 === i5) {
          const t4 = this.el.content.firstChild;
          t4.replaceWith(...t4.childNodes);
        }
        for (; null !== (r6 = P.nextNode()) && d3.length < u3; ) {
          if (1 === r6.nodeType) {
            if (r6.hasAttributes()) for (const t4 of r6.getAttributeNames()) if (t4.endsWith(h2)) {
              const i6 = v2[a3++], s4 = r6.getAttribute(t4).split(o3), e7 = /([.?@])?(.*)/.exec(i6);
              d3.push({ type: 1, index: l3, name: e7[2], strings: s4, ctor: "." === e7[1] ? I : "?" === e7[1] ? L : "@" === e7[1] ? z : H }), r6.removeAttribute(t4);
            } else t4.startsWith(o3) && (d3.push({ type: 6, index: l3 }), r6.removeAttribute(t4));
            if (y2.test(r6.tagName)) {
              const t4 = r6.textContent.split(o3), i6 = t4.length - 1;
              if (i6 > 0) {
                r6.textContent = s2 ? s2.emptyScript : "";
                for (let s4 = 0; s4 < i6; s4++) r6.append(t4[s4], c3()), P.nextNode(), d3.push({ type: 2, index: ++l3 });
                r6.append(t4[i6], c3());
              }
            }
          } else if (8 === r6.nodeType) if (r6.data === n3) d3.push({ type: 2, index: l3 });
          else {
            let t4 = -1;
            for (; -1 !== (t4 = r6.data.indexOf(o3, t4 + 1)); ) d3.push({ type: 7, index: l3 }), t4 += o3.length - 1;
          }
          l3++;
        }
      }
      static createElement(t3, i5) {
        const s4 = l2.createElement("template");
        return s4.innerHTML = t3, s4;
      }
    };
    R = class {
      constructor(t3, i5) {
        this._$AV = [], this._$AN = void 0, this._$AD = t3, this._$AM = i5;
      }
      get parentNode() {
        return this._$AM.parentNode;
      }
      get _$AU() {
        return this._$AM._$AU;
      }
      u(t3) {
        const { el: { content: i5 }, parts: s4 } = this._$AD, e6 = (t3?.creationScope ?? l2).importNode(i5, true);
        P.currentNode = e6;
        let h3 = P.nextNode(), o6 = 0, n5 = 0, r6 = s4[0];
        for (; void 0 !== r6; ) {
          if (o6 === r6.index) {
            let i6;
            2 === r6.type ? i6 = new k(h3, h3.nextSibling, this, t3) : 1 === r6.type ? i6 = new r6.ctor(h3, r6.name, r6.strings, this, t3) : 6 === r6.type && (i6 = new Z(h3, this, t3)), this._$AV.push(i6), r6 = s4[++n5];
          }
          o6 !== r6?.index && (h3 = P.nextNode(), o6++);
        }
        return P.currentNode = l2, e6;
      }
      p(t3) {
        let i5 = 0;
        for (const s4 of this._$AV) void 0 !== s4 && (void 0 !== s4.strings ? (s4._$AI(t3, s4, i5), i5 += s4.strings.length - 2) : s4._$AI(t3[i5])), i5++;
      }
    };
    k = class _k {
      get _$AU() {
        return this._$AM?._$AU ?? this._$Cv;
      }
      constructor(t3, i5, s4, e6) {
        this.type = 2, this._$AH = A, this._$AN = void 0, this._$AA = t3, this._$AB = i5, this._$AM = s4, this.options = e6, this._$Cv = e6?.isConnected ?? true;
      }
      get parentNode() {
        let t3 = this._$AA.parentNode;
        const i5 = this._$AM;
        return void 0 !== i5 && 11 === t3?.nodeType && (t3 = i5.parentNode), t3;
      }
      get startNode() {
        return this._$AA;
      }
      get endNode() {
        return this._$AB;
      }
      _$AI(t3, i5 = this) {
        t3 = M(this, t3, i5), a2(t3) ? t3 === A || null == t3 || "" === t3 ? (this._$AH !== A && this._$AR(), this._$AH = A) : t3 !== this._$AH && t3 !== E && this._(t3) : void 0 !== t3._$litType$ ? this.$(t3) : void 0 !== t3.nodeType ? this.T(t3) : d2(t3) ? this.k(t3) : this._(t3);
      }
      O(t3) {
        return this._$AA.parentNode.insertBefore(t3, this._$AB);
      }
      T(t3) {
        this._$AH !== t3 && (this._$AR(), this._$AH = this.O(t3));
      }
      _(t3) {
        this._$AH !== A && a2(this._$AH) ? this._$AA.nextSibling.data = t3 : this.T(l2.createTextNode(t3)), this._$AH = t3;
      }
      $(t3) {
        const { values: i5, _$litType$: s4 } = t3, e6 = "number" == typeof s4 ? this._$AC(t3) : (void 0 === s4.el && (s4.el = S2.createElement(V(s4.h, s4.h[0]), this.options)), s4);
        if (this._$AH?._$AD === e6) this._$AH.p(i5);
        else {
          const t4 = new R(e6, this), s5 = t4.u(this.options);
          t4.p(i5), this.T(s5), this._$AH = t4;
        }
      }
      _$AC(t3) {
        let i5 = C.get(t3.strings);
        return void 0 === i5 && C.set(t3.strings, i5 = new S2(t3)), i5;
      }
      k(t3) {
        u2(this._$AH) || (this._$AH = [], this._$AR());
        const i5 = this._$AH;
        let s4, e6 = 0;
        for (const h3 of t3) e6 === i5.length ? i5.push(s4 = new _k(this.O(c3()), this.O(c3()), this, this.options)) : s4 = i5[e6], s4._$AI(h3), e6++;
        e6 < i5.length && (this._$AR(s4 && s4._$AB.nextSibling, e6), i5.length = e6);
      }
      _$AR(t3 = this._$AA.nextSibling, s4) {
        for (this._$AP?.(false, true, s4); t3 !== this._$AB; ) {
          const s5 = i3(t3).nextSibling;
          i3(t3).remove(), t3 = s5;
        }
      }
      setConnected(t3) {
        void 0 === this._$AM && (this._$Cv = t3, this._$AP?.(t3));
      }
    };
    H = class {
      get tagName() {
        return this.element.tagName;
      }
      get _$AU() {
        return this._$AM._$AU;
      }
      constructor(t3, i5, s4, e6, h3) {
        this.type = 1, this._$AH = A, this._$AN = void 0, this.element = t3, this.name = i5, this._$AM = e6, this.options = h3, s4.length > 2 || "" !== s4[0] || "" !== s4[1] ? (this._$AH = Array(s4.length - 1).fill(new String()), this.strings = s4) : this._$AH = A;
      }
      _$AI(t3, i5 = this, s4, e6) {
        const h3 = this.strings;
        let o6 = false;
        if (void 0 === h3) t3 = M(this, t3, i5, 0), o6 = !a2(t3) || t3 !== this._$AH && t3 !== E, o6 && (this._$AH = t3);
        else {
          const e7 = t3;
          let n5, r6;
          for (t3 = h3[0], n5 = 0; n5 < h3.length - 1; n5++) r6 = M(this, e7[s4 + n5], i5, n5), r6 === E && (r6 = this._$AH[n5]), o6 ||= !a2(r6) || r6 !== this._$AH[n5], r6 === A ? t3 = A : t3 !== A && (t3 += (r6 ?? "") + h3[n5 + 1]), this._$AH[n5] = r6;
        }
        o6 && !e6 && this.j(t3);
      }
      j(t3) {
        t3 === A ? this.element.removeAttribute(this.name) : this.element.setAttribute(this.name, t3 ?? "");
      }
    };
    I = class extends H {
      constructor() {
        super(...arguments), this.type = 3;
      }
      j(t3) {
        this.element[this.name] = t3 === A ? void 0 : t3;
      }
    };
    L = class extends H {
      constructor() {
        super(...arguments), this.type = 4;
      }
      j(t3) {
        this.element.toggleAttribute(this.name, !!t3 && t3 !== A);
      }
    };
    z = class extends H {
      constructor(t3, i5, s4, e6, h3) {
        super(t3, i5, s4, e6, h3), this.type = 5;
      }
      _$AI(t3, i5 = this) {
        if ((t3 = M(this, t3, i5, 0) ?? A) === E) return;
        const s4 = this._$AH, e6 = t3 === A && s4 !== A || t3.capture !== s4.capture || t3.once !== s4.once || t3.passive !== s4.passive, h3 = t3 !== A && (s4 === A || e6);
        e6 && this.element.removeEventListener(this.name, this, s4), h3 && this.element.addEventListener(this.name, this, t3), this._$AH = t3;
      }
      handleEvent(t3) {
        "function" == typeof this._$AH ? this._$AH.call(this.options?.host ?? this.element, t3) : this._$AH.handleEvent(t3);
      }
    };
    Z = class {
      constructor(t3, i5, s4) {
        this.element = t3, this.type = 6, this._$AN = void 0, this._$AM = i5, this.options = s4;
      }
      get _$AU() {
        return this._$AM._$AU;
      }
      _$AI(t3) {
        M(this, t3);
      }
    };
    B = t2.litHtmlPolyfillSupport;
    B?.(S2, k), (t2.litHtmlVersions ??= []).push("3.3.3");
    D = (t3, i5, s4) => {
      const e6 = s4?.renderBefore ?? i5;
      let h3 = e6._$litPart$;
      if (void 0 === h3) {
        const t4 = s4?.renderBefore ?? null;
        e6._$litPart$ = h3 = new k(i5.insertBefore(c3(), t4), t4, void 0, s4 ?? {});
      }
      return h3._$AI(t3), h3;
    };
  }
});

// node_modules/lit-element/lit-element.js
var s3, i4, o4;
var init_lit_element = __esm({
  "node_modules/lit-element/lit-element.js"() {
    init_reactive_element();
    init_reactive_element();
    init_lit_html();
    init_lit_html();
    s3 = globalThis;
    i4 = class extends y {
      constructor() {
        super(...arguments), this.renderOptions = { host: this }, this._$Do = void 0;
      }
      createRenderRoot() {
        const t3 = super.createRenderRoot();
        return this.renderOptions.renderBefore ??= t3.firstChild, t3;
      }
      update(t3) {
        const r6 = this.render();
        this.hasUpdated || (this.renderOptions.isConnected = this.isConnected), super.update(t3), this._$Do = D(r6, this.renderRoot, this.renderOptions);
      }
      connectedCallback() {
        super.connectedCallback(), this._$Do?.setConnected(true);
      }
      disconnectedCallback() {
        super.disconnectedCallback(), this._$Do?.setConnected(false);
      }
      render() {
        return E;
      }
    };
    i4._$litElement$ = true, i4["finalized"] = true, s3.litElementHydrateSupport?.({ LitElement: i4 });
    o4 = s3.litElementPolyfillSupport;
    o4?.({ LitElement: i4 });
    (s3.litElementVersions ??= []).push("4.2.2");
  }
});

// node_modules/lit-html/is-server.js
var init_is_server = __esm({
  "node_modules/lit-html/is-server.js"() {
  }
});

// node_modules/lit/index.js
var init_lit = __esm({
  "node_modules/lit/index.js"() {
    init_reactive_element();
    init_lit_html();
    init_lit_element();
    init_is_server();
  }
});

// node_modules/@lit/reactive-element/decorators/custom-element.js
var init_custom_element = __esm({
  "node_modules/@lit/reactive-element/decorators/custom-element.js"() {
  }
});

// node_modules/@lit/reactive-element/decorators/property.js
function n4(t3) {
  return (e6, o6) => "object" == typeof o6 ? r4(t3, e6, o6) : ((t4, e7, o7) => {
    const r6 = e7.hasOwnProperty(o7);
    return e7.constructor.createProperty(o7, t4), r6 ? Object.getOwnPropertyDescriptor(e7, o7) : void 0;
  })(t3, e6, o6);
}
var o5, r4;
var init_property = __esm({
  "node_modules/@lit/reactive-element/decorators/property.js"() {
    init_reactive_element();
    o5 = { attribute: true, type: String, converter: u, reflect: false, hasChanged: f };
    r4 = (t3 = o5, e6, r6) => {
      const { kind: n5, metadata: i5 } = r6;
      let s4 = globalThis.litPropertyMetadata.get(i5);
      if (void 0 === s4 && globalThis.litPropertyMetadata.set(i5, s4 = /* @__PURE__ */ new Map()), "setter" === n5 && ((t3 = Object.create(t3)).wrapped = true), s4.set(r6.name, t3), "accessor" === n5) {
        const { name: o6 } = r6;
        return { set(r7) {
          const n6 = e6.get.call(this);
          e6.set.call(this, r7), this.requestUpdate(o6, n6, t3, true, r7);
        }, init(e7) {
          return void 0 !== e7 && this.C(o6, void 0, t3, e7), e7;
        } };
      }
      if ("setter" === n5) {
        const { name: o6 } = r6;
        return function(r7) {
          const n6 = this[o6];
          e6.call(this, r7), this.requestUpdate(o6, n6, t3, true, r7);
        };
      }
      throw Error("Unsupported decorator location: " + n5);
    };
  }
});

// node_modules/@lit/reactive-element/decorators/state.js
function r5(r6) {
  return n4({ ...r6, state: true, attribute: false });
}
var init_state = __esm({
  "node_modules/@lit/reactive-element/decorators/state.js"() {
    init_property();
  }
});

// node_modules/@lit/reactive-element/decorators/event-options.js
var init_event_options = __esm({
  "node_modules/@lit/reactive-element/decorators/event-options.js"() {
  }
});

// node_modules/@lit/reactive-element/decorators/base.js
var e4;
var init_base = __esm({
  "node_modules/@lit/reactive-element/decorators/base.js"() {
    e4 = (e6, t3, c4) => (c4.configurable = true, c4.enumerable = true, Reflect.decorate && "object" != typeof t3 && Object.defineProperty(e6, t3, c4), c4);
  }
});

// node_modules/@lit/reactive-element/decorators/query.js
function e5(e6, r6) {
  return (n5, s4, i5) => {
    const o6 = (t3) => t3.renderRoot?.querySelector(e6) ?? null;
    if (r6) {
      const { get: e7, set: r7 } = "object" == typeof s4 ? n5 : i5 ?? (() => {
        const t3 = Symbol();
        return { get() {
          return this[t3];
        }, set(e8) {
          this[t3] = e8;
        } };
      })();
      return e4(n5, s4, { get() {
        let t3 = e7.call(this);
        return void 0 === t3 && (t3 = o6(this), (null !== t3 || this.hasUpdated) && r7.call(this, t3)), t3;
      } });
    }
    return e4(n5, s4, { get() {
      return o6(this);
    } });
  };
}
var init_query = __esm({
  "node_modules/@lit/reactive-element/decorators/query.js"() {
    init_base();
  }
});

// node_modules/@lit/reactive-element/decorators/query-all.js
var init_query_all = __esm({
  "node_modules/@lit/reactive-element/decorators/query-all.js"() {
    init_base();
  }
});

// node_modules/@lit/reactive-element/decorators/query-async.js
var init_query_async = __esm({
  "node_modules/@lit/reactive-element/decorators/query-async.js"() {
    init_base();
  }
});

// node_modules/@lit/reactive-element/decorators/query-assigned-elements.js
var init_query_assigned_elements = __esm({
  "node_modules/@lit/reactive-element/decorators/query-assigned-elements.js"() {
    init_base();
  }
});

// node_modules/@lit/reactive-element/decorators/query-assigned-nodes.js
var init_query_assigned_nodes = __esm({
  "node_modules/@lit/reactive-element/decorators/query-assigned-nodes.js"() {
    init_base();
  }
});

// node_modules/lit/decorators.js
var init_decorators = __esm({
  "node_modules/lit/decorators.js"() {
    init_custom_element();
    init_property();
    init_state();
    init_event_options();
    init_query();
    init_query_all();
    init_query_async();
    init_query_assigned_elements();
    init_query_assigned_nodes();
  }
});

// node_modules/@loquix/core/dist/controllers/autoscroll.controller.js
var AutoScrollController;
var init_autoscroll_controller = __esm({
  "node_modules/@loquix/core/dist/controllers/autoscroll.controller.js"() {
    AutoScrollController = class {
      constructor(host, options) {
        this._scrollContainer = null;
        this._isUserScrolled = false;
        this._isProgrammaticScroll = false;
        this._resizeObserver = null;
        this._observedElements = /* @__PURE__ */ new Set();
        this._rafId = 0;
        this._scrollGuardTimer = 0;
        this._onScroll = () => {
          var _a2, _b;
          if (this._isProgrammaticScroll) return;
          const wasUserScrolled = this._isUserScrolled;
          this._isUserScrolled = !this.isAtBottom;
          if (wasUserScrolled !== this._isUserScrolled) {
            (_b = (_a2 = this._options).onScrollStateChange) == null ? void 0 : _b.call(_a2, !this._isUserScrolled);
          }
        };
        this.host = host;
        this._options = options ?? {};
        host.addController(this);
      }
      get _threshold() {
        return this._options.threshold ?? 50;
      }
      hostConnected() {
      }
      hostDisconnected() {
        this.detach();
      }
      /**
       * Attach to a scrollable container element.
       * Call this in updated() or after shadow DOM is ready.
       */
      attach(container) {
        this.detach();
        this._scrollContainer = container;
        container.addEventListener("scroll", this._onScroll, { passive: true });
        this._resizeObserver = new ResizeObserver(() => {
          if (!this._isUserScrolled) {
            cancelAnimationFrame(this._rafId);
            this._rafId = requestAnimationFrame(() => {
              this._scrollToBottomInternal("instant");
            });
          }
        });
        this._isUserScrolled = !this.isAtBottom;
        Promise.resolve().then(() => {
          var _a2, _b;
          (_b = (_a2 = this._options).onScrollStateChange) == null ? void 0 : _b.call(_a2, !this._isUserScrolled);
        });
      }
      detach() {
        var _a2;
        if (this._scrollContainer) {
          this._scrollContainer.removeEventListener("scroll", this._onScroll);
        }
        (_a2 = this._resizeObserver) == null ? void 0 : _a2.disconnect();
        this._resizeObserver = null;
        this._observedElements.clear();
        cancelAnimationFrame(this._rafId);
        this._rafId = 0;
        clearTimeout(this._scrollGuardTimer);
        this._scrollGuardTimer = 0;
        this._isProgrammaticScroll = false;
        this._scrollContainer = null;
      }
      /** Check if the user is near the bottom */
      get isAtBottom() {
        if (!this._scrollContainer) return true;
        const { scrollTop, scrollHeight, clientHeight } = this._scrollContainer;
        return scrollHeight - scrollTop - clientHeight <= this._threshold;
      }
      /** Whether the user has scrolled away from bottom */
      get isUserScrolled() {
        return this._isUserScrolled;
      }
      /**
       * Observe elements for size changes (e.g. slotted message items).
       * When an observed element resizes (streaming text, images loading),
       * auto-scroll is triggered if the user hasn't scrolled away.
       *
       * Call this whenever the set of slotted elements changes (slotchange).
       */
      observeElements(elements) {
        if (!this._resizeObserver) return;
        const newSet = new Set(elements);
        for (const el of this._observedElements) {
          if (!newSet.has(el)) {
            this._resizeObserver.unobserve(el);
          }
        }
        for (const el of newSet) {
          if (!this._observedElements.has(el)) {
            this._resizeObserver.observe(el);
          }
        }
        this._observedElements = newSet;
      }
      /** Programmatically scroll to bottom */
      scrollToBottom(behavior = "smooth") {
        this._scrollToBottomInternal(behavior);
      }
      /**
       * Scroll a specific element into view (centered in the scroll container).
       * Uses getBoundingClientRect + scrollTo to avoid cross-shadow-DOM scrolling issues.
       */
      scrollToElement(element, behavior = "smooth") {
        if (!this._scrollContainer) return;
        const containerRect = this._scrollContainer.getBoundingClientRect();
        const elementRect = element.getBoundingClientRect();
        const offset = elementRect.top - containerRect.top + this._scrollContainer.scrollTop;
        const centerOffset = offset - this._scrollContainer.clientHeight / 2 + elementRect.height / 2;
        this._setProgrammaticGuard(behavior);
        this._scrollContainer.scrollTo({
          top: Math.max(0, centerOffset),
          behavior
        });
        cancelAnimationFrame(this._rafId);
        this._rafId = requestAnimationFrame(() => {
          this._isUserScrolled = !this.isAtBottom;
        });
      }
      // ---------------------------------------------------------------------------
      // Internal
      // ---------------------------------------------------------------------------
      _scrollToBottomInternal(behavior) {
        var _a2, _b;
        if (!this._scrollContainer) return;
        const wasUserScrolled = this._isUserScrolled;
        this._setProgrammaticGuard(behavior);
        this._scrollContainer.scrollTo({
          top: this._scrollContainer.scrollHeight,
          behavior
        });
        this._isUserScrolled = false;
        if (wasUserScrolled) {
          (_b = (_a2 = this._options).onScrollStateChange) == null ? void 0 : _b.call(_a2, true);
        }
      }
      /**
       * Set a guard flag that suppresses user-scroll detection during programmatic scrolls.
       * `instant` completes immediately; `smooth` animates over ~300ms.
       */
      _setProgrammaticGuard(behavior) {
        this._isProgrammaticScroll = true;
        clearTimeout(this._scrollGuardTimer);
        const delay = behavior === "instant" ? 50 : 400;
        this._scrollGuardTimer = window.setTimeout(() => {
          this._isProgrammaticScroll = false;
        }, delay);
      }
    };
  }
});

// node_modules/@loquix/core/dist/chunks/index-PICxVGtN.js
function createLoquixEvent(name, detail, options) {
  return new CustomEvent(name, {
    bubbles: true,
    composed: true,
    detail,
    ...options
  });
}
var init_index_PICxVGtN = __esm({
  "node_modules/@loquix/core/dist/chunks/index-PICxVGtN.js"() {
  }
});

// node_modules/@loquix/core/dist/chunks/index-VPyVRNKW.js
var en, I18nRegistry, registry, LocalizeController;
var init_index_VPyVRNKW = __esm({
  "node_modules/@loquix/core/dist/chunks/index-VPyVRNKW.js"() {
    en = {
      // === loquix-action-copy ===
      "actionCopy.label": "Copy message",
      "actionCopy.copied": "Copied",
      // === loquix-action-edit ===
      "actionEdit.triggerLabel": "Edit message",
      "actionEdit.textareaLabel": "Edit message",
      "actionEdit.submitLabel": "Save & Submit",
      "actionEdit.cancelLabel": "Cancel",
      "actionEdit.editingBadge": "Editing\u2026",
      // === loquix-action-feedback ===
      "actionFeedback.positiveLabel": "Good response",
      "actionFeedback.negativeLabel": "Bad response",
      // === loquix-message-actions ===
      "messageActions.toolbarLabel": "Message actions",
      "messageActions.regenerate": "Regenerate response",
      // === loquix-chat-composer ===
      "chatComposer.placeholder": "Type a message...",
      "chatComposer.sendLabel": "Send message",
      "chatComposer.stopLabel": "Stop generation",
      // === loquix-prompt-input ===
      "promptInput.placeholder": "Type a message...",
      // === loquix-message-item ===
      "messageItem.showMore": "Show more",
      "messageItem.showLess": "Show less",
      "messageItem.errorText": "Failed to generate response",
      "messageItem.retryLabel": "Retry",
      "messageItem.userAvatar": "You",
      // === loquix-generation-controls ===
      "generationControls.toolbarLabel": "Generation controls",
      "generationControls.stop": "Stop",
      "generationControls.stopLabel": "Stop generation",
      "generationControls.pause": "Pause",
      "generationControls.pauseLabel": "Pause generation",
      "generationControls.resume": "Resume",
      "generationControls.resumeLabel": "Resume generation",
      // === loquix-typing-indicator ===
      "typingIndicator.ariaLabel": "AI is typing",
      "typingIndicator.thinkingMessage": "Thinking\u2026",
      "typingIndicator.workingMessage": "Working\u2026",
      // === loquix-template-picker ===
      "templatePicker.heading": "Choose a template",
      "templatePicker.closeLabel": "Close",
      "templatePicker.searchPlaceholder": "Search templates...",
      "templatePicker.searchLabel": "Search templates",
      "templatePicker.emptyText": "No templates match your search.",
      // === loquix-model-selector ===
      "modelSelector.placeholder": "Select model...",
      "modelSelector.searchPlaceholder": "Search models...",
      "modelSelector.searchLabel": "Search models",
      "modelSelector.emptyText": "No models found",
      // === loquix-attachment-chip ===
      "attachmentChip.pending": "Waiting\u2026",
      "attachmentChip.connecting": "Connecting\u2026",
      "attachmentChip.error": "Error",
      "attachmentChip.uploadError": "Upload error",
      "attachmentChip.retryLabel": "Retry upload {filename}",
      "attachmentChip.removeLabel": "Remove {filename}",
      // === loquix-attachment-panel ===
      "attachmentPanel.triggerLabel": "Add files",
      "attachmentPanel.dropOverlay": "Drop files here",
      // === loquix-message-attachments ===
      "messageAttachments.removeLabel": "Remove {filename}",
      "messageAttachments.moreCount": "+{count} more",
      // === loquix-suggestion-chips ===
      "suggestionChips.ariaLabel": "Suggestions",
      "suggestionChips.moreCount": "+{count} more",
      // === loquix-nudge-banner ===
      "nudgeBanner.dismissLabel": "Dismiss",
      // === loquix-dropdown-select ===
      "dropdownSelect.placeholder": "Select...",
      "dropdownSelect.searchPlaceholder": "Search...",
      // === loquix-filter-bar ===
      "filterBar.ariaLabel": "Filters",
      "filterBar.excludeLabel": "Exclude:",
      "filterBar.excludePlaceholder": "Topics to avoid...",
      // === loquix-mode-selector ===
      "modeSelector.ariaLabel": "Chat mode",
      "modeSelector.selectLabel": "Select mode",
      // === loquix-parameter-panel ===
      "parameterPanel.showAdvanced": "Show advanced",
      "parameterPanel.hideAdvanced": "Hide advanced",
      // === loquix-drop-zone ===
      "dropZone.label": "Drop files to upload",
      // === loquix-welcome-screen ===
      "welcomeScreen.heading": "How can I help you?",
      // === loquix-caveat-notice ===
      "caveatNotice.message": "AI can make mistakes. Check important info.",
      // === loquix-chat-header ===
      "chatHeader.agentName": "AI Assistant",
      // === loquix-disclosure-badge ===
      "disclosureBadge.generated": "Generated by {agent}",
      "disclosureBadge.summarized": "Summarized by {agent}",
      "disclosureBadge.rewrote": "Rewritten by {agent}",
      "disclosureBadge.suggested": "Suggested by {agent}",
      // === loquix-message-avatar ===
      "messageAvatar.altFallback": "Avatar",
      "messageAvatar.namedAlt": "{name} avatar",
      "messageAvatar.aiAlt": "AI avatar",
      // === loquix-scroll-anchor ===
      "scrollAnchor.label": "Scroll to bottom",
      // === loquix-confidence-indicator (Phase 4 PR #2) ===
      "confidenceIndicator.labelDefault": "Confidence",
      "confidenceIndicator.levelLow": "low",
      "confidenceIndicator.levelMedium": "medium",
      "confidenceIndicator.levelHigh": "high",
      // === loquix-uncertainty-marker (Phase 4 PR #2) ===
      "uncertaintyMarker.tipUnsure": "Model is uncertain about this claim",
      "uncertaintyMarker.tipNeedsVerification": "This statement needs verification",
      "uncertaintyMarker.tipSpeculative": "Speculative \u2014 not directly grounded in sources",
      // === loquix-feedback-form (Phase 4 PR #2) ===
      "feedbackForm.groupLabel": "Rate this response",
      "feedbackForm.reasonsGroupLabel": "Reason",
      "feedbackForm.helpfulLabel": "Helpful",
      "feedbackForm.notHelpfulLabel": "Not helpful",
      "feedbackForm.sendLabel": "Send",
      "feedbackForm.cancelLabel": "Cancel",
      "feedbackForm.placeholderUp": "What did you like? (optional)",
      "feedbackForm.placeholderDown": "What went wrong? (optional)",
      "feedbackForm.thanksMessage": "Thanks \u2014 your feedback was sent.",
      "feedbackForm.reasonAccurate": "Accurate",
      "feedbackForm.reasonWellWritten": "Well-written",
      "feedbackForm.reasonHelpful": "Helpful",
      "feedbackForm.reasonInaccurate": "Inaccurate",
      "feedbackForm.reasonOffTopic": "Off-topic",
      "feedbackForm.reasonUnsafe": "Unsafe",
      "feedbackForm.reasonOther": "Other",
      // === loquix-correction-input (Phase 4 PR #2) ===
      "correctionInput.originalLabel": "Original",
      "correctionInput.correctionLabel": "Correction",
      "correctionInput.reasonLabel": "Why is this wrong?",
      "correctionInput.reasonPlaceholder": "Brief explanation",
      "correctionInput.correctionPlaceholder": "Provide the correct version\u2026",
      "correctionInput.submitLabel": "Submit correction",
      "correctionInput.cancelLabel": "Cancel",
      "correctionInput.reasonRequiredMark": "*",
      // === loquix-disagreement-marker (Phase 4 PR #2) ===
      "disagreementMarker.title": "User disagreed with this response",
      "disagreementMarker.markResolvedLabel": "Mark resolved",
      "disagreementMarker.inlineLabel": "Disagreement",
      // === loquix-reasoning-block (Phase 4 PR #1) ===
      "reasoningBlock.thinking": "Thinking\u2026",
      "reasoningBlock.thoughtFor": "Thought for {seconds}s",
      "reasoningBlock.thoughtForMinutes": "Thought for {minutes}m {seconds}s",
      "reasoningBlock.tokensSuffix": "\xB7 {count} tokens",
      "reasoningBlock.expandLabel": "Show reasoning",
      "reasoningBlock.collapseLabel": "Hide reasoning",
      // === loquix-tool-call (Phase 4 PR #1) ===
      "toolCall.statusQueued": "Queued",
      "toolCall.statusRunning": "Running",
      "toolCall.statusDone": "Done",
      "toolCall.statusFailed": "Failed",
      "toolCall.argumentsLabel": "Arguments",
      "toolCall.resultLabel": "Result",
      "toolCall.errorLabel": "Error",
      "toolCall.expandLabel": "Expand tool call",
      "toolCall.collapseLabel": "Collapse tool call",
      // === loquix-tool-call-list (Phase 4 PR #1) ===
      "toolCallList.summaryFallback": "Used {count} tools",
      "toolCallList.summaryFallbackOne": "Used 1 tool",
      "toolCallList.expandLabel": "Show tool calls",
      "toolCallList.collapseLabel": "Hide tool calls",
      // === loquix-source-list (Phase 4 PR #1) ===
      "sourceList.heading": "{count} sources",
      "sourceList.headingOne": "1 source",
      "sourceList.openLabel": "Open source {index}",
      // === loquix-search-* ===
      "searchInput.placeholder": "Search or ask anything...",
      "searchInput.plainPlaceholder": "Search...",
      "searchInput.askLabel": "Ask AI",
      "searchInput.clearLabel": "Clear search",
      "searchFooter.ariaLabel": "Search keyboard shortcuts",
      "searchAnswer.title": "AI Answer",
      "searchAnswer.generatedBy": "Generated by {model}",
      "searchAnswer.copyLabel": "Copy",
      "searchAnswer.regenerateLabel": "Regenerate",
      "searchAnswer.sourcesLabel": "Cited sources",
      "searchSources.ariaLabel": "Search sources",
      "searchSources.searchingSources": "Searching {count} sources...",
      "searchSources.resultsSoFar": "{count} results so far",
      "searchSources.allLabel": "All",
      "searchSources.errorLabel": "error",
      "searchResults.empty": "No results found.",
      "searchResults.unknownSource": "Unknown source",
      "searchResults.resultCount": "{count} results",
      "searchResults.resultCountOne": "1 result",
      "searchDialog.heading": "Search",
      "searchDialog.closeLabel": "Close search",
      "searchPanel.heading": "Search results"
    };
    I18nRegistry = class {
      constructor() {
        this._overrides = {};
        this._subscribers = /* @__PURE__ */ new Set();
        this._pendingNotify = false;
      }
      /** Replace all overrides at once. */
      setLocale(translations) {
        this._overrides = { ...translations };
        this._scheduleNotify();
      }
      /** Merge additional overrides into the current locale. */
      updateLocale(translations) {
        Object.assign(this._overrides, translations);
        this._scheduleNotify();
      }
      /** Clear all overrides, reverting to the default English locale. */
      resetLocale() {
        this._overrides = {};
        this._scheduleNotify();
      }
      /** Return a shallow copy of the current overrides. */
      getLocale() {
        return { ...this._overrides };
      }
      /**
       * Resolve a translation key with optional `{variable}` interpolation.
       * Priority: override > default English.
       */
      term(key, values) {
        let str = this._overrides[key] ?? en[key];
        if (values) {
          for (const [k2, v2] of Object.entries(values)) {
            str = str.replaceAll(`{${k2}}`, String(v2));
          }
        }
        return str;
      }
      /** Subscribe a host to locale change notifications. */
      subscribe(host) {
        this._subscribers.add(host);
      }
      /** Unsubscribe a host from locale change notifications. */
      unsubscribe(host) {
        this._subscribers.delete(host);
      }
      /**
       * Microtask-batched notification — multiple calls to `setLocale` /
       * `updateLocale` within the same microtask only trigger a single
       * `requestUpdate()` per component.
       */
      _scheduleNotify() {
        if (this._pendingNotify) return;
        this._pendingNotify = true;
        queueMicrotask(() => {
          this._pendingNotify = false;
          this._subscribers.forEach((host) => {
            try {
              host.requestUpdate();
            } catch {
            }
          });
        });
      }
    };
    registry = new I18nRegistry();
    LocalizeController = class {
      constructor(host) {
        this._host = host;
        host.addController(this);
      }
      hostConnected() {
        registry.subscribe(this._host);
        this._host.requestUpdate();
      }
      hostDisconnected() {
        registry.unsubscribe(this._host);
      }
      /**
       * Resolve a translation key with optional `{variable}` interpolation.
       */
      term(key, values) {
        return registry.term(key, values);
      }
    };
  }
});

// node_modules/@loquix/core/dist/components/core/loquix-scroll-anchor.js
var styles, __defProp, __decorateClass, _LoquixScrollAnchor, LoquixScrollAnchor;
var init_loquix_scroll_anchor = __esm({
  "node_modules/@loquix/core/dist/components/core/loquix-scroll-anchor.js"() {
    init_lit();
    init_decorators();
    init_index_VPyVRNKW();
    styles = i`
  :host {
    display: block;
    position: absolute;
    bottom: var(--loquix-scroll-anchor-bottom, 16px);
    right: var(--loquix-scroll-anchor-right, 16px);
    z-index: 10;
    pointer-events: none;
    opacity: 0;
    transform: translateY(8px);
    transition:
      opacity 200ms cubic-bezier(0.4, 0, 0.2, 1),
      transform 200ms cubic-bezier(0.4, 0, 0.2, 1);
  }

  :host([visible]) {
    pointer-events: auto;
    opacity: 1;
    transform: translateY(0);
  }

  .anchor-button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: var(--loquix-scroll-anchor-size, 36px);
    height: var(--loquix-scroll-anchor-size, 36px);
    padding: 0;
    border: 1px solid var(--loquix-border-color, #e2e8f0);
    border-radius: var(--loquix-scroll-anchor-border-radius, 50%);
    background: var(--loquix-scroll-anchor-bg, var(--loquix-surface-color, #ffffff));
    color: var(--loquix-scroll-anchor-color, var(--loquix-text-secondary-color, #6b7280));
    cursor: pointer;
    box-shadow: var(--loquix-scroll-anchor-shadow, 0 2px 8px rgba(0, 0, 0, 0.1));
    transition:
      background 200ms cubic-bezier(0.4, 0, 0.2, 1),
      color 200ms cubic-bezier(0.4, 0, 0.2, 1);
  }

  .anchor-button:hover {
    background: var(--loquix-scroll-anchor-hover-bg, var(--loquix-surface-secondary-bg, #f9fafb));
    color: var(--loquix-scroll-anchor-hover-color, var(--loquix-text-color, #111827));
  }

  .anchor-button:focus-visible {
    outline: 2px solid var(--loquix-input-focus-color, #7c3aed);
    outline-offset: 1px;
  }

  /* Icon via CSS mask — supports currentColor for theming.
     Override with --loquix-scroll-anchor-icon: url('custom.png') */
  .anchor-button::before {
    content: '';
    display: block;
    width: var(--loquix-scroll-anchor-icon-size, 18px);
    height: var(--loquix-scroll-anchor-icon-size, 18px);
    background-color: currentColor;
    -webkit-mask: var(
        --loquix-scroll-anchor-icon,
        url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='white' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E")
      )
      center / contain no-repeat;
    mask: var(
        --loquix-scroll-anchor-icon,
        url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='white' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E")
      )
      center / contain no-repeat;
  }

  @media (prefers-reduced-motion: reduce) {
    :host {
      transition: none;
    }

    .anchor-button {
      transition: none;
    }
  }
`;
    __defProp = Object.defineProperty;
    __decorateClass = (decorators, target, key, kind) => {
      var result = void 0;
      for (var i5 = decorators.length - 1, decorator; i5 >= 0; i5--)
        if (decorator = decorators[i5])
          result = decorator(target, key, result) || result;
      if (result) __defProp(target, key, result);
      return result;
    };
    _LoquixScrollAnchor = class _LoquixScrollAnchor2 extends i4 {
      constructor() {
        super(...arguments);
        this._localize = new LocalizeController(this);
        this.visible = false;
      }
      _handleClick() {
        this.dispatchEvent(
          new CustomEvent("loquix-scroll-anchor-click", {
            bubbles: true,
            composed: true
          })
        );
      }
      render() {
        return b2`
      <button
        part="button"
        class="anchor-button"
        aria-label=${this.label ?? this._localize.term("scrollAnchor.label")}
        @click=${this._handleClick}
      ></button>
    `;
      }
    };
    _LoquixScrollAnchor.styles = [styles];
    LoquixScrollAnchor = _LoquixScrollAnchor;
    __decorateClass([
      n4({ type: Boolean, reflect: true })
    ], LoquixScrollAnchor.prototype, "visible");
    __decorateClass([
      n4({ type: String })
    ], LoquixScrollAnchor.prototype, "label");
  }
});

// node_modules/@loquix/core/dist/components/core/define-scroll-anchor.js
var init_define_scroll_anchor = __esm({
  "node_modules/@loquix/core/dist/components/core/define-scroll-anchor.js"() {
    init_loquix_scroll_anchor();
    if (!customElements.get("loquix-scroll-anchor")) {
      customElements.define("loquix-scroll-anchor", LoquixScrollAnchor);
    }
  }
});

// node_modules/@loquix/core/dist/components/core/loquix-message-list.js
var styles2, __defProp2, __decorateClass2, _LoquixMessageList, LoquixMessageList;
var init_loquix_message_list = __esm({
  "node_modules/@loquix/core/dist/components/core/loquix-message-list.js"() {
    init_lit();
    init_decorators();
    init_autoscroll_controller();
    init_index_PICxVGtN();
    init_define_scroll_anchor();
    styles2 = i`
  :host {
    display: block;
    width: 100%;
    height: 100%;
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }

  .list {
    height: 100%;
    display: flex;
    flex-direction: column;
    position: relative; /* positioning context for scroll-anchor */
  }

  .scroll-container {
    flex: 1;
    overflow-y: auto;
    scrollbar-gutter: stable;
    display: flex;
    flex-direction: column;
    gap: var(--loquix-message-gap, 16px);
    padding: var(--loquix-message-list-padding, 16px);
    background: var(--loquix-message-list-bg, transparent);

    /* Smooth scrollbar (webkit) */
    scrollbar-width: thin;
    scrollbar-color: var(--loquix-scrollbar-thumb, #cbd5e1) transparent;
  }

  .scroll-container::-webkit-scrollbar {
    width: 6px;
  }

  .scroll-container::-webkit-scrollbar-track {
    background: transparent;
  }

  .scroll-container::-webkit-scrollbar-thumb {
    background: var(--loquix-scrollbar-thumb, #cbd5e1);
    border-radius: 3px;
  }

  /* Push messages to the bottom when there are few */
  ::slotted(*) {
    flex-shrink: 0;
  }
`;
    __defProp2 = Object.defineProperty;
    __decorateClass2 = (decorators, target, key, kind) => {
      var result = void 0;
      for (var i5 = decorators.length - 1, decorator; i5 >= 0; i5--)
        if (decorator = decorators[i5])
          result = decorator(target, key, result) || result;
      if (result) __defProp2(target, key, result);
      return result;
    };
    _LoquixMessageList = class _LoquixMessageList2 extends i4 {
      constructor() {
        super(...arguments);
        this.autoScroll = true;
        this.virtualize = false;
        this.showTimestamps = false;
        this.showScrollAnchor = true;
        this.scrollOnSend = true;
        this._hasMessages = false;
        this._showAnchor = false;
        this._submitRoot = null;
        this._autoScrollController = new AutoScrollController(this, {
          onScrollStateChange: (isAtBottom) => {
            var _a2;
            this._showAnchor = !isAtBottom;
            if (isAtBottom) {
              this.dispatchEvent(createLoquixEvent("loquix-scroll-bottom", {}));
            } else {
              this.dispatchEvent(
                createLoquixEvent("loquix-scroll-away", {
                  scrollTop: ((_a2 = this._scrollContainer) == null ? void 0 : _a2.scrollTop) ?? 0
                })
              );
            }
          }
        });
        this._attached = false;
        this._handleSubmitForScroll = (_e) => {
          if (this.scrollOnSend && this.autoScroll) {
            requestAnimationFrame(() => this._autoScrollController.scrollToBottom("instant"));
          }
        };
      }
      firstUpdated() {
        this._checkForMessages();
        this._attachIfNeeded();
      }
      updated(changed) {
        if (changed.has("autoScroll")) {
          if (this.autoScroll) {
            this._attachIfNeeded();
          } else {
            this._detach();
          }
        }
      }
      connectedCallback() {
        super.connectedCallback();
        this._submitRoot = this.getRootNode();
        this._submitRoot.addEventListener(
          "loquix-submit",
          this._handleSubmitForScroll
        );
        if (this.autoScroll && this.hasUpdated) {
          this.updateComplete.then(() => this._attachIfNeeded());
        }
      }
      disconnectedCallback() {
        var _a2;
        super.disconnectedCallback();
        (_a2 = this._submitRoot) == null ? void 0 : _a2.removeEventListener(
          "loquix-submit",
          this._handleSubmitForScroll
        );
        this._submitRoot = null;
        this._attached = false;
      }
      _attachIfNeeded() {
        if (!this.autoScroll || !this._scrollContainer || this._attached) return;
        this._autoScrollController.attach(this._scrollContainer);
        this._observeSlottedElements();
        this._autoScrollController.scrollToBottom("instant");
        this._attached = true;
      }
      _detach() {
        this._autoScrollController.detach();
        this._showAnchor = false;
        this._attached = false;
      }
      // ---------------------------------------------------------------------------
      // Public methods
      // ---------------------------------------------------------------------------
      /** Programmatically scroll to the bottom. */
      scrollToBottom(behavior = "smooth") {
        this._autoScrollController.scrollToBottom(behavior);
      }
      /**
       * Scroll to a message element by its message-id attribute.
       * @returns true if the message was found and scrolled to.
       */
      scrollToMessage(messageId) {
        var _a2;
        const slot = (_a2 = this.shadowRoot) == null ? void 0 : _a2.querySelector("slot:not([name])");
        if (!slot) return false;
        const elements = slot.assignedElements({ flatten: true });
        const target = elements.find((el) => el.getAttribute("message-id") === messageId);
        if (!target) return false;
        this._autoScrollController.scrollToElement(target);
        return true;
      }
      _handleAnchorClick() {
        this._autoScrollController.scrollToBottom("smooth");
      }
      _handleSlotChange() {
        this._checkForMessages();
        this._observeSlottedElements();
        if (this.autoScroll && !this._autoScrollController.isUserScrolled) {
          requestAnimationFrame(() => {
            this._autoScrollController.scrollToBottom("instant");
          });
        }
      }
      _observeSlottedElements() {
        var _a2;
        const slot = (_a2 = this.shadowRoot) == null ? void 0 : _a2.querySelector("slot:not([name])");
        if (!slot) return;
        const elements = slot.assignedElements({ flatten: true });
        this._autoScrollController.observeElements(elements);
      }
      _checkForMessages() {
        var _a2;
        const slot = (_a2 = this.shadowRoot) == null ? void 0 : _a2.querySelector("slot:not([name])");
        if (slot) {
          const assigned = slot.assignedElements({ flatten: true });
          this._hasMessages = assigned.length > 0;
        }
      }
      // ---------------------------------------------------------------------------
      // Render
      // ---------------------------------------------------------------------------
      render() {
        return b2`
      <div part="list" class="list">
        <div part="scroll-container" class="scroll-container">
          <slot name="loading"></slot>

          ${!this._hasMessages ? b2`<slot name="empty-state"></slot>` : A}

          <slot @slotchange=${this._handleSlotChange}></slot>
        </div>

        ${this.showScrollAnchor && this.autoScroll ? b2`<loquix-scroll-anchor
              part="scroll-anchor"
              ?visible=${this._showAnchor}
              @loquix-scroll-anchor-click=${this._handleAnchorClick}
            ></loquix-scroll-anchor>` : A}
      </div>
    `;
      }
    };
    _LoquixMessageList.styles = [styles2];
    LoquixMessageList = _LoquixMessageList;
    __decorateClass2([
      n4({ type: Boolean, attribute: "auto-scroll" })
    ], LoquixMessageList.prototype, "autoScroll");
    __decorateClass2([
      n4({ type: Boolean })
    ], LoquixMessageList.prototype, "virtualize");
    __decorateClass2([
      n4({ type: Boolean, attribute: "show-timestamps" })
    ], LoquixMessageList.prototype, "showTimestamps");
    __decorateClass2([
      n4({ type: Boolean, attribute: "show-scroll-anchor" })
    ], LoquixMessageList.prototype, "showScrollAnchor");
    __decorateClass2([
      n4({ type: Boolean, attribute: "scroll-on-send" })
    ], LoquixMessageList.prototype, "scrollOnSend");
    __decorateClass2([
      e5(".scroll-container")
    ], LoquixMessageList.prototype, "_scrollContainer");
    __decorateClass2([
      r5()
    ], LoquixMessageList.prototype, "_hasMessages");
    __decorateClass2([
      r5()
    ], LoquixMessageList.prototype, "_showAnchor");
  }
});

// node_modules/@loquix/core/dist/components/core/define-message-list.js
var init_define_message_list = __esm({
  "node_modules/@loquix/core/dist/components/core/define-message-list.js"() {
    init_loquix_message_list();
    if (!customElements.get("loquix-message-list")) {
      customElements.define("loquix-message-list", LoquixMessageList);
    }
  }
});

// node_modules/@loquix/core/dist/components/core/loquix-message-avatar.js
var styles3, __defProp3, __decorateClass3, _LoquixMessageAvatar, LoquixMessageAvatar;
var init_loquix_message_avatar = __esm({
  "node_modules/@loquix/core/dist/components/core/loquix-message-avatar.js"() {
    init_lit();
    init_decorators();
    init_index_VPyVRNKW();
    styles3 = i`
  :host {
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }

  .container {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 50%;
    overflow: hidden;
    background: var(--loquix-avatar-bg, var(--loquix-ai-color-subtle, #ede9fe));
    color: var(--loquix-avatar-color, var(--loquix-ai-color, #7c3aed));
    transition: box-shadow var(--loquix-transition-duration, 200ms)
      var(--loquix-transition-easing, cubic-bezier(0.4, 0, 0.2, 1));
  }

  /* Size variants */
  :host([size='sm']) .container {
    width: var(--loquix-avatar-size-sm, 24px);
    height: var(--loquix-avatar-size-sm, 24px);
    font-size: 0.625rem;
  }

  :host([size='md']) .container,
  .container {
    width: var(--loquix-avatar-size-md, 32px);
    height: var(--loquix-avatar-size-md, 32px);
    font-size: 0.75rem;
  }

  :host([size='lg']) .container {
    width: var(--loquix-avatar-size-lg, 40px);
    height: var(--loquix-avatar-size-lg, 40px);
    font-size: 0.875rem;
  }

  /* Pulsing animation for thinking / streaming */
  :host([state='thinking']) .container,
  :host([state='streaming']) .container {
    animation: loquix-pulse 1.5s ease-in-out infinite;
  }

  @keyframes loquix-pulse {
    0%,
    100% {
      box-shadow: 0 0 0 0 var(--loquix-avatar-pulse-color, rgba(124, 58, 237, 0.4));
    }
    50% {
      box-shadow: 0 0 0 6px var(--loquix-avatar-pulse-color, rgba(124, 58, 237, 0));
    }
  }

  .image {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }

  .initials {
    font-family: var(--loquix-font-family, sans-serif);
    font-weight: 600;
    text-transform: uppercase;
    user-select: none;
    line-height: 1;
  }

  .icon {
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .icon svg {
    width: 60%;
    height: 60%;
    fill: currentColor;
  }
`;
    __defProp3 = Object.defineProperty;
    __decorateClass3 = (decorators, target, key, kind) => {
      var result = void 0;
      for (var i5 = decorators.length - 1, decorator; i5 >= 0; i5--)
        if (decorator = decorators[i5])
          result = decorator(target, key, result) || result;
      if (result) __defProp3(target, key, result);
      return result;
    };
    _LoquixMessageAvatar = class _LoquixMessageAvatar2 extends i4 {
      constructor() {
        super(...arguments);
        this._localize = new LocalizeController(this);
        this.state = "idle";
        this.size = "md";
        this.variant = "icon";
      }
      // ---------------------------------------------------------------------------
      // Helpers
      // ---------------------------------------------------------------------------
      _getInitials() {
        if (!this.name) return "";
        const parts = this.name.trim().split(/\s+/);
        if (parts.length === 1) return parts[0].charAt(0);
        return parts[0].charAt(0) + parts[parts.length - 1].charAt(0);
      }
      // ---------------------------------------------------------------------------
      // Render
      // ---------------------------------------------------------------------------
      _renderContent() {
        if (this.src) {
          return b2`<img
        part="image"
        class="image"
        src="${this.src}"
        alt="${this.name ?? this._localize.term("messageAvatar.altFallback")}"
      />`;
        }
        if (this.name) {
          return b2`<span part="initials" class="initials">${this._getInitials()}</span>`;
        }
        return b2`
      <span class="icon">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path
            d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48
               10-10S17.52 2 12 2zm0 3c1.66 0 3 1.34 3 3s-1.34
               3-3 3-3-1.34-3-3 1.34-3 3-3zm0 14.2a7.2 7.2 0 0
               1-6-3.22c.03-1.99 4-3.08 6-3.08 1.99 0 5.97 1.09
               6 3.08a7.2 7.2 0 0 1-6 3.22z"
          />
        </svg>
      </span>
    `;
      }
      render() {
        return b2`
      <div
        part="container"
        class="container"
        role="img"
        aria-label="${this.name ? this._localize.term("messageAvatar.namedAlt", { name: this.name }) : this._localize.term("messageAvatar.aiAlt")}"
      >
        ${this._renderContent()}
      </div>
    `;
      }
    };
    _LoquixMessageAvatar.styles = [styles3];
    LoquixMessageAvatar = _LoquixMessageAvatar;
    __decorateClass3([
      n4({ type: String, reflect: true })
    ], LoquixMessageAvatar.prototype, "src");
    __decorateClass3([
      n4({ type: String, reflect: true })
    ], LoquixMessageAvatar.prototype, "name");
    __decorateClass3([
      n4({ type: String, reflect: true })
    ], LoquixMessageAvatar.prototype, "state");
    __decorateClass3([
      n4({ type: String, reflect: true })
    ], LoquixMessageAvatar.prototype, "size");
    __decorateClass3([
      n4({ type: String, reflect: true })
    ], LoquixMessageAvatar.prototype, "variant");
  }
});

// node_modules/@loquix/core/dist/components/core/define-message-avatar.js
var init_define_message_avatar = __esm({
  "node_modules/@loquix/core/dist/components/core/define-message-avatar.js"() {
    init_loquix_message_avatar();
    if (!customElements.get("loquix-message-avatar")) {
      customElements.define("loquix-message-avatar", LoquixMessageAvatar);
    }
  }
});

// node_modules/@loquix/core/dist/components/core/loquix-message-content.js
var _a, styles4, __defProp4, __decorateClass4, _LoquixMessageContent, LoquixMessageContent;
var init_loquix_message_content = __esm({
  "node_modules/@loquix/core/dist/components/core/loquix-message-content.js"() {
    init_lit();
    init_decorators();
    styles4 = i`
  :host {
    display: block;
    font-size: var(--loquix-message-font-size, 0.9375rem);
    line-height: var(--loquix-message-line-height, 1.6);
    font-family: var(--loquix-font-family, sans-serif);
    color: var(--loquix-text-color, #111827);
    word-break: break-word;
  }

  /* ── Text content ── */

  .content {
    position: relative;
  }

  /* ── Code block ── */

  .code-block {
    font-family: var(--loquix-code-font-family, 'Fira Code', 'Cascadia Code', monospace);
    font-size: var(--loquix-code-font-size, 0.875em);
    line-height: var(--loquix-code-line-height, 1.5);
    background: var(--loquix-surface-secondary-bg, #f4f4f8);
    border-radius: var(--loquix-code-border-radius, 8px);
    padding: var(--loquix-code-padding, 12px 16px);
    overflow-x: auto;
    margin: 0;
    white-space: pre;
    tab-size: 2;
  }

  .code-block code {
    font-family: inherit;
    background: none;
    padding: 0;
    border-radius: 0;
  }

  /* ── Streaming: inline last slotted element only when cursor is visible ── */

  :host([streaming][streaming-cursor]:not([streaming-cursor='none'])) ::slotted(*:last-child) {
    display: inline;
  }

  /* ── Streaming cursor variants ── */

  .streaming-cursor {
    display: inline-block;
    margin-left: 2px;
    vertical-align: text-bottom;
  }

  /* Caret: thin blinking line (|) */
  .streaming-cursor.caret {
    width: 2px;
    height: 1lh;
    background: var(--loquix-cursor-color, currentColor);
    animation: loquix-blink 1s step-end infinite;
  }

  /* Block: solid pulsing rectangle — ChatGPT-style */
  .streaming-cursor.block {
    width: 0.5em;
    height: 1lh;
    background: var(--loquix-cursor-color, currentColor);
    border-radius: 1px;
    animation: loquix-pulse 1.2s ease-in-out infinite;
  }

  @keyframes loquix-blink {
    0%,
    100% {
      opacity: 1;
    }
    50% {
      opacity: 0;
    }
  }

  @keyframes loquix-pulse {
    0%,
    100% {
      opacity: 1;
    }
    50% {
      opacity: 0.3;
    }
  }

  /* ── Accessibility: reduce motion ── */
  @media (prefers-reduced-motion: reduce) {
    .streaming-cursor {
      animation: none;
      opacity: 0.7;
    }
  }
`;
    __defProp4 = Object.defineProperty;
    __decorateClass4 = (decorators, target, key, kind) => {
      var result = void 0;
      for (var i5 = decorators.length - 1, decorator; i5 >= 0; i5--)
        if (decorator = decorators[i5])
          result = decorator(target, key, result) || result;
      if (result) __defProp4(target, key, result);
      return result;
    };
    _LoquixMessageContent = (_a = class extends i4 {
      constructor() {
        super(...arguments);
        this.type = "text";
        this.streaming = false;
        this.streamingCursor = "none";
        this.allowInlineActions = false;
      }
      // ── Rendering ──────────────────────────────────────────────
      render() {
        return this.type === "code" ? this._renderCode() : this._renderText();
      }
      _renderText() {
        return b2` <div class="content" part="content"><slot></slot>${this._renderCursor()}</div> `;
      }
      _renderCode() {
        return b2`
      <pre class="code-block" part="code-block"><code>${this.code ?? ""}</code>${this._renderCursor()}</pre>
    `;
      }
      _renderCursor() {
        if (!this.streaming || !_a._validCursors.has(this.streamingCursor))
          return A;
        return b2`<span
      part="cursor"
      class="streaming-cursor ${this.streamingCursor}"
      aria-hidden="true"
    ></span>`;
      }
    }, _a.styles = styles4, _a._validCursors = /* @__PURE__ */ new Set(["caret", "block"]), _a);
    __decorateClass4([
      n4({ reflect: true })
    ], _LoquixMessageContent.prototype, "type");
    __decorateClass4([
      n4({ type: Boolean, reflect: true })
    ], _LoquixMessageContent.prototype, "streaming");
    __decorateClass4([
      n4({ attribute: "streaming-cursor", reflect: true })
    ], _LoquixMessageContent.prototype, "streamingCursor");
    __decorateClass4([
      n4({ type: Boolean, attribute: "allow-inline-actions", reflect: true })
    ], _LoquixMessageContent.prototype, "allowInlineActions");
    __decorateClass4([
      n4()
    ], _LoquixMessageContent.prototype, "code");
    LoquixMessageContent = _LoquixMessageContent;
  }
});

// node_modules/@loquix/core/dist/components/core/define-message-content.js
var init_define_message_content = __esm({
  "node_modules/@loquix/core/dist/components/core/define-message-content.js"() {
    init_loquix_message_content();
    if (!customElements.get("loquix-message-content")) {
      customElements.define("loquix-message-content", LoquixMessageContent);
    }
  }
});

// node_modules/@loquix/core/dist/chunks/loquix-action-button-fInoFxl3.js
var styles5, __defProp5, __decorateClass5, _LoquixActionButton, LoquixActionButton;
var init_loquix_action_button_fInoFxl3 = __esm({
  "node_modules/@loquix/core/dist/chunks/loquix-action-button-fInoFxl3.js"() {
    init_lit();
    init_decorators();
    init_index_PICxVGtN();
    styles5 = i`
  :host {
    display: inline-flex;
  }

  :host([disabled]) {
    pointer-events: none;
    opacity: 0.4;
  }

  .action {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: var(--loquix-action-size, 28px);
    height: var(--loquix-action-size, 28px);
    padding: 0;
    border: none;
    border-radius: var(--loquix-action-border-radius, 6px);
    background: var(--loquix-action-bg, transparent);
    color: var(--loquix-action-color, var(--loquix-text-secondary-color, #6b7280));
    cursor: pointer;
    transition:
      background var(--loquix-transition-duration, 200ms)
        var(--loquix-transition-easing, cubic-bezier(0.4, 0, 0.2, 1)),
      color var(--loquix-transition-duration, 200ms)
        var(--loquix-transition-easing, cubic-bezier(0.4, 0, 0.2, 1));
  }

  .action:hover {
    background: var(--loquix-action-hover-bg, var(--loquix-surface-secondary-bg, #f9fafb));
    color: var(--loquix-action-hover-color, var(--loquix-text-color, #111827));
  }

  .action:focus-visible {
    outline: 2px solid var(--loquix-input-focus-color, #7c3aed);
    outline-offset: 1px;
  }

  .action svg,
  .action ::slotted(svg) {
    width: 16px;
    height: 16px;
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
`;
    __defProp5 = Object.defineProperty;
    __decorateClass5 = (decorators, target, key, kind) => {
      var result = void 0;
      for (var i5 = decorators.length - 1, decorator; i5 >= 0; i5--)
        if (decorator = decorators[i5])
          result = decorator(target, key, result) || result;
      if (result) __defProp5(target, key, result);
      return result;
    };
    _LoquixActionButton = class _LoquixActionButton2 extends i4 {
      constructor() {
        super(...arguments);
        this.action = "";
        this.label = "";
        this.disabled = false;
      }
      _handleClick() {
        if (!this.action) return;
        this.dispatchEvent(createLoquixEvent(this.action, {}));
      }
      render() {
        return b2`
      <button
        part="button"
        class="action"
        aria-label=${this.label}
        ?disabled=${this.disabled}
        @click=${this._handleClick}
      >
        <slot></slot>
      </button>
    `;
      }
    };
    _LoquixActionButton.styles = [styles5];
    LoquixActionButton = _LoquixActionButton;
    __decorateClass5([
      n4()
    ], LoquixActionButton.prototype, "action");
    __decorateClass5([
      n4()
    ], LoquixActionButton.prototype, "label");
    __decorateClass5([
      n4({ type: Boolean, reflect: true })
    ], LoquixActionButton.prototype, "disabled");
  }
});

// node_modules/@loquix/core/dist/components/core/define-action-button.js
var init_define_action_button = __esm({
  "node_modules/@loquix/core/dist/components/core/define-action-button.js"() {
    init_loquix_action_button_fInoFxl3();
    if (!customElements.get("loquix-action-button")) {
      customElements.define("loquix-action-button", LoquixActionButton);
    }
  }
});

// node_modules/@loquix/core/dist/components/core/loquix-action-copy.js
var styles6, __defProp6, __decorateClass6, _LoquixActionCopy, LoquixActionCopy;
var init_loquix_action_copy = __esm({
  "node_modules/@loquix/core/dist/components/core/loquix-action-copy.js"() {
    init_lit();
    init_decorators();
    init_loquix_action_button_fInoFxl3();
    init_index_VPyVRNKW();
    styles6 = i`
  :host([copied]) .action {
    color: var(--loquix-action-copied-color, #22c55e);
  }

  :host([copied]) .action:hover {
    color: var(--loquix-action-copied-color, #22c55e);
  }
`;
    __defProp6 = Object.defineProperty;
    __decorateClass6 = (decorators, target, key, kind) => {
      var result = void 0;
      for (var i5 = decorators.length - 1, decorator; i5 >= 0; i5--)
        if (decorator = decorators[i5])
          result = decorator(target, key, result) || result;
      if (result) __defProp6(target, key, result);
      return result;
    };
    _LoquixActionCopy = class _LoquixActionCopy2 extends LoquixActionButton {
      constructor() {
        super();
        this._localize = new LocalizeController(this);
        this.copied = false;
        this.action = "loquix-copy";
      }
      disconnectedCallback() {
        super.disconnectedCallback();
        clearTimeout(this._copyTimer);
      }
      _handleClick() {
        super._handleClick();
        this.copied = true;
        clearTimeout(this._copyTimer);
        this._copyTimer = setTimeout(() => {
          this.copied = false;
        }, 2e3);
      }
      render() {
        return b2`
      <button
        part="button"
        class="action"
        aria-label=${this.copied ? this._localize.term("actionCopy.copied") : this.label || this._localize.term("actionCopy.label")}
        ?disabled=${this.disabled}
        @click=${this._handleClick}
      >
        ${this.copied ? b2`
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
            ` : b2`
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
              </svg>
            `}
      </button>
    `;
      }
    };
    _LoquixActionCopy.styles = [styles5, styles6];
    LoquixActionCopy = _LoquixActionCopy;
    __decorateClass6([
      n4({ type: Boolean, reflect: true })
    ], LoquixActionCopy.prototype, "copied");
  }
});

// node_modules/@loquix/core/dist/components/core/define-action-copy.js
var init_define_action_copy = __esm({
  "node_modules/@loquix/core/dist/components/core/define-action-copy.js"() {
    init_loquix_action_copy();
    if (!customElements.get("loquix-action-copy")) {
      customElements.define("loquix-action-copy", LoquixActionCopy);
    }
  }
});

// node_modules/@loquix/core/dist/components/core/loquix-action-feedback.js
var styles7, __defProp7, __decorateClass7, _LoquixActionFeedback, LoquixActionFeedback;
var init_loquix_action_feedback = __esm({
  "node_modules/@loquix/core/dist/components/core/loquix-action-feedback.js"() {
    init_lit();
    init_decorators();
    init_index_PICxVGtN();
    init_index_VPyVRNKW();
    init_loquix_action_button_fInoFxl3();
    styles7 = i`
  :host([active]) .action {
    color: var(--loquix-action-active-color, var(--loquix-ai-color, #7c3aed));
  }

  :host([active]) .action:hover {
    color: var(--loquix-action-active-color, var(--loquix-ai-color, #7c3aed));
  }
`;
    __defProp7 = Object.defineProperty;
    __decorateClass7 = (decorators, target, key, kind) => {
      var result = void 0;
      for (var i5 = decorators.length - 1, decorator; i5 >= 0; i5--)
        if (decorator = decorators[i5])
          result = decorator(target, key, result) || result;
      if (result) __defProp7(target, key, result);
      return result;
    };
    _LoquixActionFeedback = class _LoquixActionFeedback2 extends LoquixActionButton {
      constructor() {
        super();
        this._localize = new LocalizeController(this);
        this.sentiment = "positive";
        this.active = false;
        this.action = "loquix-feedback";
      }
      _handleClick() {
        var _a2;
        this.active = !this.active;
        if (this.active) {
          const parent = this.parentElement ?? this.getRootNode();
          const siblings = (_a2 = parent.querySelectorAll) == null ? void 0 : _a2.call(
            parent,
            "loquix-action-feedback"
          );
          siblings == null ? void 0 : siblings.forEach((el) => {
            if (el !== this && el.sentiment !== this.sentiment) {
              el.active = false;
            }
          });
        }
        this.dispatchEvent(createLoquixEvent("loquix-feedback", { sentiment: this.sentiment }));
      }
      render() {
        const label = this.sentiment === "positive" ? this._localize.term("actionFeedback.positiveLabel") : this._localize.term("actionFeedback.negativeLabel");
        return b2`
      <button
        part="button"
        class="action"
        aria-label=${label}
        aria-pressed=${this.active}
        ?disabled=${this.disabled}
        @click=${this._handleClick}
      >
        ${this.sentiment === "positive" ? b2`
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path
                  d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"
                ></path>
              </svg>
            ` : b2`
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path
                  d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"
                ></path>
              </svg>
            `}
      </button>
    `;
      }
    };
    _LoquixActionFeedback.styles = [styles5, styles7];
    LoquixActionFeedback = _LoquixActionFeedback;
    __decorateClass7([
      n4({ reflect: true })
    ], LoquixActionFeedback.prototype, "sentiment");
    __decorateClass7([
      n4({ type: Boolean, reflect: true })
    ], LoquixActionFeedback.prototype, "active");
  }
});

// node_modules/@loquix/core/dist/components/core/define-action-feedback.js
var init_define_action_feedback = __esm({
  "node_modules/@loquix/core/dist/components/core/define-action-feedback.js"() {
    init_loquix_action_feedback();
    if (!customElements.get("loquix-action-feedback")) {
      customElements.define("loquix-action-feedback", LoquixActionFeedback);
    }
  }
});

// node_modules/@loquix/core/dist/components/core/loquix-action-edit.js
var styles8, __defProp8, __decorateClass8, _LoquixActionEdit, LoquixActionEdit;
var init_loquix_action_edit = __esm({
  "node_modules/@loquix/core/dist/components/core/loquix-action-edit.js"() {
    init_lit();
    init_decorators();
    init_index_PICxVGtN();
    init_index_VPyVRNKW();
    styles8 = i`
  :host {
    display: inline-flex;
  }

  :host([disabled]) {
    pointer-events: none;
    opacity: 0.4;
  }

  :host([editing]) {
    display: block;
    width: 100%;
  }

  /* ── Trigger button ── */

  .trigger {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: var(--loquix-action-size, 28px);
    height: var(--loquix-action-size, 28px);
    padding: 0;
    border: none;
    border-radius: var(--loquix-action-border-radius, 6px);
    background: var(--loquix-action-bg, transparent);
    color: var(
      --loquix-edit-icon-color,
      var(--loquix-action-color, var(--loquix-text-secondary-color, #6b7280))
    );
    cursor: pointer;
    transition:
      background var(--loquix-transition-duration, 200ms)
        var(--loquix-transition-easing, cubic-bezier(0.4, 0, 0.2, 1)),
      color var(--loquix-transition-duration, 200ms)
        var(--loquix-transition-easing, cubic-bezier(0.4, 0, 0.2, 1));
  }

  .trigger:hover {
    background: var(--loquix-action-hover-bg, var(--loquix-surface-secondary-bg, #f9fafb));
    color: var(
      --loquix-edit-icon-color-hover,
      var(--loquix-action-hover-color, var(--loquix-text-color, #111827))
    );
  }

  .trigger:focus-visible {
    outline: 2px solid var(--loquix-input-focus-color, #7c3aed);
    outline-offset: 1px;
  }

  .trigger svg,
  .trigger ::slotted(svg) {
    width: var(--loquix-edit-icon-size, 16px);
    height: var(--loquix-edit-icon-size, 16px);
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
  }

  /* ── Inline editor ── */

  .editor {
    display: flex;
    flex-direction: column;
  }

  .textarea {
    display: block;
    width: 100%;
    min-height: var(--loquix-edit-textarea-min-height, 60px);
    max-height: var(--loquix-edit-textarea-max-height, 300px);
    padding: var(--loquix-edit-padding, 8px 10px);
    border: 1px solid var(--loquix-edit-border-color, var(--loquix-border-color, #d1d5db));
    border-radius: var(--loquix-edit-border-radius, 8px);
    background: var(--loquix-edit-textarea-bg, var(--loquix-surface-color, #fff));
    color: var(--loquix-text-color, #1e293b);
    font-family: inherit;
    font-size: inherit;
    line-height: 1.5;
    resize: none;
    overflow-y: auto;
    box-sizing: border-box;
    transition: border-color var(--loquix-transition-duration, 200ms)
      var(--loquix-transition-easing, cubic-bezier(0.4, 0, 0.2, 1));
  }

  .textarea:focus {
    outline: none;
    border-color: var(
      --loquix-edit-focus-color,
      var(--loquix-input-focus-color, var(--loquix-ai-color, #7c3aed))
    );
  }

  .textarea::placeholder {
    color: var(--loquix-text-muted-color, #94a3b8);
  }

  ::slotted([slot='editor-footer']) {
    margin-top: 6px;
    font-size: 0.75rem;
    color: var(--loquix-text-muted-color, #94a3b8);
  }

  .editor-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: var(--loquix-edit-button-gap, 8px);
    margin-top: 8px;
  }

  .cancel-btn {
    padding: 6px 14px;
    border: 1px solid var(--loquix-border-color, #e2e8f0);
    border-radius: 6px;
    background: var(--loquix-surface-color, #fff);
    color: var(--loquix-text-color, #374151);
    font-family: inherit;
    font-size: 0.8125rem;
    font-weight: 500;
    cursor: pointer;
    transition:
      background var(--loquix-transition-duration, 200ms)
        var(--loquix-transition-easing, cubic-bezier(0.4, 0, 0.2, 1)),
      border-color var(--loquix-transition-duration, 200ms)
        var(--loquix-transition-easing, cubic-bezier(0.4, 0, 0.2, 1));
  }

  .cancel-btn:hover {
    background: var(--loquix-surface-secondary-bg, #f3f4f6);
    border-color: var(--loquix-border-color-hover, #d1d5db);
  }

  .submit-btn {
    padding: 6px 16px;
    border: none;
    border-radius: 6px;
    background: var(--loquix-ai-color, #7c3aed);
    color: #fff;
    font-family: inherit;
    font-size: 0.8125rem;
    font-weight: 500;
    cursor: pointer;
    transition:
      background var(--loquix-transition-duration, 200ms)
        var(--loquix-transition-easing, cubic-bezier(0.4, 0, 0.2, 1)),
      opacity var(--loquix-transition-duration, 200ms)
        var(--loquix-transition-easing, cubic-bezier(0.4, 0, 0.2, 1));
  }

  .submit-btn:hover:not(:disabled) {
    background: var(--loquix-ai-color-hover, #6d28d9);
  }

  .submit-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  /* ── Composer editing badge ── */

  .editing-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: 9999px;
    background: var(--loquix-edit-highlight-color, var(--loquix-ai-color, #7c3aed));
    color: #fff;
    font-size: 0.75rem;
    font-weight: 500;
  }

  .editing-badge svg {
    width: 12px;
    height: 12px;
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
`;
    __defProp8 = Object.defineProperty;
    __decorateClass8 = (decorators, target, key, kind) => {
      var result = void 0;
      for (var i5 = decorators.length - 1, decorator; i5 >= 0; i5--)
        if (decorator = decorators[i5])
          result = decorator(target, key, result) || result;
      if (result) __defProp8(target, key, result);
      return result;
    };
    _LoquixActionEdit = class _LoquixActionEdit2 extends i4 {
      constructor() {
        super(...arguments);
        this._localize = new LocalizeController(this);
        this.mode = "inline";
        this.messageId = "";
        this.content = "";
        this.editing = false;
        this.disabled = false;
        this.placeholder = "";
        this._editText = "";
        this._originalContent = "";
      }
      // ---------------------------------------------------------------------------
      // Public API
      // ---------------------------------------------------------------------------
      /** Programmatically enter editing mode. */
      startEditing() {
        if (this.editing) return;
        this._originalContent = this.content;
        this._editText = this.content;
        this.editing = true;
        this.dispatchEvent(
          createLoquixEvent("loquix-edit-start", {
            messageId: this.messageId,
            content: this.content
          })
        );
        if (this.mode === "inline") {
          this.updateComplete.then(() => this._focusTextarea());
        }
      }
      /** Programmatically cancel editing. */
      cancelEditing() {
        if (!this.editing) return;
        this.editing = false;
        this._editText = "";
        this.dispatchEvent(
          createLoquixEvent("loquix-edit-cancel", {
            messageId: this.messageId
          })
        );
      }
      /** Programmatically complete editing with new content (used by composer mode). */
      completeEdit(newContent) {
        const oldContent = this._originalContent;
        this.editing = false;
        this._editText = "";
        this.dispatchEvent(
          createLoquixEvent("loquix-edit-submit", {
            messageId: this.messageId,
            oldContent,
            newContent
          })
        );
      }
      // ---------------------------------------------------------------------------
      // Private handlers
      // ---------------------------------------------------------------------------
      _handleTriggerClick() {
        this.startEditing();
      }
      _handleSubmit() {
        if (!this._canSubmit) return;
        this.completeEdit(this._editText);
      }
      _handleCancel() {
        this.cancelEditing();
      }
      _handleInput(e6) {
        const textarea = e6.target;
        this._editText = textarea.value;
        this._autoResize(textarea);
      }
      _handleKeydown(e6) {
        if (e6.key === "Escape") {
          e6.preventDefault();
          this._handleCancel();
          return;
        }
        if (e6.key === "Enter" && (e6.metaKey || e6.ctrlKey)) {
          e6.preventDefault();
          this._handleSubmit();
        }
      }
      _autoResize(textarea) {
        textarea.style.height = "auto";
        textarea.style.height = `${textarea.scrollHeight}px`;
      }
      _focusTextarea() {
        const el = this._textareaEl;
        if (!el) return;
        el.focus();
        el.selectionStart = el.selectionEnd = el.value.length;
        this._autoResize(el);
      }
      get _canSubmit() {
        const text = this._editText.trim();
        return text.length > 0 && text !== this._originalContent.trim();
      }
      // ---------------------------------------------------------------------------
      // Lifecycle
      // ---------------------------------------------------------------------------
      updated(changed) {
        if (changed.has("editing") && this.editing && !this._originalContent) {
          this._originalContent = this.content;
          this._editText = this.content;
          if (this.mode === "inline") {
            this.updateComplete.then(() => this._focusTextarea());
          }
        }
        if (changed.has("editing") && !this.editing) {
          this._originalContent = "";
        }
      }
      // ---------------------------------------------------------------------------
      // Render
      // ---------------------------------------------------------------------------
      _renderTrigger() {
        return b2`
      <button
        part="trigger"
        class="trigger"
        aria-label=${this._localize.term("actionEdit.triggerLabel")}
        ?disabled=${this.disabled}
        @click=${this._handleTriggerClick}
      >
        <slot name="icon">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"></path>
            <path d="m15 5 4 4"></path>
          </svg>
        </slot>
      </button>
    `;
      }
      _renderInlineEditor() {
        return b2`
      <div class="editor">
        <textarea
          part="textarea"
          class="textarea"
          aria-label=${this._localize.term("actionEdit.textareaLabel")}
          .value=${this._editText}
          placeholder=${this.placeholder}
          @input=${this._handleInput}
          @keydown=${this._handleKeydown}
        ></textarea>
        <slot name="editor-footer"></slot>
        <div part="actions" class="editor-actions">
          <button part="cancel-button" class="cancel-btn" @click=${this._handleCancel}>
            ${this.cancelLabel ?? this._localize.term("actionEdit.cancelLabel")}
          </button>
          <button
            part="submit-button"
            class="submit-btn"
            ?disabled=${!this._canSubmit}
            @click=${this._handleSubmit}
          >
            ${this.submitLabel ?? this._localize.term("actionEdit.submitLabel")}
          </button>
        </div>
      </div>
    `;
      }
      _renderComposerBadge() {
        return b2`
      <slot name="editing-indicator">
        <span part="editing-badge" class="editing-badge">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"></path>
            <path d="m15 5 4 4"></path>
          </svg>
          ${this._localize.term("actionEdit.editingBadge")}
        </span>
      </slot>
    `;
      }
      render() {
        if (!this.editing) {
          return this._renderTrigger();
        }
        if (this.mode === "inline") {
          return this._renderInlineEditor();
        }
        return this._renderComposerBadge();
      }
    };
    _LoquixActionEdit.styles = [styles8];
    LoquixActionEdit = _LoquixActionEdit;
    __decorateClass8([
      n4({ reflect: true })
    ], LoquixActionEdit.prototype, "mode");
    __decorateClass8([
      n4({ attribute: "message-id" })
    ], LoquixActionEdit.prototype, "messageId");
    __decorateClass8([
      n4()
    ], LoquixActionEdit.prototype, "content");
    __decorateClass8([
      n4({ type: Boolean, reflect: true })
    ], LoquixActionEdit.prototype, "editing");
    __decorateClass8([
      n4({ type: Boolean, reflect: true })
    ], LoquixActionEdit.prototype, "disabled");
    __decorateClass8([
      n4({ attribute: "submit-label" })
    ], LoquixActionEdit.prototype, "submitLabel");
    __decorateClass8([
      n4({ attribute: "cancel-label" })
    ], LoquixActionEdit.prototype, "cancelLabel");
    __decorateClass8([
      n4()
    ], LoquixActionEdit.prototype, "placeholder");
    __decorateClass8([
      r5()
    ], LoquixActionEdit.prototype, "_editText");
    __decorateClass8([
      e5(".textarea")
    ], LoquixActionEdit.prototype, "_textareaEl");
  }
});

// node_modules/@loquix/core/dist/components/core/define-action-edit.js
var init_define_action_edit = __esm({
  "node_modules/@loquix/core/dist/components/core/define-action-edit.js"() {
    init_loquix_action_edit();
    if (!customElements.get("loquix-action-edit")) {
      customElements.define("loquix-action-edit", LoquixActionEdit);
    }
  }
});

// node_modules/@loquix/core/dist/components/core/loquix-message-actions.js
var styles9, __defProp9, __decorateClass9, _LoquixMessageActions, LoquixMessageActions;
var init_loquix_message_actions = __esm({
  "node_modules/@loquix/core/dist/components/core/loquix-message-actions.js"() {
    init_lit();
    init_decorators();
    init_index_VPyVRNKW();
    init_define_action_button();
    init_define_action_copy();
    init_define_action_feedback();
    init_define_action_edit();
    styles9 = i`
  :host {
    display: block;
  }

  /* Hover position: hidden by default, visible on host hover */
  :host([position='hover']) .toolbar {
    opacity: 0;
    pointer-events: none;
    transition: opacity var(--loquix-transition-duration, 200ms)
      var(--loquix-transition-easing, cubic-bezier(0.4, 0, 0.2, 1));
  }

  :host([position='hover']:hover) .toolbar,
  :host([position='hover']:focus-within) .toolbar {
    opacity: 1;
    pointer-events: auto;
  }

  /* Always visible */
  :host([position='always']) .toolbar {
    opacity: 1;
  }

  /* Show only after streaming completes (handled via host attribute externally) */
  :host([position='on-complete']) .toolbar {
    opacity: 1;
  }

  .toolbar {
    display: flex;
    align-items: center;
    gap: var(--loquix-actions-gap, 0.25rem);
    padding: var(--loquix-actions-padding, 0.125rem 0);
  }

  :host([direction='vertical']) .toolbar {
    flex-direction: column;
  }
`;
    __defProp9 = Object.defineProperty;
    __decorateClass9 = (decorators, target, key, kind) => {
      var result = void 0;
      for (var i5 = decorators.length - 1, decorator; i5 >= 0; i5--)
        if (decorator = decorators[i5])
          result = decorator(target, key, result) || result;
      if (result) __defProp9(target, key, result);
      return result;
    };
    _LoquixMessageActions = class _LoquixMessageActions2 extends i4 {
      constructor() {
        super(...arguments);
        this._localize = new LocalizeController(this);
        this.direction = "horizontal";
        this.position = "hover";
      }
      // ---------------------------------------------------------------------------
      // Default button sets
      // ---------------------------------------------------------------------------
      _renderUserDefaults() {
        return b2` <loquix-action-edit></loquix-action-edit> `;
      }
      _renderAssistantDefaults() {
        return b2`
      <loquix-action-copy></loquix-action-copy>
      <loquix-action-button
        action="loquix-regenerate"
        label=${this._localize.term("messageActions.regenerate")}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <polyline points="23 4 23 10 17 10"></polyline>
          <polyline points="1 20 1 14 7 14"></polyline>
          <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
        </svg>
      </loquix-action-button>
      <loquix-action-feedback sentiment="positive"></loquix-action-feedback>
      <loquix-action-feedback sentiment="negative"></loquix-action-feedback>
    `;
      }
      _renderSystemDefaults() {
        return b2`<loquix-action-copy></loquix-action-copy>`;
      }
      // ---------------------------------------------------------------------------
      // Render
      // ---------------------------------------------------------------------------
      render() {
        const isUser = this.messageRole === "user";
        const isAssistant = this.messageRole === "assistant";
        const defaults = isUser ? this._renderUserDefaults() : isAssistant ? this._renderAssistantDefaults() : this._renderSystemDefaults();
        return b2`
      <div
        part="toolbar"
        class="toolbar"
        role="toolbar"
        aria-label=${this._localize.term("messageActions.toolbarLabel")}
      >
        <slot>${defaults}</slot>
      </div>
    `;
      }
    };
    _LoquixMessageActions.styles = [styles9];
    LoquixMessageActions = _LoquixMessageActions;
    __decorateClass9([
      n4({ reflect: true })
    ], LoquixMessageActions.prototype, "direction");
    __decorateClass9([
      n4({ type: String, reflect: true })
    ], LoquixMessageActions.prototype, "position");
    __decorateClass9([
      n4({ type: String, attribute: "message-role", reflect: true })
    ], LoquixMessageActions.prototype, "messageRole");
  }
});

// node_modules/@loquix/core/dist/components/core/define-message-actions.js
var init_define_message_actions = __esm({
  "node_modules/@loquix/core/dist/components/core/define-message-actions.js"() {
    init_loquix_message_actions();
    if (!customElements.get("loquix-message-actions")) {
      customElements.define("loquix-message-actions", LoquixMessageActions);
    }
  }
});

// node_modules/@loquix/core/dist/components/core/loquix-typing-indicator.js
var styles10, __defProp10, __decorateClass10, _LoquixTypingIndicator, LoquixTypingIndicator;
var init_loquix_typing_indicator = __esm({
  "node_modules/@loquix/core/dist/components/core/loquix-typing-indicator.js"() {
    init_lit();
    init_decorators();
    init_index_VPyVRNKW();
    styles10 = i`
  :host {
    display: inline-flex;
  }

  .container {
    display: inline-flex;
    align-items: center;
    gap: var(--loquix-typing-gap, 0.5rem);
    padding: var(--loquix-typing-padding, 0.5rem 0.75rem);
    background: var(--loquix-typing-bg, var(--loquix-surface-secondary-bg, #f9fafb));
    border-radius: var(--loquix-typing-border-radius, var(--loquix-border-radius, 12px));
    font-family: var(--loquix-font-family, sans-serif);
    font-size: var(--loquix-typing-font-size, 0.8125rem);
    color: var(--loquix-typing-text-color, var(--loquix-text-secondary-color, #6b7280));
    line-height: 1.4;
  }

  /* --- Dots variant --- */
  .dots {
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }

  .dot {
    width: var(--loquix-typing-dot-size, 6px);
    height: var(--loquix-typing-dot-size, 6px);
    border-radius: 50%;
    background: var(--loquix-typing-dot-color, var(--loquix-ai-color, #7c3aed));
    animation: loquix-bounce 1.4s ease-in-out infinite both;
  }

  .dot:nth-child(1) {
    animation-delay: 0s;
  }

  .dot:nth-child(2) {
    animation-delay: 0.16s;
  }

  .dot:nth-child(3) {
    animation-delay: 0.32s;
  }

  @keyframes loquix-bounce {
    0%,
    80%,
    100% {
      transform: scale(0);
      opacity: 0.4;
    }
    40% {
      transform: scale(1);
      opacity: 1;
    }
  }

  /* --- Text / Steps variants --- */
  .text {
    color: inherit;
  }
`;
    __defProp10 = Object.defineProperty;
    __decorateClass10 = (decorators, target, key, kind) => {
      var result = void 0;
      for (var i5 = decorators.length - 1, decorator; i5 >= 0; i5--)
        if (decorator = decorators[i5])
          result = decorator(target, key, result) || result;
      if (result) __defProp10(target, key, result);
      return result;
    };
    _LoquixTypingIndicator = class _LoquixTypingIndicator2 extends i4 {
      constructor() {
        super(...arguments);
        this._localize = new LocalizeController(this);
        this.variant = "dots";
      }
      // ---------------------------------------------------------------------------
      // Render
      // ---------------------------------------------------------------------------
      _renderDots() {
        return b2`
      <span class="dots" aria-hidden="true">
        <span part="dot" class="dot"></span>
        <span part="dot" class="dot"></span>
        <span part="dot" class="dot"></span>
      </span>
    `;
      }
      render() {
        const showDots = this.variant === "dots";
        const displayMessage = this.variant === "text" ? this.message ?? this._localize.term("typingIndicator.thinkingMessage") : this.variant === "steps" ? this.message ?? this._localize.term("typingIndicator.workingMessage") : A;
        return b2`
      <div
        part="container"
        class="container"
        role="status"
        aria-label="${showDots ? this._localize.term("typingIndicator.ariaLabel") : ""}"
      >
        ${showDots ? this._renderDots() : A}
        ${displayMessage !== A ? b2`<span class="text">${displayMessage}</span>` : A}
      </div>
    `;
      }
    };
    _LoquixTypingIndicator.styles = [styles10];
    LoquixTypingIndicator = _LoquixTypingIndicator;
    __decorateClass10([
      n4({ type: String, reflect: true })
    ], LoquixTypingIndicator.prototype, "variant");
    __decorateClass10([
      n4({ type: String })
    ], LoquixTypingIndicator.prototype, "message");
  }
});

// node_modules/@loquix/core/dist/components/core/define-typing-indicator.js
var init_define_typing_indicator = __esm({
  "node_modules/@loquix/core/dist/components/core/define-typing-indicator.js"() {
    init_loquix_typing_indicator();
    if (!customElements.get("loquix-typing-indicator")) {
      customElements.define("loquix-typing-indicator", LoquixTypingIndicator);
    }
  }
});

// node_modules/@loquix/core/dist/components/core/loquix-message-item.js
var styles11, __defProp11, __decorateClass11, _LoquixMessageItem, LoquixMessageItem;
var init_loquix_message_item = __esm({
  "node_modules/@loquix/core/dist/components/core/loquix-message-item.js"() {
    init_lit();
    init_decorators();
    init_index_PICxVGtN();
    init_index_VPyVRNKW();
    init_define_message_avatar();
    init_define_message_content();
    init_define_message_actions();
    init_define_typing_indicator();
    init_define_action_edit();
    styles11 = i`
  :host {
    display: block;
    width: 100%;
  }

  .container {
    display: flex;
    align-items: flex-start;
    gap: var(--loquix-message-gap, 12px);
    max-width: var(--loquix-message-max-width, 100%);
    padding: 4px 0;
  }

  /* User messages: align to the right */
  :host([sender='user']) .container {
    flex-direction: row-reverse;
  }

  /* ── Avatar column ── */

  .avatar-column {
    display: flex;
    flex-direction: column;
    align-items: center;
    flex-shrink: 0;
  }

  /* ── Message area ── */

  .message-area {
    flex: 1;
    min-width: 0;
  }

  /* ── Bubble group: constrains bubble + bottom-actions to same width ── */

  .bubble-group {
    display: flex;
    flex-direction: column;
    width: fit-content;
    max-width: 80%;
  }

  /* Expand bubble-group when editing for comfortable text input */
  .bubble-group:has(.bubble--editing) {
    width: 100%;
  }

  /* ── Bubble column: wraps above-bubble slot, bubble, and below-bubble slot ── */

  .bubble-column {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }

  /* Spacing between slotted attachments and the bubble */
  .bubble-column > ::slotted([slot='above-bubble']) {
    margin-bottom: var(--loquix-message-attachments-spacing, 8px);
  }

  .bubble-column > ::slotted([slot='below-bubble']) {
    margin-top: var(--loquix-message-attachments-spacing, 8px);
  }

  .bubble-group:has(.bubble--editing) .bubble-column {
    width: 100%;
  }

  :host([sender='user']) .bubble-group {
    margin-left: auto;
  }

  .bubble {
    border-radius: var(--loquix-message-bubble-radius, 12px);
    padding: var(--loquix-message-bubble-padding, 10px 14px);
    background: var(--loquix-message-assistant-bg, transparent);
    color: var(--loquix-text-color, #1e293b);
    word-wrap: break-word;
    overflow-wrap: break-word;
  }

  :host([sender='user']) .bubble {
    background: var(--loquix-message-user-bg, #f8fafc);
  }

  /* Editing state — expand bubble to fill bubble-group width for comfortable editing */
  .bubble--editing {
    width: 100%;
    box-sizing: border-box;
  }

  /* Error state */
  :host([status='error']) .bubble {
    border: 1px solid var(--loquix-message-error-border-color, #ef4444);
  }

  .header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 4px;
    font-size: 0.75rem;
    color: var(--loquix-text-muted-color, #64748b);
  }

  .model {
    font-weight: 500;
  }

  .timestamp {
    font-size: 0.6875rem;
  }

  .content {
    position: relative;
    line-height: 1.5;
    font-size: var(--loquix-message-font-size, 0.875rem);
    overflow: hidden;
    transition: max-height 300ms var(--loquix-transition-easing, cubic-bezier(0.4, 0, 0.2, 1));
  }

  /* Remove top/bottom margin on standard HTML elements (which have
     user-agent margins) to avoid extra spacing alongside bubble padding.
     Uses :where() so custom elements (e.g. loquix-follow-up-suggestions)
     keep their own :host margins intact. */
  .content
    ::slotted(
      :where(p, div, ul, ol, dl, h1, h2, h3, h4, h5, h6, blockquote, pre, figure, hr, table)
    ) {
    margin-top: 0;
    margin-bottom: 0;
  }

  .content--collapsed {
    overflow: hidden;
  }

  .content-fade {
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    height: 48px;
    pointer-events: none;
    background: linear-gradient(to bottom, transparent, var(--loquix-message-user-bg, #f8fafc));
  }

  .show-more {
    position: relative;
    z-index: 1;
    display: inline-block;
    margin-top: 4px;
    padding: 0;
    border: none;
    background-color: var(--loquix-message-show-more-bg, var(--loquix-message-user-bg, #f8fafc));
    color: var(--loquix-message-show-more-color, var(--loquix-ai-color, #7c3aed));
    font-size: 0.8125rem;
    font-family: inherit;
    cursor: pointer;
  }

  .show-more:hover {
    text-decoration: underline;
  }

  /* ── Actions toolbar (outside bubble) ── */

  .actions {
    margin-top: 4px;
    opacity: 0;
    pointer-events: none;
    transition: opacity var(--loquix-transition-duration, 200ms)
      var(--loquix-transition-easing, cubic-bezier(0.4, 0, 0.2, 1));
  }

  :host(:hover) .actions,
  :host(:focus-within) .actions {
    opacity: 1;
    pointer-events: auto;
  }

  /* Hide actions when not complete */
  :host(:not([status='complete'])) .actions {
    display: none;
  }

  /* ── Bottom positions: actions inside bubble-group ── */

  :host([actions-position='bottom-start']) .bubble-group .actions,
  :host(:not([actions-position])) .bubble-group .actions {
    align-self: flex-start;
  }

  :host([actions-position='bottom-end']) .bubble-group .actions {
    align-self: flex-end;
  }

  /* ── Inline positions ── */

  /* Inline-opposite: bubble-group becomes a row so actions sit next to bubble */
  .bubble-group--inline {
    flex-direction: row;
    align-items: flex-start;
    gap: 4px;
  }

  .bubble-group--inline > .bubble-column {
    flex: 1;
    min-width: 0;
  }

  .bubble-group--inline .actions {
    margin-top: 0;
  }

  /* Avatar-side inline: actions stacked under avatar */
  .avatar-column .actions {
    margin-top: 8px;
  }

  /* Error bar */
  .error-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 8px;
    font-size: 0.75rem;
  }

  .error-message {
    color: var(--loquix-message-error-text-color, #dc2626);
  }

  .retry-button {
    border: 1px solid var(--loquix-message-error-border-color, #ef4444);
    background: transparent;
    color: var(--loquix-message-error-text-color, #dc2626);
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 0.75rem;
    cursor: pointer;
    transition: background var(--loquix-transition-duration, 200ms)
      var(--loquix-transition-easing, cubic-bezier(0.4, 0, 0.2, 1));
  }

  .retry-button:hover {
    background: var(--loquix-message-retry-hover-bg, rgba(239, 68, 68, 0.08));
  }
`;
    __defProp11 = Object.defineProperty;
    __decorateClass11 = (decorators, target, key, kind) => {
      var result = void 0;
      for (var i5 = decorators.length - 1, decorator; i5 >= 0; i5--)
        if (decorator = decorators[i5])
          result = decorator(target, key, result) || result;
      if (result) __defProp11(target, key, result);
      return result;
    };
    _LoquixMessageItem = class _LoquixMessageItem2 extends i4 {
      constructor() {
        super(...arguments);
        this._localize = new LocalizeController(this);
        this.sender = "assistant";
        this.status = "complete";
        this.privateMode = false;
        this.showAvatar = false;
        this.avatarSize = "sm";
        this.actionsPosition = "bottom-start";
        this.collapseHeight = 120;
        this._collapsed = true;
        this._overflows = false;
        this._editing = false;
        this._editContent = "";
        this._editSubmitLabel = "";
        this._editCancelLabel = "";
        this._editPlaceholder = "";
        this._resizeObserver = null;
        this._dispatching = false;
        this._onCopy = (e6) => {
          if (this._dispatching) return;
          e6.stopPropagation();
          const content = this._getMessageText();
          navigator.clipboard.writeText(content).catch(() => {
          });
          this._dispatching = true;
          this.dispatchEvent(
            createLoquixEvent("loquix-copy", {
              messageId: this.messageId ?? "",
              content
            })
          );
          this._dispatching = false;
        };
        this._onRegenerate = (e6) => {
          if (this._dispatching) return;
          e6.stopPropagation();
          this._dispatching = true;
          this.dispatchEvent(
            createLoquixEvent("loquix-regenerate", {
              messageId: this.messageId ?? ""
            })
          );
          this._dispatching = false;
        };
        this._onFeedback = (e6) => {
          if (this._dispatching) return;
          e6.stopPropagation();
          const detail = e6.detail;
          this._dispatching = true;
          this.dispatchEvent(
            createLoquixEvent("loquix-feedback", {
              messageId: this.messageId ?? "",
              sentiment: (detail == null ? void 0 : detail.sentiment) ?? "positive"
            })
          );
          this._dispatching = false;
        };
        this._onEditStart = (e6) => {
          var _a2, _b, _c;
          if (this._dispatching) return;
          e6.stopPropagation();
          const trigger = e6.target;
          const triggerContent = ((_a2 = trigger == null ? void 0 : trigger.tagName) == null ? void 0 : _a2.toLowerCase()) === "loquix-action-edit" ? trigger.content : "";
          const content = triggerContent || this._getMessageText();
          this._editContent = content;
          const triggerMode = ((_b = trigger == null ? void 0 : trigger.getAttribute) == null ? void 0 : _b.call(trigger, "mode")) ?? "inline";
          if (((_c = trigger == null ? void 0 : trigger.tagName) == null ? void 0 : _c.toLowerCase()) === "loquix-action-edit") {
            this._editSubmitLabel = trigger.getAttribute("submit-label") ?? "";
            this._editCancelLabel = trigger.getAttribute("cancel-label") ?? "";
            this._editPlaceholder = trigger.getAttribute("placeholder") ?? "";
          } else {
            this._editSubmitLabel = "";
            this._editCancelLabel = "";
            this._editPlaceholder = "";
          }
          if (triggerMode === "inline") {
            this._editing = true;
          }
          this._dispatching = true;
          this.dispatchEvent(
            createLoquixEvent("loquix-edit-start", {
              messageId: this.messageId ?? "",
              content
            })
          );
          this._dispatching = false;
        };
        this._onEditSubmit = (e6) => {
          if (this._dispatching) return;
          e6.stopPropagation();
          const detail = e6.detail;
          this._editing = false;
          this._editContent = "";
          this._resetEditTriggers();
          this._dispatching = true;
          this.dispatchEvent(
            createLoquixEvent("loquix-edit-submit", {
              messageId: this.messageId ?? "",
              oldContent: (detail == null ? void 0 : detail.oldContent) ?? "",
              newContent: (detail == null ? void 0 : detail.newContent) ?? ""
            })
          );
          this._dispatching = false;
        };
        this._onEditCancel = (e6) => {
          if (this._dispatching) return;
          e6.stopPropagation();
          this._editing = false;
          this._editContent = "";
          this._resetEditTriggers();
          this._dispatching = true;
          this.dispatchEvent(
            createLoquixEvent("loquix-edit-cancel", {
              messageId: this.messageId ?? ""
            })
          );
          this._dispatching = false;
        };
      }
      // ---------------------------------------------------------------------------
      // Lifecycle
      // ---------------------------------------------------------------------------
      connectedCallback() {
        super.connectedCallback();
        this.addEventListener("loquix-copy", this._onCopy);
        this.addEventListener("loquix-regenerate", this._onRegenerate);
        this.addEventListener("loquix-feedback", this._onFeedback);
        this.addEventListener("loquix-edit-start", this._onEditStart);
        this.addEventListener("loquix-edit-submit", this._onEditSubmit);
        this.addEventListener("loquix-edit-cancel", this._onEditCancel);
      }
      firstUpdated() {
        if (this.sender === "user" && this.collapseHeight > 0) {
          this._observeContent();
        }
      }
      updated(changed) {
        var _a2;
        if (changed.has("collapseHeight") && this.sender === "user") {
          if (this.collapseHeight > 0) {
            if (!this._resizeObserver) {
              this._observeContent();
            }
            this._checkOverflow();
          } else {
            (_a2 = this._resizeObserver) == null ? void 0 : _a2.disconnect();
            this._resizeObserver = null;
            this._overflows = false;
          }
        }
      }
      disconnectedCallback() {
        var _a2;
        super.disconnectedCallback();
        this.removeEventListener("loquix-copy", this._onCopy);
        this.removeEventListener("loquix-regenerate", this._onRegenerate);
        this.removeEventListener("loquix-feedback", this._onFeedback);
        this.removeEventListener("loquix-edit-start", this._onEditStart);
        this.removeEventListener("loquix-edit-submit", this._onEditSubmit);
        this.removeEventListener("loquix-edit-cancel", this._onEditCancel);
        (_a2 = this._resizeObserver) == null ? void 0 : _a2.disconnect();
        this._resizeObserver = null;
      }
      _observeContent() {
        const el = this._contentEl;
        if (!el) return;
        this._resizeObserver = new ResizeObserver(() => this._checkOverflow());
        this._resizeObserver.observe(el);
      }
      _checkOverflow() {
        const el = this._contentEl;
        if (!el) return;
        if (this.collapseHeight <= 0) {
          this._overflows = false;
          return;
        }
        this._overflows = el.scrollHeight > this.collapseHeight;
      }
      _handleToggleCollapse() {
        this._collapsed = !this._collapsed;
      }
      // ---------------------------------------------------------------------------
      // Render helpers
      // ---------------------------------------------------------------------------
      /** True only when the user message actually needs collapsing right now. */
      get _isCollapsed() {
        if (this._editing) return false;
        return this.sender === "user" && this.collapseHeight > 0 && this._overflows && this._collapsed;
      }
      _renderShowMore() {
        if (this.sender !== "user" || this.collapseHeight <= 0 || !this._overflows || this._editing)
          return A;
        return b2`
      <button
        class="show-more"
        aria-expanded=${!this._collapsed}
        @click=${this._handleToggleCollapse}
      >
        ${this._collapsed ? this._localize.term("messageItem.showMore") : this._localize.term("messageItem.showLess")}
      </button>
    `;
      }
      _renderHeader() {
        const showModel = this.model && !this.privateMode;
        const showTimestamp = !!this.timestamp;
        if (!showModel && !showTimestamp) return A;
        return b2`
      <div part="header" class="header">
        <slot name="header">
          ${showModel ? b2`<span class="model">${this.model}</span>` : A}
          ${showTimestamp ? b2`<time class="timestamp">${this.timestamp}</time>` : A}
        </slot>
      </div>
    `;
      }
      _renderContent() {
        if (this._editing && this.sender === "user") {
          return b2`
        <loquix-action-edit
          editing
          mode="inline"
          message-id=${this.messageId ?? ""}
          .content=${this._editContent}
          submit-label=${this._editSubmitLabel || this._localize.term("actionEdit.submitLabel")}
          cancel-label=${this._editCancelLabel || this._localize.term("actionEdit.cancelLabel")}
          placeholder=${this._editPlaceholder || ""}
        ></loquix-action-edit>
      `;
        }
        if (this.status === "streaming") {
          return b2`
        <slot>
          <loquix-typing-indicator variant="dots"></loquix-typing-indicator>
        </slot>
      `;
        }
        if (this.status === "pending") {
          return b2` <loquix-typing-indicator variant="dots"></loquix-typing-indicator> `;
        }
        return b2`<slot></slot>`;
      }
      // ---------------------------------------------------------------------------
      // Action event interception — enrich with messageId + content
      // ---------------------------------------------------------------------------
      /** Extract text content from the default (unnamed) slot, excluding attachment components. */
      _getMessageText() {
        var _a2;
        const slot = (_a2 = this.shadowRoot) == null ? void 0 : _a2.querySelector("slot:not([name])");
        if (!slot) return "";
        return slot.assignedElements().filter((el) => !el.matches("loquix-message-attachments")).map((el) => el.textContent ?? "").join("\n").trim();
      }
      /** Reset editing state on any slotted action-edit triggers. */
      _resetEditTriggers() {
        this.querySelectorAll("loquix-action-edit").forEach((el) => {
          el.editing = false;
        });
      }
      get _isInline() {
        return this.actionsPosition === "inline-start" || this.actionsPosition === "inline-end";
      }
      /** Whether inline actions go on the same side as the avatar. */
      get _inlineOnAvatarSide() {
        if (!this._isInline) return false;
        if (this.sender === "user") return this.actionsPosition === "inline-end";
        return this.actionsPosition === "inline-start";
      }
      _renderActions() {
        if (this.status !== "complete" || this._editing) return A;
        const direction = this._isInline ? "vertical" : "horizontal";
        return b2`
      <div part="actions" class="actions">
        <slot name="actions">
          <loquix-message-actions
            message-role=${this.sender}
            position="always"
            direction=${direction}
          ></loquix-message-actions>
        </slot>
      </div>
    `;
      }
      _renderErrorRetry() {
        if (this.status !== "error") return A;
        return b2`
      <div class="error-bar">
        <span class="error-message">${this._localize.term("messageItem.errorText")}</span>
        <button
          class="retry-button"
          aria-label=${this._localize.term("messageItem.retryLabel")}
          @click=${this._handleRetry}
        >
          ${this._localize.term("messageItem.retryLabel")}
        </button>
      </div>
    `;
      }
      _handleRetry() {
        this.dispatchEvent(
          new CustomEvent("loquix-regenerate", {
            bubbles: true,
            composed: true,
            detail: { messageId: this.messageId ?? "" }
          })
        );
      }
      // ---------------------------------------------------------------------------
      // Render
      // ---------------------------------------------------------------------------
      render() {
        const inlineAvatar = this._isInline && this._inlineOnAvatarSide;
        const inlineOpposite = this._isInline && !this._inlineOnAvatarSide;
        const isBottom = !this._isInline;
        const inlineBefore = inlineOpposite && this.actionsPosition === "inline-start";
        const inlineAfter = inlineOpposite && this.actionsPosition === "inline-end";
        return b2`
      <div part="container" class="container">
        ${this.showAvatar ? b2`
              <div class="avatar-column">
                <slot name="avatar">
                  ${this.sender === "user" ? b2`<loquix-message-avatar
                        name=${this._localize.term("messageItem.userAvatar")}
                        size=${this.avatarSize}
                      ></loquix-message-avatar>` : b2`<loquix-message-avatar
                        state=${this.status === "streaming" ? "streaming" : "idle"}
                        size=${this.avatarSize}
                      ></loquix-message-avatar>`}
                </slot>
                ${inlineAvatar ? this._renderActions() : A}
              </div>
            ` : A}

        <div class="message-area">
          <div class="bubble-group ${inlineOpposite ? "bubble-group--inline" : ""}">
            ${inlineBefore ? this._renderActions() : A}
            <div part="bubble-column" class="bubble-column">
              <slot name="above-bubble"></slot>
              <div part="bubble" class="bubble ${this._editing ? "bubble--editing" : ""}">
                ${this._renderHeader()}
                <div
                  class="content ${this._isCollapsed ? "content--collapsed" : ""}"
                  style=${this._isCollapsed ? `max-height: ${this.collapseHeight}px` : ""}
                >
                  ${this._renderContent()}
                  ${this._isCollapsed ? b2`<div class="content-fade"></div>` : A}
                </div>
                ${this._renderErrorRetry()}
              </div>
              ${this._renderShowMore()}
              <slot name="below-bubble"></slot>
            </div>
            ${inlineAfter ? this._renderActions() : A}
            ${isBottom ? this._renderActions() : A}
          </div>
        </div>

        <slot name="footer"></slot>
      </div>
    `;
      }
    };
    _LoquixMessageItem.styles = [styles11];
    LoquixMessageItem = _LoquixMessageItem;
    __decorateClass11([
      n4({ type: String, reflect: true })
    ], LoquixMessageItem.prototype, "sender");
    __decorateClass11([
      n4({ type: String, reflect: true })
    ], LoquixMessageItem.prototype, "status");
    __decorateClass11([
      n4({ type: String })
    ], LoquixMessageItem.prototype, "model");
    __decorateClass11([
      n4({ type: String })
    ], LoquixMessageItem.prototype, "timestamp");
    __decorateClass11([
      n4({ type: String, attribute: "message-id" })
    ], LoquixMessageItem.prototype, "messageId");
    __decorateClass11([
      n4({ type: Boolean, attribute: "private-mode", reflect: true })
    ], LoquixMessageItem.prototype, "privateMode");
    __decorateClass11([
      n4({ type: Boolean, attribute: "show-avatar", reflect: true })
    ], LoquixMessageItem.prototype, "showAvatar");
    __decorateClass11([
      n4({ type: String, attribute: "avatar-size" })
    ], LoquixMessageItem.prototype, "avatarSize");
    __decorateClass11([
      n4({ attribute: "actions-position", reflect: true })
    ], LoquixMessageItem.prototype, "actionsPosition");
    __decorateClass11([
      n4({ type: Number, attribute: "collapse-height" })
    ], LoquixMessageItem.prototype, "collapseHeight");
    __decorateClass11([
      r5()
    ], LoquixMessageItem.prototype, "_collapsed");
    __decorateClass11([
      r5()
    ], LoquixMessageItem.prototype, "_overflows");
    __decorateClass11([
      e5(".content")
    ], LoquixMessageItem.prototype, "_contentEl");
    __decorateClass11([
      r5()
    ], LoquixMessageItem.prototype, "_editing");
    __decorateClass11([
      r5()
    ], LoquixMessageItem.prototype, "_editContent");
  }
});

// node_modules/@loquix/core/dist/components/core/define-message-item.js
var init_define_message_item = __esm({
  "node_modules/@loquix/core/dist/components/core/define-message-item.js"() {
    init_loquix_message_item();
    if (!customElements.get("loquix-message-item")) {
      customElements.define("loquix-message-item", LoquixMessageItem);
    }
  }
});

// node_modules/@loquix/core/dist/controllers/keyboard.controller.js
var KeyboardController;
var init_keyboard_controller = __esm({
  "node_modules/@loquix/core/dist/controllers/keyboard.controller.js"() {
    KeyboardController = class {
      constructor(host) {
        this._bindings = [];
        this._target = null;
        this._onKeyDown = (event) => {
          if (event.isComposing || event.keyCode === 229) {
            return;
          }
          for (const binding of this._bindings) {
            if (this._matchesBinding(event, binding)) {
              binding.handler(event);
              return;
            }
          }
        };
        this.host = host;
        host.addController(this);
      }
      hostConnected() {
        this.attach(this.host);
      }
      hostDisconnected() {
        this.detach();
      }
      /** Attach keyboard listener to a specific element */
      attach(target) {
        this.detach();
        this._target = target;
        target.addEventListener("keydown", this._onKeyDown);
      }
      detach() {
        if (this._target) {
          this._target.removeEventListener("keydown", this._onKeyDown);
          this._target = null;
        }
      }
      /** Register a key binding */
      addBinding(binding) {
        this._bindings.push(binding);
      }
      /** Remove a key binding by key */
      removeBinding(key) {
        this._bindings = this._bindings.filter((b3) => b3.key !== key);
      }
      /** Clear all bindings */
      clearBindings() {
        this._bindings = [];
      }
      _matchesBinding(event, binding) {
        if (event.key !== binding.key) return false;
        if (binding.ctrl && !event.ctrlKey) return false;
        if (binding.shift && !event.shiftKey) return false;
        if (binding.alt && !event.altKey) return false;
        if (binding.meta && !event.metaKey) return false;
        if (!binding.ctrl && event.ctrlKey) return false;
        if (!binding.shift && event.shiftKey) return false;
        if (!binding.alt && event.altKey) return false;
        if (!binding.meta && event.metaKey) return false;
        return true;
      }
    };
  }
});

// node_modules/@loquix/core/dist/controllers/resize.controller.js
var ResizeController;
var init_resize_controller = __esm({
  "node_modules/@loquix/core/dist/controllers/resize.controller.js"() {
    ResizeController = class {
      constructor(host, options) {
        this._textarea = null;
        this._minRows = 1;
        this._maxRows = 10;
        this._onInput = () => {
          this.resize();
        };
        this.host = host;
        host.addController(this);
        if (options == null ? void 0 : options.minRows) this._minRows = options.minRows;
        if (options == null ? void 0 : options.maxRows) this._maxRows = options.maxRows;
      }
      hostConnected() {
      }
      hostDisconnected() {
        this.detach();
      }
      /** Attach to a textarea element */
      attach(textarea) {
        this.detach();
        this._textarea = textarea;
        textarea.addEventListener("input", this._onInput);
        this.resize();
      }
      detach() {
        if (this._textarea) {
          this._textarea.removeEventListener("input", this._onInput);
          this._textarea = null;
        }
      }
      /** Trigger a resize calculation */
      resize() {
        const textarea = this._textarea;
        if (!textarea) return;
        textarea.style.height = "auto";
        const computed = getComputedStyle(textarea);
        const lineHeight = parseFloat(computed.lineHeight) || parseFloat(computed.fontSize) * 1.2;
        const paddingTop = parseFloat(computed.paddingTop) || 0;
        const paddingBottom = parseFloat(computed.paddingBottom) || 0;
        const borderTop = parseFloat(computed.borderTopWidth) || 0;
        const borderBottom = parseFloat(computed.borderBottomWidth) || 0;
        const minHeight = lineHeight * this._minRows + paddingTop + paddingBottom + borderTop + borderBottom;
        const maxHeight = lineHeight * this._maxRows + paddingTop + paddingBottom + borderTop + borderBottom;
        const scrollHeight = textarea.scrollHeight;
        const newHeight = Math.min(Math.max(scrollHeight, minHeight), maxHeight);
        textarea.style.height = `${newHeight}px`;
        textarea.style.overflowY = scrollHeight > maxHeight ? "auto" : "hidden";
      }
    };
  }
});

// node_modules/@loquix/core/dist/components/core/loquix-prompt-input.js
var styles12, __defProp12, __decorateClass12, _LoquixPromptInput, LoquixPromptInput;
var init_loquix_prompt_input = __esm({
  "node_modules/@loquix/core/dist/components/core/loquix-prompt-input.js"() {
    init_lit();
    init_decorators();
    init_index_PICxVGtN();
    init_keyboard_controller();
    init_resize_controller();
    init_index_VPyVRNKW();
    styles12 = i`
  :host {
    display: block;
    width: 100%;
  }

  :host([disabled]) {
    opacity: 0.5;
    pointer-events: none;
  }

  .container {
    display: flex;
    width: 100%;
  }

  .input {
    width: 100%;
    margin: 0;
    box-sizing: border-box;
    resize: none;
    border: 1px solid var(--loquix-input-border-color, var(--loquix-border-color, #e2e8f0));
    border-radius: var(--loquix-input-border-radius, 8px);
    padding: var(--loquix-input-padding, 10px 12px);
    font-family: var(--loquix-input-font-family, var(--loquix-font-family, sans-serif));
    font-size: var(--loquix-input-font-size, 0.875rem);
    line-height: 1.5;
    color: var(--loquix-input-color, var(--loquix-text-color, #1e293b));
    background: var(--loquix-input-bg, var(--loquix-surface-color, #ffffff));
    outline: none;
    transition:
      border-color var(--loquix-transition-duration, 200ms)
        var(--loquix-transition-easing, cubic-bezier(0.4, 0, 0.2, 1)),
      box-shadow var(--loquix-transition-duration, 200ms)
        var(--loquix-transition-easing, cubic-bezier(0.4, 0, 0.2, 1));
  }

  .input::placeholder {
    color: var(--loquix-input-placeholder-color, #94a3b8);
  }

  .input:focus {
    border-color: var(--loquix-input-focus-border-color, var(--loquix-ai-color, #7c3aed));
    box-shadow: 0 0 0 2px var(--loquix-input-focus-ring-color, rgba(124, 58, 237, 0.15));
  }

  .input:disabled {
    cursor: not-allowed;
    background: var(--loquix-input-disabled-bg, #f1f5f9);
  }

  /* Variant styles */
  :host([variant='inline']) .input {
    border: none;
    border-bottom: 1px solid var(--loquix-input-border-color, var(--loquix-border-color, #e2e8f0));
    border-radius: 0;
    padding: var(--loquix-input-padding, 6px 0);
  }

  :host([variant='inline']) .input:focus {
    box-shadow: none;
    border-bottom-color: var(--loquix-input-focus-border-color, var(--loquix-ai-color, #7c3aed));
  }

  :host([variant='command']) .input {
    font-family: var(--loquix-mono-font-family, 'SF Mono', 'Fira Code', monospace);
    border-radius: var(--loquix-input-border-radius, 4px);
    background: var(--loquix-input-command-bg, #1e293b);
    color: var(--loquix-input-command-color, #e2e8f0);
    padding: var(--loquix-input-padding, 12px);
  }

  :host([variant='panel']) .input {
    border-radius: 0;
    border: none;
    border-top: 1px solid var(--loquix-input-border-color, var(--loquix-border-color, #e2e8f0));
    padding: var(--loquix-input-padding, 12px 16px);
  }

  :host([variant='panel']) .input:focus {
    box-shadow: none;
  }
`;
    __defProp12 = Object.defineProperty;
    __decorateClass12 = (decorators, target, key, kind) => {
      var result = void 0;
      for (var i5 = decorators.length - 1, decorator; i5 >= 0; i5--)
        if (decorator = decorators[i5])
          result = decorator(target, key, result) || result;
      if (result) __defProp12(target, key, result);
      return result;
    };
    _LoquixPromptInput = class _LoquixPromptInput2 extends i4 {
      constructor() {
        super(...arguments);
        this._localize = new LocalizeController(this);
        this.variant = "chat";
        this.value = "";
        this.rows = 1;
        this.autoResize = true;
        this.submitOnEnter = true;
        this.disabled = false;
        this._keyboard = new KeyboardController(this);
        this._resize = new ResizeController(this);
      }
      // ---------------------------------------------------------------------------
      // Lifecycle
      // ---------------------------------------------------------------------------
      connectedCallback() {
        super.connectedCallback();
        if (this.hasUpdated && this._textarea) {
          this._attachControllers(this._textarea);
        }
      }
      firstUpdated() {
        const textarea = this._textarea;
        if (!textarea) return;
        this._attachControllers(textarea);
      }
      updated(changed) {
        if (changed.has("autoResize") && this._textarea) {
          if (this.autoResize) {
            this._resize.attach(this._textarea);
          } else {
            this._resize.detach();
          }
        }
      }
      _attachControllers(textarea) {
        this._keyboard.attach(textarea);
        this._keyboard.addBinding({
          key: "Enter",
          handler: (e6) => {
            if (!this.submitOnEnter) return;
            e6.preventDefault();
            this._handleSubmit();
          }
        });
        if (this.autoResize) {
          this._resize.attach(textarea);
        }
      }
      // ---------------------------------------------------------------------------
      // Event helpers
      // ---------------------------------------------------------------------------
      _handleInput(e6) {
        const textarea = e6.target;
        this.value = textarea.value;
        this.dispatchEvent(createLoquixEvent("loquix-change", { value: this.value }));
      }
      _handlePaste(e6) {
        var _a2, _b;
        const files = Array.from(((_a2 = e6.clipboardData) == null ? void 0 : _a2.files) ?? []);
        if (files.length === 0 && ((_b = e6.clipboardData) == null ? void 0 : _b.items)) {
          for (const item of Array.from(e6.clipboardData.items)) {
            if (item.kind === "file") {
              const file = item.getAsFile();
              if (file) files.push(file);
            }
          }
        }
        if (files.length === 0) return;
        e6.preventDefault();
        this.dispatchEvent(createLoquixEvent("loquix-paste-files", { files }));
      }
      _handleSubmit() {
        const content = this.value.trim();
        if (!content) return;
        this.dispatchEvent(createLoquixEvent("loquix-submit", { content }));
        this.value = "";
        if (this._textarea) {
          this._textarea.value = "";
        }
        this._resize.resize();
      }
      // ---------------------------------------------------------------------------
      // Render
      // ---------------------------------------------------------------------------
      render() {
        return b2`
      <div part="container" class="container">
        <textarea
          part="input"
          class="input"
          aria-label=${this.placeholder ?? this._localize.term("promptInput.placeholder")}
          .value=${this.value}
          rows=${this.rows}
          placeholder=${this.placeholder ?? this._localize.term("promptInput.placeholder")}
          ?disabled=${this.disabled}
          @input=${this._handleInput}
          @paste=${this._handlePaste}
        ></textarea>
      </div>
    `;
      }
    };
    _LoquixPromptInput.styles = [styles12];
    LoquixPromptInput = _LoquixPromptInput;
    __decorateClass12([
      n4({ type: String, reflect: true })
    ], LoquixPromptInput.prototype, "variant");
    __decorateClass12([
      n4({ type: String })
    ], LoquixPromptInput.prototype, "value");
    __decorateClass12([
      n4({ type: Number })
    ], LoquixPromptInput.prototype, "rows");
    __decorateClass12([
      n4({ type: Boolean, attribute: "auto-resize" })
    ], LoquixPromptInput.prototype, "autoResize");
    __decorateClass12([
      n4({ type: Boolean, attribute: "submit-on-enter" })
    ], LoquixPromptInput.prototype, "submitOnEnter");
    __decorateClass12([
      n4({ type: String })
    ], LoquixPromptInput.prototype, "placeholder");
    __decorateClass12([
      n4({ type: Boolean, reflect: true })
    ], LoquixPromptInput.prototype, "disabled");
    __decorateClass12([
      e5("textarea")
    ], LoquixPromptInput.prototype, "_textarea");
  }
});

// node_modules/@loquix/core/dist/components/core/define-prompt-input.js
var init_define_prompt_input = __esm({
  "node_modules/@loquix/core/dist/components/core/define-prompt-input.js"() {
    init_loquix_prompt_input();
    if (!customElements.get("loquix-prompt-input")) {
      customElements.define("loquix-prompt-input", LoquixPromptInput);
    }
  }
});

// node_modules/@loquix/core/dist/components/core/loquix-drop-zone.js
var styles13, __defProp13, __decorateClass13, _LoquixDropZone, LoquixDropZone;
var init_loquix_drop_zone = __esm({
  "node_modules/@loquix/core/dist/components/core/loquix-drop-zone.js"() {
    init_lit();
    init_decorators();
    init_index_PICxVGtN();
    init_index_VPyVRNKW();
    styles13 = i`
  :host {
    display: block;
    position: relative;
  }

  /* === Content wrapper (holds slotted children) === */

  .content {
    transition:
      filter 0.2s ease-out,
      opacity 0.2s ease-out;
  }

  :host([drag-over]) .content {
    filter: blur(var(--loquix-drop-zone-content-blur, 1.5px));
    opacity: var(--loquix-drop-zone-content-opacity, 0.5);
  }

  /* === Overlay (always in DOM for CSS transition) === */

  .overlay {
    position: absolute;
    inset: var(--loquix-drop-zone-overlay-inset, 0);
    z-index: 10;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 8px;
    border-radius: var(--loquix-drop-zone-border-radius, 16px);
    border: 2px dashed var(--loquix-drop-zone-border-color, var(--loquix-ai-color, #7c3aed));
    background: var(--loquix-drop-zone-bg, rgba(124, 58, 237, 0.06));
    backdrop-filter: blur(var(--loquix-drop-zone-overlay-blur, 2px));
    color: var(--loquix-drop-zone-color, var(--loquix-ai-color, #7c3aed));
    pointer-events: none;
    opacity: 0;
    transition:
      opacity 0.2s ease-out,
      backdrop-filter 0.2s ease-out;
  }

  :host([drag-over]) .overlay {
    opacity: 1;
  }

  /* === Overlay icon === */

  .overlay__icon {
    width: var(--loquix-drop-zone-icon-size, 40px);
    height: var(--loquix-drop-zone-icon-size, 40px);
    opacity: 0.8;
  }

  .overlay__icon svg {
    width: 100%;
    height: 100%;
  }

  /* === Overlay label === */

  .overlay__label {
    font-family: var(--loquix-font-family, system-ui, -apple-system, sans-serif);
    font-size: 13px;
    font-weight: 500;
  }
`;
    __defProp13 = Object.defineProperty;
    __decorateClass13 = (decorators, target, key, kind) => {
      var result = void 0;
      for (var i5 = decorators.length - 1, decorator; i5 >= 0; i5--)
        if (decorator = decorators[i5])
          result = decorator(target, key, result) || result;
      if (result) __defProp13(target, key, result);
      return result;
    };
    _LoquixDropZone = class _LoquixDropZone2 extends i4 {
      constructor() {
        super(...arguments);
        this._localize = new LocalizeController(this);
        this.disabled = false;
        this.accept = "";
        this.multiple = true;
        this._dragCounter = 0;
        this._boundGlobalDragEnd = this._handleGlobalDragEnd.bind(this);
        this._plusSvg = w`<svg viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
    <circle cx="24" cy="24" r="20"/>
    <line x1="24" y1="14" x2="24" y2="34"/>
    <line x1="14" y1="24" x2="34" y2="24"/>
  </svg>`;
      }
      // === Lifecycle ===
      connectedCallback() {
        super.connectedCallback();
        document.addEventListener("dragend", this._boundGlobalDragEnd);
      }
      disconnectedCallback() {
        super.disconnectedCallback();
        document.removeEventListener("dragend", this._boundGlobalDragEnd);
      }
      willUpdate(changed) {
        if (changed.has("disabled") && this.disabled) {
          this._dragCounter = 0;
        }
      }
      updated(changed) {
        const isDragOver = this._dragCounter > 0 && !this.disabled;
        if (isDragOver) {
          this.setAttribute("drag-over", "");
        } else {
          this.removeAttribute("drag-over");
        }
        if (changed.has("overlayInset")) {
          if (this.overlayInset != null) {
            this.style.setProperty("--loquix-drop-zone-overlay-inset", this.overlayInset);
          } else {
            this.style.removeProperty("--loquix-drop-zone-overlay-inset");
          }
        }
      }
      // === Drag handlers ===
      /** Check if the drag contains files (not text/urls). */
      _hasFiles(e6) {
        var _a2;
        const types = (_a2 = e6.dataTransfer) == null ? void 0 : _a2.types;
        if (!types) return false;
        if (typeof types.contains === "function") {
          return types.contains("Files");
        }
        return Array.prototype.includes.call(types, "Files");
      }
      _handleDragEnter(e6) {
        e6.preventDefault();
        if (!this._hasFiles(e6)) return;
        if (this.disabled) return;
        const prev = this._dragCounter;
        this._dragCounter++;
        if (prev === 0) this.requestUpdate();
      }
      _handleDragOver(e6) {
        if (this._hasFiles(e6) || this._dragCounter > 0) {
          e6.preventDefault();
          if (!this.disabled && e6.dataTransfer) e6.dataTransfer.dropEffect = "copy";
        }
      }
      _handleDragLeave(e6) {
        e6.preventDefault();
        if (!this._hasFiles(e6)) return;
        if (this.disabled) return;
        const prev = this._dragCounter;
        this._dragCounter = Math.max(0, this._dragCounter - 1);
        if (prev === 1) this.requestUpdate();
      }
      _handleDrop(e6) {
        var _a2;
        if (this._hasFiles(e6) || this._dragCounter > 0) {
          e6.preventDefault();
        }
        const wasDragging = this._dragCounter > 0;
        this._dragCounter = 0;
        if (wasDragging) this.requestUpdate();
        if (this.disabled) return;
        const files = (_a2 = e6.dataTransfer) == null ? void 0 : _a2.files;
        if (!(files == null ? void 0 : files.length)) return;
        const filtered = this._filterFiles(files);
        if (filtered.length === 0) return;
        this.dispatchEvent(createLoquixEvent("loquix-drop", { files: filtered }));
      }
      /** [Fix #2] Global dragend resets stuck drag state (e.g., drag exits viewport). */
      _handleGlobalDragEnd() {
        if (this._dragCounter > 0) {
          this._dragCounter = 0;
          this.requestUpdate();
        }
      }
      // === Accept filter (matches loquix-attachment-panel._processFiles pattern) ===
      _filterFiles(fileList) {
        let candidates = Array.from(fileList);
        if (this.accept) {
          const tokens = this.accept.split(",").map((t3) => t3.trim().toLowerCase()).filter(Boolean);
          const isAllowAll = tokens.length === 0 || tokens.includes("*") || tokens.includes("*/*");
          if (tokens.length > 0 && !isAllowAll) {
            candidates = candidates.filter((file) => {
              const mimeType = (file.type || "").toLowerCase();
              const fileName = file.name.toLowerCase();
              const ext = fileName.includes(".") ? `.${fileName.split(".").pop()}` : "";
              return tokens.some((token) => {
                if (token.startsWith(".")) {
                  return ext === token;
                }
                if (token.endsWith("/*")) {
                  const prefix = token.slice(0, -1);
                  return mimeType.startsWith(prefix);
                }
                return mimeType === token;
              });
            });
          }
        }
        if (!this.multiple) {
          candidates = candidates.slice(0, 1);
        }
        return candidates;
      }
      render() {
        const isDragOver = this._dragCounter > 0 && !this.disabled;
        return b2`
      <div
        class="content"
        @dragenter=${this._handleDragEnter}
        @dragover=${this._handleDragOver}
        @dragleave=${this._handleDragLeave}
        @drop=${this._handleDrop}
      >
        <slot></slot>
      </div>
      <div
        part="overlay"
        class="overlay"
        aria-hidden=${isDragOver ? "false" : "true"}
        role="status"
      >
        <span part="icon" class="overlay__icon" aria-hidden="true">${this._plusSvg}</span>
        <span part="label" class="overlay__label"
          >${this.label ?? this._localize.term("dropZone.label")}</span
        >
      </div>
    `;
      }
    };
    _LoquixDropZone.styles = [styles13];
    LoquixDropZone = _LoquixDropZone;
    __decorateClass13([
      n4({ type: Boolean, reflect: true })
    ], LoquixDropZone.prototype, "disabled");
    __decorateClass13([
      n4({ type: String })
    ], LoquixDropZone.prototype, "label");
    __decorateClass13([
      n4({ type: String })
    ], LoquixDropZone.prototype, "accept");
    __decorateClass13([
      n4({ type: Boolean })
    ], LoquixDropZone.prototype, "multiple");
    __decorateClass13([
      n4({ type: String, attribute: "overlay-inset" })
    ], LoquixDropZone.prototype, "overlayInset");
  }
});

// node_modules/@loquix/core/dist/components/core/define-drop-zone.js
var init_define_drop_zone = __esm({
  "node_modules/@loquix/core/dist/components/core/define-drop-zone.js"() {
    init_loquix_drop_zone();
    if (!customElements.get("loquix-drop-zone")) {
      customElements.define("loquix-drop-zone", LoquixDropZone);
    }
  }
});

// node_modules/@loquix/core/dist/components/core/loquix-chat-composer.js
var styles14, __defProp14, __decorateClass14, _LoquixChatComposer, LoquixChatComposer;
var init_loquix_chat_composer = __esm({
  "node_modules/@loquix/core/dist/components/core/loquix-chat-composer.js"() {
    init_lit();
    init_decorators();
    init_index_PICxVGtN();
    init_index_VPyVRNKW();
    init_define_prompt_input();
    init_define_drop_zone();
    styles14 = i`
  :host {
    display: block;
    width: 100%;
  }

  :host([disabled]) {
    opacity: 0.6;
  }

  .composer {
    display: flex;
    flex-direction: column;
    gap: var(--loquix-composer-gap, 8px);
    padding: var(--loquix-composer-padding, 12px 16px);
    background: var(--loquix-composer-bg, var(--loquix-surface-color, #ffffff));
    border-top: 1px solid var(--loquix-composer-border-color, var(--loquix-border-color, #e2e8f0));
  }

  .toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  /* Hide empty toolbars to avoid phantom spacing.
     Note: only hide the toolbar itself when empty, NOT slotted children —
     components like attachment-panel render content in shadow DOM and
     appear :empty to CSS even when they have visible content. */
  .toolbar:empty {
    display: none;
  }

  .input-row {
    display: flex;
    align-items: flex-start;
    gap: 8px;
  }

  .input-row ::slotted(*),
  .input-row loquix-prompt-input {
    flex: 1;
    min-width: 0;
  }

  .send-button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    box-sizing: border-box;
    width: 43px;
    height: 43px;
    border: none;
    border-radius: 50%;
    cursor: pointer;
    background: var(--loquix-send-button-bg, var(--loquix-ai-color, #7c3aed));
    color: var(--loquix-send-button-color, #ffffff);
    transition:
      opacity var(--loquix-transition-duration, 200ms)
        var(--loquix-transition-easing, cubic-bezier(0.4, 0, 0.2, 1)),
      transform var(--loquix-transition-duration, 200ms)
        var(--loquix-transition-easing, cubic-bezier(0.4, 0, 0.2, 1)),
      background var(--loquix-transition-duration, 200ms)
        var(--loquix-transition-easing, cubic-bezier(0.4, 0, 0.2, 1));
  }

  .send-button:hover:not(:disabled) {
    opacity: 0.85;
    transform: scale(1.05);
  }

  .send-button:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .send-button--stop {
    background: var(--loquix-gen-stop-bg, var(--loquix-error-color, #dc2626));
  }

  .send-button svg {
    width: 18px;
    height: 18px;
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
  }

  .send-button--stop svg {
    fill: currentColor;
    stroke: none;
  }

  /* === Contained variant (Claude/ChatGPT style) === */

  .composer--contained {
    border-top: none;
    padding: var(--loquix-composer-padding, 12px 16px);
  }

  .composer--contained .container {
    display: flex;
    flex-direction: column;
    border: 1px solid
      var(--loquix-composer-container-border-color, var(--loquix-border-color, #e2e8f0));
    border-radius: var(--loquix-composer-container-border-radius, 16px);
    background: var(--loquix-composer-container-bg, var(--loquix-surface-color, #ffffff));
    overflow: hidden;
    transition:
      border-color 0.15s ease,
      box-shadow 0.15s ease;
  }

  .composer--contained .container:focus-within {
    border-color: var(
      --loquix-composer-container-focus-border-color,
      var(--loquix-ai-color, #7c3aed)
    );
    box-shadow: 0 0 0 2px var(--loquix-input-focus-ring-color, rgba(124, 58, 237, 0.1));
  }

  .composer--contained .container__input {
    padding: 0;
  }

  /* Strip prompt-input borders inside container */
  .composer--contained .container__input loquix-prompt-input {
    --loquix-input-border-color: transparent;
    --loquix-input-focus-border-color: transparent;
    --loquix-input-focus-ring-color: transparent;
    --loquix-input-bg: transparent;
    --loquix-input-border-radius: 0;
  }

  .composer--contained .container__input ::slotted(*) {
    --loquix-input-border-color: transparent;
    --loquix-input-focus-border-color: transparent;
    --loquix-input-focus-ring-color: transparent;
    --loquix-input-bg: transparent;
    --loquix-input-border-radius: 0;
  }

  .actions-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: var(--loquix-actions-bar-padding, 6px 8px);
    gap: 8px;
  }

  .actions-bar__left {
    display: flex;
    align-items: center;
    gap: 4px;
    flex: 1;
    min-width: 0;
  }

  .actions-bar__right {
    display: flex;
    align-items: center;
    gap: 4px;
    flex-shrink: 0;
  }

  /* Smaller send button in contained variant */
  .composer--contained .send-button {
    width: 32px;
    height: 32px;
  }

  .composer--contained .send-button svg {
    width: 16px;
    height: 16px;
  }
`;
    __defProp14 = Object.defineProperty;
    __decorateClass14 = (decorators, target, key, kind) => {
      var result = void 0;
      for (var i5 = decorators.length - 1, decorator; i5 >= 0; i5--)
        if (decorator = decorators[i5])
          result = decorator(target, key, result) || result;
      if (result) __defProp14(target, key, result);
      return result;
    };
    _LoquixChatComposer = class _LoquixChatComposer2 extends i4 {
      constructor() {
        super(...arguments);
        this._localize = new LocalizeController(this);
        this.variant = "contained";
        this.disabled = false;
        this.submitDisabled = false;
        this.streaming = false;
        this.maxLength = 0;
        this._hasContent = false;
        this._inputValue = "";
        this._boundHandlePasteFiles = this._handlePasteFiles.bind(this);
      }
      connectedCallback() {
        super.connectedCallback();
        this.addEventListener("loquix-paste-files", this._boundHandlePasteFiles);
      }
      disconnectedCallback() {
        super.disconnectedCallback();
        this.removeEventListener("loquix-paste-files", this._boundHandlePasteFiles);
      }
      _handlePasteFiles(e6) {
        const panels = this.querySelectorAll("loquix-attachment-panel");
        for (const panel of panels) {
          if ("addFiles" in panel && typeof panel.addFiles === "function") {
            panel.addFiles(e6.detail.files);
            e6.stopPropagation();
            return;
          }
        }
      }
      // ---------------------------------------------------------------------------
      // Drop-zone auto-wiring
      // ---------------------------------------------------------------------------
      _handleDrop(e6) {
        const panels = this.querySelectorAll("loquix-attachment-panel");
        for (const panel of panels) {
          if ("addFiles" in panel && typeof panel.addFiles === "function") {
            panel.addFiles(e6.detail.files);
            e6.stopPropagation();
            return;
          }
        }
      }
      // ---------------------------------------------------------------------------
      // Event handlers
      // ---------------------------------------------------------------------------
      _handleInputChange(e6) {
        const value = e6.detail.value;
        this._inputValue = value;
        this._hasContent = value.trim().length > 0;
      }
      _handleSubmitFromInput(e6) {
        if (this.submitDisabled) {
          e6.stopPropagation();
          return;
        }
        this._inputValue = "";
        this._hasContent = false;
      }
      _handleSendClick() {
        const content = this._inputValue.trim();
        if (!content || this.submitDisabled) return;
        this.dispatchEvent(createLoquixEvent("loquix-submit", { content }));
        this._inputValue = "";
        this._hasContent = false;
      }
      // ---------------------------------------------------------------------------
      // Render
      // ---------------------------------------------------------------------------
      _handleStopClick() {
        this.dispatchEvent(createLoquixEvent("loquix-stop", {}));
      }
      _renderSendButton() {
        return b2`
      <button
        part="send-button"
        class="send-button"
        aria-label=${this._localize.term("chatComposer.sendLabel")}
        ?disabled=${!this._hasContent || this.disabled || this.submitDisabled}
        @click=${this._handleSendClick}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <line x1="22" y1="2" x2="11" y2="13"></line>
          <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
        </svg>
      </button>
    `;
      }
      _renderStopButton() {
        return b2`
      <button
        part="send-button"
        class="send-button send-button--stop"
        aria-label=${this._localize.term("chatComposer.stopLabel")}
        @click=${this._handleStopClick}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <rect x="6" y="6" width="12" height="12" rx="2" ry="2"></rect>
        </svg>
      </button>
    `;
      }
      _renderDefaultLayout() {
        return b2`
      <loquix-drop-zone ?disabled=${this.disabled} @loquix-drop=${this._handleDrop}>
        <div part="composer" class="composer">
          <div part="toolbar" class="toolbar toolbar--top">
            <slot name="toolbar-top"></slot>
          </div>

          <div class="input-row">
            <slot name="input">
              <loquix-prompt-input
                .value=${this._inputValue}
                placeholder=${this.placeholder ?? this._localize.term("chatComposer.placeholder")}
                ?disabled=${this.disabled}
                auto-resize
                submit-on-enter
                @loquix-change=${this._handleInputChange}
                @loquix-submit=${this._handleSubmitFromInput}
              ></loquix-prompt-input>
            </slot>

            ${this.streaming ? this._renderStopButton() : this._renderSendButton()}
          </div>

          <slot name="suggestions"></slot>

          <div part="toolbar" class="toolbar toolbar--bottom">
            <slot name="toolbar-bottom"></slot>
          </div>

          <slot name="footer"></slot>
        </div>
      </loquix-drop-zone>
    `;
      }
      _renderContainedLayout() {
        return b2`
      <loquix-drop-zone ?disabled=${this.disabled} @loquix-drop=${this._handleDrop}>
        <div part="composer" class="composer composer--contained">
          <div part="toolbar" class="toolbar toolbar--top">
            <slot name="toolbar-top"></slot>
          </div>

          <div part="container" class="container">
            <div class="container__input">
              <slot name="input">
                <loquix-prompt-input
                  .value=${this._inputValue}
                  placeholder=${this.placeholder ?? this._localize.term("chatComposer.placeholder")}
                  ?disabled=${this.disabled}
                  variant="panel"
                  auto-resize
                  submit-on-enter
                  @loquix-change=${this._handleInputChange}
                  @loquix-submit=${this._handleSubmitFromInput}
                ></loquix-prompt-input>
              </slot>
            </div>

            <div part="actions-bar" class="actions-bar">
              <div class="actions-bar__left">
                <slot name="actions-left"></slot>
              </div>
              <div class="actions-bar__right">
                <slot name="actions-right"></slot>
                ${this.streaming ? this._renderStopButton() : this._renderSendButton()}
              </div>
            </div>
          </div>

          <slot name="suggestions"></slot>

          <div part="toolbar" class="toolbar toolbar--bottom">
            <slot name="toolbar-bottom"></slot>
          </div>

          <slot name="footer"></slot>
        </div>
      </loquix-drop-zone>
    `;
      }
      render() {
        return this.variant === "contained" ? this._renderContainedLayout() : this._renderDefaultLayout();
      }
    };
    _LoquixChatComposer.styles = [styles14];
    LoquixChatComposer = _LoquixChatComposer;
    __decorateClass14([
      n4({ type: String, reflect: true })
    ], LoquixChatComposer.prototype, "variant");
    __decorateClass14([
      n4({ type: String })
    ], LoquixChatComposer.prototype, "placeholder");
    __decorateClass14([
      n4({ type: Boolean, reflect: true })
    ], LoquixChatComposer.prototype, "disabled");
    __decorateClass14([
      n4({ type: Boolean, reflect: true, attribute: "submit-disabled" })
    ], LoquixChatComposer.prototype, "submitDisabled");
    __decorateClass14([
      n4({ type: Boolean, reflect: true })
    ], LoquixChatComposer.prototype, "streaming");
    __decorateClass14([
      n4({ type: Number, attribute: "max-length" })
    ], LoquixChatComposer.prototype, "maxLength");
    __decorateClass14([
      r5()
    ], LoquixChatComposer.prototype, "_hasContent");
    __decorateClass14([
      r5()
    ], LoquixChatComposer.prototype, "_inputValue");
  }
});

// node_modules/@loquix/core/dist/components/core/define-chat-composer.js
var init_define_chat_composer = __esm({
  "node_modules/@loquix/core/dist/components/core/define-chat-composer.js"() {
    init_loquix_chat_composer();
    if (!customElements.get("loquix-chat-composer")) {
      customElements.define("loquix-chat-composer", LoquixChatComposer);
    }
  }
});

// node_modules/@loquix/core/dist/components/core/loquix-attachment-panel.js
var styles15, __defProp15, __decorateClass15, _LoquixAttachmentPanel, LoquixAttachmentPanel;
var init_loquix_attachment_panel = __esm({
  "node_modules/@loquix/core/dist/components/core/loquix-attachment-panel.js"() {
    init_lit();
    init_decorators();
    init_index_PICxVGtN();
    init_index_VPyVRNKW();
    styles15 = i`
  :host {
    display: block;
    font-family: var(--loquix-font-family, system-ui, -apple-system, sans-serif);
  }

  /* === Panel container === */

  .panel {
    position: relative;
    border-radius: var(--loquix-attachment-panel-border-radius, 12px);
    background: var(--loquix-attachment-panel-bg, transparent);
    transition:
      border-color 0.15s ease,
      background 0.15s ease;
  }

  /* === Chips grid === */

  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: var(--loquix-attachment-panel-gap, 8px);
    padding: var(--loquix-attachment-panel-padding, 8px);
  }

  /* === Trigger button === */

  .trigger {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: var(--loquix-attachment-trigger-padding, 6px 12px);
    border: 1px dashed var(--loquix-attachment-trigger-border-color, rgba(0, 0, 0, 0.15));
    border-radius: 8px;
    background: var(--loquix-attachment-trigger-bg, transparent);
    color: var(--loquix-attachment-trigger-color, var(--loquix-text-secondary-color, #4b5563));
    font-family: inherit;
    font-size: 0.8125rem;
    cursor: pointer;
    transition:
      background 0.15s ease,
      border-color 0.15s ease;
    margin: var(--loquix-attachment-panel-padding, 8px);
  }

  .trigger:hover:not(:disabled) {
    background: var(--loquix-attachment-trigger-hover-bg, rgba(0, 0, 0, 0.04));
    border-color: var(--loquix-attachment-trigger-hover-border-color, rgba(0, 0, 0, 0.25));
  }

  .trigger:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .trigger__icon {
    width: 16px;
    height: 16px;
    flex-shrink: 0;
  }

  /* === Hidden file input === */

  .file-input {
    display: none;
  }
`;
    __defProp15 = Object.defineProperty;
    __decorateClass15 = (decorators, target, key, kind) => {
      var result = void 0;
      for (var i5 = decorators.length - 1, decorator; i5 >= 0; i5--)
        if (decorator = decorators[i5])
          result = decorator(target, key, result) || result;
      if (result) __defProp15(target, key, result);
      return result;
    };
    _LoquixAttachmentPanel = class _LoquixAttachmentPanel2 extends i4 {
      constructor() {
        super(...arguments);
        this._localize = new LocalizeController(this);
        this.attachments = [];
        this.acceptedTypes = "*";
        this.maxFiles = 10;
        this.maxSize = 10485760;
        this.multiple = true;
        this.disabled = false;
        this.noTrigger = false;
        this._previewUrls = /* @__PURE__ */ new Map();
        this._uploadSvg = w`<svg class="trigger__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>`;
      }
      // === Getters ===
      get _isMaxReached() {
        return this.attachments.length >= this.maxFiles;
      }
      // === Preview helpers ===
      _isImageFile(attachment) {
        const mime = attachment.filetype.toLowerCase();
        if (mime.startsWith("image/")) return true;
        const ext = attachment.filename.toLowerCase().split(".").pop() ?? "";
        return ["png", "jpg", "jpeg", "gif", "webp", "avif"].includes(ext);
      }
      /**
       * Returns a blob URL preview for image attachments that have a File object.
       * Lazily creates and caches the URL to avoid re-creating on each render.
       */
      _getPreviewUrl(attachment) {
        if (!this._isImageFile(attachment) || !attachment.file) return void 0;
        let url = this._previewUrls.get(attachment.id);
        if (!url) {
          url = URL.createObjectURL(attachment.file);
          this._previewUrls.set(attachment.id, url);
        }
        return url;
      }
      /** Revoke blob URLs for attachments no longer in the list. */
      _cleanupPreviewUrls() {
        const activeIds = new Set(this.attachments.map((a3) => a3.id));
        for (const [id, url] of this._previewUrls) {
          if (!activeIds.has(id)) {
            URL.revokeObjectURL(url);
            this._previewUrls.delete(id);
          }
        }
      }
      // === Lifecycle ===
      updated(changed) {
        if (changed.has("attachments")) {
          this._cleanupPreviewUrls();
        }
      }
      disconnectedCallback() {
        super.disconnectedCallback();
        for (const url of this._previewUrls.values()) {
          URL.revokeObjectURL(url);
        }
        this._previewUrls.clear();
      }
      // === File handling ===
      _handleTriggerClick() {
        this.openFilePicker();
      }
      /**
       * Programmatically opens the native file picker dialog.
       * Respects `disabled` and `maxFiles` constraints.
       */
      openFilePicker() {
        var _a2;
        if (this.disabled || this._isMaxReached) return;
        (_a2 = this._fileInput) == null ? void 0 : _a2.click();
      }
      /**
       * Programmatically add files (e.g. from paste or external drop).
       * Respects `disabled`, `acceptedTypes`, `maxSize`, and `maxFiles` constraints.
       */
      addFiles(files) {
        if (this.disabled) return;
        if (files.length === 0) return;
        this._emitValidFiles(files);
      }
      _handleFileSelect(e6) {
        var _a2;
        const input = e6.target;
        if (!((_a2 = input.files) == null ? void 0 : _a2.length)) return;
        this._emitValidFiles(input.files);
        input.value = "";
      }
      /**
       * Centralized validation for files from both the file-input and drag-and-drop.
       * Filters by acceptedTypes (MIME type and extension), maxSize, multiple flag,
       * and maxFiles remaining capacity. Returns the validated subset of files.
       */
      _processFiles(files) {
        var _a2;
        let candidates = Array.from(files);
        const accepted = (_a2 = this.acceptedTypes) == null ? void 0 : _a2.trim();
        if (accepted && accepted !== "*" && accepted !== "*/*") {
          const tokens = this.acceptedTypes.split(",").map((t3) => t3.trim().toLowerCase()).filter(Boolean);
          candidates = candidates.filter((file) => {
            const mimeType = file.type.toLowerCase();
            const fileName = file.name.toLowerCase();
            const ext = fileName.includes(".") ? `.${fileName.split(".").pop()}` : "";
            return tokens.some((token) => {
              if (token === "*" || token === "*/*") {
                return true;
              }
              if (token.startsWith(".")) {
                return ext === token;
              }
              if (token.endsWith("/*")) {
                const prefix = token.slice(0, -1);
                return mimeType.startsWith(prefix);
              }
              return mimeType === token;
            });
          });
        }
        if (this.maxSize > 0) {
          candidates = candidates.filter((file) => file.size <= this.maxSize);
        }
        if (!this.multiple) {
          candidates = candidates.slice(0, 1);
        }
        const remaining = this.maxFiles - this.attachments.length;
        candidates = candidates.slice(0, Math.max(0, remaining));
        return candidates;
      }
      /** Run validation then dispatch the attachment-add event. */
      _emitValidFiles(files) {
        const filesToAdd = this._processFiles(files);
        const attachments = filesToAdd.map((file) => ({
          id: crypto.randomUUID(),
          file,
          filename: file.name,
          filetype: file.type || file.name.split(".").pop() || "",
          size: file.size,
          status: "pending",
          progress: 0
        }));
        if (attachments.length > 0) {
          this.dispatchEvent(
            createLoquixEvent("loquix-attachment-add", {
              attachments
            })
          );
        }
      }
      // === Child chip events ===
      _handleChipRemove(e6, attachment) {
        e6.stopPropagation();
        this.dispatchEvent(
          createLoquixEvent("loquix-attachment-remove", {
            attachment
          })
        );
      }
      // === Render ===
      render() {
        return b2`
      <div part="panel" class="panel">
        <input
          class="file-input"
          type="file"
          .accept=${this.acceptedTypes}
          ?multiple=${this.multiple}
          @change=${this._handleFileSelect}
        />

        ${this.attachments.length > 0 ? b2`
              <div part="chips" class="chips">
                ${this.attachments.map(
          (a3) => b2`
                    <loquix-attachment-chip
                      .filename=${a3.filename}
                      .filetype=${a3.filetype}
                      .size=${a3.size}
                      .purpose=${a3.purpose}
                      .status=${a3.status}
                      .progress=${a3.progress ?? 0}
                      .attachmentId=${a3.id}
                      .preview=${this._getPreviewUrl(a3) ?? ""}
                      @loquix-attachment-remove=${(e6) => this._handleChipRemove(e6, a3)}
                    ></loquix-attachment-chip>
                  `
        )}
              </div>
            ` : A}
        ${!this.noTrigger && !this._isMaxReached ? b2`
              <slot name="trigger">
                <button
                  part="trigger"
                  class="trigger"
                  ?disabled=${this.disabled}
                  @click=${this._handleTriggerClick}
                >
                  <slot name="trigger-icon">${this._uploadSvg}</slot>
                  <span
                    >${this.triggerLabel ?? this._localize.term("attachmentPanel.triggerLabel")}</span
                  >
                </button>
              </slot>
            ` : A}
      </div>
    `;
      }
    };
    _LoquixAttachmentPanel.styles = [styles15];
    LoquixAttachmentPanel = _LoquixAttachmentPanel;
    __decorateClass15([
      n4({ type: Array })
    ], LoquixAttachmentPanel.prototype, "attachments");
    __decorateClass15([
      n4({ type: String, attribute: "accepted-types" })
    ], LoquixAttachmentPanel.prototype, "acceptedTypes");
    __decorateClass15([
      n4({ type: Number, attribute: "max-files" })
    ], LoquixAttachmentPanel.prototype, "maxFiles");
    __decorateClass15([
      n4({ type: Number, attribute: "max-size" })
    ], LoquixAttachmentPanel.prototype, "maxSize");
    __decorateClass15([
      n4({ type: Boolean })
    ], LoquixAttachmentPanel.prototype, "multiple");
    __decorateClass15([
      n4({ type: Boolean, reflect: true })
    ], LoquixAttachmentPanel.prototype, "disabled");
    __decorateClass15([
      n4({ type: Boolean, reflect: true, attribute: "no-trigger" })
    ], LoquixAttachmentPanel.prototype, "noTrigger");
    __decorateClass15([
      n4({ type: String, attribute: "trigger-label" })
    ], LoquixAttachmentPanel.prototype, "triggerLabel");
    __decorateClass15([
      e5(".file-input")
    ], LoquixAttachmentPanel.prototype, "_fileInput");
  }
});

// node_modules/@loquix/core/dist/components/core/loquix-attachment-chip.js
var styles16, __defProp16, __decorateClass16, _LoquixAttachmentChip, LoquixAttachmentChip;
var init_loquix_attachment_chip = __esm({
  "node_modules/@loquix/core/dist/components/core/loquix-attachment-chip.js"() {
    init_lit();
    init_decorators();
    init_index_PICxVGtN();
    init_index_VPyVRNKW();
    styles16 = i`
  :host {
    display: inline-block;
    font-family: var(--loquix-font-family, system-ui, -apple-system, sans-serif);
  }

  .chip {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    box-sizing: border-box;
    padding: var(--loquix-attachment-chip-padding, 6px 10px);
    border: 1px solid var(--loquix-attachment-chip-border-color, rgba(0, 0, 0, 0.08));
    border-radius: var(--loquix-attachment-chip-border-radius, 8px);
    background: var(--loquix-attachment-chip-bg, rgba(0, 0, 0, 0.04));
    color: var(--loquix-attachment-chip-color, inherit);
    font-size: var(--loquix-attachment-chip-font-size, 0.8125rem);
    line-height: 1.4;
    position: relative;
    overflow: hidden;
    max-width: 280px;
    transition:
      border-color 0.15s ease,
      background 0.15s ease;
  }

  .chip--pending {
    border-style: dashed;
  }

  .chip--error {
    border-color: var(--loquix-attachment-chip-error-color, var(--loquix-error-color, #dc2626));
  }

  /* === Icon / Preview === */

  .chip__icon {
    flex-shrink: 0;
    font-size: 1rem;
    line-height: 1;
  }

  .chip__icon ::slotted(*) {
    display: block;
    width: var(--loquix-attachment-chip-preview-size, 28px);
    height: var(--loquix-attachment-chip-preview-size, 28px);
    object-fit: cover;
    border-radius: var(--loquix-attachment-chip-preview-radius, 4px);
  }

  .chip__preview {
    display: block;
    width: var(--loquix-attachment-chip-preview-size, 28px);
    height: var(--loquix-attachment-chip-preview-size, 28px);
    object-fit: cover;
    border-radius: var(--loquix-attachment-chip-preview-radius, 4px);
  }

  /* === Content === */

  .chip__content {
    flex: 1;
    min-width: 0;
  }

  .chip__filename {
    display: block;
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .chip__meta {
    display: block;
    font-size: 0.6875rem;
    color: var(--loquix-attachment-chip-meta-color, rgba(0, 0, 0, 0.62));
    white-space: nowrap;
  }

  /* === Status badge in meta === */

  .chip__status {
    font-weight: 500;
    color: var(--loquix-attachment-chip-status-color, rgba(0, 0, 0, 0.62));
  }

  /* === Progress bar === */

  .chip__progress {
    position: absolute;
    bottom: 1px;
    left: 1px;
    right: 1px;
    height: var(--loquix-attachment-chip-progress-height, 2px);
    background: var(--loquix-attachment-chip-progress-color, var(--loquix-ai-color, #7c3aed));
    width: var(--progress, 0%);
    transition: width 0.2s ease;
    border-radius: 0 var(--loquix-attachment-chip-progress-height, 2px)
      var(--loquix-attachment-chip-progress-height, 2px) 0;
    overflow: hidden;
  }

  /* Sweep highlight moving left→right across the determinate progress bar */
  .chip__progress::after {
    content: '';
    position: absolute;
    inset: 0;
    background: var(
      --loquix-attachment-chip-progress-sweep-bg,
      linear-gradient(90deg, transparent 0%, rgba(255, 255, 255, 0.35) 50%, transparent 100%)
    );
    animation: var(--loquix-attachment-chip-progress-sweep, chip-sweep 1.6s ease-in-out infinite);
  }

  @keyframes chip-sweep {
    0% {
      transform: translateX(-100%);
    }
    100% {
      transform: translateX(100%);
    }
  }

  /* Indeterminate shimmer when uploading at 0% — neutral color to avoid jarring jump */
  .chip__progress--indeterminate {
    width: auto;
    background: var(--loquix-attachment-chip-shimmer-bg, rgba(0, 0, 0, 0.12));
    border-radius: 0;
    animation: chip-shimmer 1.8s ease-in-out infinite;
  }

  /* No sweep on indeterminate — it already pulses */
  .chip__progress--indeterminate::after {
    animation: none;
    background: none;
  }

  @keyframes chip-shimmer {
    0%,
    100% {
      opacity: 0.3;
    }
    50% {
      opacity: 1;
    }
  }

  /* === Error icon (tooltip trigger) === */

  .chip__error-icon {
    flex-shrink: 0;
    width: 14px;
    height: 14px;
    color: var(--loquix-attachment-chip-error-color, var(--loquix-error-color, #dc2626));
    cursor: help;
    outline: none;
    border-radius: 50%;
  }

  .chip__error-icon:focus-visible {
    outline: 2px solid var(--loquix-attachment-chip-error-color, var(--loquix-error-color, #dc2626));
    outline-offset: 2px;
  }

  /* === Error tooltip (Popover API) === */

  .chip__error-tooltip {
    position: fixed;
    transform: translateX(-50%) translateY(-100%);
    margin: 0;
    padding: 4px 10px;
    border: none;
    border-radius: 6px;
    background: var(--loquix-attachment-chip-error-color, var(--loquix-error-color, #dc2626));
    color: #fff;
    font-family: inherit;
    font-size: 0.6875rem;
    font-weight: 500;
    line-height: 1.4;
    white-space: nowrap;
    max-width: 260px;
    overflow: hidden;
    text-overflow: ellipsis;
    pointer-events: none;
    z-index: 1;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
  }

  /* Arrow */
  .chip__error-tooltip::after {
    content: '';
    position: absolute;
    top: 100%;
    left: 50%;
    transform: translateX(-50%);
    border: 5px solid transparent;
    border-top-color: var(--loquix-attachment-chip-error-color, var(--loquix-error-color, #dc2626));
  }

  /* === Retry button === */

  .chip__retry {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 18px;
    height: 18px;
    padding: 0;
    border: none;
    border-radius: 4px;
    background: transparent;
    color: var(--loquix-attachment-chip-error-color, var(--loquix-error-color, #dc2626));
    cursor: pointer;
    flex-shrink: 0;
    opacity: 0.8;
    transition:
      opacity 0.15s ease,
      background 0.15s ease;
  }

  .chip__retry:hover {
    opacity: 1;
    background: rgba(220, 38, 38, 0.1);
  }

  .chip__retry:focus-visible {
    outline: 2px solid var(--loquix-attachment-chip-error-color, var(--loquix-error-color, #dc2626));
    outline-offset: 2px;
  }

  .chip__retry svg {
    width: 12px;
    height: 12px;
  }

  /* === Remove button === */

  .chip__remove {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 18px;
    height: 18px;
    padding: 0;
    border: none;
    border-radius: 4px;
    background: transparent;
    color: inherit;
    cursor: pointer;
    flex-shrink: 0;
    opacity: 0.65;
    transition:
      opacity 0.15s ease,
      background 0.15s ease;
  }

  .chip__remove:hover {
    opacity: 1;
    background: rgba(128, 128, 128, 0.15);
  }

  .chip__remove:focus-visible {
    outline: 2px solid currentColor;
    outline-offset: 2px;
  }

  .chip__remove svg {
    width: 12px;
    height: 12px;
  }
`;
    __defProp16 = Object.defineProperty;
    __decorateClass16 = (decorators, target, key, kind) => {
      var result = void 0;
      for (var i5 = decorators.length - 1, decorator; i5 >= 0; i5--)
        if (decorator = decorators[i5])
          result = decorator(target, key, result) || result;
      if (result) __defProp16(target, key, result);
      return result;
    };
    _LoquixAttachmentChip = class _LoquixAttachmentChip2 extends i4 {
      constructor() {
        super(...arguments);
        this._localize = new LocalizeController(this);
        this.filename = "";
        this.filetype = "";
        this.size = 0;
        this.removable = true;
        this.status = "complete";
        this.progress = 0;
        this.attachmentId = "";
        this.error = "";
        this.maxFilenameLength = 0;
        this.noRetry = false;
        this.preview = "";
        this._prevWidth = 0;
        this._widthTimer = 0;
        this._closeSvg = w`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
        this._errorSvg = w`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`;
        this._retrySvg = w`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>`;
      }
      /**
       * Sanitised preview URL — allows blob:, https:, http: protocols.
       * For data: URLs, only `data:image/*` is permitted (excluding `data:image/svg+xml`
       * which can contain executable scripts).
       */
      get _safePreviewUrl() {
        if (!this.preview) return "";
        try {
          const parsed = new URL(this.preview, window.location.href);
          if (["https:", "http:", "blob:"].includes(parsed.protocol)) return this.preview;
          if (parsed.protocol === "data:") {
            const lower = this.preview.toLowerCase();
            if (lower.startsWith("data:image/") && !lower.startsWith("data:image/svg+xml")) {
              return this.preview;
            }
            return "";
          }
          return "";
        } catch {
          return "";
        }
      }
      disconnectedCallback() {
        super.disconnectedCallback();
        window.clearTimeout(this._widthTimer);
      }
      // === Smooth width transition on status change ===
      //
      // Two concerns:
      //  1. When *status* changes (e.g. error → uploading), elements appear/disappear
      //     and the chip width can jump.  We animate width between old & new values.
      //  2. While status is "uploading" or "pending", the status label text keeps
      //     changing ("Connecting…" → "3%" → "15%" …) causing micro-shifts.
      //     We lock `min-width` for the duration of that status to prevent jitter.
      //
      // The lock is applied *synchronously* — in the same JS task that clears the
      // animation's inline `width` — so the browser never paints an unlocked frame.
      willUpdate(changed) {
        if (changed.has("status") && this.renderRoot) {
          const chip = this.renderRoot.querySelector(".chip");
          if (chip) {
            this._prevWidth = chip.offsetWidth;
            chip.style.minWidth = "";
          }
        }
      }
      firstUpdated() {
        if (this.status === "uploading" || this.status === "pending") {
          const chip = this.renderRoot.querySelector(".chip");
          if (chip) chip.style.minWidth = `${chip.offsetWidth}px`;
        }
      }
      updated(changed) {
        if (!changed.has("status") || this._prevWidth <= 0) return;
        const chip = this.renderRoot.querySelector(".chip");
        if (!chip) {
          this._prevWidth = 0;
          return;
        }
        const newWidth = chip.offsetWidth;
        const needsTransition = Math.abs(newWidth - this._prevWidth) > 1;
        const isInProgress = this.status === "uploading" || this.status === "pending";
        if (needsTransition) {
          window.clearTimeout(this._widthTimer);
          chip.style.width = `${this._prevWidth}px`;
          chip.offsetWidth;
          chip.style.transition = "width 0.2s ease, border-color 0.15s ease, background 0.15s ease";
          chip.style.width = `${newWidth}px`;
          this._widthTimer = window.setTimeout(() => {
            chip.style.transition = "";
            if (isInProgress) {
              chip.style.minWidth = `${newWidth}px`;
            }
            chip.style.width = "";
          }, 220);
        } else {
          if (isInProgress) chip.style.minWidth = `${newWidth}px`;
        }
        this._prevWidth = 0;
      }
      // === Attachment object builder ===
      get _attachment() {
        return {
          id: this.attachmentId,
          filename: this.filename,
          filetype: this.filetype,
          size: this.size,
          purpose: this.purpose,
          status: this.status,
          progress: this.progress,
          error: this.error || void 0
        };
      }
      // === Private helpers ===
      _formatSize(bytes) {
        if (bytes === 0) return "0 B";
        const units = ["B", "KB", "MB", "GB"];
        const k2 = 1024;
        const i5 = Math.floor(Math.log(bytes) / Math.log(k2));
        const val = bytes / Math.pow(k2, i5);
        return `${val < 10 ? val.toFixed(1) : Math.round(val)} ${units[i5]}`;
      }
      _getFileIcon() {
        const ft = this.filetype.toLowerCase();
        if (ft.startsWith("image") || ["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(ft))
          return "\u{1F5BC}\uFE0F";
        if (ft === "application/pdf" || ft === "pdf") return "\u{1F4C4}";
        if (["ts", "js", "tsx", "jsx", "py", "rs", "go", "java", "cpp", "c", "html", "css"].includes(ft))
          return "\u{1F4BB}";
        if (ft.startsWith("video") || ["mp4", "mov", "webm"].includes(ft)) return "\u{1F3AC}";
        if (ft.startsWith("audio") || ["mp3", "wav", "ogg"].includes(ft)) return "\u{1F3B5}";
        if (["zip", "tar", "gz", "rar", "7z"].includes(ft)) return "\u{1F4E6}";
        if (["doc", "docx", "txt", "md", "rtf"].includes(ft)) return "\u{1F4DD}";
        if (["xls", "xlsx", "csv"].includes(ft)) return "\u{1F4CA}";
        return "\u{1F4CE}";
      }
      /** Truncate filename preserving extension: "very-long-name.pdf" → "very-lo…me.pdf" */
      _truncateFilename(name, max) {
        if (max <= 0 || name.length <= max) return name;
        const dotIdx = name.lastIndexOf(".");
        if (dotIdx <= 0) {
          return name.slice(0, max - 1) + "\u2026";
        }
        const ext = name.slice(dotIdx);
        const base = name.slice(0, dotIdx);
        const availableForBase = max - ext.length - 1;
        if (availableForBase <= 2) {
          return name.slice(0, max - 1) + "\u2026";
        }
        const headLen = Math.ceil(availableForBase / 2);
        const tailLen = Math.floor(availableForBase / 2);
        return base.slice(0, headLen) + "\u2026" + base.slice(-tailLen) + ext;
      }
      // === Event handlers ===
      _handleRemove() {
        this.dispatchEvent(
          createLoquixEvent("loquix-attachment-remove", {
            attachment: this._attachment
          })
        );
      }
      _handleRetry() {
        this.dispatchEvent(
          createLoquixEvent("loquix-attachment-retry", {
            attachment: this._attachment
          })
        );
      }
      // === Tooltip via Popover API ===
      _showErrorTooltip(e6) {
        const tooltip = this._tooltipEl;
        if (!tooltip || !this.error) return;
        const icon = e6.currentTarget;
        const rect = icon.getBoundingClientRect();
        tooltip.style.left = `${rect.left + rect.width / 2}px`;
        tooltip.style.top = `${rect.top - 6}px`;
        try {
          tooltip.showPopover();
        } catch {
        }
      }
      _hideErrorTooltip() {
        var _a2;
        try {
          (_a2 = this._tooltipEl) == null ? void 0 : _a2.hidePopover();
        } catch {
        }
      }
      // === Render ===
      _getStatusLabel() {
        switch (this.status) {
          case "pending":
            return this._localize.term("attachmentChip.pending");
          case "uploading":
            return this.progress > 0 ? `${this.progress}%` : this._localize.term("attachmentChip.connecting");
          case "error":
            return this._localize.term("attachmentChip.error");
          default:
            return "";
        }
      }
      render() {
        const meta = [this._formatSize(this.size)];
        if (this.purpose) meta.push(this.purpose);
        const statusLabel = this._getStatusLabel();
        const displayName = this._truncateFilename(this.filename, this.maxFilenameLength);
        return b2`
      <div part="chip" class="chip chip--${this.status}">
        <span class="chip__icon" part="icon">
          <slot name="icon"
            >${this._safePreviewUrl ? b2`<img
                  class="chip__preview"
                  src="${this._safePreviewUrl}"
                  alt=""
                  loading="lazy"
                  referrerpolicy="no-referrer"
                />` : this._getFileIcon()}</slot
          >
        </span>

        <div class="chip__content">
          <span
            class="chip__filename"
            title="${displayName !== this.filename ? this.filename : A}"
            >${displayName}</span
          >
          <span class="chip__meta"
            >${meta.join(" \xB7 ")}${statusLabel ? b2` · <span class="chip__status">${statusLabel}</span>` : A}</span
          >
        </div>

        ${this.status === "uploading" ? b2`<div
              class="chip__progress ${this.progress === 0 ? "chip__progress--indeterminate" : ""}"
              style="--progress: ${this.progress}%"
            ></div>` : A}
        ${this.status === "error" ? b2`
              <span
                class="chip__error-icon"
                role="img"
                aria-label="${this.error || this._localize.term("attachmentChip.uploadError")}"
                @mouseenter=${this._showErrorTooltip}
                @mouseleave=${this._hideErrorTooltip}
                @focus=${this._showErrorTooltip}
                @blur=${this._hideErrorTooltip}
                tabindex="0"
                >${this._errorSvg}</span
              >

              ${this.error ? b2`
                    <div
                      id="error-tooltip"
                      part="error-tooltip"
                      popover
                      class="chip__error-tooltip"
                      role="tooltip"
                    >
                      ${this.error}
                    </div>
                  ` : A}
              ${!this.noRetry ? b2`
                    <button
                      part="retry"
                      class="chip__retry"
                      aria-label=${this._localize.term("attachmentChip.retryLabel", {
          filename: this.filename
        })}
                      @click=${this._handleRetry}
                    >
                      ${this._retrySvg}
                    </button>
                  ` : A}
            ` : A}
        ${this.removable ? b2`
              <button
                part="remove"
                class="chip__remove"
                aria-label=${this._localize.term("attachmentChip.removeLabel", {
          filename: this.filename
        })}
                @click=${this._handleRemove}
              >
                ${this._closeSvg}
              </button>
            ` : A}
      </div>
    `;
      }
    };
    _LoquixAttachmentChip.styles = [styles16];
    LoquixAttachmentChip = _LoquixAttachmentChip;
    __decorateClass16([
      n4({ type: String })
    ], LoquixAttachmentChip.prototype, "filename");
    __decorateClass16([
      n4({ type: String })
    ], LoquixAttachmentChip.prototype, "filetype");
    __decorateClass16([
      n4({ type: Number })
    ], LoquixAttachmentChip.prototype, "size");
    __decorateClass16([
      n4({ type: String, reflect: true })
    ], LoquixAttachmentChip.prototype, "purpose");
    __decorateClass16([
      n4({ type: Boolean, reflect: true })
    ], LoquixAttachmentChip.prototype, "removable");
    __decorateClass16([
      n4({ type: String, reflect: true })
    ], LoquixAttachmentChip.prototype, "status");
    __decorateClass16([
      n4({ type: Number })
    ], LoquixAttachmentChip.prototype, "progress");
    __decorateClass16([
      n4({ type: String, attribute: "attachment-id" })
    ], LoquixAttachmentChip.prototype, "attachmentId");
    __decorateClass16([
      n4({ type: String })
    ], LoquixAttachmentChip.prototype, "error");
    __decorateClass16([
      n4({ type: Number, attribute: "max-filename-length" })
    ], LoquixAttachmentChip.prototype, "maxFilenameLength");
    __decorateClass16([
      n4({ type: Boolean, reflect: true, attribute: "no-retry" })
    ], LoquixAttachmentChip.prototype, "noRetry");
    __decorateClass16([
      n4({ type: String })
    ], LoquixAttachmentChip.prototype, "preview");
    __decorateClass16([
      e5("#error-tooltip")
    ], LoquixAttachmentChip.prototype, "_tooltipEl");
  }
});

// node_modules/@loquix/core/dist/components/core/define-attachment-chip.js
var init_define_attachment_chip = __esm({
  "node_modules/@loquix/core/dist/components/core/define-attachment-chip.js"() {
    init_loquix_attachment_chip();
    if (!customElements.get("loquix-attachment-chip")) {
      customElements.define("loquix-attachment-chip", LoquixAttachmentChip);
    }
  }
});

// node_modules/@loquix/core/dist/components/core/define-attachment-panel.js
var init_define_attachment_panel = __esm({
  "node_modules/@loquix/core/dist/components/core/define-attachment-panel.js"() {
    init_loquix_attachment_panel();
    init_define_attachment_chip();
    if (!customElements.get("loquix-attachment-panel")) {
      customElements.define("loquix-attachment-panel", LoquixAttachmentPanel);
    }
  }
});

// entry.js
var require_entry = __commonJS({
  "entry.js"() {
    init_define_message_list();
    init_define_message_item();
    init_define_chat_composer();
    init_define_attachment_panel();
    init_define_message_content();
  }
});
export default require_entry();
/*! Bundled license information:

@lit/reactive-element/css-tag.js:
  (**
   * @license
   * Copyright 2019 Google LLC
   * SPDX-License-Identifier: BSD-3-Clause
   *)

@lit/reactive-element/reactive-element.js:
lit-html/lit-html.js:
lit-element/lit-element.js:
@lit/reactive-element/decorators/custom-element.js:
@lit/reactive-element/decorators/property.js:
@lit/reactive-element/decorators/state.js:
@lit/reactive-element/decorators/event-options.js:
@lit/reactive-element/decorators/base.js:
@lit/reactive-element/decorators/query.js:
@lit/reactive-element/decorators/query-all.js:
@lit/reactive-element/decorators/query-async.js:
@lit/reactive-element/decorators/query-assigned-nodes.js:
  (**
   * @license
   * Copyright 2017 Google LLC
   * SPDX-License-Identifier: BSD-3-Clause
   *)

lit-html/is-server.js:
  (**
   * @license
   * Copyright 2022 Google LLC
   * SPDX-License-Identifier: BSD-3-Clause
   *)

@lit/reactive-element/decorators/query-assigned-elements.js:
  (**
   * @license
   * Copyright 2021 Google LLC
   * SPDX-License-Identifier: BSD-3-Clause
   *)
*/
