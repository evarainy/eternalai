"""The remaining browser JS projects only allowlisted Site metadata and coverage."""

# The embedded JavaScript is kept intact for readable browser-side scenarios.
# ruff: noqa: E501

from __future__ import annotations

import json
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "app/infra/browser/dom_snapshot.js"


def test_thin_js_keeps_private_values_out_and_distinguishes_partial_coverage() -> None:
    harness = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const project = vm.runInNewContext(`(${fs.readFileSync(process.argv[1], 'utf8')})`);
class Element {
  constructor(tag, attrs = {}, value = '') {
    this.localName = tag; this.attrs = attrs; this.value = value; this.isConnected = true;
    this.ownerDocument = {defaultView: {getComputedStyle: () => ({display: 'block', visibility: 'visible'})}};
    this.children = []; this.markers = {};
  }
  getAttribute(key) { return Object.prototype.hasOwnProperty.call(this.attrs, key) ? this.attrs[key] : null; }
  closest() { return null; }
  getClientRects() { return [1]; }
  querySelectorAll(selector) { return selector === '*' ? this.children : (this.markers[selector] || []); }
}
const input = new Element('input', {'type': 'text', 'data-browser-context': 'Inbox', 'data-browser-row-label': 'R1'}, 'PRIVATE-VALUE');
const secret = new Element('input', {'type': 'password'}, 'PASSWORD-NEVER-EXPORT');
const unapproved = new Element('button', {'data-browser-context': 'PRIVATE-LABEL'});
const candidate = {kind: 'candidate', context: ['Inbox'], rowLabels: ['R1'], columnLabels: []};
const projected = project(input, candidate);
if (project(secret, candidate) !== null || project(unapproved, candidate) !== null) throw Error('unapproved candidate escaped');
if (projected.value_state !== 'nonempty' || projected.row_label !== 'R1') throw Error('metadata missing');
for (const forbidden of ['PRIVATE-VALUE', 'PASSWORD-NEVER-EXPORT', 'PRIVATE-LABEL']) {
  if (JSON.stringify(projected).includes(forbidden)) throw Error('private value escaped');
}
const region = new Element('section');
const marker = new Element('span');
region.markers = {'[complete]': [marker], '[empty]': [marker], '[next]': [], '[virtual]': []};
const site = {kind: 'coverage', site: {completeSelector: '[complete]', emptySelector: '[empty]', paginationSelector: '[next]', virtualizedSelector: '[virtual]'}};
const complete = project(region, site);
if (!complete.trusted_empty || complete.state !== 'complete') throw Error('trusted empty proof missing');
region.markers['[next]'] = [marker];
const pagination = project(region, site);
if (pagination.reason !== 'pagination' || pagination.trusted_empty) throw Error('pagination claimed complete');
region.markers['[virtual]'] = [marker];
const virtualized = project(region, site);
if (virtualized.reason !== 'virtualized') throw Error('virtualized claimed complete');
region.markers['iframe,frame'] = [marker];
const nestedFrame = project(region, site);
if (nestedFrame.state !== 'partial' || nestedFrame.trusted_empty)
  throw Error('nested frame claimed full coverage');
const markerElements = (count, hidden = false) => Array.from({length: count}, () => {
  const element = new Element('span'); element.hidden = hidden; return element;
});
const coverageCases = [
  {name: 'multiple-pagination', pagination: 2, virtualized: 0, expected: ['partial', 'pagination', false]},
  {name: 'multiple-virtualized', pagination: 0, virtualized: 3, expected: ['partial', 'virtualized', false]},
  {name: 'virtualized-precedes-pagination', pagination: 2, virtualized: 2, expected: ['partial', 'virtualized', false]},
  {name: 'single-virtualized', pagination: 0, virtualized: 1, expected: ['partial', 'virtualized', false]},
  {name: 'hidden-markers', pagination: 0, virtualized: 0, expected: ['complete', 'complete', true]},
  {name: 'single-pagination-with-hidden', pagination: 1, virtualized: 0, expected: ['partial', 'pagination', false]},
  {name: 'ambiguous-complete', pagination: 0, virtualized: 0, complete: 2, expected: ['partial', 'unobservable', false]},
  {name: 'ambiguous-empty', pagination: 0, virtualized: 0, empty: 2, expected: ['complete', 'complete', false]},
];
const coverageFailures = [];
for (const scenario of coverageCases) {
  const scopedEmpty = new Element('section');
  scopedEmpty.markers = {
    '[complete]': markerElements(scenario.complete ?? 1),
    '[empty]': markerElements(scenario.empty ?? 1),
    '[next]': [...markerElements(scenario.pagination), ...markerElements(2, true)],
    '[virtual]': [...markerElements(scenario.virtualized), ...markerElements(2, true)],
  };
  const observed = project(scopedEmpty, site);
  const actual = [observed.state, observed.reason, observed.trusted_empty];
  if (JSON.stringify(actual) !== JSON.stringify(scenario.expected))
    coverageFailures.push(`${scenario.name}: ${JSON.stringify(actual)}`);
}
if (coverageFailures.length) throw Error(`coverage regressions: ${coverageFailures.join('; ')}`);
region.children = Array.from({length: 4097}, () => marker);
const bounded = project(region, site);
if (bounded.overflow !== true) throw Error('region bound missing');
process.stdout.write(JSON.stringify({valueState: projected.value_state, partialReason: pagination.reason, overflow: bounded.overflow, coverageCases: coverageCases.length}));
"""
    result = subprocess.run(
        ["node", "--permission", f"--allow-fs-read={SCRIPT}", "-e", harness, str(SCRIPT)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "valueState": "nonempty", "partialReason": "pagination", "overflow": True,
        "coverageCases": 8,
    }
