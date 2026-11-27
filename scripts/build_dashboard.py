"""Assemble a standalone dashboard.html from the Design Composer prototype.

Takes the decoded Treasury.dc.html, extracts the authored markup + the
`Component` logic class (both framework-agnostic), and wraps them with a tiny
runtime that interprets {{ }} / sc-for / sc-if bindings so the design runs
without the proprietary support.js. Data comes from dashboard_data.js, which
`python export.py` writes.

    python scripts/build_dashboard.py     # after any change under design/
"""
import base64
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "design" / "Treasury.dc.html"   # the design imported from Claude Design
OUT = ROOT / "dashboard.html"

lines = SRC.read_text(encoding="utf-8").split("\n")
lines = [l + "\n" for l in lines]   # re-attach the newlines split() dropped

# Slice the two framework-agnostic chunks out of the <x-dc> prototype:
#   the authored body markup, and the `Component extends DCLogic` logic class.
_body_start = next(i for i, l in enumerate(lines) if 'padding:38px 28px 90px' in l)
_script_line = next(i for i, l in enumerate(lines) if 'data-dc-script' in l)
_class_start = _script_line + 1                                    # `class Component ...`
_class_end = next(i for i in range(len(lines) - 1, 0, -1) if lines[i].strip() == '}')
_body_end = next(i for i in range(_script_line, 0, -1) if '</x-dc>' in lines[i])

template = "".join(lines[_body_start:_body_end])
logic    = "".join(lines[_class_start:_class_end + 1])

# --- data loading: the prototype fetched a sample JSON; the real dashboard
#     reads window.__DATA__, set by the <script src="dashboard_data.js"> below —
#     that works served and from file:// alike, and needs no network call. ---
assert "uploads/sample_data.json" in logic, "fetch target not found"
logic = logic.replace(
    "fetch('uploads/sample_data.json').then(r=>r.json()).then(data=>{",
    "(window.__DATA__ ? Promise.resolve(window.__DATA__) : Promise.reject(new Error('dashboard_data.js not loaded'))).then(data=>{")
logic = logic.replace(
    "e=>console.error('data load failed', e)",
    "e=>{ console.error('data load failed', e); window.__dashLoadError && window.__dashLoadError(e); }")

# --- fill the decorative crest image-slot with the chest photo (embedded as a
#     base64 data URI so the dashboard stays self-contained); fall back to a
#     hand-drawn coin emblem if design/chest.jpeg is missing. ---
_chest = ROOT / "design" / "chest.jpeg"
if _chest.exists():
    _b64 = base64.b64encode(_chest.read_bytes()).decode("ascii")
    CREST = ('<img src="data:image/jpeg;base64,' + _b64 + '" alt="treasure chest" '
             'style="width:103px;height:103px;object-fit:cover;border-radius:50%">')
else:
    CREST = ('<svg viewBox="0 0 100 100" style="width:96px;height:96px" fill="none" '
             'stroke="#d9b06a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
             '<ellipse cx="50" cy="32" rx="26" ry="9"></ellipse>'
             '<path d="M24 32 V58 Q24 67 50 67 Q76 67 76 58 V32"></path>'
             '<path d="M24 45 Q24 54 50 54 Q76 54 76 45"></path>'
             '<path d="M42 79 H58 M46 67 V79 M54 67 V79"></path>'
             '</svg>')
template = re.sub(r'<x-import\b[^>]*>\s*</x-import>', CREST, template)
template = re.sub(r'<x-import\b[^>]*/>', CREST, template)

# --- convert sc-for / sc-if wrapper tags into data-* attributes on their
#     single child element (innermost first), so the HTML parser never
#     foster-parents a directive out of a <table>. ---
def insert_attr(body, extra):
    m = re.search(r'<([a-zA-Z][\w-]*)', body)
    if not m:
        raise SystemExit("directive body has no element:\n" + body[:200])
    i = m.end()
    return body[:i] + " " + extra + body[i:]

pat = re.compile(r'<sc-(for|if)\b([^>]*)>((?:(?!<sc-(?:for|if)\b).)*?)</sc-\1>', re.S)
while True:
    m = pat.search(template)
    if not m:
        break
    kind, attrs, body = m.group(1), m.group(2), m.group(3)
    if kind == "for":
        lst = re.search(r'list="\{\{\s*(.*?)\s*\}\}"', attrs).group(1)
        asn = re.search(r'as="(.*?)"', attrs).group(1)
        newbody = insert_attr(body, f'data-scfor="{lst}" data-scas="{asn}"')
    else:
        val = re.search(r'value="\{\{\s*(.*?)\s*\}\}"', attrs).group(1)
        newbody = insert_attr(body, f'data-scif="{val}"')
    template = template[:m.start()] + newbody + template[m.end():]

for bad in ("sc-for", "sc-if", "x-import", "<helmet", "<x-dc"):
    assert bad not in template, f"leftover {bad} in template"

# --- head styles lifted from the prototype's <helmet> ---
HEAD_STYLE = re.search(r"<helmet>.*?<style>(.*?)</style>", "".join(lines), re.S).group(1)

RUNTIME = r"""
/* ── minimal Design-Composer runtime ──────────────────────────────────────
   Interprets {{ path }} interpolation, data-scfor / data-scif directives,
   onClick/onChange/onMouseEnter/onMouseLeave/onMouseMove bindings and style-hover, then
   reconciles the live DOM in place so inputs keep focus and sliders keep drag. */
(function(){
'use strict';
var SVG='http://www.w3.org/2000/svg';

function evalExpr(expr, scope){
  expr = expr.trim();
  if(expr==='true') return true;
  if(expr==='false') return false;
  if(expr==='null') return null;
  if(/^-?\d+(\.\d+)?$/.test(expr)) return Number(expr);
  var parts = expr.split('.'), v = scope;
  for(var i=0;i<parts.length;i++){ if(v==null) return undefined; v = v[parts[i]]; }
  return v;
}
var BIND=/\{\{\s*([^}]*?)\s*\}\}/g;
function interpolate(str, scope){
  return str.replace(BIND, function(_,e){ var v=evalExpr(e,scope); return v==null?'':String(v); });
}
function singleBinding(str){
  var m = /^\{\{\s*([^}]*?)\s*\}\}$/.exec(str);
  return m ? m[1].trim() : null;
}

function renderChildren(node, scope, out){
  var kids = node.childNodes;
  for(var i=0;i<kids.length;i++) renderNode(kids[i], scope, out);
}
function renderNode(node, scope, out){
  if(node.nodeType===3){                       // text
    var t = node.nodeValue;
    out.appendChild(document.createTextNode(t.indexOf('{{')>=0 ? interpolate(t,scope) : t));
    return;
  }
  if(node.nodeType!==1) return;                // skip comments etc.
  if(node.hasAttribute('data-scif') && !evalExpr(node.getAttribute('data-scif'), scope)) return;
  if(node.hasAttribute('data-scfor')){
    var list = evalExpr(node.getAttribute('data-scfor'), scope) || [];
    var as = node.getAttribute('data-scas');
    for(var i=0;i<list.length;i++){
      var cs = Object.create(scope); cs[as] = list[i]; cs.$index = i;
      buildElement(node, cs, out);
    }
    return;
  }
  buildElement(node, scope, out);
}
var EVENTS = {onclick:1,oninput:1,onchange:1,onmouseenter:1,onmouseleave:1,onmousemove:1,onkeydown:1};
function buildElement(node, scope, out){
  var el = node.namespaceURI===SVG
    ? document.createElementNS(SVG, node.localName)
    : document.createElement(node.localName);
  var ev = {}, enters = [], leaves = [], hover = null, pendingVal, hasVal = false;
  var attrs = node.attributes;
  for(var k=0;k<attrs.length;k++){
    var name = attrs[k].name, raw = attrs[k].value, ln = name.toLowerCase();
    if(ln==='data-scfor'||ln==='data-scas'||ln==='data-scif') continue;
    if(ln.indexOf('hint-')===0||ln==='data-comment-anchor') continue;
    if(ln==='style-hover'){ hover = raw; continue; }
    if(EVENTS[ln]){
      var b = singleBinding(raw), fn = b!=null ? evalExpr(b, scope) : null;
      if(typeof fn==='function'){
        if(ln==='onmouseenter') enters.push(fn);
        else if(ln==='onmouseleave') leaves.push(fn);
        else if(ln==='onchange'){ var ty=(node.getAttribute('type')||'text').toLowerCase(); ev[ty==='number'?'change':'input']=fn; }
        else if(ln==='onclick') ev.click=fn;
        else if(ln==='oninput') ev.input=fn;
        else if(ln==='onkeydown') ev.keydown=fn;
        else if(ln==='onmousemove') ev.mousemove=fn;
      }
      continue;
    }
    if(ln==='value' && (el.tagName==='INPUT'||el.tagName==='TEXTAREA'||el.tagName==='SELECT')){
      pendingVal = raw.indexOf('{{')>=0 ? interpolate(raw,scope) : raw; hasVal = true; continue;
    }
    el.setAttribute(name, raw.indexOf('{{')>=0 ? interpolate(raw,scope) : raw);
  }
  el.__baseStyle = el.getAttribute('style') || '';
  el.onclick   = ev.click   || null;
  el.oninput   = ev.input   || null;
  el.onchange  = ev.change  || null;
  el.onkeydown = ev.keydown || null;
  el.onmousemove = ev.mousemove || null;
  if(hover!=null){
    enters.push(function(){ this.setAttribute('style', (this.__baseStyle||'') + ';' + hover); });
    leaves.push(function(){ this.setAttribute('style', this.__baseStyle||''); });
  }
  el.onmouseenter = enters.length ? function(e){ for(var i=0;i<enters.length;i++) enters[i].call(this,e); } : null;
  el.onmouseleave = leaves.length ? function(e){ for(var i=0;i<leaves.length;i++) leaves[i].call(this,e); } : null;
  if(hasVal){ el.__val = pendingVal; el.value = pendingVal; }
  renderChildren(node, scope, el);
  out.appendChild(el);
}

/* ── in-place reconciliation ── */
var HANDLERS = ['onclick','oninput','onchange','onkeydown','onmouseenter','onmouseleave','onmousemove'];
function sameNode(a, b){
  if(a.nodeType!==b.nodeType) return false;
  if(a.nodeType!==1) return true;
  return a.namespaceURI===b.namespaceURI && a.tagName===b.tagName;
}
function morph(live, fresh){
  var fk = Array.prototype.slice.call(fresh.childNodes);
  var lk = Array.prototype.slice.call(live.childNodes);
  for(var i=0;i<fk.length;i++){
    var f = fk[i], l = lk[i];
    if(!l){ live.appendChild(f); continue; }
    if(sameNode(l, f)) patchNode(l, f);
    else live.replaceChild(f, l);
  }
  for(var j=lk.length-1;j>=fk.length;j--){ if(lk[j].parentNode===live) live.removeChild(lk[j]); }
}
function patchNode(l, f){
  if(l.nodeType===3){ if(l.nodeValue!==f.nodeValue) l.nodeValue = f.nodeValue; return; }
  if(l.nodeType!==1) return;
  // attributes
  for(var i=l.attributes.length-1;i>=0;i--){ var n=l.attributes[i].name; if(!f.hasAttribute(n)) l.removeAttribute(n); }
  for(var j=0;j<f.attributes.length;j++){ var a=f.attributes[j]; if(l.getAttribute(a.name)!==a.value) l.setAttribute(a.name,a.value); }
  // handlers + bound props
  for(var h=0;h<HANDLERS.length;h++){ var p=HANDLERS[h]; if(l[p]!==f[p]) l[p]=f[p]; }
  l.__baseStyle = f.__baseStyle;
  if(f.__val!==undefined){ l.__val=f.__val; if(l.value!==f.__val) l.value=f.__val; }
  morph(l, f);
}

/* ── component base: setState + rAF-batched render into #app ── */
function DCLogic(){}
DCLogic.prototype.setState = function(patch, cb){
  Object.assign(this.state, patch);
  if(cb) cb();
  if(!this._raf){ var self=this; this._raf = requestAnimationFrame(function(){ self._raf=0; self._render(); }); }
};
DCLogic.prototype.mount = function(tpl, app){
  this._tpl = tpl; this._app = app;
  if(this.componentDidMount) this.componentDidMount();
  this._render();
};
DCLogic.prototype._render = function(){
  var V = this.renderVals();
  var fresh = document.createElement('div');
  renderChildren(this._tpl, V, fresh);
  if(!this._mounted){
    while(this._app.firstChild) this._app.removeChild(this._app.firstChild);
    while(fresh.firstChild) this._app.appendChild(fresh.firstChild);
    this._mounted = true;
  } else morph(this._app, fresh);
};
window.DCLogic = DCLogic;
})();
"""

BOOTSTRAP = r"""
window.__dashLoadError = function(e){
  var box = document.getElementById('loaderr');
  if(box){ box.style.display='block'; }
};
(function(){
  var holder = document.createElement('div');
  holder.innerHTML = document.getElementById('tpl').innerHTML;
  var app = document.getElementById('app');
  var c = new Component();
  c.props = {mcRuns:1000, showMilestones:true, tableRows:12};
  c.mount(holder, app);
  window.__treasury = c;
})();
"""

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Treasury — Finances Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin="">
<!-- Fonts load async (media swap) so a slow/offline network never blocks paint;
     falls back to a system serif if they never arrive. -->
<link rel="stylesheet" media="print" onload="this.media='all'" href="https://fonts.googleapis.com/css2?family=Cinzel:wght@400;500;600;700;900&amp;family=Spectral:ital,wght@0,300;0,400;0,500;0,600;1,400&amp;display=swap">
<noscript><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Cinzel:wght@400;500;600;700;900&amp;family=Spectral:ital,wght@0,300;0,400;0,500;0,600;1,400&amp;display=swap"></noscript>
<style>
{HEAD_STYLE}
#loaderr{{display:none;max-width:760px;margin:80px auto;padding:26px 30px;border:1px solid #6d5330;background:linear-gradient(165deg,#101a2d,#0b1322);color:#c9d4e3;font-family:Spectral,Georgia,serif;box-shadow:0 20px 44px rgba(0,0,0,.45)}}
#loaderr h2{{font-family:Cinzel,serif;letter-spacing:.2em;color:#ecd39a;font-size:16px;margin:0 0 12px}}
#loaderr code{{background:#0a1120;border:1px solid #4f3d22;color:#ecd39a;padding:2px 7px}}
#loaderr p{{line-height:1.55;color:#a8b4c7}}
</style>
</head>
<body>
<div id="app"></div>
<div id="loaderr">
  <h2>THE VAULT IS SEALED</h2>
  <p>Couldn't load <code>dashboard_data.js</code>, the data file that sits next to this page.</p>
  <p>From the project folder run <code>python serve.py</code> — it rebuilds the data file and
  opens the dashboard. (<code>python export.py</code> rebuilds the file on its own.)</p>
</div>

<script type="text/html" id="tpl">
{template}</script>

<!-- `python export.py` writes dashboard_data.js (window.__DATA__ = ...). A plain
     script tag, so the dashboard also opens by double-click (file://). -->
<script src="dashboard_data.js" onerror="void 0"></script>
<script>{RUNTIME}</script>
<script>
{logic}
</script>
<script>{BOOTSTRAP}</script>
</body>
</html>
"""

OUT.write_text(html, encoding="utf-8")
print("wrote", OUT, len(html), "bytes")
