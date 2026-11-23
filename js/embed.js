/* ============================================================
   NORUS & LUUM · Widget embebible de mercado en tiempo real
   El token se recibe por postMessage y vive solo en memoria.
   ============================================================ */
(function () {
  'use strict';

  const ENDPOINT = '/api/mercado/tiempo-real';
  const CONFIG_URL = '/api/embed/config';
  const POLL_MS = 60000;
  const ESTADOS = {
    en_vivo: 'En vivo',
    parcial: 'Parcial',
    historico: 'Último válido',
    sin_datos: 'Sin datos'
  };

  let token = '';
  let claims = {};
  let parentOrigin = '';
  let allowed = [];
  let chart = null;
  let timer = null;
  let inflight = false;
  let reintentos = 0;
  let opciones = { propiedad_id: '', ventana: '24h' };

  const $ = (id) => document.getElementById(id);

  function decodeJwt(value) {
    try {
      const payload = value.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
      const padded = payload + '='.repeat((4 - (payload.length % 4)) % 4);
      const bytes = atob(padded);
      const json = decodeURIComponent(Array.prototype.map.call(bytes, (c) =>
        '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2)
      ).join(''));
      return JSON.parse(json);
    } catch (_) { return {}; }
  }

  function esOrigenPermitido(origin) {
    return allowed.some((item) => item.replace(/\/$/, '') === String(origin || '').replace(/\/$/, ''));
  }

  function destino() {
    if (claims.parent && esOrigenPermitido(claims.parent)) return claims.parent;
    if (parentOrigin && esOrigenPermitido(parentOrigin)) return parentOrigin;
    return allowed[0] || '*';
  }

  function avisar(payload) {
    if (window.parent === window) return;
    window.parent.postMessage(Object.assign({ tipo: 'norus:evento', origen: 'norus' }, payload), destino());
  }

  function estado(texto, tipo) {
    const el = $('norusStatus');
    el.textContent = texto;
    if (tipo) el.dataset.estado = tipo;
  }

  function numero(value, decimales) {
    if (value === null || value === undefined || !isFinite(value)) return '—';
    return Number(value).toLocaleString('es-CL', { maximumFractionDigits: decimales === undefined ? 1 : decimales });
  }

  function fmt(value, moneda, porcentaje) {
    if (value === null || value === undefined || !isFinite(value)) return '—';
    if (porcentaje) return (value >= 0 ? '+' : '') + Number(value).toFixed(1) + '%';
    return (moneda || '') + ' ' + numero(value, 0);
  }

  function renderKpis(data) {
    const moneda = data.moneda || 'CLP';
    const interno = data.interno || {};
    const externo = data.externo || {};
    const cmp = data.comparativa || {};
    const cards = [
      ['ADR hotel', fmt(interno.adr, moneda), ''],
      ['ADR mercado', fmt(externo.adr, moneda), ''],
      ['Brecha ADR', fmt(cmp.brecha_adr_pct, moneda, true),
        (cmp.brecha_adr_pct || 0) >= 10 ? 'alerta' : (cmp.brecha_adr_pct || 0) <= -10 ? 'oportunidad' : ''],
      ['Ocupación hotel', numero(interno.ocupacion) + '%', ''],
      ['Brecha ocupación', cmp.brecha_ocupacion_pp === null || cmp.brecha_ocupacion_pp === undefined
        ? '—' : numero(cmp.brecha_ocupacion_pp) + ' pp', ''],
      ['Pickup reservas', fmt(interno.variacion_reservas_pct, moneda, true), '']
    ];
    const contenedor = $('norusKpis');
    contenedor.replaceChildren();
    cards.forEach(([label, valor, tono]) => {
      const div = document.createElement('div');
      div.className = 'norus-kpi';
      if (tono) div.dataset.tono = tono;
      const l = document.createElement('span'); l.textContent = label;
      const v = document.createElement('strong'); v.textContent = valor;
      div.append(l, v);
      contenedor.appendChild(div);
    });
    $('norusMoneda').textContent = moneda;
  }

  function renderChart(data) {
    if (typeof Chart === 'undefined') return;
    const canvas = $('norusChart');
    const interno = data.interno || {};
    const externo = data.externo || {};
    if (chart) { chart.destroy(); chart = null; }
    chart = new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: {
        labels: ['Hotel', 'Mercado'],
        datasets: [
          { label: 'ADR', data: [interno.adr, externo.adr], backgroundColor: '#167c80', borderRadius: 5, yAxisID: 'y' },
          { label: 'Ocupación %', data: [interno.ocupacion, externo.ocupacion], backgroundColor: '#a98b5f', borderRadius: 5, yAxisID: 'y1' }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom' }, tooltip: { callbacks: { label: (ctx) => ctx.dataset.label + ': ' + (ctx.parsed.y === null ? '—' : ctx.parsed.y.toLocaleString('es-CL', { maximumFractionDigits: 1 })) } } },
        scales: {
          y: { beginAtZero: true, grid: { color: 'rgba(20,43,54,0.07)' } },
          y1: { position: 'right', beginAtZero: true, max: 100, grid: { drawOnChartArea: false } },
          x: { grid: { display: false } }
        }
      }
    });
  }

  function renderSignals(data) {
    const lista = $('norusSignals');
    lista.replaceChildren();
    const senales = data.senales || [];
    if (!senales.length) {
      const li = document.createElement('li');
      li.className = 'norus-muted';
      li.textContent = 'Sin señales relevantes.';
      lista.appendChild(li);
      return;
    }
    senales.forEach((s) => {
      const li = document.createElement('li');
      li.dataset.severidad = s.severidad || 'info';
      li.textContent = s.mensaje || s.codigo || '';
      lista.appendChild(li);
    });
  }

  function render(data) {
    const clave = data.estado || 'sin_datos';
    estado(ESTADOS[clave] || clave, clave);
    const segundos = data.antiguedad_segundos || 0;
    $('norusUpdated').textContent = 'Actualizado ' + data.generado_en +
      (segundos ? ' · hace ' + segundos + ' s' : '') +
      (data.cache === 'hit' ? ' · caché corta' : '') +
      (data.cache === 'stale' ? ' · datos históricos' : '');
    $('norusNote').textContent = data.nota || '';
    renderKpis(data);
    renderChart(data);
    renderSignals(data);
    $('norus-widget').setAttribute('aria-busy', 'false');
    avisar({
      tipo: 'norus:datos',
      estado: data.estado,
      generado_en: data.generado_en,
      brecha_adr_pct: (data.comparativa || {}).brecha_adr_pct,
      propiedad_id: data.propiedad_id
    });
  }

  function parar() {
    if (timer) { clearTimeout(timer); timer = null; }
  }

  function programar(ms) {
    parar();
    timer = setTimeout(cargar, ms);
  }

  async function cargar() {
    if (!token || inflight) return;
    if (document.visibilityState === 'hidden') { programar(5000); return; }
    inflight = true;
    const params = new URLSearchParams({
      moneda: claims.moneda || 'CLP',
      ventana: opciones.ventana || '24h',
      refresh: reintentos > 0 ? 'true' : 'false'
    });
    if (opciones.propiedad_id) params.set('propiedad_id', opciones.propiedad_id);
    try {
      const res = await fetch(ENDPOINT + '?' + params.toString(), {
        headers: { Authorization: 'Bearer ' + token },
        cache: 'no-store'
      });
      if (res.status === 401) {
        token = '';
        parar();
        estado('Sesión expirada', 'sin_datos');
        $('norusUpdated').textContent = 'Solicite un nuevo token a la aplicación padre.';
        avisar({ tipo: 'norus:sesion_expirada' });
        return;
      }
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      reintentos = 0;
      render(data);
      programar(POLL_MS);
    } catch (err) {
      reintentos = Math.min(reintentos + 1, 5);
      estado('Datos no actualizados', 'historico');
      $('norusUpdated').textContent = 'No se pudo consultar el mercado (' + err.message + '). Se conserva el último gráfico válido.';
      avisar({ tipo: 'norus:error', mensaje: err.message });
      programar(Math.min(2000 * Math.pow(2, reintentos), 30000));
    } finally {
      inflight = false;
    }
  }

  function iniciar(nuevoToken, origen, config) {
    token = String(nuevoToken || '');
    claims = decodeJwt(token);
    if (origen) parentOrigin = origen;
    if (config && config.propiedad_id) opciones.propiedad_id = config.propiedad_id;
    if (config && config.ventana) opciones.ventana = config.ventana;
    if (!token || !esOrigenPermitido(parentOrigin || claims.parent)) {
      estado('Origen no autorizado', 'sin_datos');
      $('norusUpdated').textContent = 'Este contenido solo puede integrarse desde un origen permitido.';
      return;
    }
    reintentos = 0;
    cargar();
  }

  window.addEventListener('message', (event) => {
    const data = event.data || {};
    if (!esOrigenPermitido(event.origin)) return;
    if (data.tipo === 'norus:token') {
      iniciar(data.token, event.origin, data.config);
    } else if (data.tipo === 'norus:config') {
      opciones = Object.assign({}, opciones, data.config || {});
      if (token) programar(1000);
    }
  });

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && token) programar(1000);
    else parar();
  });

  async function boot() {
    try {
      const res = await fetch(CONFIG_URL, { cache: 'no-store' });
      const cfg = await res.json();
      allowed = cfg.origenes_permitidos || [];
      if (!cfg.habilitado) {
        estado('Integración desactivada', 'sin_datos');
        $('norusUpdated').textContent = 'El administrador debe configurar NORUS_EMBED_ALLOWED_ORIGINS.';
        return;
      }
      allowed.forEach((origin) => {
        if (window.parent !== window) window.parent.postMessage({ tipo: 'norus:listo' }, origin);
      });
      // Compatibilidad con integraciones simples: token en el fragmento.
      const hash = new URLSearchParams(location.hash.replace(/^#/, ''));
      const fragToken = hash.get('token');
      const fragParent = hash.get('parent');
      if (fragToken && esOrigenPermitido(fragParent)) iniciar(fragToken, fragParent, {});
      else estado('Esperando token', 'parcial');
    } catch (err) {
      estado('Error de configuración', 'sin_datos');
      $('norusUpdated').textContent = err.message;
    }
  }

  boot();
})();
