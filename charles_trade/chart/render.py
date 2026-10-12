"""Write a local Lightweight Charts page for 30-minute candles + regimes."""

from __future__ import annotations

import json
from pathlib import Path

from charles_trade.chart.bars_30m import Bar30m
from charles_trade.entities.data.regime import Regime

_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>__TITLE__</title>
  <script src="https://unpkg.com/lightweight-charts@5.0.9/dist/lightweight-charts.standalone.production.js"></script>
  <style>
    html, body { margin: 0; height: 100%; background: #ffffff; color: #1a1a1a; font-family: sans-serif; }
    #wrap { display: flex; height: 100%; }
    #main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
    #meta { padding: 8px 12px; font-size: 13px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
            border-bottom: 1px solid #e0e3eb; }
    #meta button { background: #f0f3fa; color: #1a1a1a; border: 1px solid #d1d4dc; border-radius: 4px; padding: 4px 10px; cursor: pointer; }
    #meta button.active { background: #2962ff; border-color: #2962ff; color: #fff; }
    #meta button:disabled { opacity: 0.5; cursor: default; }
    #status { color: #6a6d74; font-size: 12px; }
    #chartWrap { position: relative; flex: 1; min-height: 0; background: #ffffff; }
    #chart { position: absolute; inset: 0; z-index: 0; }
    #overlay {
      position: absolute; inset: 0; z-index: 2; pointer-events: none;
      overflow: hidden;
    }
    #overlay .band {
      position: absolute; top: 0; bottom: 28px;
      box-sizing: border-box;
    }
    #overlay .seg {
      position: absolute; height: 2px; pointer-events: none; z-index: 3;
      border-radius: 1px;
    }
    #overlay .seg.sigma { height: 1px; opacity: 0.9; }
    #overlay .vwap-poly {
      position: absolute; inset: 0; width: 100%; height: 100%;
      pointer-events: none; z-index: 3; overflow: visible;
    }
    #overlay .vp-tick {
      position: absolute; height: 2px; background: rgba(21, 101, 192, 0.55);
      border-radius: 1px;
    }
    #overlay .vp-tick.poc { background: rgba(21, 101, 192, 0.95); height: 3px; }
    #overlay .pending {
      position: absolute; top: 0; bottom: 28px; width: 0;
      border-left: 2px dashed #2962ff;
    }
    #side { width: 320px; border-left: 1px solid #e0e3eb; display: flex; flex-direction: column; background: #fafafa; }
    #side h3 { margin: 0; padding: 10px 12px; font-size: 13px; border-bottom: 1px solid #e0e3eb; }
    #list { flex: 1; overflow: auto; padding: 8px; min-height: 0; }
    #detail { border-top: 1px solid #e0e3eb; padding: 8px 10px; max-height: 42%; overflow: auto; font-size: 12px; }
    #detail h4 { margin: 0 0 6px; font-size: 12px; }
    #detail .stat { color: #6a6d74; margin-bottom: 2px; }
    #vpBars { margin-top: 8px; }
    #vpBars .vp-row { display: flex; align-items: center; gap: 4px; height: 3px; margin-bottom: 1px; }
    #vpBars .vp-price { width: 58px; text-align: right; font-size: 10px; color: #6a6d74; flex-shrink: 0; }
    #vpBars .vp-bar { height: 100%; background: #90caf9; border-radius: 1px; }
    #vpBars .vp-bar.poc { background: #1565c0; }
    .item { background: #ffffff; border: 1px solid #e0e3eb; border-radius: 4px; padding: 8px; margin-bottom: 8px; font-size: 12px; cursor: pointer; }
    .item.selected { border-color: #2962ff; box-shadow: inset 0 0 0 1px #2962ff; }
    .item .row { display: flex; justify-content: space-between; gap: 8px; align-items: center; }
    .item .vwap-line { color: #6a6d74; margin-top: 4px; font-size: 11px; }
    .tag { padding: 1px 6px; border-radius: 3px; font-weight: 600; text-transform: uppercase; }
    .tag.up { background: rgba(38,166,154,.2); color: #00897b; }
    .tag.down { background: rgba(239,83,80,.2); color: #e53935; }
    .tag.range { background: rgba(255,160,0,.2); color: #ef6c00; }
    .item button { background: transparent; border: 1px solid #d1d4dc; color: #1a1a1a; border-radius: 3px; cursor: pointer; }
  </style>
</head>
<body>
  <div id="wrap">
    <div id="main">
      <div id="meta">
        <span>__TITLE__</span>
        <button id="btnDraw" type="button">Draw regime</button>
        <button id="btnSave" type="button">Save</button>
        <button id="btnVp" type="button" class="active" title="Show / hide volume profile">VP</button>
        <button id="btnVwap" type="button" class="active" title="Show / hide VWAP">VWAP</button>
        <span id="status"></span>
      </div>
      <div id="chartWrap">
        <div id="chart"></div>
        <div id="overlay"></div>
      </div>
    </div>
    <div id="side">
      <h3>Regimes</h3>
      <div id="list"></div>
      <div id="detail">
        <h4>Selected</h4>
        <div id="detailBody" style="color:#6a6d74;">Click a regime</div>
      </div>
    </div>
  </div>
  <script>
    const candleData = __DATA__;
    const instrumentPk = __INSTRUMENT__;
    const apiBase = __API_BASE__;
    const tickSize = __TICK_SIZE__;
    const pricePrecision = Math.max(0, Math.ceil(-Math.log10(tickSize) - 1e-12));
    let regimes = __REGIMES__;
    let drawMode = false;
    let pendingStart = null;
    let selectedId = null;
    let applyingCrosshair = false;
    let lastSnapPrice = null;
    let lastSnapTime = null;
    let vpCache = {};
    let vwapCache = {};
    let showVp = true;
    let showVwap = true;

    const fmtNy = (unixSec) => new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(new Date(unixSec * 1000));

    const statusEl = document.getElementById('status');
    const setStatus = (msg) => { statusEl.textContent = msg || ''; };

    const overlay = document.getElementById('overlay');
    const chartWrap = document.getElementById('chartWrap');
    const BAR = 30 * 60;
    const timeIndex = new Map(candleData.map((b, i) => [b.time, i]));

    function snapToTick(price) {
      if (price === null || price === undefined || !Number.isFinite(price)) return null;
      const ticks = Math.round(price / tickSize);
      return Number((ticks * tickSize).toFixed(pricePrecision));
    }

    const chart = LightweightCharts.createChart(document.getElementById('chart'), {
      autoSize: true,
      layout: { background: { type: 'solid', color: '#ffffff' }, textColor: '#1a1a1a' },
      grid: { vertLines: { color: '#e0e3eb' }, horzLines: { color: '#e0e3eb' } },
      rightPriceScale: { borderColor: '#d1d4dc' },
      timeScale: { borderColor: '#d1d4dc', timeVisible: true, secondsVisible: false },
      localization: { timeFormatter: (time) => fmtNy(time) },
      // Normal: do not magnet-lock Y to OHLC; we snap Y to tick ourselves.
      crosshair: {
        mode: LightweightCharts.CrosshairMode.Normal,
        vertLine: { labelVisible: true },
        horzLine: {
          labelVisible: true,
          labelBackgroundColor: '#2962ff',
        },
      },
    });
    const candles = chart.addSeries(LightweightCharts.CandlestickSeries, {
      upColor: '#26a69a', downColor: '#ef5350',
      borderVisible: false,
      wickUpColor: '#26a69a', wickDownColor: '#ef5350',
      priceFormat: { type: 'price', precision: pricePrecision, minMove: tickSize },
    });

    function nearestIndex(unixSec) {
      if (timeIndex.has(unixSec)) return timeIndex.get(unixSec);
      let lo = 0, hi = candleData.length - 1;
      if (hi < 0) return null;
      if (unixSec <= candleData[0].time) return 0;
      if (unixSec >= candleData[hi].time) return hi;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        const t = candleData[mid].time;
        if (t === unixSec) return mid;
        if (t < unixSec) lo = mid + 1;
        else hi = mid - 1;
      }
      const a = Math.max(0, lo - 1), b = Math.min(candleData.length - 1, lo);
      return Math.abs(candleData[a].time - unixSec) <= Math.abs(candleData[b].time - unixSec) ? a : b;
    }

    function xAt(unixSec) {
      const ts = chart.timeScale();
      let x = ts.timeToCoordinate(unixSec);
      if (x !== null && x !== undefined) return x;
      const idx = nearestIndex(unixSec);
      if (idx === null) return null;
      x = ts.logicalToCoordinate(idx);
      return (x === null || x === undefined) ? null : x;
    }

    function clickTime(param) {
      if (typeof param.time === 'number') return param.time;
      if (param.point) {
        const t = chart.timeScale().coordinateToTime(param.point.x);
        if (typeof t === 'number') {
          const idx = nearestIndex(t);
          return idx === null ? null : candleData[idx].time;
        }
      }
      return null;
    }

    function bandColor(r) {
      if (r.color) return r.color.replace('0.22', '0.18');
      const code = Number(r.label);
      if (code === 1) return 'rgba(38, 166, 154, 0.18)';
      if (code === 2) return 'rgba(239, 83, 80, 0.18)';
      return 'rgba(255, 160, 0, 0.18)';
    }

    function labelName(r) {
      if (r.label_name) return r.label_name;
      const code = Number(r.label);
      if (code === 1) return 'up';
      if (code === 2) return 'down';
      if (code === 3) return 'range';
      return 'label_' + code;
    }

    function addSeg(x1, x2, y, color, dashed) {
      if (y === null || y === undefined || x1 === null || x2 === null) return;
      const left = Math.min(x1, x2);
      const width = Math.max(1, Math.abs(x2 - x1));
      const el = document.createElement('div');
      el.className = 'seg' + (dashed ? ' sigma' : '');
      el.style.left = left + 'px';
      el.style.width = width + 'px';
      el.style.top = (y - 1) + 'px';
      if (dashed) {
        el.style.background = 'repeating-linear-gradient(90deg,' + color + ' 0 5px, transparent 5px 9px)';
      } else {
        el.style.background = color;
      }
      overlay.appendChild(el);
    }

    function drawRegimeVwap(r) {
      const points = vwapCache[r.id];
      if (!points || points.length < 1) return;
      const toXY = (p) => {
        const x = xAt(p.time);
        const y = candles.priceToCoordinate(p.vwap);
        if (x === null || y === null || y === undefined) return null;
        return [x, y];
      };
      const toXYBand = (p, key, color) => {
        const x = xAt(p.time);
        const y = candles.priceToCoordinate(p[key]);
        if (x === null || y === null || y === undefined) return null;
        return [x, y];
      };
      const mkPoly = (coords, color, width, dash) => {
        if (coords.length < 2) return;
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('class', 'vwap-poly');
        const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
        poly.setAttribute('fill', 'none');
        poly.setAttribute('stroke', color);
        poly.setAttribute('stroke-width', String(width));
        if (dash) poly.setAttribute('stroke-dasharray', dash);
        poly.setAttribute('points', coords.map(([x, y]) => x + ',' + y).join(' '));
        svg.appendChild(poly);
        overlay.appendChild(svg);
      };
      const main = [];
      const hi = [];
      const lo = [];
      for (const p of points) {
        const a = toXY(p);
        if (a) main.push(a);
        const b = toXYBand(p, 'first_higher');
        if (b) hi.push(b);
        const c = toXYBand(p, 'first_lower');
        if (c) lo.push(c);
      }
      mkPoly(hi, '#2e7d32', 1, '4 4');
      mkPoly(lo, '#c62828', 1, '4 4');
      mkPoly(main, '#1565c0', 2, null);
      if (main.length === 1) {
        const [x, y] = main[0];
        addSeg(x - 4, x + 4, y, '#1565c0', false);
      }
    }

    function drawRegimeVp(r, x1, x2) {
      const levels = vpCache[r.id];
      if (!levels || !levels.length || x1 === null || x2 === null) return;
      let maxVol = 1;
      for (const lv of levels) maxVol = Math.max(maxVol, lv.total_vol);
      let poc = levels[0];
      for (const lv of levels) if (lv.total_vol > poc.total_vol) poc = lv;
      const bandW = Math.max(8, Math.abs(x2 - x1));
      const maxBar = Math.min(bandW * 0.85, 120);
      const right = Math.max(x1, x2);
      const step = Math.max(1, Math.floor(levels.length / 120));
      for (let i = 0; i < levels.length; i += step) {
        const lv = levels[i];
        const y = candles.priceToCoordinate(lv.price);
        if (y === null || y === undefined) continue;
        const w = Math.max(2, Math.round(maxBar * lv.total_vol / maxVol));
        const el = document.createElement('div');
        el.className = 'vp-tick' + (lv.price_raw === poc.price_raw ? ' poc' : '');
        el.style.top = (y - 1) + 'px';
        el.style.width = w + 'px';
        el.style.left = (right - w) + 'px';
        overlay.appendChild(el);
      }
    }

    function drawOverlay() {
      overlay.replaceChildren();
      if (!chart.timeScale().width()) return;
      for (const r of regimes) {
        const lastBarTs = Math.max(r.start_ts, r.end_ts - BAR);
        const x1 = xAt(r.start_ts);
        const x2 = xAt(lastBarTs);
        if (x1 === null || x2 === null) continue;
        const left = Math.min(x1, x2);
        const right = Math.max(x1, x2) + 8;
        const el = document.createElement('div');
        el.className = 'band';
        el.style.left = left + 'px';
        el.style.width = Math.max(6, right - left) + 'px';
        el.style.background = bandColor(r);
        if (selectedId !== null && r.id === selectedId) {
          el.style.outline = '1px solid #2962ff';
          el.style.outlineOffset = '-1px';
        }
        overlay.appendChild(el);
        if (showVp) drawRegimeVp(r, left, right);
        if (showVwap) drawRegimeVwap(r);
      }
      if (pendingStart !== null) {
        const x = xAt(pendingStart);
        if (x !== null) {
          const el = document.createElement('div');
          el.className = 'pending';
          el.style.left = x + 'px';
          overlay.appendChild(el);
        }
      }
    }

    function scheduleDraw() {
      requestAnimationFrame(drawOverlay);
    }

    function fmtPx(p) {
      if (p === null || p === undefined) return '—';
      return Number(p).toFixed(pricePrecision);
    }

    function renderVpSidebar(levels) {
      const body = document.getElementById('detailBody');
      if (!levels || !levels.length) {
        body.innerHTML += '<div class="stat" style="margin-top:8px;">No volume profile</div>';
        return;
      }
      let maxVol = 1;
      for (const lv of levels) maxVol = Math.max(maxVol, lv.total_vol);
      let poc = levels[0];
      for (const lv of levels) if (lv.total_vol > poc.total_vol) poc = lv;
      const step = Math.max(1, Math.floor(levels.length / 80));
      let html = '<div id="vpBars">';
      for (let i = levels.length - 1; i >= 0; i -= step) {
        const lv = levels[i];
        const w = Math.max(1, Math.round(100 * lv.total_vol / maxVol));
        const isPoc = lv.price_raw === poc.price_raw;
        html += `<div class="vp-row"><span class="vp-price">${fmtPx(lv.price)}</span>`
          + `<div class="vp-bar${isPoc ? ' poc' : ''}" style="width:${w}%"></div></div>`;
      }
      html += `</div><div class="stat" style="margin-top:6px;">POC ${fmtPx(poc.price)} · ${levels.length} levels</div>`;
      body.innerHTML += html;
    }

    async function renderDetail() {
      const body = document.getElementById('detailBody');
      const r = regimes.find((x) => x.id === selectedId);
      if (!r) {
        body.innerHTML = '<span style="color:#6a6d74;">Click a regime</span>';
        scheduleDraw();
        return;
      }
      body.innerHTML = `
        <div class="stat">VWAP ${fmtPx(r.vwap)}</div>
        <div class="stat">+1σ ${fmtPx(r.first_higher)} · −1σ ${fmtPx(r.first_lower)}</div>
        <div class="stat">Vol ${r.volume ?? 0} · Trades ${r.trade_count ?? 0}</div>
        <div class="stat" style="margin-top:6px;">Volume profile</div>`;
      if (!apiBase || r.id === null || r.id === undefined || r.id < 0) {
        body.innerHTML += '<div class="stat">Save to compute & load VP</div>';
        scheduleDraw();
        return;
      }
      if (!vpCache[r.id]) {
        body.innerHTML += '<div class="stat">Loading VP…</div>';
        try {
          const res = await fetch(apiBase + '/api/regime_vp?regime_id=' + encodeURIComponent(r.id));
          if (!res.ok) throw new Error(await res.text());
          vpCache[r.id] = await res.json();
        } catch (e) {
          body.innerHTML = body.innerHTML.replace('Loading VP…', 'VP failed: ' + e.message);
          scheduleDraw();
          return;
        }
      }
      body.innerHTML = `
        <div class="stat">VWAP ${fmtPx(r.vwap)}</div>
        <div class="stat">+1σ ${fmtPx(r.first_higher)} · −1σ ${fmtPx(r.first_lower)}</div>
        <div class="stat">Vol ${r.volume ?? 0} · Trades ${r.trade_count ?? 0}</div>
        <div class="stat" style="margin-top:6px;">Volume profile</div>`;
      renderVpSidebar(vpCache[r.id]);
      scheduleDraw();
    }

    function renderList() {
      const list = document.getElementById('list');
      list.innerHTML = '';
      const sorted = [...regimes].sort((a, b) => a.start_ts - b.start_ts);
      for (const r of sorted) {
        const div = document.createElement('div');
        div.className = 'item' + (selectedId !== null && r.id === selectedId ? ' selected' : '');
        const vwapTxt = (r.vwap !== null && r.vwap !== undefined)
          ? `VWAP ${fmtPx(r.vwap)}`
          : 'VWAP — (Save to compute)';
        div.innerHTML = `
          <div class="row">
            <span class="tag ${labelName(r)}">${labelName(r)}</span>
            <button data-id="${r.id}" class="del">Delete</button>
          </div>
          <div>${fmtNy(r.start_ts)} → ${fmtNy(r.end_ts)}</div>
          <div class="vwap-line">${vwapTxt}</div>`;
        div.querySelector('.del').onclick = async (e) => {
          e.stopPropagation();
          await deleteRegime(r);
        };
        div.onclick = (e) => {
          if (e.target.classList.contains('del')) return;
          selectedId = r.id;
          renderList();
          drawOverlay();
          renderDetail();
        };
        list.appendChild(div);
      }
      if (!sorted.length) {
        list.innerHTML = '<div style="color:#6a6d74;padding:8px;">No regimes yet. Click Draw regime, then two candles.</div>';
      }
    }

    function nextTempId() {
      const nums = regimes.map((r) => r.id).filter((x) => typeof x === 'number');
      return (nums.length ? Math.min(...nums, 0) : 0) - 1;
    }

    async function loadRegimeOverlays(r) {
      if (!apiBase || r.id === null || r.id === undefined || r.id < 0) return;
      try {
        const vpRes = await fetch(apiBase + '/api/regime_vp?regime_id=' + encodeURIComponent(r.id));
        if (vpRes.ok) vpCache[r.id] = await vpRes.json();
      } catch (e) {}
      try {
        const vwRes = await fetch(apiBase + '/api/regime_vwap?regime_id=' + encodeURIComponent(r.id));
        if (vwRes.ok) vwapCache[r.id] = await vwRes.json();
      } catch (e) {}
    }

    async function deleteRegime(r) {
      if (!r) return;
      const id = r.id;
      if (apiBase && typeof id === 'number' && id > 0) {
        try {
          const res = await fetch(
            apiBase + '/api/regimes/' + encodeURIComponent(id),
            { method: 'DELETE' }
          );
          if (!res.ok) throw new Error(await res.text());
        } catch (e) {
          setStatus('Delete failed: ' + e.message);
          return;
        }
      }
      regimes = regimes.filter((x) => x.id !== id);
      if (selectedId === id) selectedId = null;
      delete vpCache[id];
      delete vwapCache[id];
      renderList();
      drawOverlay();
      renderDetail();
      setStatus(typeof id === 'number' && id > 0 ? 'Deleted' : 'Removed unsaved regime');
    }

    function selectRegime(r) {
      if (!r) return;
      selectedId = r.id;
      renderList();
      drawOverlay();
      renderDetail();
      setStatus('Selected — press Delete to remove');
    }

    function regimeAtTime(t) {
      const hits = regimes.filter((r) => t >= r.start_ts && t < r.end_ts);
      if (!hits.length) return null;
      hits.sort((a, b) => (a.end_ts - a.start_ts) - (b.end_ts - b.start_ts));
      return hits[0];
    }

    // Subscribe BEFORE fitContent so the initial range/size events are caught.
    chart.timeScale().subscribeVisibleLogicalRangeChange(() => scheduleDraw());
    chart.timeScale().subscribeSizeChange(() => scheduleDraw());
    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(() => scheduleDraw()).observe(chartWrap);
    }
    window.addEventListener('resize', scheduleDraw);

    chart.subscribeCrosshairMove((param) => {
      if (applyingCrosshair) return;
      if (!param.point || param.time === undefined || param.time === null) {
        lastSnapPrice = null;
        lastSnapTime = null;
        return;
      }
      const t = typeof param.time === 'number' ? param.time : null;
      if (t === null) return;
      const raw = candles.coordinateToPrice(param.point.y);
      const snapped = snapToTick(raw);
      if (snapped === null) return;
      if (snapped === lastSnapPrice && t === lastSnapTime) return;
      lastSnapPrice = snapped;
      lastSnapTime = t;
      applyingCrosshair = true;
      try {
        // Keep vertical on bar time; place horizontal on tick-aligned price (right axis label).
        chart.setCrosshairPosition(snapped, t, candles);
      } finally {
        applyingCrosshair = false;
      }
    });

    chart.subscribeClick((param) => {
      const t = clickTime(param);
      if (!drawMode) {
        if (t === null) return;
        const hit = regimeAtTime(t);
        if (!hit) {
          selectedId = null;
          renderList();
          drawOverlay();
          renderDetail();
          setStatus('');
          return;
        }
        selectRegime(hit);
        return;
      }
      if (t === null) {
        setStatus('Click a candle body');
        return;
      }
      if (pendingStart === null) {
        pendingStart = t;
        setStatus(`Start ${fmtNy(t)} — click end candle`);
        drawOverlay();
        return;
      }
      let start = pendingStart;
      let end = t;
      if (end < start) [start, end] = [end, start];
      end = end + BAR;
      regimes.push({
        id: nextTempId(),
        instrument_pk: instrumentPk,
        start_ts: start,
        end_ts: end,
        label: 3,
        label_name: 'range',
        color: 'rgba(255, 160, 0, 0.22)',
      });
      pendingStart = null;
      setStatus('Regime added — click next start (Draw mode on)');
      renderList();
      drawOverlay();
    });

    window.addEventListener('keydown', async (e) => {
      if (e.key !== 'Delete' && e.key !== 'Backspace') return;
      const tag = (e.target && e.target.tagName) ? e.target.tagName.toLowerCase() : '';
      if (tag === 'input' || tag === 'textarea') return;
      if (selectedId === null || selectedId === undefined) return;
      const r = regimes.find((x) => x.id === selectedId);
      if (!r) return;
      e.preventDefault();
      await deleteRegime(r);
    });

    document.getElementById('btnDraw').onclick = () => {
      drawMode = !drawMode;
      pendingStart = null;
      document.getElementById('btnDraw').classList.toggle('active', drawMode);
      setStatus(
        drawMode
          ? 'Draw mode: click start → end (repeat); click Draw again to exit'
          : 'Draw mode off'
      );
      drawOverlay();
    };

    document.getElementById('btnVp').onclick = () => {
      showVp = !showVp;
      document.getElementById('btnVp').classList.toggle('active', showVp);
      drawOverlay();
      setStatus(showVp ? 'VP shown' : 'VP hidden');
    };

    document.getElementById('btnVwap').onclick = () => {
      showVwap = !showVwap;
      document.getElementById('btnVwap').classList.toggle('active', showVwap);
      drawOverlay();
      setStatus(showVwap ? 'VWAP shown' : 'VWAP hidden');
    };

    document.getElementById('btnSave').onclick = async () => {
      if (!apiBase) {
        setStatus('Open via python scripts/serve_chart.py to save');
        return;
      }
      const pending = regimes.filter((r) => typeof r.id === 'number' && r.id < 0);
      if (!pending.length) {
        setStatus('Nothing new to save');
        return;
      }
      setStatus('Saving ' + pending.length + ' new regime(s)…');
      try {
        const created = [];
        for (const r of pending) {
          const res = await fetch(
            apiBase + '/api/regimes?instrument_pk=' + encodeURIComponent(instrumentPk),
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ start_ts: r.start_ts, end_ts: r.end_ts }),
            }
          );
          if (!res.ok) throw new Error(await res.text());
          created.push(await res.json());
        }
        const kept = regimes.filter((r) => !(typeof r.id === 'number' && r.id < 0));
        regimes = kept.concat(created);
        for (const r of created) await loadRegimeOverlays(r);
        selectedId = null;
        renderList();
        drawOverlay();
        renderDetail();
        setStatus('Saved ' + created.length + ' new regime(s)');
      } catch (e) {
        setStatus('Save failed: ' + e.message);
      }
    };

    candles.setData(candleData);
    chart.timeScale().fitContent();
    renderList();
    renderDetail();
    scheduleDraw();
    setTimeout(scheduleDraw, 0);
    setTimeout(scheduleDraw, 100);
    (async () => {
      if (!apiBase) return;
      for (const r of regimes) await loadRegimeOverlays(r);
      scheduleDraw();
      if (selectedId !== null) renderDetail();
    })();

    async function applyTipBars(bars) {
      if (!bars || !bars.length) return;
      let changed = false;
      for (const b of bars) {
        const bar = { time: b.time, open: b.open, high: b.high, low: b.low, close: b.close };
        const idx = timeIndex.get(b.time);
        if (idx !== undefined) {
          const prev = candleData[idx];
          if (prev.open !== bar.open || prev.high !== bar.high || prev.low !== bar.low || prev.close !== bar.close) {
            candleData[idx] = bar;
            candles.update(bar);
            changed = true;
          }
        } else if (!candleData.length || b.time > candleData[candleData.length - 1].time) {
          candleData.push(bar);
          timeIndex.set(b.time, candleData.length - 1);
          candles.update(bar);
          changed = true;
        }
      }
      if (changed) scheduleDraw();
    }

    async function refreshTipFromApi() {
      if (!apiBase) return;
      try {
        const res = await fetch(
          apiBase + '/api/bars?instrument_pk=' + encodeURIComponent(instrumentPk) + '&tip=8'
        );
        if (!res.ok) return;
        const data = await res.json();
        await applyTipBars(data.bars || []);
      } catch (e) {}
    }

    if (apiBase && typeof EventSource !== 'undefined') {
      const es = new EventSource(apiBase + '/api/events');
      es.addEventListener('hello', () => {
        setStatus('Live SSE connected');
      });
      es.addEventListener('bars_updated', async (ev) => {
        let info = {};
        try { info = JSON.parse(ev.data); } catch (e) {}
        await refreshTipFromApi();
        const wm = info.watermark ? (' · ' + info.watermark) : '';
        setStatus('Live updated' + wm);
      });
      es.onerror = () => {
        setStatus('Live SSE reconnecting…');
      };
    } else {
      setStatus('Ready — click band to select, Delete to remove · VP/VWAP toggles');
    }
    if (!apiBase) setStatus('Ready — Draw regime → two clicks → Save');
  </script>
</body>
</html>
"""


def render_30m_html(
    bars: list[Bar30m],
    *,
    title: str,
    path: Path,
    instrument_pk: str = "6E",
    regimes: list[Regime] | None = None,
    api_base: str = "",
    tick_size: float = 0.00005,
) -> Path:
    payload = [
        {
            "time": bar.time,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
        }
        for bar in bars
    ]
    regime_payload = [r.to_dict() for r in (regimes or [])]
    html = (
        _TEMPLATE.replace("__TITLE__", title)
        .replace("__DATA__", json.dumps(payload))
        .replace("__REGIMES__", json.dumps(regime_payload))
        .replace("__INSTRUMENT__", json.dumps(instrument_pk))
        .replace("__API_BASE__", json.dumps(api_base))
        .replace("__TICK_SIZE__", json.dumps(tick_size))
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
