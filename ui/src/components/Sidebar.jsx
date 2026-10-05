import React from 'react';
import { DEMO } from '../client.js';
import { kindOf } from '../kinds.js';

export default function Sidebar({ modes, modeId, setMode, sources, selected, toggleSource, resetSources, custom, health, job, onIngest }) {
  const points = health?.points ?? 0;
  const pct = job?.running && job.total ? Math.round((job.done / job.total) * 100) : 0;
  return (
    <aside className="side">
      <div className="brand">
        <div className="logo">QA</div>
        <div>
          <h1>QA Copilot</h1>
          <p>Hybrid RAG for QA engineers</p>
        </div>
      </div>
      <div className="side-scroll">
        <div className="sec-title">What do you want to do?</div>
        <div className="modes">
          {modes.map((m) => (
            <button key={m.id} className={`mode ${m.id === modeId ? 'on' : ''}`} onClick={() => setMode(m.id)}>
              <span className="mi">{m.icon}</span>
              <span>
                <b>{m.label}</b>
                <span>{m.description}</span>
              </span>
            </button>
          ))}
        </div>

        <div className="sec-title">
          <span>Knowledge sources</span>
          {custom && (
            <button className="linkbtn" onClick={resetSources}>
              reset to mode
            </button>
          )}
        </div>
        <div className="srcs">
          {sources.map((s) => {
            const k = kindOf(s.kind);
            const disabled = s.phase > 1;
            return (
              <label key={s.id} className={`src ${disabled || s.chunks === 0 ? 'off' : ''}`} title={`${s.description}\n${s.path}`}>
                <input type="checkbox" checked={!disabled && selected.includes(s.id)} disabled={disabled} onChange={() => toggleSource(s.id)} />
                <span className="si">{k.icon}</span>
                <span className="sl">{s.label}</span>
                {disabled ? <span className="tag">phase 2</span> : <span className="sc">{s.chunks ?? '—'}</span>}
              </label>
            );
          })}
        </div>

        <div className="sec-title">Index</div>
        <div className="index-card">
          <div className="big">{health ? points.toLocaleString() : '…'}</div>
          <div className="row">
            <span>chunks in the knowledge base</span>
          </div>
          {health?.indexed_at && (
            <div className="row">
              <span>indexed {new Date(health.indexed_at).toLocaleString()}</span>
            </div>
          )}
          {!DEMO && !health?.ingest_enabled && (
            <div className="row" style={{ marginTop: 8 }}>
              <span>Indexing runs locally — this deployment is read-only.</span>
            </div>
          )}
          {!DEMO && health?.ingest_enabled && (
            <>
              {job?.running ? (
                <>
                  <div className="row" style={{ marginTop: 8 }}>
                    <span>
                      {job.stage} {job.done}/{job.total}
                    </span>
                    <span>{pct}%</span>
                  </div>
                  <div className="progress">
                    <i style={{ width: `${pct}%` }} />
                  </div>
                </>
              ) : (
                <button className="ghost" style={{ marginTop: 10, width: '100%' }} onClick={onIngest}>
                  ↻ Re-index changed files
                </button>
              )}
              {job?.error && <div className="err" style={{ marginTop: 8 }}>{job.error}</div>}
            </>
          )}
          {DEMO && (
            <div className="row" style={{ marginTop: 8 }}>
              <span>Re-indexing runs on the self-hosted server only.</span>
            </div>
          )}
        </div>
      </div>
    </aside>
  );
}
