// Run the actual annotation UI with synthetic API replies; no server or AI calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const { test } = require('node:test');

const root = path.resolve(__dirname, '../..');
const { JSDOM } = createRequire(path.join(root, 'frontend/package.json'))('jsdom');
const read = relative => fs.readFileSync(path.join(root, relative), 'utf8');
const conversation = JSON.parse(read('fixtures/synthetic/conversation.json'));
const checklist = JSON.parse(read('config/checklists/default.v1.json'));

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

async function until(predicate) {
  const deadline = Date.now() + 2000;
  while (!predicate()) {
    assert.ok(Date.now() < deadline, 'UI did not reach the expected state');
    await new Promise(resolve => setTimeout(resolve, 0));
  }
}

async function openWorkspace(t) {
  const state = { revision: 1, frozen: true, seed: 42, holdout_groups: 1,
    grouped_by: 'Synthetic reviewer', rows: [{
      conversation_version_id: conversation.id, conversation_id: conversation.conversation_id,
      language: conversation.language, channel: conversation.channel,
      message_count: conversation.messages.length, selected: true,
      scenario_group: 'Synthetic scenario', split: 'tune', complete: false,
    }] };
  const record = { conversation, checklist, annotation: {
    answers: checklist.criteria.map(c => ({ criterion_id: c.id, answer: null,
      explanation: null, evidence: [], ambiguity_note: null, resolution_note: null })),
    review: { annotated_by: null, language_understood: false, reviewed_by: null }, split: 'tune',
  } };
  const saves = [];
  const reads = [];
  const dom = new JSDOM(read('evals/review_ui/index.html'), {
    url: 'http://127.0.0.1:8787', runScripts: 'outside-only',
  });
  t.after(() => dom.window.close());
  const window = dom.window;
  window.HTMLElement.prototype.scrollIntoView = () => {};
  window.confirm = () => true;
  window.fetch = async (url, options) => {
    if (url === '/api/state') return { ok: true, json: async () => structuredClone(state) };
    if (url.startsWith('/api/record/')) {
      if (saves.length) {
        const gate = deferred(); reads.push(gate); await gate.promise;
      }
      return { ok: true, json: async () => structuredClone(record) };
    }
    assert.equal(url, '/api/annotation', 'Unexpected request in offline test');
    const gate = deferred();
    const body = JSON.parse(options.body);
    saves.push({ body, ...gate });
    const ok = await gate.promise;
    if (ok) {
      state.revision++;
      record.annotation.answers = body.answers;
    }
    return { ok, json: async () => ok ? structuredClone(state) : { detail: 'Synthetic save conflict' } };
  };
  window.eval(read('evals/review_ui/review.js'));
  const doc = window.document;
  await until(() => doc.querySelector('.open-dialog') && !doc.querySelector('.open-dialog').disabled);
  doc.querySelector('.open-dialog').click();
  await until(() => !doc.querySelector('#annotation-form').hidden && !doc.querySelector('#save-draft').disabled);
  return { doc, window, saves, reads };
}

function setRange(doc, window, row, message) {
  for (const select of row.querySelectorAll('select')) {
    select.value = message;
    select.dispatchEvent(new window.Event('input', { bubbles: true }));
  }
  assert.match(doc.querySelector('#save-state').textContent, /несохранённые/);
}

test('evidence is protected throughout save and refresh; later edits remain savable', async t => {
  const { doc, window, saves, reads } = await openWorkspace(t);
  let card = doc.querySelector('.criterion');
  const message = conversation.messages[0].message_id;
  setRange(doc, window, card.querySelector('.evidence-row'), message);
  doc.querySelector('#save-draft').click();
  await until(() => saves.length === 1);
  assert.equal(saves[0].body.answers[0].evidence.length, 1);
  for (const button of card.querySelectorAll('.add-range, .remove-range')) {
    assert.equal(button.disabled, true);
    button.click();
    // A directly dispatched event also must not bypass the pending-save guard.
    button.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  }
  assert.equal(card.querySelectorAll('.evidence-row').length, 1);
  assert.equal(card.querySelector('.range-start').value, message);
  saves[0].resolve(true);
  await until(() => reads.length === 1);
  assert.equal(card.querySelector('.add-range').disabled, true);
  assert.equal(doc.querySelector('#save-draft').disabled, true);
  reads[0].resolve();
  await until(() => !doc.querySelector('#save-draft').disabled);
  card = doc.querySelector('.criterion');
  assert.equal(card.querySelector('.range-start').value, message);
  assert.equal(card.querySelector('.add-range').disabled, false);
  card.querySelector('.add-range').click();
  setRange(doc, window, card.querySelector('.evidence-row:last-child'), conversation.messages[1].message_id);
  assert.equal(card.querySelectorAll('.evidence-row').length, 2);
  doc.querySelector('#save-draft').click();
  await until(() => saves.length === 2);
  assert.equal(saves[1].body.answers[0].evidence.length, 2);
  saves[1].resolve(true);
  await until(() => reads.length === 2);
  reads[1].resolve();
  await until(() => !doc.querySelector('#save-draft').disabled);
  card = doc.querySelector('.criterion');
  assert.equal(card.querySelectorAll('.evidence-row').length, 2);
  card.querySelector('.remove-range').click();
  assert.equal(card.querySelectorAll('.evidence-row').length, 1);
  assert.match(doc.querySelector('#save-state').textContent, /несохранённые/);
});

test('failed save preserves evidence and restores editing and retry', async t => {
  const { doc, window, saves, reads } = await openWorkspace(t);
  const card = doc.querySelector('.criterion');
  const message = conversation.messages[0].message_id;
  setRange(doc, window, card.querySelector('.evidence-row'), message);
  doc.querySelector('#save-draft').click();
  await until(() => saves.length === 1);
  saves[0].resolve(false);
  await until(() => !doc.querySelector('#save-draft').disabled);
  assert.equal(reads.length, 0);
  assert.equal(card.querySelector('.range-start').value, message);
  assert.equal(card.querySelector('.add-range').disabled, false);
  assert.equal(card.querySelector('.remove-range').disabled, false);
  assert.match(doc.querySelector('#notice').textContent, /Synthetic save conflict/);
  assert.match(doc.querySelector('#save-state').textContent, /несохранённые/);
  card.querySelector('.add-range').click();
  setRange(doc, window, card.querySelector('.evidence-row:last-child'), conversation.messages[1].message_id);
  doc.querySelector('#save-draft').click();
  await until(() => saves.length === 2);
  assert.equal(saves[1].body.answers[0].evidence.length, 2);
  assert.equal(saves[1].body.revision, saves[0].body.revision);
  saves[1].resolve(true);
  await until(() => reads.length === 1);
  reads[0].resolve();
  await until(() => !doc.querySelector('#save-draft').disabled);
  assert.equal(doc.querySelector('.criterion').querySelectorAll('.evidence-row').length, 2);
});
