import {useState} from 'react';
import {api, send} from './api.js';

const EXAMPLE = {
  column_name: 'payment_amount',
  data_type: 'decimal',
  description: 'Payment amount in USD, excluding tax.',
  mode: 'auto',
  pad_repetitions: 2
};

const VARIANT_NAMES = {
  original: 'Original witness',
  flip: 'Controlled opposite',
  remove: 'Claim removed',
  pad: 'Witness plus neutral noise'
};

function score(value) {
  return value == null ? 'n/a' : `${Math.round(value * 100)}%`;
}

function Variant({variant}) {
  return <article className={`mutation-case ${variant.passed ? 'passed' : 'failed'}`}>
    <div className="mutation-head">
      <div>
        <span>{VARIANT_NAMES[variant.kind]}</span>
        <strong>{variant.passed ? 'Pass' : 'Fail'}</strong>
      </div>
      <small>{variant.engine}{variant.llm_fallback ? ' · fallback excluded' : ''}</small>
    </div>
    <div className="label-pair">
      <span>Expected <b>{variant.expected_label}</b></span>
      <span>Observed <b>{variant.observed_label}</b></span>
    </div>
    <dl>
      <div><dt>Metadata variant</dt><dd>{variant.description || '(empty)'}</dd></div>
      <div><dt>Raw output</dt><dd>{variant.raw_output}</dd></div>
      <div><dt>Provider status</dt><dd>{variant.provider_status}</dd></div>
    </dl>
  </article>;
}

function Proof({proof}) {
  return <article className="proof-card">
    <div className="proof-head">
      <div>
        <p className="eyebrow">Compiled proof</p>
        <h4>{proof.claim}</h4>
      </div>
      <code>{proof.id}</code>
    </div>
    <div className="proof-meta">
      <span>Evidence <b>{proof.evidence_span.start}:{proof.evidence_span.end}</b></span>
      <span>Mutation <b>{proof.mutation_span.start}:{proof.mutation_span.end}</b></span>
      <span>Operator <b>{proof.operator.source} → {proof.operator.opposite}</b></span>
      <span>Family <b>{proof.operator.family}</b></span>
    </div>
    <p className="proof-evidence"><b>Exact evidence:</b> “{proof.evidence_span.text}”</p>
    <div className="mutation-grid">
      {proof.variants.map(variant => <Variant key={variant.kind} variant={variant} />)}
    </div>
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
        data_type: form.data_type.trim() || null,
        pad_repetitions: Number(form.pad_repetitions)
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
        <p className="eyebrow">Proof-carrying probe compiler</p>
        <h3>Turn one description into grounding tests automatically</h3>
      </div>
      <span>{engineAvailable ? 'real model ready' : 'simulation only'}</span>
    </div>
    <p className="play-intro">
      Paste metadata only. Contextprobe extracts exact reversible claims, then tests the
      original, a controlled opposite, removal, and neutral padding. You do not write a
      question, expected answer, marker, or grading rule.
    </p>
    <form className="play-form" onSubmit={submit}>
      <div className="play-grid">
        <label>Column name
          <input required maxLength="128" value={form.column_name}
            onChange={event => update('column_name', event.target.value)} />
        </label>
        <label>Data type <small>optional model context</small>
          <input maxLength="128" value={form.data_type}
            onChange={event => update('data_type', event.target.value)} />
        </label>
      </div>
      <label>Catalog description
        <textarea required rows="3" maxLength="5000" value={form.description}
          onChange={event => update('description', event.target.value)} />
      </label>
      <div className="play-grid compact">
        <label>Execution mode
          <select value={form.mode} onChange={event => update('mode', event.target.value)}>
            <option value="auto">auto · real model when configured</option>
            <option value="llm">real model only</option>
            <option value="simulated">deterministic lexical check</option>
          </select>
        </label>
        <label>Neutral padding repetitions
          <input type="number" min="1" max="3" value={form.pad_repetitions}
            onChange={event => update('pad_repetitions', event.target.value)} />
        </label>
      </div>
      <div className="play-actions">
        <button className="primary" disabled={busy}>
          {busy ? 'Compiling and testing…' : 'Compile mutation tests'}
        </button>
        <button type="button" className="secondary" disabled={busy}
          onClick={() => { setForm(EXAMPLE); setResult(null); setError(''); }}>
          Load example
        </button>
      </div>
    </form>
    {error && <p className="error" role="alert">{error}</p>}
    {result && <div className="play-output">
      <div className="compiler-summary">
        <div><span>Claims</span><strong>{result.proofs.length}</strong></div>
        <div><span>Total score</span><strong>{score(result.summary.total_score)}</strong></div>
        <div><span>Model-only</span><strong>{score(result.summary.model_only_score)}</strong></div>
        <div><span>Fallback cases</span><strong>{result.summary.fallback_cases}</strong></div>
        <div><span>Compiler</span><strong>{result.compiler_version}</strong></div>
      </div>
      {result.diagnostics.length > 0 && <div className="diagnostics">
        <strong>Compiler diagnostics</strong>
        <ul>{result.diagnostics.map((item, index) =>
          <li key={`${item.code}-${index}`}><code>{item.code}</code> {item.message}</li>
        )}</ul>
      </div>}
      <div className="proof-list">
        {result.proofs.map(proof => <Proof key={proof.id} proof={proof} />)}
      </div>
      <p className="source-hash">Source SHA-256: <code>{result.source_description_sha256}</code></p>
      <p className="caution">{result.caution}</p>
    </div>}
  </section>;
}
