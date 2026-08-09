import {useEffect, useRef, useState} from 'react';
import {api, send} from './api.js';
import Playground from './Playground.jsx';

const OUTCOME_LABEL = {
  correct: 'Correct',
  abstained: 'Abstained',
  confident_wrong: 'Confident wrong'
};

function Header({engineAvailable, onProbeAll, running}) {
  return <header className="topbar">
    <div>
      <p className="eyebrow">Metadata risk · agent probe harness</p>
      <h1>Find the metadata that will break an agent.</h1>
      <p className="lede">
        Coverage counts descriptions. Contextprobe asks whether an agent can actually
        answer with them, and ranks what it would get confidently wrong.
      </p>
    </div>
    <div className="topbar-actions">
      <span className="engine-pill">
        <i className={engineAvailable ? 'live' : ''} />
        {engineAvailable ? 'Real model ready in playground' : 'Simulation only'}
      </span>
      <button className="primary" onClick={onProbeAll} disabled={running}>
        {running ? 'Probing catalog…' : 'Probe whole catalog'}
      </button>
    </div>
  </header>;
}

function PairedComparison({pair}) {
  if (!pair?.columns?.length) return null;
  return <section className="panel paired">
    <div className="section-title">
      <div>
        <p className="eyebrow">Controlled pair</p>
        <h3>Same asset. Same coverage. Same questions.</h3>
      </div>
    </div>
    <div className="paired-grid">
      {pair.columns.map(column => {
        const bad = column.confident_wrong > 0;
        return <article key={column.column} className={bad ? 'bad' : 'good'}>
          <code>{column.column}</code>
          <p className="desc">{column.description || 'No description'}</p>
          <div className="paired-stats">
            <span>Documented<strong>{column.has_description ? 'Yes' : 'No'}</strong></span>
            <span>Correct<strong>{column.correct}/{column.probes}</strong></span>
            <span className={bad ? 'danger' : ''}>
              Confident wrong<strong>{column.confident_wrong}/{column.probes}</strong>
            </span>
          </div>
        </article>;
      })}
    </div>
    <p className="footnote">{pair.note}</p>
  </section>;
}


function RiskQueue({queue, selectedId, onSelect}) {
  return <section className="panel queue">
    <div className="section-title">
      <div>
        <p className="eyebrow">Repair queue</p>
        <h3>Ranked by confident wrong answers × blast radius</h3>
      </div>
      <span>{queue.filter(item => item.probed).length} probed</span>
    </div>
    <div className="queue-head">
      <span>Asset</span><span>Coverage</span><span>Correct</span>
      <span>Abstained</span><span>Wrong</span><span>Downstream</span><span>Risk</span>
    </div>
    {queue.map(item => <button key={item.asset_id}
      className={`queue-row ${selectedId === item.asset_id ? 'active' : ''} ${item.risk > 0 ? 'risky' : ''}`}
      onClick={() => onSelect(item.asset_id)}>
      <span className="asset-cell">
        <strong>{item.asset}</strong>
        <small>
          {item.asset_type}
          {item.certified && <em className="badge certified">certified</em>}
          {item.deprecated && <em className="badge deprecated">deprecated</em>}
        </small>
      </span>
      <span>{Math.round(item.column_coverage * 100)}%</span>
      <span>{item.correct}</span>
      <span>{item.abstained}</span>
      <span className={item.confident_wrong ? 'danger' : ''}>{item.confident_wrong}</span>
      <span>{item.downstream_assets}</span>
      <span className="risk-cell"><b>{item.risk.toFixed(2)}</b></span>
    </button>)}
    <p className="footnote">
      Risk = confident-wrong rate × (1 + downstream assets) × 1.5 if certified.
      Demo weights, not calibrated on production data.
    </p>
  </section>;
}

function ProbeResults({results}) {
  if (!results.length) {
    return <p className="empty-note">Not probed yet. Run the probes to see agent behaviour.</p>;
  }
  return <ul className="probe-list">
    {results.map(result => <li key={result.id || result.probe_id} className={result.outcome}>
      <span className={`chip ${result.outcome}`}>{OUTCOME_LABEL[result.outcome]}</span>
      <div>
        <strong>{result.question || result.probe_id}</strong>
        <small className="answer">{result.answer}</small>
        <small className="context">Context seen: {result.context_seen || '(no description)'}</small>
      </div>
    </li>)}
  </ul>;
}


function FixPanel({asset, columns, onSaved}) {
  const targets = [
    {value: '', label: `${asset.name} (asset description)`, current: asset.description},
    ...columns.map(column => ({value: column.name, label: column.name, current: column.description}))
  ];
  const [target, setTarget] = useState(targets[0].value);
  const [draft, setDraft] = useState(targets[0].current || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setTarget(targets[0].value);
    setDraft(targets[0].current || '');
    setError('');
  }, [asset.id]);

  function pick(value) {
    setTarget(value);
    const found = targets.find(item => item.value === value);
    setDraft(found?.current || '');
  }

  async function saveAndRerun(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api(`/assets/${asset.id}/description`, send('PATCH', {
        column_name: target || null,
        description: draft
      }));
      await onSaved();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  return <form className="fix-panel" onSubmit={saveAndRerun}>
    <p className="eyebrow">Fix and re-probe</p>
    <label>
      Target
      <select value={target} onChange={event => pick(event.target.value)}>
        {targets.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
      </select>
    </label>
    <label>
      Description
      <textarea value={draft} rows="4" maxLength="2000"
        placeholder="State what is included, excluded, the units and the grain."
        onChange={event => setDraft(event.target.value)} />
    </label>
    {error && <p className="error" role="alert">{error}</p>}
    <button className="primary" disabled={busy}>
      {busy ? 'Saving and re-probing…' : 'Save and re-probe this asset'}
    </button>
  </form>;
}


function RepairPanel({asset, breakdown, onApplied}) {
  const failing = breakdown.filter(row => row.confident_wrong > 0 && row.column !== '(asset level)');
  const [column, setColumn] = useState(failing[0]?.column || '');
  const [strategy, setStrategy] = useState('grounded');
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setResult(null);
    setColumn(failing[0]?.column || '');
  }, [asset.id]);

  if (!failing.length) {
    return <div className="repair-panel">
      <p className="eyebrow">Repair gate</p>
      <p className="empty-note">No column on this asset is currently failing a probe.</p>
    </div>;
  }

  async function propose(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      setResult(await api(`/assets/${asset.id}/repair`, send('POST', {
        column_name: column, strategy, mode: 'auto'
      })));
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  async function applyCandidate() {
    setBusy(true);
    setError('');
    try {
      await api(`/assets/${asset.id}/description`, send('PATCH', {
        column_name: result.column_name, description: result.after_description
      }));
      setResult(null);
      await onApplied();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  const accepted = result?.verdict === 'accepted';
  return <form className="repair-panel" onSubmit={propose}>
    <p className="eyebrow">Repair gate &#183; dry run</p>
    <p className="repair-lede">
      Generate a rewrite from upstream and sibling metadata only, then re-probe.
      Accepted only if a failing question becomes answerable and none regress.
    </p>
    <div className="repair-controls">
      <label>
        Column
        <select value={column} onChange={event => setColumn(event.target.value)}>
          {failing.map(row => <option key={row.column} value={row.column}>{row.column}</option>)}
        </select>
      </label>
      <label>
        Strategy
        <select value={strategy} onChange={event => setStrategy(event.target.value)}>
          <option value="grounded">grounded &#8212; concise</option>
          <option value="verbose">verbose &#8212; padded</option>
          <option value="narrow">narrow &#8212; single clause</option>
        </select>
      </label>
      <button className="secondary" disabled={busy}>
        {busy ? 'Probing…' : 'Propose rewrite'}
      </button>
    </div>
    {error && <p className="error" role="alert">{error}</p>}
    {result && <div className="repair-result">
      <div className={`verdict ${result.verdict}`}>
        {accepted ? 'ACCEPTED' : 'REJECTED'}
        <small>
          {result.fixed_count} fixed &#183; {result.regressed_count} regressed &#183;
          {' '}{result.candidate_length}/{result.salience_chars} chars
        </small>
      </div>
      {result.reject_reason && <p className="reject-reason">{result.reject_reason}</p>}
      <div className="diff">
        <div><span>before</span><code>{result.before_description || 'no description'}</code></div>
        <div><span>candidate</span><code>{result.after_description}</code></div>
      </div>
      <table className="transitions">
        <tbody>
          {result.probe_transitions.filter(row => row.transition !== 'unchanged').map(row =>
            <tr key={row.probe_id} className={row.transition}>
              <td>{row.probe_id}</td>
              <td>{row.before}</td>
              <td>&#8594;</td>
              <td>{row.after}</td>
              <td>{row.transition}</td>
            </tr>
          )}
        </tbody>
      </table>
      <p className="grounding">
        Drew on {result.grounding_used.length} of {result.grounding_available} documented
        clauses: {result.grounding_used.map(item =>
          `${item.from_column || item.from_asset} (${item.origin})`).join(', ')}
      </p>
      <p className="caution">{result.caution}</p>
      {accepted && <button type="button" className="primary" disabled={busy}
        onClick={applyCandidate}>Apply this rewrite</button>}
    </div>}
  </form>;
}

function AssetDetail({detail, onProbe, onSaved, running}) {
  const {asset, columns, results, column_breakdown: breakdown} = detail;
  const enriched = results.map(result => {
    const probe = detail.probes.find(item => item.id === result.probe_id);
    return {...result, question: probe?.question};
  });
  return <section className="panel detail">
    <div className="section-title">
      <div>
        <p className="eyebrow">{asset.asset_type} · owner {asset.owner || 'unassigned'}</p>
        <h3>{asset.name}</h3>
      </div>
      <button className="secondary" onClick={onProbe} disabled={running}>
        {running ? 'Probing…' : 'Probe this asset'}
      </button>
    </div>
    <div className="detail-stats">
      <span>Column coverage<strong>{Math.round(detail.column_coverage * 100)}%</strong></span>
      <span>Documented<strong>{detail.described_columns}/{columns.length}</strong></span>
      <span>Downstream<strong>{detail.downstream_assets.length}</strong></span>
      <span>Probes<strong>{detail.probes.length}</strong></span>
    </div>
    {detail.downstream_assets.length > 0 && <p className="downstream">
      Feeds: {detail.downstream_assets.join(' → ')}
    </p>}
    {breakdown.length > 0 && <div className="breakdown">
      {breakdown.map(row => <div key={row.column} className={row.confident_wrong ? 'danger-row' : ''}>
        <code>{row.column}</code>
        <small>{row.description || 'No description'}</small>
        <span>{row.correct}/{row.probes} correct · {row.confident_wrong} wrong</span>
      </div>)}
    </div>}
    <ProbeResults results={enriched} />
    <RepairPanel asset={asset} breakdown={breakdown} onApplied={onSaved} />
    <FixPanel asset={asset} columns={columns} onSaved={onSaved} />
  </section>;
}

export default function App() {
  const [queue, setQueue] = useState([]);
  const [engineAvailable, setEngineAvailable] = useState(false);
  const [selectedId, setSelectedId] = useState('');
  const [detail, setDetail] = useState(null);
  const [pair, setPair] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');

  async function loadQueue() {
    const data = await api('/queue');
    setQueue(data.queue);
    setEngineAvailable(data.engine_available);
    return data.queue;
  }

  async function loadReport() {
    const data = await api('/report');
    setPair(data.paired_comparison);
  }

  // Clicking through the queue quickly can resolve responses out of order; only
  // the most recently requested asset may write to state.
  const latestRequest = useRef('');

  async function loadDetail(assetId) {
    if (!assetId) return;
    latestRequest.current = assetId;
    const data = await api(`/assets/${assetId}`);
    if (latestRequest.current === assetId) setDetail(data);
  }

  useEffect(() => {
    Promise.all([loadQueue(), loadReport()])
      .then(([items]) => {
        const first = items.find(item => item.probed) || items[0];
        if (first) setSelectedId(first.asset_id);
      })
      .catch(requestError => setError(requestError.message));
  }, []);

  useEffect(() => {
    loadDetail(selectedId).catch(requestError => setError(requestError.message));
  }, [selectedId]);

  async function refreshAll() {
    await Promise.all([loadQueue(), loadReport(), loadDetail(selectedId)]);
  }

  async function probeAll() {
    setRunning(true);
    setError('');
    try {
      await api('/probe', send('POST', {mode: 'auto'}));
      await refreshAll();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setRunning(false);
    }
  }

  async function probeAsset() {
    setRunning(true);
    setError('');
    try {
      await api(`/assets/${selectedId}/probe`, send('POST', {mode: 'auto'}));
      await refreshAll();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setRunning(false);
    }
  }

  async function saveAndRerun() {
    await api(`/assets/${selectedId}/probe`, send('POST', {mode: 'auto'}));
    await refreshAll();
  }

  return <div className="app">
    <Header engineAvailable={engineAvailable} onProbeAll={probeAll} running={running} />
    {error && <p className="error" role="alert">{error}</p>}
    <Playground engineAvailable={engineAvailable} />
    <PairedComparison pair={pair} />
    <div className="layout">
      <RiskQueue queue={queue} selectedId={selectedId} onSelect={setSelectedId} />
      {detail && <AssetDetail detail={detail} onProbe={probeAsset}
        onSaved={saveAndRerun} running={running} />}
    </div>
  </div>;
}
