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
  // El rol definitivo llega de /api/auth/me después del arranque; se mantiene
  // actualizado con refreshPermissions() para evitar botones desactivados por
  // una lectura temprana de sessionStorage.
  let roleActual = window.__norusRole || sessionStorage.getItem('norus_role') || '';
  const canEdit = () => ['admin', 'editor'].includes(roleActual);
  const currency = () => ($('#moneda') ? $('#moneda').value : 'CLP') || 'CLP';
  const metric = () => ($('#marketMetric') ? $('#marketMetric').value : 'adr') || 'adr';
  const granularity = () => ($('#marketGranularity') ? $('#marketGranularity').value : 'mes') || 'mes';
  const selectedProperty = () => ($('#marketProperty') ? $('#marketProperty').value : '');
  const monthFrom = () => (($('#desde') ? $('#desde').value : '') || '').slice(0, 7);
  const monthTo = () => (($('#hasta') ? $('#hasta').value : '') || '').slice(0, 7);

  let chart = null;
  let references = [];
  let rtTimer = null;
  let rtEnCurso = false;
  let rtPendiente = false;
  let rtErrores = 0;
  let avisoDiarioHecho = false;

  // ---------- Caché local para evitar el estado en blanco ----------
  function marketCacheKey(tipo, ventana) {
    return window.NorusCache.key(
      tipo, roleActual, metric(), selectedProperty(), currency(), granularity(),
      monthFrom(), monthTo(), ventana || ''
    );
  }

  function edadSegundos(payload) {
    if (!payload || !payload.generado_en) return Infinity;
    const fecha = Date.parse(payload.generado_en);
    return isFinite(fecha) ? (Date.now() - fecha) / 1000 : Infinity;
  }

  function restaurarCachesMercado() {
    if (!window.NorusCache) return;
    const comparacion = window.NorusCache.get(marketCacheKey('mercado_comparacion'));
    if (comparacion) aplicarComparacion(comparacion, true);
    const ventana = ($('#rtVentana') ? $('#rtVentana').value : '24h') || '24h';
    const realtime = window.NorusCache.get(marketCacheKey('mercado_realtime', ventana));
    if (realtime) renderRealtime(realtime, true);
  }

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

  function aplicarComparacion(data, desdeCache) {
    renderKpis(data);
    renderChart(data);
    references = data.referencias || [];
    renderTable();
    populateProperties(data.propiedades || []);
    if (desdeCache) {
      const note = $('#marketNote');
      if (note) note.textContent = 'Últimos datos guardados · actualizando…';
    }
  }

  async function loadComparison() {
    const clave = marketCacheKey('mercado_comparacion');
    const cached = window.NorusCache ? window.NorusCache.get(clave) : null;
    if (cached) aplicarComparacion(cached, true);
    try {
      const data = await window.API.marketComparison({
        metrica: metric(),
        propiedad_id: selectedProperty(),
        moneda: currency(),
        granularidad: granularity(),
        desde: monthFrom(),
        hasta: monthTo()
      });
      aplicarComparacion(data, false);
      if (window.NorusCache) window.NorusCache.set(clave, data);
    } catch (e) {
      notify('No se pudo cargar la comparación de mercado: ' + e.message, 'error');
      if (!cached) {
        references = [];
        renderTable();
        const note = $('#marketNote');
        if (note) note.textContent = 'No se pudo cargar la comparación: ' + e.message;
      }
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
          '. El análisis en tiempo real las consulta bajo demanda (caché corta); la sincronización manual guarda referencias con URL, período y moneda verificables.';
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

  // ---------- Inteligencia de mercado en casi tiempo real ----------
  const RT_ESTADOS = {
    en_vivo: 'en vivo', parcial: 'parcial', historico: 'histórico', sin_datos: 'sin datos'
  };

  function fmtRT(value, tipo) {
    if (value === null || value === undefined || !isFinite(value)) return '—';
    if (tipo === 'pct') return (value >= 0 ? '+' : '') + Number(value).toFixed(1) + '%';
    if (tipo === 'pp') return (value >= 0 ? '+' : '') + Number(value).toFixed(1) + ' pp';
    if (tipo === 'num') return Number(value).toLocaleString('es-CL');
    return currency() + ' ' + Number(value).toLocaleString('es-CL', { maximumFractionDigits: 0 });
  }

  function renderRealtime(data, desdeCache) {
    const estado = $('#rtEstado');
    if (estado) {
      estado.textContent = RT_ESTADOS[data.estado] || data.estado || '—';
      estado.style.color = data.estado === 'en_vivo' ? '#3e7a5a'
        : data.estado === 'parcial' ? '#a98b5f' : '#a9584d';
    }
    const cont = $('#rtKpis');
    if (cont) {
      const interno = data.interno || {};
      const externo = data.externo || {};
      const comp = data.comparativa || {};
      const cards = [
        ['ADR hotel', fmtRT(interno.adr, 'money'), 'teal'],
        ['ADR mercado', fmtRT(externo.adr, 'money'), 'accent'],
        ['Brecha ADR', fmtRT(comp.brecha_adr_pct, 'pct'), (comp.brecha_adr_pct || 0) >= 0 ? 'green' : 'red'],
        ['Ocupación vs mercado', fmtRT(comp.brecha_ocupacion_pp, 'pp'), (comp.brecha_ocupacion_pp || 0) >= 0 ? 'green' : 'red'],
        ['Reservas ' + (interno.ventana_horas || 24) + ' h', fmtRT(interno.reservas_recientes, 'num'), ''],
        ['Pickup reservas', fmtRT(interno.variacion_reservas_pct, 'pct'), (interno.variacion_reservas_pct || 0) >= 0 ? 'green' : 'red']
      ];
      cont.replaceChildren();
      cards.forEach(([label, value, cls]) => {
        const div = document.createElement('div');
        div.className = 'kpi ' + (cls || '');
        const l = document.createElement('div'); l.className = 'kpi-label'; l.textContent = label;
        const v = document.createElement('div'); v.className = 'kpi-value'; v.textContent = value;
        div.append(l, v);
        cont.appendChild(div);
      });
    }
    const frescura = $('#rtFrescura');
    if (frescura) {
      const antiguedad = data.antiguedad_segundos == null ? null : Number(data.antiguedad_segundos);
      const detalle = 'caché ' + (data.cache || '—') +
        (antiguedad == null ? '' : ' · antigüedad ' + antiguedad + ' s') +
        (data.generado_en ? ' · leído ' + String(data.generado_en).replace('T', ' ').slice(0, 19) : '');
      frescura.textContent = desdeCache ? 'Últimos datos guardados · actualizando… · ' + detalle : detalle;
    }
    const senales = $('#rtSenales');
    if (senales) {
      senales.replaceChildren();
      (data.senales || []).forEach(s => {
        const chip = document.createElement('span');
        chip.className = 'chip-chip';
        chip.style.borderLeft = '3px solid ' + (s.severidad === 'alerta' ? '#a9584d'
          : s.severidad === 'oportunidad' ? '#3e7a5a' : '#a98b5f');
        chip.style.padding = '6px 10px';
        chip.textContent = s.mensaje || s.codigo || '';
        chip.title = s.codigo || '';
        senales.appendChild(chip);
      });
    }
    const nota = $('#rtNota');
    if (nota) nota.textContent = data.nota || '';
  }

  function programarRealtime() {
    if (rtTimer) clearTimeout(rtTimer);
    const espera = rtErrores ? Math.min(300000, 60000 * Math.pow(2, rtErrores)) : 60000;
    rtTimer = setTimeout(() => loadRealtime(false), espera);
  }

  async function loadRealtime(force) {
    if (rtEnCurso) { rtPendiente = rtPendiente || Boolean(force); return; }
    const view = document.getElementById('view-mercado');
    if (!force && (document.hidden || !view || !view.classList.contains('active'))) {
      programarRealtime();
      return;
    }
    const ventana = ($('#rtVentana') ? $('#rtVentana').value : '24h') || '24h';
    const clave = marketCacheKey('mercado_realtime', ventana);
    const cached = window.NorusCache ? window.NorusCache.get(clave) : null;
    const antiguedad = edadSegundos(cached);
    if (cached && !force) renderRealtime(cached, true);
    // Garantía de actualización diaria aunque no haya cron disponible: si el
    // último snapshot válido tiene más de 24 h, se fuerza lectura de fuentes.
    const necesitaDiario = antiguedad > 86400;
    if (necesitaDiario && !avisoDiarioHecho) {
      avisoDiarioHecho = true;
      notify('El análisis de mercado tiene más de 24 h; actualizando fuentes.', 'info');
    }
    rtEnCurso = true;
    try {
      const data = await window.API.marketRealtime({
        propiedad_id: selectedProperty(),
        moneda: currency(),
        ventana,
        refresh: force || necesitaDiario ? 1 : undefined
      });
      rtErrores = 0;
      renderRealtime(data, false);
      if (window.NorusCache) window.NorusCache.set(clave, data);
    } catch (e) {
      rtErrores += 1;
      const estado = $('#rtEstado');
      if (estado) { estado.textContent = 'sin conexión'; estado.style.color = '#a9584d'; }
      const nota = $('#rtNota');
      if (nota) nota.textContent = 'No se pudo actualizar el análisis en tiempo real: ' + e.message +
        '. Se reintentará automáticamente.';
      console.warn('Mercado tiempo real:', e);
    } finally {
      rtEnCurso = false;
      programarRealtime();
      if (rtPendiente) { rtPendiente = false; loadRealtime(true); }
    }
  }

  function refreshPermissions(rol) {
    roleActual = rol || '';
    const publico = $('#marketPublicRefresh');
    if (publico) publico.disabled = !canEdit();
    const save = $('#marketSave');
    if (save) save.disabled = !canEdit();
    const file = $('#marketFile');
    if (file) file.disabled = !canEdit();
    renderTable();
  }

  async function load() {
    await Promise.all([loadComparison(), loadRealtime(false)]);
  }

  function init() {
    restaurarCachesMercado();
    const publicButton = $('#marketPublicRefresh');
    if (publicButton) publicButton.addEventListener('click', syncPublicSources);
    refreshPermissions(roleActual);
    const rtVentana = $('#rtVentana');
    if (rtVentana) rtVentana.addEventListener('change', () => loadRealtime(true));
    const rtRefresh = $('#rtRefresh');
    if (rtRefresh) rtRefresh.addEventListener('click', () => loadRealtime(true));
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
  window.Mercado = { load, loadComparison, loadRealtime, refreshPermissions };
})();
