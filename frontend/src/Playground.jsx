import {useState} from 'react';
import {api, send} from './api.js';

const EXAMPLE = {
  column_name: 'net_revenue',
  description: 'Net revenue.',
  question: 'Does net_revenue include tax?',
  expected_answer: 'No. Net revenue excludes tax.',
  required_terms: 'tax, excl',
  correct_markers: 'no, excl',
  proposed_rewrite: 'Net revenue in USD after refunds, excluding tax.',
  mode: 'auto',
  downstream_count: 2,
  certified: true
};

const LABELS = {
  correct: 'Correct',
  abstained: 'Safe abstention',
  confident_wrong: 'Confident wrong'
};

function ResultCard({title, result}) {
  if (!result) return null;
  return <article className={`play-result ${result.outcome}`}>
    <div className="play-result-head">
      <div>
        <span>{title}</span>
        <strong>{LABELS[result.outcome]}</strong>
      </div>
      <small>{result.engine}{result.llm_fallback ? ' · fallback' : ''}</small>
    </div>
    <dl>
      <div><dt>Answer</dt><dd>{result.answer}</dd></div>
      <div><dt>Description</dt><dd>{result.description || '(empty)'}</dd></div>
      <div><dt>Context seen</dt><dd>{result.context_seen || '(none)'}</dd></div>
      <div><dt>Matched markers</dt><dd>{result.matched_markers.join(', ') || 'none'}</dd></div>
      <div><dt>Risk</dt><dd>{result.risk.toFixed(2)}</dd></div>
    </dl>
  </article>;
}

export default function Playground({engineAvailable}) {
  const [form, setForm] = useState(EXAMPLE);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  function update(name, value) {
    setForm(current => ({...current, [name]: value}));
  }

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setResult(null);
    try {
      setResult(await api('/playground', send('POST', {
        ...form,
        downstream_count: Number(form.downstream_count)
      })));
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  return <section className="panel playground">
    <div className="section-title">
      <div>
        <p className="eyebrow">Live playground · your metadata</p>
        <h3>Will the model answer, abstain, or confidently get it wrong?</h3>
      </div>
      <span>{engineAvailable ? 'real model ready' : 'simulation only'}</span>
    </div>
    <p className="play-intro">
      Paste one catalog description and a question. The expected answer, required
      terms, and markers are hidden from the model and used only for deterministic grading.
    </p>
    <form className="play-form" onSubmit={submit}>
      <div className="play-grid">
        <label>Column name
          <input required maxLength="128" value={form.column_name}
            onChange={event => update('column_name', event.target.value)} />
        </label>
        <label>Execution mode
          <select value={form.mode} onChange={event => update('mode', event.target.value)}>
            <option value="auto">auto · real model when configured</option>
            <option value="llm">real model only</option>
            <option value="simulated">transparent simulation</option>
          </select>
        </label>
      </div>
      <label>Current catalog description
        <textarea rows="2" maxLength="2000" value={form.description}
          onChange={event => update('description', event.target.value)} />
      </label>
      <label>Question to ask using only that description
        <input required maxLength="500" value={form.question}
          onChange={event => update('question', event.target.value)} />
      </label>
      <div className="play-grid">
        <label>Expected answer <small>hidden from model</small>
          <input required maxLength="1000" value={form.expected_answer}
            onChange={event => update('expected_answer', event.target.value)} />
        </label>
        <label>Correct answer markers <small>comma separated</small>
          <input required maxLength="500" value={form.correct_markers}
            onChange={event => update('correct_markers', event.target.value)} />
        </label>
      </div>
      <label>Facts that must appear in the description <small>comma separated</small>
        <input required maxLength="500" value={form.required_terms}
          onChange={event => update('required_terms', event.target.value)} />
      </label>
      <label>Optional proposed rewrite
        <textarea rows="2" maxLength="2000" value={form.proposed_rewrite}
          placeholder="Leave blank to test only the current description."
          onChange={event => update('proposed_rewrite', event.target.value)} />
      </label>
      <div className="play-grid compact">
        <label>Downstream assets
          <input type="number" min="0" max="10000" value={form.downstream_count}
            onChange={event => update('downstream_count', event.target.value)} />
        </label>
        <label className="check-label">
          <input type="checkbox" checked={form.certified}
            onChange={event => update('certified', event.target.checked)} />
          Certified asset
        </label>
      </div>
      <div className="play-actions">
        <button className="primary" disabled={busy}>
          {busy ? 'Running probe…' : 'Run live probe'}
        </button>
        <button type="button" className="secondary" disabled={busy}
          onClick={() => { setForm(EXAMPLE); setResult(null); setError(''); }}>
          Load example
        </button>
      </div>
    </form>
    {error && <p className="error" role="alert">{error}</p>}
    {result && <div className="play-output">
      <div className="play-summary">
        <span>Expected answer</span><strong>{result.expected_answer}</strong>
        {result.comparison && <em className={result.comparison.transition}>
          Rewrite: {result.comparison.transition.replace('_', ' ')} · risk {' '}
          {result.comparison.risk_before.toFixed(2)} → {result.comparison.risk_after.toFixed(2)}
        </em>}
      </div>
      <div className="play-results">
        <ResultCard title="Current description" result={result.original} />
        <ResultCard title="Proposed rewrite" result={result.rewrite} />
      </div>
      <p className="caution">{result.caution}</p>
    </div>}
  </section>;
}
