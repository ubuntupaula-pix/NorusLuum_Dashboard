/* ============================================================
   NORUS & LUUM · Mercado
   Comparación de largo plazo contra referencias públicas
   registradas, importadas o sincronizadas desde APIs públicas sin
   clave. Nunca se inventan valores: los períodos sin fuente se
   muestran vacíos y cada dato conserva su atribución.
   ============================================================ */
(function () {
  'use strict';

  const $ = (s) => document.querySelector(s);
  const canEdit = () => ['admin', 'editor'].includes(sessionStorage.getItem('norus_role'));
  const currency = () => ($('#moneda') ? $('#moneda').value : 'CLP') || 'CLP';
  const metric = () => ($('#marketMetric') ? $('#marketMetric').value : 'adr') || 'adr';
  const granularity = () => ($('#marketGranularity') ? $('#marketGranularity').value : 'mes') || 'mes';
  const selectedProperty = () => ($('#marketProperty') ? $('#marketProperty').value : '');
  const monthFrom = () => (($('#desde') ? $('#desde').value : '') || '').slice(0, 7);
  const monthTo = () => (($('#hasta') ? $('#hasta').value : '') || '').slice(0, 7);

  let chart = null;
  let references = [];

  const METRIC_LABEL = {
    adr: 'ADR',
    precio: 'Precio largo plazo',
    revpar: 'RevPAR',
    ocupacion: 'Ocupación'
  };

  function notify(msg, tipo) {
    if (typeof window.notificar === 'function') window.notificar(msg, tipo || 'info');
    else console.info(msg);
  }

  function fmtValue(value) {
    if (value === null || value === undefined || !isFinite(value)) return '—';
    if (metric() === 'ocupacion') return Number(value).toFixed(1) + '%';
    return currency() + ' ' + Number(value).toLocaleString('es-CL', { maximumFractionDigits: 0 });
  }

  function fmtGap(value) {
    if (value === null || value === undefined || !isFinite(value)) return '—';
    return (value >= 0 ? '+' : '') + Number(value).toFixed(1) + '%';
  }

  function cell(row, text) {
    const td = document.createElement('td');
    td.textContent = text;
    row.appendChild(td);
    return td;
  }

  function renderKpis(data) {
    const container = $('#marketKpis');
    if (!container) return;
    const promedio = data.promedio || {};
    const cards = [
      ['Promedio hotel', fmtValue(promedio.hotel), 'teal'],
      ['Promedio mercado', fmtValue(promedio.mercado), 'accent'],
      ['Brecha vs mercado', fmtGap(promedio.brecha_pct), (promedio.brecha_pct || 0) >= 0 ? 'green' : 'red'],
      ['Referencias usadas', data.referencias ? data.referencias.length : 0, '']
    ];
    container.replaceChildren();
    cards.forEach(([label, value, cls]) => {
      const div = document.createElement('div');
      div.className = 'kpi ' + (cls || '');
      const l = document.createElement('div'); l.className = 'kpi-label'; l.textContent = label;
      const v = document.createElement('div'); v.className = 'kpi-value'; v.textContent = value;
      div.append(l, v);
      container.appendChild(div);
    });
    const note = $('#marketNote');
    if (note) note.textContent = data.nota || '';
  }

  function renderChart(data) {
    const canvas = $('#chartMarket');
    if (!canvas || typeof Chart === 'undefined') return;
    if (chart) { chart.destroy(); chart = null; }
    const suffix = metric() === 'ocupacion' ? '%' : ' ' + currency();
    chart = new Chart(canvas.getContext('2d'), {
      type: 'line',
      data: {
        labels: data.periodos || [],
        datasets: [
          {
            label: 'Hotel',
            data: data.hotel || [],
            borderColor: '#1e3a4a',
            backgroundColor: 'rgba(30,58,74,0.10)',
            fill: true,
            tension: .35,
            borderWidth: 2.5,
            spanGaps: true
          },
          {
            label: 'Mercado',
            data: data.mercado || [],
            borderColor: '#a98b5f',
            borderDash: [5, 4],
            tension: .35,
            borderWidth: 2.5,
            spanGaps: true
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { position: 'bottom' } },
        scales: {
          y: {
            grid: { color: 'rgba(19,35,46,0.07)' },
            ticks: { callback: v => v + suffix }
          },
          x: { grid: { display: false } }
        }
      }
    });
  }

  function renderTable() {
    const tbody = $('#marketTable tbody');
    if (!tbody) return;
    tbody.replaceChildren();
    references.forEach(item => {
      const tr = document.createElement('tr');
      cell(tr, item.periodo || '—');
      cell(tr, METRIC_LABEL[item.metrica] || item.metrica);
      const value = item.metrica === 'ocupacion'
        ? Number(item.valor).toFixed(1) + '%'
        : (item.moneda || 'CLP') + ' ' + Number(item.valor).toLocaleString('es-CL', { maximumFractionDigits: 0 });
      cell(tr, value);
      if (item.url) {
        const td = document.createElement('td');
        const a = document.createElement('a');
        a.href = item.url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        a.textContent = item.fuente || 'Fuente';
        td.appendChild(a);
        tr.appendChild(td);
      } else {
        cell(tr, item.fuente || '—');
      }
      cell(tr, item.propiedad || 'Global');
      const td = document.createElement('td');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn btn-ghost';
      btn.textContent = 'Eliminar';
      btn.disabled = !canEdit();
      btn.addEventListener('click', async () => {
        if (!window.confirm('¿Eliminar esta referencia de mercado?')) return;
        try {
          await window.API.marketReferenceDelete(item.id);
          notify('Referencia eliminada.', 'success');
          await load();
        } catch (e) { notify('No se pudo eliminar: ' + e.message, 'error'); }
      });
      td.appendChild(btn);
      tr.appendChild(td);
      tbody.appendChild(tr);
    });
    const empty = $('#marketTableEmpty');
    if (empty) empty.hidden = references.length > 0;
    const count = $('#marketCount');
    if (count) count.textContent = String(references.length);
  }

  async function loadComparison() {
    try {
      const data = await window.API.marketComparison({
        metrica: metric(),
        propiedad_id: selectedProperty(),
        moneda: currency(),
        granularidad: granularity(),
        desde: monthFrom(),
        hasta: monthTo()
      });
      renderKpis(data);
      renderChart(data);
      references = data.referencias || [];
      renderTable();
      populateProperties(data.propiedades || []);
    } catch (e) {
      notify('No se pudo cargar la comparación de mercado: ' + e.message, 'error');
      references = [];
      renderTable();
      const note = $('#marketNote');
      if (note) note.textContent = 'No se pudo cargar la comparación: ' + e.message;
    }
  }

  function populateProperties(propiedades) {
    ['#marketProperty', '#marketFormProperty'].forEach(selector => {
      const select = $(selector);
      if (!select) return;
      const current = select.value;
      const first = select.options.length ? select.options[0].textContent : 'Todas las propiedades';
      select.replaceChildren(new Option(first, ''));
      propiedades.forEach(p => select.add(new Option(p.nombre, p.id)));
      if (current) select.value = current;
    });
  }

  async function loadPublicSources() {
    const note = $('#marketPublicNote');
    if (!note || typeof window.API.marketPublicSources !== 'function') return;
    try {
      const sources = await window.API.marketPublicSources();
      const active = sources.filter(s => s.activa).map(s => s.nombre);
      if (active.length) {
        note.textContent = 'Fuentes públicas sin clave activas: ' + active.join(', ') +
          '. Sincronización manual con atribución, URL, período y moneda verificables.';
      }
    } catch (_) {
      // Se conserva el texto informativo del HTML si falla la consulta.
    }
  }

  async function syncPublicSources() {
    const button = $('#marketPublicRefresh');
    if (!button || !canEdit()) return;
    button.disabled = true;
    try {
      const result = await window.API.marketPublicRefresh({
        moneda: currency(),
        propiedad_id: selectedProperty()
      });
      const ok = (result.fuentes || []).filter(f => f.estado === 'ok');
      const failed = (result.fuentes || []).filter(f => f.estado === 'error');
      let message = 'Sincronización ' + result.estado + ': ' + result.creadas +
        ' referencias nuevas y ' + result.actualizadas + ' actualizadas.';
      if (ok.length) message += ' Fuentes: ' + ok.map(f => f.nombre).join(', ') + '.';
      if (failed.length) message += ' Sin datos de: ' + failed.map(f => f.nombre).join(', ') + '.';
      notify(message, result.estado === 'fallida' ? 'error' : 'success');
      await loadPublicSources();
      await loadComparison();
    } catch (e) {
      notify('No se pudo sincronizar la API pública: ' + e.message, 'error');
    } finally {
      button.disabled = false;
    }
  }

  function init() {
    const publicButton = $('#marketPublicRefresh');
    if (publicButton) {
      publicButton.disabled = !canEdit();
      publicButton.addEventListener('click', syncPublicSources);
    }
    loadPublicSources();

    const form = $('#marketForm');
    if (form) {
      form.addEventListener('submit', async event => {
        event.preventDefault();
        if (!canEdit()) return;
        const body = Object.fromEntries(new FormData(form).entries());
        body.moneda = (body.moneda || 'CLP').toUpperCase();
        body.valor = Number(body.valor);
        if (body.propiedad_id) body.propiedad_id = Number(body.propiedad_id);
        else delete body.propiedad_id;
        try {
          await window.API.marketReferenceCreate(body);
          notify('Referencia registrada.', 'success');
          form.reset();
          const money = form.querySelector('[name="moneda"]');
          if (money) money.value = 'CLP';
          await loadComparison();
        } catch (e) { notify('No se pudo guardar la referencia: ' + e.message, 'error'); }
      });
    }

    const fileInput = $('#marketFile');
    if (fileInput) {
      fileInput.addEventListener('change', async () => {
        if (!canEdit()) return;
        const file = fileInput.files && fileInput.files[0];
        if (!file) return;
        try {
          const result = await window.API.marketImport(file, selectedProperty());
          notify(
            'Importación: ' + result.exitosas + ' referencias cargadas, ' +
            result.fallidas + ' rechazadas.',
            result.fallidas ? 'info' : 'success'
          );
          if (result.errores && result.errores.length) console.warn('Errores de importación:', result.errores);
          await loadComparison();
        } catch (e) {
          notify('No se pudo importar el archivo: ' + e.message, 'error');
        } finally {
          fileInput.value = '';
        }
      });
    }

    ['marketMetric', 'marketProperty', 'marketGranularity'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('change', loadComparison);
    });
    const refresh = $('#marketRefresh');
    if (refresh) refresh.addEventListener('click', loadComparison);

    ['moneda', 'desde', 'hasta'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('change', () => {
        const view = document.getElementById('view-mercado');
        if (view && view.classList.contains('active')) loadComparison();
      });
    });
  }

  init();
  window.Mercado = { load: loadComparison };
})();
