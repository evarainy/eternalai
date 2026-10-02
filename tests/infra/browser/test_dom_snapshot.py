"""Exercise the fixed browser-side projection in a synthetic JavaScript DOM.

This proves filtering before serialization. A real Chromium DOM test remains a
separate M0-03 environment obligation and is not claimed by these fakes.
"""

# The embedded Node fixture is JavaScript source; Python line wrapping would
# make its security cases harder to inspect.
# ruff: noqa: E501

from __future__ import annotations

import json
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "app/infra/browser/dom_snapshot.js"


def test_fixed_js_filters_sensitive_values_and_unapproved_labels_before_serialization() -> None:
    harness = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const project = vm.runInNewContext(`(${fs.readFileSync(process.argv[1], 'utf8')})`);
class Element {
  constructor(tag, attrs = {}, value = '') {
    this.localName = tag; this.attrs = attrs; this.value = value;
    this.isConnected = true; this.hidden = false; this.textContent = attrs.text || '';
    this.ownerDocument = { defaultView: { getComputedStyle: () => ({display: 'block', visibility: 'visible'}) }, getElementById: () => null };
  }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null; }
  hasAttribute(name) { return this.getAttribute(name) !== null; }
  closest() { return null; }
  getClientRects() { return [1]; }
  matches(selector) { return selector === ':disabled' && this.hasAttribute('disabled'); }
}
class Region extends Element {
  constructor(children, markers) { super('section'); this.children = children; this.markers = markers; }
  querySelectorAll(selector) { return selector === '*' ? this.children : (this.markers[selector] || []); }
}
const allowed = new Element('button', {'aria-label': 'Open', 'data-browser-context': 'Inbox', 'data-browser-row-label': 'R1', 'data-browser-column-label': 'Action'});
const duplicate = new Element('button', {'aria-label': 'Open', 'disabled': ''});
const secret = new Element('input', {'type': 'password', 'aria-label': 'Open'}, 'P4ssword-NEVER-EXPORT');
const token = new Element('input', {'type': 'text', 'name': 'access_token', 'aria-label': 'Open'}, 'TOKEN-NEVER-EXPORT');
const privateLabel = new Element('button', {'aria-label': 'PERSONAL-NAME-NEVER-EXPORT'});
const hidden = new Element('button', {'aria-label': 'Open'}); hidden.hidden = true;
const field = new Element('input', {'type': 'text', 'aria-label': 'Query'}, 'PRIVATE-INPUT-NEVER-EXPORT');
const complete = new Element('span');
const config = {
  policy: { roles: ['button', 'textbox'], names: ['Open', 'Query'], context: ['Inbox'], rowLabels: ['R1'], columnLabels: ['Action'], maximumCandidates: 8 },
  site: { completeSelector: '[complete]', emptySelector: '[empty]', paginationSelector: '[next]', virtualizedSelector: '[virtual]' }
};
const region = new Region([allowed, duplicate, secret, token, privateLabel, hidden, field], {'[complete]': [complete]});
const result = project(region, config);
if (result.metadata.candidates.length !== 3) throw Error('wrong candidate count');
if (result.metadata.candidates[0].row_label !== 'R1' || result.metadata.candidates[0].column_label !== 'Action') throw Error('labels missing');
if (result.metadata.candidates[1].enabled !== false) throw Error('disabled candidate state lost');
if (result.metadata.candidates[2].value_state !== 'nonempty') throw Error('value state missing');
if (result.metadata.coverage.trusted_empty) throw Error('nonempty region trusted empty');
const serialized = JSON.stringify(result.metadata);
for (const forbidden of ['P4ssword-NEVER-EXPORT', 'TOKEN-NEVER-EXPORT', 'PERSONAL-NAME-NEVER-EXPORT', 'PRIVATE-INPUT-NEVER-EXPORT']) {
  if (serialized.includes(forbidden)) throw Error('unapproved value escaped');
}
const empty = new Region([], {'[complete]': [complete], '[empty]': [new Element('span')]});
if (!project(empty, config).metadata.coverage.trusted_empty) throw Error('trusted empty proof missing');
empty.markers['[next]'] = [new Element('button')];
const partial = project(empty, config).metadata.coverage;
if (partial.state !== 'partial' || partial.reason !== 'pagination' || partial.trusted_empty) throw Error('pagination claimed complete');
const bounded = new Region([allowed, duplicate, field], {'[complete]': [complete]});
const resultBounded = project(bounded, {...config, policy: {...config.policy, maximumCandidates: 2}});
if (!resultBounded.metadata.overflow || resultBounded.metadata.candidates.length) throw Error('overflow silently truncated');
process.stdout.write(JSON.stringify({candidateCount: result.metadata.candidates.length, partialReason: partial.reason, overflow: resultBounded.metadata.overflow}));
"""
    result = subprocess.run(
        ["node", "--permission", f"--allow-fs-read={SCRIPT}", "-e", harness, str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "candidateCount": 3,
        "partialReason": "pagination",
        "overflow": True,
    }
