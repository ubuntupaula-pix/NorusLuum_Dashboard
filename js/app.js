/* ============================================================
   NORUS & LUUM · Dashboard Comercial y de Gestión
   Lógica: navegación, KPI, gráficos Chart.js, búsqueda, export
   ============================================================ */
(function () {
  'use strict';

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  // ---------- Auth: la sesión real vive en una cookie HttpOnly ----------
  // El HTML del dashboard solo se entrega con sesión válida; además ocultamos
  // la interfaz hasta confirmar /api/auth/me para evitar cualquier destello.
  let authUser = sessionStorage.getItem('norus_user') || 'usuario';
  let authRole = sessionStorage.getItem('norus_role') || 'lector';
  let authMe = null;

  // ---------- Render inmediato (stale-while-revalidate) ----------
  // Pintamos el último snapshot válido antes de esperar a /auth/me. Así, al
  // ocultarse el overlay de autenticación, los KPI y gráficos ya están visibles
  // y solo se sustituyen cuando llega la respuesta fresca del servidor.
  function dashboardCacheKey(f) {
    const filtros = f || filtrosActuales();
    return window.NorusCache.key(
      'dashboard', authUser, authRole,
      filtros.moneda, filtros.granularidad, filtros.desde, filtros.hasta,
      filtros.propiedadId
    );
  }

  function aplicarDashboard(datos, desdeCache) {
    cargarDatosRespuesta(datos);
    renderAll();
    if (desdeCache) {
      const status = $('#estadoTexto');
      if (status) status.textContent = 'Últimos datos guardados · actualizando…';
    }
  }

  function restaurarDashboardCache() {
    if (!window.NorusCache) return false;
    const datos = window.NorusCache.get(dashboardCacheKey());
    if (!datos) return false;
    aplicarDashboard(datos, true);
    return true;
  }

  const logoutBtn = document.getElementById('btnLogout');
  if (logoutBtn) logoutBtn.addEventListener('click', async function () {
    try { await window.API.logout(); } catch (_) { /* se elimina igualmente el indicador local */ }
    window.API.clearSessionHint();
    window.location.href = 'login.html';
  });

  function applyAuth(me) {
    authMe = me;
    authUser = me.username || 'usuario';
    authRole = me.rol || 'lector';
    window.API.setToken(true);
    sessionStorage.setItem('norus_user', authUser);
    sessionStorage.setItem('norus_role', authRole);
    // Los módulos cargados antes de resolver /auth/me pueden haber tomado una
    // decisión de permisos con el rol todavía desconocido: se reevalúan aquí.
    window.__norusRole = authRole;
    if (window.Mercado && typeof window.Mercado.refreshPermissions === 'function') {
      window.Mercado.refreshPermissions(authRole);
    }

    const avatarEl = document.querySelector('.avatar');
    if (avatarEl) {
      avatarEl.textContent = authUser.toUpperCase().slice(0, 2);
      avatarEl.title = authUser + ' (' + authRole + ')';
    }

    if (authRole !== 'admin') {
      const auditNav = document.querySelector('[data-view="auditoria"]');
      if (auditNav) auditNav.hidden = true;
    }
    if (authRole === 'lector') {
      ['btnUpload', 'btnNuevo', 'dataAlertUpload'].forEach(id => { const el = document.getElementById(id); if (el) el.hidden = true; });
      const reservationNav = document.querySelector('[data-view="base"]');
      if (reservationNav) reservationNav.hidden = true;
    }

    document.body.classList.remove('auth-pending');
    const loader = $('#authLoading');
    if (loader) loader.setAttribute('aria-hidden', 'true');
  }

  Chart.defaults.font.family = "'Inter', system-ui, sans-serif";
  Chart.defaults.color = '#5a6a72';

  // ---------- Auditoría centralizada en el servidor ----------
  function audit(accion, detalle) {
    try {
      // Auditoría en el servidor (producción)
      if (window.API && window.API.getToken()) {
        window.API.registrarEvento(accion, detalle).catch(function () {});
      }
    } catch (e) { /* la auditoría nunca debe romper la app */ }
  }

  function escHtml(v) {
    return String(v === undefined || v === null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  const PALETA = {
    primary: '#1e3a4a',
    ink: '#13232e',
    teal: '#4f7466',
    accent: '#a98b5f',
    green: '#3e7a5a',
    red: '#a9584d',
    yellow: '#c4a05f',
    grid: 'rgba(19,35,46,0.07)'
  };

  let graficos = {};

  const fmtOpt = (v) => (v === null || v === undefined || !isFinite(v)) ? '—' : v;
  const fmtMoneda = (v) => (v === null || v === undefined || !isFinite(v)) ? '—' : (Datos.moneda || 'Moneda') + ' ' + fmtMillar(v);

  function serieTemporal() {
    return {
      labels: Datos.meses || [],
      ocupacion: Datos.ocupacionMensual || [],
      adr: Datos.adrMensual || [],
      revpar: Datos.revparMensual || [],
      ingresos: Datos.ingresosMensuales || []
    };
  }

  // ---------- Navegación lateral ----------
  const titulos = {
    resumen: ['Resumen Ejecutivo', 'Indicadores consolidados de gestión comercial y operativa'],
    ocupacion: ['Ocupación y Tarifas', 'Análisis de ocupación, ADR y RevPAR'],
    ingresos: ['Ingresos y Canales de Venta', 'Distribución de ingresos por canal y habitación'],
    reservas: ['Reservas y Mercados', 'Anticipación de reservas y nacionalidad de huéspedes'],
    incidencias: ['Incidencias y Satisfacción', 'Calidad de servicio, incidencias y experiencia del cliente'],
    marca: ['Comparativa por propiedad', 'Indicadores de las propiedades con datos cargados'],
    habitaciones: ['Habitaciones por tipo', 'Ocupación, ADR y RevPAR por tipo con capacidad declarada'],
    mercado: ['Mercado hotelero', 'Comparación de largo plazo contra referencias públicas con fuente'],
    base: ['Base de Datos (CRUD)', 'Gestión de registros: crear, editar, eliminar y consultar'],
    auditoria: ['Auditoría y Seguridad', 'Registro de eventos de acceso, actividad y exportaciones'],
    operaciones: ['Operación Hotelera', 'Tareas, incidencias, mantenimiento, inventario, objetos perdidos y checklists'],
    seguridad: ['Seguridad y Acceso', 'MFA, contraseña, roles y permisos por propiedad']
  };

  function navegar(view) {
    $$('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.view === view));
    $$('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + view));
    $('#viewTitle').textContent = titulos[view][0];
    $('#viewSubtitle').textContent = titulos[view][1];
    $('#sidebar').classList.remove('open');
    if (view === 'base' && window.onCrudRefresh) window.onCrudRefresh();
    if (view === 'auditoria') renderAuditoria();
    if (view === 'seguridad') initSeguridad();
    if (view === 'operaciones' && window.Operaciones) window.Operaciones.load();
    if (view === 'habitaciones' && window.Habitaciones) window.Habitaciones.load();
    if (view === 'mercado' && window.Mercado) window.Mercado.load();
  }

  $$('.nav-item').forEach(btn => btn.addEventListener('click', () => navegar(btn.dataset.view)));
  $('#menuToggle').addEventListener('click', () => $('#sidebar').classList.toggle('open'));

  // ---------- Formato ----------
  function fmtMillar(n, dec = 0) {
    return n.toLocaleString('es-MX', { minimumFractionDigits: dec, maximumFractionDigits: dec });
  }

  // ---------- KPI ----------
  function renderKPI(containerSel, items) {
    const cont = $(containerSel);
    cont.innerHTML = items.map(it => `
      <div class="kpi ${escHtml(it.cls || '')} ${it.key ? 'is-selectable' : ''}"
           ${it.key ? `data-kpi="${escHtml(it.key)}" data-target="${escHtml(it.target || '')}" role="button" tabindex="0" aria-pressed="false"` : ''}>
        <div class="kpi-label">${escHtml(it.label)}</div>
        <div class="kpi-value">${it.valor === null || it.valor === undefined ? '—' : escHtml(it.valor)}<small>${escHtml(it.unit || '')}</small></div>
        ${it.delta === null || it.delta === undefined ? '' : `<div class="kpi-delta ${it.delta>=0?'up':'down'}">${it.delta>=0?'▲':'▼'} ${Math.abs(it.delta)}%</div>`}
        <div class="kpi-sub">${escHtml(it.sub || '')}</div>
      </div>`).join('');
  }

  // Los KPI del resumen funcionan como accesos al gráfico relacionado.
  function bindKpiSelection(containerSel) {
    const cards = $$(containerSel + ' .kpi.is-selectable');
    cards.forEach(card => {
      const toggle = () => {
        const wasActive = card.classList.contains('active');
        cards.forEach(c => {
          c.classList.remove('active');
          c.setAttribute('aria-pressed', 'false');
        });
        if (wasActive) return;
        card.classList.add('active');
        card.setAttribute('aria-pressed', 'true');
        const target = card.dataset.target && document.getElementById(card.dataset.target);
        if (target && typeof target.scrollIntoView === 'function') {
          target.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      };
      card.addEventListener('click', toggle);
      card.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          toggle();
        }
      });
    });
  }

  // ---------- Gráficos ----------
  function nuevo(canvasId, config) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    if (graficos[canvasId]) graficos[canvasId].destroy();
    graficos[canvasId] = new Chart(canvas.getContext('2d'), config);
  }

  // ---------- Vista RESUMEN ----------
  function renderResumen(per) {
    const k = Datos.kpi[per];
    renderKPI('#kpiResumen', [
      { label: 'Ocupación', valor: fmtOpt(k.ocupacion.valor), unit: '%', delta: k.ocupacion.delta, sub: k.ocupacion.sub, cls: 'teal', key: 'ocupacion', target: 'chartOcupacionTarifa' },
      { label: 'Tarifa Promedio (ADR)', valor: fmtMoneda(k.adr.valor), delta: k.adr.delta, sub: k.adr.sub, cls: 'accent', key: 'adr', target: 'chartOcupacionTarifa' },
      { label: 'RevPAR', valor: fmtMoneda(k.revpar.valor), delta: k.revpar.delta, sub: k.revpar.sub, key: 'revpar', target: 'chartIngresos' },
      { label: 'Ingresos Totales', valor: fmtMoneda(k.ingresos.valor), delta: k.ingresos.delta, sub: k.ingresos.sub, cls: 'green', key: 'ingresos', target: 'chartIngresos' }
    ]);
    bindKpiSelection('#kpiResumen');

    $('#titleIngresos').textContent = 'Ingresos mensuales';
    $('#titleOcupacionTarifa').textContent = 'Ocupación vs tarifa promedio por mes';

    const s = serieTemporal();
    nuevo('chartIngresos', {
      type: 'bar',
      data: {
        labels: s.labels,
        datasets: [{
          label: 'Ingresos (' + (Datos.moneda || 'moneda de origen') + ')',
          data: s.ingresos,
          backgroundColor: 'rgba(30,58,74,0.75)',
          hoverBackgroundColor: PALETA.accent,
          borderRadius: 6,
          barThickness: 22,
          tension: .4
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { grid: { color: PALETA.grid }, ticks: { callback: v => (Datos.moneda || '') + ' ' + v } },
          x: { grid: { display: false } }
        }
      }
    });

    nuevo('chartOcupacionTarifa', {
      type: 'line',
      data: {
        labels: s.labels,
        datasets: [
          { label: 'Ocupación (%)', data: s.ocupacion, borderColor: PALETA.teal, backgroundColor: 'rgba(79,116,102,0.12)', fill: true, tension: .4, borderWidth: 2.5, yAxisID: 'y' },
          { label: 'ADR (' + (Datos.moneda || 'moneda de origen') + ')', data: s.adr, borderColor: PALETA.accent, tension: .4, borderWidth: 2.5, yAxisID: 'y1', borderDash: [5,4] }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        scales: {
          y: { position: 'left', grid: { color: PALETA.grid }, min: 0, max: 100, ticks: { callback: v => v + '%' } },
          y1: { position: 'right', grid: { drawOnChartArea: false }, ticks: { callback: v => (Datos.moneda || '') + ' ' + v } },
          x: { grid: { display: false } }
        }
      }
    });

    nuevo('chartCanales', {
      type: 'doughnut',
      data: {
        labels: Datos.canales.labels,
        datasets: [{
          data: Datos.canales.valores,
          backgroundColor: [PALETA.primary, PALETA.teal, PALETA.accent, '#7c6a8a', '#6d8ea3', '#b79a72'],
          borderWidth: 2, borderColor: '#fff'
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        cutout: '62%',
        plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } } }
      }
    });

    nuevo('chartNacionalidad', {
      type: 'bar',
      data: {
        labels: Datos.nacionalidad.labels,
        datasets: [{
          label: 'Reservas',
          data: Datos.nacionalidad.valores,
          backgroundColor: 'rgba(79,116,102,0.8)',
          borderRadius: 6
        }]
      },
      options: {
        indexAxis: 'y',
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { color: PALETA.grid }, beginAtZero: true, ticks: { callback: v => v } },
          y: { grid: { display: false } }
        }
      }
    });

    $('#npsScore').textContent = Datos.nps.score == null ? '—' : Datos.nps.score;
    $('#npsPromotores').textContent = Datos.nps.promotores == null ? '—' : Datos.nps.promotores + '%';
    $('#npsNeutros').textContent = Datos.nps.neutros == null ? '—' : Datos.nps.neutros + '%';
    $('#npsDetractores').textContent = Datos.nps.detractores == null ? '—' : Datos.nps.detractores + '%';
    $('#npsGaugeFill').style.width = Math.max(0, Math.min(100, Datos.nps.fill || 0)) + '%';
  }

  // ---------- Vista OCUPACIÓN ----------
  function renderOcupacion(per) {
    const k = Datos.kpi[per];
    renderKPI('#kpiOcupacion', [
      { label: 'Ocupación', valor: fmtOpt(k.ocupacion.valor), unit: '%', delta: k.ocupacion.delta, sub: k.ocupacion.sub, cls: 'teal' },
      { label: 'ADR', valor: fmtMoneda(k.adr.valor), delta: k.adr.delta, sub: k.adr.sub, cls: 'accent' },
      { label: 'RevPAR', valor: fmtMoneda(k.revpar.valor), delta: k.revpar.delta, sub: k.revpar.sub },
      { label: 'Noches Vendidas', valor: fmtOpt(k.noches.valor), delta: k.noches.delta, sub: k.noches.sub }
    ]);

    $('#titleOcupacionMes').textContent = 'Ocupación mensual';
    $('#titleADR').textContent = 'ADR vs RevPAR por mes';

    const s = serieTemporal();
    nuevo('chartOcupacionMes', {
      type: 'line',
      data: {
        labels: s.labels,
        datasets: [{
          label: 'Ocupación (%)',
          data: s.ocupacion,
          borderColor: PALETA.teal,
          backgroundColor: 'rgba(79,116,102,0.15)',
          fill: true, tension: .4, borderWidth: 3
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { grid: { color: PALETA.grid }, beginAtZero: true, ticks: { callback: v => v } },
          x: { grid: { display: false } }
        }
      }
    });

    nuevo('chartADR', {
      type: 'bar',
      data: {
        labels: s.labels,
        datasets: [
          { label: 'ADR (' + (Datos.moneda || 'moneda de origen') + ')', data: s.adr, backgroundColor: 'rgba(169,139,95,0.85)', borderRadius: 6, barThickness: 18 },
          { label: 'RevPAR (' + (Datos.moneda || 'moneda de origen') + ')', data: s.revpar, backgroundColor: 'rgba(30,58,74,0.7)', borderRadius: 6, barThickness: 18 }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: {
          y: { grid: { color: PALETA.grid }, ticks: { callback: v => (Datos.moneda || '') + ' ' + v } },
          x: { grid: { display: false } }
        }
      }
    });

    nuevo('chartTarifaSegmento', {
      type: 'bar',
      data: {
        labels: Datos.segmentos.labels,
        datasets: [{
          label: 'ADR (' + (Datos.moneda || 'moneda de origen') + ')',
          data: Datos.segmentos.adr,
          backgroundColor: 'rgba(30,58,74,0.75)',
          borderRadius: 8,
          barThickness: 36
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { grid: { color: PALETA.grid }, ticks: { callback: v => (Datos.moneda || '') + ' ' + v } },
          x: { grid: { display: false } }
        }
      }
    });

    nuevo('chartDiaSemana', {
      type: 'bar',
      data: {
        labels: Datos.diaSemana.labels,
        datasets: [{
          label: 'Reservas',
          data: Datos.diaSemana.valores,
          backgroundColor: (ctx) => (ctx.dataIndex >= 4 ? 'rgba(169,139,95,0.9)' : 'rgba(79,116,102,0.8)'),
          borderRadius: 8
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { grid: { color: PALETA.grid }, beginAtZero: true, ticks: { callback: v => v } },
          x: { grid: { display: false } }
        }
      }
    });
  }

  // ---------- Vista INGRESOS ----------
  function renderIngresos(per) {
    const k = Datos.kpi[per];
    const reservasTotal = Datos.reservasTotal || 0;
    const directo = Datos.aportacionCanal ? Datos.aportacionCanal.directo : null;
    renderKPI('#kpiIngresos', [
      { label: 'Ingresos', valor: fmtMoneda(k.ingresos.valor), delta: k.ingresos.delta, sub: k.ingresos.sub, cls: 'green' },
      { label: 'Canales con actividad', valor: Datos.canales.labels.length || '—', sub: 'canales observados', cls: 'teal' },
      { label: 'Participación directa', valor: directo == null ? '—' : directo + '%', sub: 'de los ingresos por canal', cls: 'accent' },
      { label: 'Reservas registradas', valor: fmtMillar(reservasTotal), sub: 'registros en la moneda seleccionada' }
    ]);

    nuevo('chartIngresosCanal', {
      type: 'bar',
      data: {
        labels: Datos.canales.labels,
        datasets: [{
          label: 'Ingresos (' + (Datos.moneda || 'moneda de origen') + ')',
          data: Datos.canales.ingresos,
          backgroundColor: 'rgba(30,58,74,0.78)',
          borderRadius: 8,
          barThickness: 34
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { grid: { color: PALETA.grid }, ticks: { callback: v => (Datos.moneda || '') + ' ' + v } },
          x: { grid: { display: false } }
        }
      }
    });

    nuevo('chartAportacionCanal', {
      type: 'doughnut',
      data: {
        labels: ['Directa','OTA','GDS / Agencias','Otros'],
        datasets: [{
          data: [Datos.aportacionCanal.directo, Datos.aportacionCanal.ota, Datos.aportacionCanal.gds, Datos.aportacionCanal.otros],
          backgroundColor: [PALETA.green, PALETA.accent, PALETA.primary, '#a4adaf'],
          borderWidth: 2, borderColor: '#fff'
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        cutout: '58%',
        plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } } }
      }
    });

    nuevo('chartIngresosHabitacion', {
      type: 'bar',
      data: {
        labels: Datos.habitaciones.labels,
        datasets: [{
          label: 'Ingresos (' + (Datos.moneda || 'moneda de origen') + ')',
          data: Datos.habitaciones.ingresos,
          backgroundColor: ['#1e3a4a','#4f7466','#a98b5f','#7c6a8a','#6d8ea3'],
          borderRadius: 10,
          barThickness: 44
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { grid: { color: PALETA.grid }, ticks: { callback: v => (Datos.moneda || '') + ' ' + v } },
          x: { grid: { display: false } }
        }
      }
    });
  }

  // ---------- Vista RESERVAS ----------
  function renderReservas(per) {
    const kpiReservas = Datos.kpiReservas || {};
    renderKPI('#kpiReservas', [
      { label: 'Anticipación media', valor: fmtOpt(kpiReservas.anticipacion_media), unit: ' días', sub: 'según reservas con dato', cls: 'teal' },
      { label: 'Mercados emisores', valor: fmtMillar(kpiReservas.mercados || 0), sub: 'nacionalidades identificadas', cls: 'accent' },
      { label: 'Reservas con ≥90 días', valor: kpiReservas.reservas_anticipadas_pct == null ? '—' : kpiReservas.reservas_anticipadas_pct + '%', sub: 'de reservas registradas', cls: 'green' },
      { label: 'Tasa de cancelación', valor: kpiReservas.cancelacion_pct == null ? '—' : kpiReservas.cancelacion_pct + '%', sub: fmtMillar(kpiReservas.canceladas || 0) + ' reservas canceladas' }
    ]);

    nuevo('chartAnticipacion', {
      type: 'bar',
      data: {
        labels: Datos.anticipacion.labels,
        datasets: [{
          label: 'Reservas',
          data: Datos.anticipacion.valores,
          backgroundColor: ['#6d8ea3','#4f7466','#a98b5f','#7c6a8a','#a4adaf'],
          borderRadius: 8,
          barThickness: 40
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { grid: { color: PALETA.grid }, beginAtZero: true, ticks: { callback: v => v } },
          x: { grid: { display: false } }
        }
      }
    });

    nuevo('chartNacionalidad2', {
      type: 'bar',
      data: {
        labels: Datos.nacionalidad.labels,
        datasets: [{
          label: 'Reservas',
          data: Datos.nacionalidad.valores,
          backgroundColor: 'rgba(79,116,102,0.85)',
          borderRadius: 8,
          barThickness: 34
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { grid: { color: PALETA.grid }, beginAtZero: true, ticks: { callback: v => v } },
          x: { grid: { display: false } }
        }
      }
    });

    nuevo('chartNacionalidadTorta', {
      type: 'pie',
      data: {
        labels: Datos.nacionalidad.labels,
        datasets: [{
          data: Datos.nacionalidad.valores,
          backgroundColor: [PALETA.primary, PALETA.teal, PALETA.accent, '#7c6a8a', '#6d8ea3', '#b79a72', '#a4adaf'],
          borderWidth: 2, borderColor: '#fff'
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'right', labels: { boxWidth: 12, font: { size: 11 } } } }
      }
    });

    nuevo('chartAnticipacionCanal', {
      type: 'bar',
      data: {
        labels: Datos.canales.labels,
        datasets: [{
          label: 'Anticipación (días)',
          data: Datos.canales.anticipacion,
          backgroundColor: ['rgba(79,116,102,0.85)','rgba(169,139,95,0.85)','rgba(30,58,74,0.85)','#7c6a8a','#6d8ea3','#b79a72'],
          borderRadius: 8,
          barThickness: 34
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { grid: { color: PALETA.grid }, ticks: { callback: v => v + ' d' } },
          x: { grid: { display: false } }
        }
      }
    });
  }

  // ---------- Vista INCIDENCIAS ----------
  function renderIncidencias(per) {
    const k = Datos.kpi[per];
    renderKPI('#kpiIncidencias', [
      { label: 'Índice NPS', valor: k.nps.valor, delta: k.nps.delta, sub: k.nps.sub, cls: 'green' },
      { label: 'Satisfacción Media', valor: k.satisfaccion.valor, delta: k.satisfaccion.delta, sub: k.satisfaccion.sub, cls: 'teal' },
      { label: 'Incidencias Totales', valor: fmtMillar(k.incidencias.valor), delta: k.incidencias.delta, sub: k.incidencias.sub, cls: 'red' },
      { label: 'Resolución promedio', valor: fmtOpt(Datos.incidencias.tiempo_resolucion_promedio), unit: Datos.incidencias.tiempo_resolucion_promedio == null ? '' : ' h', sub: 'tiempo observado en registros', cls: 'accent' }
    ]);

    nuevo('chartIncidencias', {
      type: 'bar',
      data: {
        labels: Datos.incidencias.tipos,
        datasets: [{
          label: 'Incidencias',
          data: Datos.incidencias.valores,
          backgroundColor: ['#4f7466','#a98b5f','#7c6a8a','#6d8ea3','#b79a72','#27ae60','#a4adaf'],
          borderRadius: 8,
          barThickness: 30
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { grid: { color: PALETA.grid } },
          x: { grid: { display: false } }
        }
      }
    });

    nuevo('chartResolucion', {
      type: 'bar',
      data: {
        labels: Datos.incidencias.resolucion.labels,
        datasets: [{
          label: 'Horas de resolución',
          data: Datos.incidencias.resolucion.horas,
          backgroundColor: 'rgba(169,139,95,0.85)',
          borderRadius: 8,
          barThickness: 42
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { grid: { color: PALETA.grid }, ticks: { callback: v => v + ' h' } },
          x: { grid: { display: false } }
        }
      }
    });

    nuevo('chartSatisfaccionArea', {
      type: 'radar',
      data: {
        labels: Datos.satisfaccion.areas,
        datasets: [{
          label: 'Satisfacción (/5)',
          data: Datos.satisfaccion.nota,
          backgroundColor: 'rgba(30,58,74,0.18)',
          borderColor: PALETA.primary,
          borderWidth: 2.5,
          pointBackgroundColor: PALETA.accent,
          pointRadius: 4
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: {
          r: { min: 0, max: 5, ticks: { stepSize: 1 } }
        }
      }
    });

    nuevo('chartEncuestas', {
      type: 'line',
      data: {
        labels: Datos.satisfaccion.encuestas.labels,
        datasets: [
          { label: 'Satisfechos (%)', data: Datos.satisfaccion.encuestas.satisfecho, borderColor: PALETA.green, tension: .4, borderWidth: 2.5, fill: false },
          { label: 'Neutrales (%)', data: Datos.satisfaccion.encuestas.neutral, borderColor: PALETA.yellow, tension: .4, borderWidth: 2, borderDash: [4,4] },
          { label: 'Insatisfechos (%)', data: Datos.satisfaccion.encuestas.insatisfecho, borderColor: PALETA.red, tension: .4, borderWidth: 2, borderDash: [4,4] }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        scales: {
          y: { grid: { color: PALETA.grid }, min: 0, max: 100, ticks: { callback: v => v + '%' } },
          x: { grid: { display: false } }
        }
      }
    });
  }

  // ---------- Vista MULTI-MARCA (unificación de las 3 propiedades) ----------
  function renderMarca(per) {
    const k = Datos.kpiMarca;
    // KPIs consolidados: tarjeta por marca
    $('#kpiMarca').innerHTML = k.map((m, i) => `
      <div class="kpi">
        <div class="kpi-label">${escHtml(m.nombre)} <small style="opacity:.7" >(${m.habitaciones == null ? 'capacidad s/d' : m.habitaciones + ' habs.'})</small></div>
        <div class="kpi-sub" style="margin:2px 0 8px">Ocupación ${m.ocupacion == null ? '—' : fmtOpt(m.ocupacion) + '%'} · ADR ${fmtMoneda(m.adr)}</div>
        <div class="kpi-value">${fmtMoneda(m.ingresos)}</div>
        <div class="kpi-sub">${m.deltas && m.deltas.ingresos != null ? 'Variación ' + m.deltas.ingresos + '% vs periodo anterior' : 'Sin comparación de periodo anterior'}</div>
        <span class="kpi-marca-bar" style="background:${Datos.marcaColores[i] || PALETA.primary}"></span>
      </div>`).join('');

    // Ocupación mensual por marca
    nuevo('chartOcupacionMarca', {
      type: 'bar',
      data: {
        labels: Datos.meses,
        datasets: Datos.marcas.map((m, i) => ({
          label: m,
          data: Datos.ocupacionMarca[i],
          backgroundColor: Datos.marcaColores[i] + (i === 2 ? 'B3' : 'B8'),
          borderRadius: 4,
          barPercentage: 0.9,
          categoryPercentage: 0.75
        }))
      },
      options: chartMultiOpciones({ yMin: 0, yMax: 100, ySufijo: '%', grid: true })
    });

    // ADR mensual por marca
    nuevo('chartADRMarca', {
      type: 'line',
      data: {
        labels: Datos.meses,
        datasets: Datos.marcas.map((m, i) => ({
          label: m,
          data: Datos.adrMarca[i],
          borderColor: Datos.marcaColores[i],
          backgroundColor: Datos.marcaColores[i] + '22',
          fill: i === 2, tension: .4, borderWidth: 2.5, pointRadius: 3
        }))
      },
      options: chartMultiOpciones({ ySufijo: ' ' + Datos.moneda, grid: true })
    });

    // Ingresos mensuales por marca
    nuevo('chartIngresosMarca', {
      type: 'bar',
      data: {
        labels: Datos.meses,
        datasets: Datos.marcas.map((m, i) => ({
          label: m,
          data: Datos.ingresosMarca[i],
          backgroundColor: Datos.marcaColores[i],
          borderRadius: 4,
          barPercentage: 0.85,
          categoryPercentage: 0.7
        }))
      },
      options: chartMultiOpciones({ ySufijo: ' ' + Datos.moneda, grid: true })
    });

    // Conteo observado de reservas por segmento y mes calendario.
    nuevo('chartEstacionalidad', {
      type: 'bar',
      data: {
        labels: Datos.temporadas.meses,
        datasets: Object.entries(Datos.temporadas.series || {}).map(([segment, values], i) => ({
          label: segment,
          data: values,
          backgroundColor: PALETA[["primary", "teal", "accent", "green", "red", "yellow"][i % 6]],
          borderRadius: 4
        }))
      },
      options: chartMultiOpciones({ grid: true, leyenda: true })
    });

    // Revenue management por temporada (ADR vs ocupación)
    nuevo('chartRevenue', {
      type: 'bar',
      data: {
        labels: Datos.revenue.labels,
        datasets: [
          { label: 'ADR (' + (Datos.moneda || 'moneda de origen') + ')', data: Datos.revenue.adr, backgroundColor: 'rgba(169,139,95,0.85)', borderRadius: 6, yAxisID: 'y', barThickness: 34 },
          { label: 'Ocupación (%)', data: Datos.revenue.ocupacion, backgroundColor: 'rgba(30,58,74,0.7)', borderRadius: 6, yAxisID: 'y1', barThickness: 34 }
        ]
      },
      options: chartMultiOpciones({ leyenda: true, dualAxis: true, grid: true })
    });

    // Mix de nacionalidades por marca
    nuevo('chartNacionalidadMarca', {
      type: 'bar',
      data: {
        labels: Datos.nacionalidadMarca.labels,
        datasets: Datos.marcas.map((m, i) => ({
          label: m,
          data: (Datos.nacionalidadMarca.series || {})[m] || [],
          backgroundColor: Datos.marcaColores[i],
          borderRadius: 4,
          barPercentage: 0.9,
          categoryPercentage: 0.72
        }))
      },
      options: chartMultiOpciones({ grid: true, leyenda: true })
    });

    // Participación de ingresos consolidada por marca
    nuevo('chartIngresosTortaMarca', {
      type: 'doughnut',
      data: {
        labels: Datos.marcas,
        datasets: [{
          data: Datos.ingresosMarca.map(arr => arr.length ? arr[arr.length - 1] : null),
          backgroundColor: Datos.marcaColores,
          borderWidth: 2, borderColor: '#fff'
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        cutout: '58%',
        plugins: { legend: { position: 'bottom', labels: { boxWidth: 12 } } }
      }
    });
  }

  // ---------- Opciones comunes de gráficos multi-marca ----------
  function chartMultiOpciones({ yMin, yMax, ySufijo = '', grid = false, leyenda = false, dualAxis = false }) {
    const scales = {};
    if (dualAxis) {
      scales.y = { position: 'left', grid: { color: PALETA.grid }, ticks: { callback: v => (Datos.moneda || '') + ' ' + v } };
      scales.y1 = { position: 'right', grid: { drawOnChartArea: false }, min: 0, max: 100, ticks: { callback: v => v + '%' } };
    } else {
      scales.y = { grid: grid ? { color: PALETA.grid } : { display: false } };
      if (yMin !== undefined) scales.y.min = yMin;
      if (yMax !== undefined) scales.y.max = yMax;
      if (ySufijo) scales.y.ticks = { callback: v => v + ySufijo };
    }
    scales.x = { grid: { display: false } };
    return {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { display: leyenda, position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } } },
      scales
    };
  }

  // ---------- Filtros de período, fecha y propiedad ----------
  function renderAll() {
    // El backend entrega el período vigente bajo kpi.anio.
    const per = 'anio';
    renderResumen(per);
    renderOcupacion(per);
    renderIngresos(per);
    renderReservas(per);
    renderMarca(per);
    renderIncidencias(per);
  }

  function filtrosActuales() {
    return {
      moneda: $('#moneda') ? $('#moneda').value : 'CLP',
      granularidad: $('#granularidad') ? $('#granularidad').value : 'mes',
      desde: $('#desde') ? $('#desde').value : '',
      hasta: $('#hasta') ? $('#hasta').value : '',
      propiedadId: $('#propiedad') ? $('#propiedad').value : ''
    };
  }

  function avisar(msg, tipo) {
    if (typeof window.notificar === 'function') window.notificar(msg, tipo || 'info');
    else console.info(msg);
  }

  let cargandoDashboard = false;
  let recargaPendiente = false;

  async function cargarDashboardServidor() {
    // No descartar cambios de filtro rápidos: se reprocesa con los valores
    // finales en cuanto termine la petición en curso.
    if (cargandoDashboard) { recargaPendiente = true; return; }
    const f = filtrosActuales();
    if (f.desde && f.hasta && f.desde > f.hasta) {
      avisar('La fecha “desde” no puede ser posterior a “hasta”.', 'error');
      return;
    }
    cargandoDashboard = true;
    const clave = dashboardCacheKey(f);
    try {
      const datos = await window.API.dashboard(f.moneda, f.granularidad, f.desde, f.hasta, f.propiedadId);
      aplicarDashboard(datos, false);
      if (window.NorusCache) window.NorusCache.set(clave, datos);
    } catch (err) {
      beSafeNotify('No se pudo cargar el dashboard: ' + err.message);
      console.error('Error cargando datos del servidor:', err);
    } finally {
      cargandoDashboard = false;
      if (recargaPendiente) {
        recargaPendiente = false;
        await cargarDashboardServidor();
      }
    }
  }

  function beSafeNotify(message) {
    if (typeof window.notificar === 'function') window.notificar(message, 'error');
  }

  // ---------- Export PDF (descarga + opción de imprimir) ----------
  const pdfOverlay = $('#pdfModalOverlay');
  let pdfLastFocus = null;
  function cerrarPdfModal() {
    if (!pdfOverlay.classList.contains('show')) return;
    pdfOverlay.classList.remove('show');
    pdfOverlay.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('modal-open');
    if (pdfLastFocus && typeof pdfLastFocus.focus === 'function') pdfLastFocus.focus();
  }
  function mostrarPdfModal() {
    pdfLastFocus = document.activeElement;
    pdfOverlay.classList.add('show');
    pdfOverlay.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
    $('#pdfModalClose').focus();
  }
  $('#pdfModalClose').addEventListener('click', cerrarPdfModal);
  $('#pdfModalCancel').addEventListener('click', cerrarPdfModal);
  pdfOverlay.addEventListener('click', (e) => { if (e.target === pdfOverlay) cerrarPdfModal(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') cerrarPdfModal(); });
  $('#pdfModalPrint').addEventListener('click', () => {
    cerrarPdfModal();
    audit('imprimir_pdf', 'Impresión desde el diálogo del sistema');
    window.print();
  });

  const dataAlertUpload = $('#dataAlertUpload');
  if (dataAlertUpload) dataAlertUpload.addEventListener('click', () => {
    const upload = $('#btnUpload');
    if (upload) upload.click();
  });

  $('#btnExport').addEventListener('click', () => {
    audit('exportar_pdf', 'Impresión o guardado como PDF desde el navegador');
    notificar('En el diálogo de impresión, elige “Guardar como PDF”.', 'info');
    window.print();
  });

// ---------- Export CSV ----------
  // Neutraliza fórmulas (=, +, -, @) para evitar inyección al abrir en Excel.
  function csvCell(value) {
    let s = value === null || value === undefined ? '' : String(value);
    if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
    if (/[",\n\r;]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
    return s;
  }

  $('#btnCSV').addEventListener('click', () => {
    const k = Datos.kpi.anio;
    const granularidad = ($('#granularidad') && $('#granularidad').value) || 'mes';
    const rows = [
      ['Datos', 'KPI', 'Valor'],
      ['Moneda', 'ISO4217', Datos.moneda],
      ['Ocupación', '%', k.ocupacion.valor],
      ['ADR', Datos.moneda, k.adr.valor],
      ['RevPAR', Datos.moneda, k.revpar.valor],
      ['Ingresos', Datos.moneda, k.ingresos.valor],
      ['NPS', 'índice', k.nps.valor],
    ];
    Datos.meses.forEach((m, i) => rows.push(['Ingresos_' + m, Datos.moneda, Datos.ingresosMensuales[i]]));
    const csv = rows.map(row => row.map(csvCell).join(',')).join('\n') + '\n';
    const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'norus_luum_dashboard.csv';
    a.click();
    audit('exportar_csv', 'Granularidad ' + granularidad + ' · ' + Datos.meses.length + ' períodos');
  });

  // ---------- Vista AUDITORÍA ----------
  async function renderAuditoria() {
    const body = $('#auditBody');
    const empty = $('#auditEmpty');
    const count = $('#auditCountText');
    if (!body || !empty) return;

    if (window.API && window.API.getToken()) {
      try {
        const eventos = await window.API.listarAuditoria(0, 300);
        empty.style.display = eventos.length ? 'none' : 'block';
        count.textContent = eventos.length ? eventos.length + ' eventos (más recientes primero)' : 'Sin eventos registrados aún';
        body.innerHTML = eventos.slice().reverse().map(e => `
          <tr>
            <td class="mono">${escHtml(e.creado_en || '')}</td>
            <td><span class="seg-btn${/fallo|rechaz/i.test(e.accion||'') ? ' active' : ''}" style="cursor:default">${escHtml(e.accion || '')}</span></td>
            <td>${escHtml(e.usuario || '')}</td>
            <td>${escHtml(e.detalle || '')}</td>
          </tr>`).join('');
        return;
      } catch (err) {
        count.textContent = 'No se pudo cargar la auditoría';
        body.innerHTML = '';
        empty.style.display = 'block';
        empty.querySelector('p').textContent = 'Revisa la conexión con el servidor e inténtalo de nuevo.';
        return;
      }
    }
    count.textContent = 'Sesión no disponible';
    body.innerHTML = '';
    empty.style.display = 'block';
  }

  $('#btnAuditRefresh').addEventListener('click', renderAuditoria);
  if ($('#btnAuditCerrar')) $('#btnAuditCerrar').addEventListener('click', () => navegar('resumen'));

  // ---------- Iniciar: filtros de período y sesión ----------
  ['granularidad', 'desde', 'hasta', 'propiedad', 'moneda'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', cargarDashboardServidor);
  });

  function cargarDatosRespuesta(datos) {
    const nombres = {
      ocupacion_mensual: 'ocupacionMensual', adr_mensual: 'adrMensual', revpar_mensual: 'revparMensual',
      ingresos_mensuales: 'ingresosMensuales', ocupacion_marca: 'ocupacionMarca', adr_marca: 'adrMarca',
      ingresos_marca: 'ingresosMarca', marca_colores: 'marcaColores', kpi_marca: 'kpiMarca',
      nacionalidad_marca: 'nacionalidadMarca', dia_semana: 'diaSemana', aportacion_canal: 'aportacionCanal',
      reservas_total: 'reservasTotal', kpi_reservas: 'kpiReservas'
    };
    Object.keys(datos || {}).forEach(key => { Datos[nombres[key] || key] = datos[key]; });
    Datos.moneda = datos && datos.moneda ? datos.moneda : ($('#moneda') ? $('#moneda').value : 'CLP');
    document.querySelectorAll('[data-currency-label]').forEach(el => { el.textContent = Datos.moneda; });
    Datos._importado = true;
    Datos._formatoOrigen = 'servidor';
    const status = $('#estadoTexto');
    const hayMetricas = Boolean(
      (Datos.reservas && Datos.reservas.length) || (Datos.meses && Datos.meses.length)
    );
    if (status) {
      const unidades = { dia: 'días', semana: 'semanas', mes: 'meses', anio: 'años' };
      const unidad = unidades[(datos && datos.granularidad) || 'mes'] || 'períodos';
      status.textContent = hayMetricas
        ? 'Datos cargados · ' + Datos.moneda + ' · ' + Datos.meses.length + ' ' + unidad
        : 'Sin datos cargados · importa el archivo del PMS';
    }
    const alertEl = $('#dataAlert');
    if (alertEl) alertEl.hidden = hayMetricas;
  }

  async function cargarPropiedades() {
    try {
      const props = await window.API.dashboardProperties();
      propiedadesCache = props;
      const select = $('#propiedad');
      if (select) {
        const current = select.value;
        select.replaceChildren(new Option('Todas las propiedades', ''));
        props.forEach(p => select.add(new Option(p.nombre, p.id)));
        if (current && [...select.options].some(o => o.value === current)) select.value = current;
      }
      fillPropertyOptions($('#uProps'), []);
    } catch (err) {
      console.warn('No se pudieron cargar las propiedades:', err);
    }
  }

  // ---------- Seguridad: MFA, contraseña y usuarios ----------
  let propiedadesCache = [];

  function fillPropertyOptions(select, selected) {
    if (!select) return;
    select.replaceChildren();
    propiedadesCache.forEach(p => {
      const opt = new Option(p.nombre, p.id);
      opt.selected = (selected || []).map(Number).includes(Number(p.id));
      select.add(opt);
    });
  }

  function mfaEstadoVisual(enabled) {
    const badge = $('#mfaBadge');
    if (badge) {
      badge.textContent = enabled ? 'activo' : 'inactivo';
      badge.style.background = enabled ? '#e8f1eb' : '#f7eae7';
      badge.style.color = enabled ? '#3e7a5a' : '#a9584d';
    }
    const status = $('#mfaStatus');
    if (status) {
      status.textContent = enabled
        ? 'La autenticación en dos pasos está activa. Se solicitará un código al iniciar sesión.'
        : 'La autenticación en dos pasos no está activa. Te recomendamos configurarla.';
    }
    const setupBtn = $('#mfaSetupBtn');
    if (setupBtn) setupBtn.hidden = enabled;
    const disableForm = $('#mfaDisableForm');
    if (disableForm) disableForm.hidden = !enabled;
    if (enabled) {
      const panel = $('#mfaEnablePanel');
      if (panel) panel.hidden = true;
    }
  }

  const mfaSetupBtn = $('#mfaSetupBtn');
  if (mfaSetupBtn) mfaSetupBtn.addEventListener('click', async () => {
    try {
      const data = await window.API.mfaSetup();
      $('#mfaSecret').textContent = data.secret;
      $('#mfaUri').textContent = data.otpauth_uri;
      $('#mfaBackupCodes').hidden = true;
      $('#mfaEnablePanel').hidden = false;
      $('#mfaEnableCode').value = '';
      $('#mfaEnableCode').focus();
    } catch (e) { avisar('No se pudo iniciar MFA: ' + e.message, 'error'); }
  });

  const mfaEnableBtn = $('#mfaEnableBtn');
  if (mfaEnableBtn) mfaEnableBtn.addEventListener('click', async () => {
    const code = ($('#mfaEnableCode').value || '').trim();
    if (!code) { avisar('Introduce el código de tu aplicación.', 'error'); return; }
    mfaEnableBtn.disabled = true;
    try {
      const data = await window.API.mfaEnable(code);
      const list = $('#mfaBackupList');
      list.replaceChildren(...(data.backup_codes || []).map(codeValue => {
        const li = document.createElement('li'); li.textContent = codeValue; return li;
      }));
      $('#mfaBackupCodes').hidden = false;
      $('#mfaEnablePanel').hidden = true;
      if (authMe) authMe.mfa_habilitado = 1;
      mfaEstadoVisual(true);
      avisar('MFA activado. Guarda tus códigos de respaldo.', 'success');
      audit('mfa_habilitado', 'Segundo factor activado');
    } catch (e) { avisar('No se pudo activar MFA: ' + e.message, 'error'); }
    finally { mfaEnableBtn.disabled = false; }
  });

  const mfaDisableForm = $('#mfaDisableForm');
  if (mfaDisableForm) mfaDisableForm.addEventListener('submit', async event => {
    event.preventDefault();
    try {
      await window.API.mfaDisable($('#mfaDisablePass').value, $('#mfaDisableCode').value.trim());
      if (authMe) authMe.mfa_habilitado = 0;
      mfaEstadoVisual(false);
      mfaDisableForm.reset();
      avisar('MFA desactivado.', 'info');
      audit('mfa_deshabilitado', 'Segundo factor desactivado');
    } catch (e) { avisar('No se pudo desactivar MFA: ' + e.message, 'error'); }
  });

  const passwordForm = $('#passwordForm');
  if (passwordForm) passwordForm.addEventListener('submit', async event => {
    event.preventDefault();
    const actual = $('#passActual').value;
    const nueva = $('#passNueva').value;
    const confirm = $('#passConfirm').value;
    if (nueva !== confirm) { avisar('Las contraseñas nuevas no coinciden.', 'error'); return; }
    if (nueva.length < 12) { avisar('La nueva contraseña debe tener al menos 12 caracteres.', 'error'); return; }
    try {
      await window.API.changePassword(actual, nueva);
      passwordForm.reset();
      avisar('Contraseña actualizada. Se cerraron las demás sesiones.', 'success');
      audit('cambio_password', 'Contraseña actualizada por el usuario');
    } catch (e) { avisar('No se pudo cambiar la contraseña: ' + e.message, 'error'); }
  });

  function celda(tr, ...nodes) {
    const td = document.createElement('td');
    nodes.forEach(n => td.appendChild(typeof n === 'string' ? document.createTextNode(n) : n));
    tr.appendChild(td);
  }

  function filaUsuario(u) {
    const tr = document.createElement('tr');
    const propio = authMe && u.id === authMe.id;

    const name = document.createElement('div');
    const strong = document.createElement('b');
    strong.textContent = u.username + (propio ? ' (tú)' : '');
    name.appendChild(strong);
    const active = document.createElement('input');
    active.type = 'checkbox'; active.checked = Boolean(u.activo); active.disabled = Boolean(propio);
    const activeWrap = document.createElement('label');
    activeWrap.className = 'check-line';
    activeWrap.append(active, document.createTextNode(' activo'));
    celda(tr, name, activeWrap);

    const email = document.createElement('input');
    email.type = 'email'; email.className = 'users-email'; email.maxLength = 255; email.value = u.email || '';
    celda(tr, email);

    const rol = document.createElement('select');
    rol.className = 'users-role'; rol.disabled = Boolean(propio);
    ['lector', 'editor', 'admin'].forEach(r => {
      const option = new Option(r, r); option.selected = u.rol === r; rol.add(option);
    });
    celda(tr, rol);

    celda(tr, u.mfa_habilitado ? 'Activo' : 'Inactivo');

    const all = document.createElement('input');
    all.type = 'checkbox'; all.checked = Boolean(u.acceso_todas_propiedades);
    const allWrap = document.createElement('label');
    allWrap.className = 'check-line';
    allWrap.append(all, document.createTextNode(' global'));
    celda(tr, allWrap);

    const propsWrap = document.createElement('div');
    propsWrap.className = 'users-props';
    const checks = propiedadesCache.map(p => {
      const cb = document.createElement('input');
      cb.type = 'checkbox'; cb.value = p.id;
      cb.checked = (u.propiedades || []).map(Number).includes(Number(p.id));
      const label = document.createElement('label');
      label.append(cb, document.createTextNode(p.nombre));
      propsWrap.appendChild(label);
      return cb;
    });

    const actions = document.createElement('div');
    actions.className = 'users-actions';
    const save = document.createElement('button');
    save.type = 'button'; save.className = 'btn btn-primary'; save.textContent = 'Guardar';
    save.addEventListener('click', async () => {
      const payload = {
        email: email.value.trim() || null,
        acceso_todas_propiedades: all.checked,
        propiedades: checks.filter(c => c.checked).map(c => Number(c.value)),
      };
      if (!propio) { payload.rol = rol.value; payload.activo = active.checked; }
      try {
        await window.API.actualizarUsuario(u.id, payload);
        avisar('Usuario actualizado: ' + u.username, 'success');
        await cargarUsuarios();
      } catch (e) { avisar('No se pudo actualizar: ' + e.message, 'error'); }
    });
    const reset = document.createElement('button');
    reset.type = 'button'; reset.className = 'btn btn-ghost'; reset.textContent = 'Restablecer clave';
    reset.addEventListener('click', async () => {
      const nueva = window.prompt('Nueva contraseña para ' + u.username + ' (mínimo 12 caracteres y 3 tipos):');
      if (!nueva) return;
      try {
        await window.API.resetPassword(u.id, nueva);
        avisar('Contraseña restablecida para ' + u.username, 'success');
      } catch (e) { avisar('No se pudo restablecer: ' + e.message, 'error'); }
    });
    actions.append(save, reset);

    const cell = document.createElement('td');
    cell.append(propsWrap, actions);
    tr.appendChild(cell);
    return tr;
  }

  async function cargarUsuarios() {
    const body = $('#usersBody');
    if (!body) return;
    try {
      if (!propiedadesCache.length) {
        try { propiedadesCache = await window.API.opsProperties(); } catch (_) { propiedadesCache = []; }
      }
      fillPropertyOptions($('#uProps'), []);
      const users = await window.API.listarUsuarios();
      body.replaceChildren(...users.map(filaUsuario));
      const empty = $('#usersEmpty');
      if (empty) empty.style.display = users.length ? 'none' : 'block';
    } catch (e) {
      avisar('No se pudieron cargar los usuarios: ' + e.message, 'error');
    }
  }

  // ---------- Datos de demostración: banner siempre visible ----------
  async function actualizarDemo() {
    const banner = $('#demoAlert');
    if (!banner) return;
    let estado;
    try {
      estado = await window.API.demoEstado();
    } catch (_) {
      banner.hidden = true;
      return;
    }
    banner.hidden = !estado.activo;
    const esAdmin = authRole === 'admin';
    const borrar = $('#demoBorrar');
    const recargar = $('#demoRecargar');
    if (borrar) borrar.hidden = !esAdmin;
    if (recargar) recargar.hidden = !esAdmin;
    const detalle = $('#demoAlertDetalle');
    if (detalle && estado.activo) {
      const filas = Number(estado.reservas || 0).toLocaleString('es-CL');
      detalle.textContent = filas + ' reservas reales · Antonio, de Almeida & Nunes (2019), CC BY 4.0 · '
        + 'ADR convertido a CLP con FX real · capacidad de referencia 100 hab./hotel · mercado: INE-EMAT (Chile).';
    }
  }

  async function accionDemo(tipo) {
    const borrar = tipo === 'borrar';
    const botones = [$('#demoBorrar'), $('#demoRecargar')].filter(Boolean);
    botones.forEach(boton => { boton.disabled = true; });
    try {
      if (borrar) await window.API.demoBorrar();
      else await window.API.demoCargar({});
      avisar(borrar ? 'Datos de demostración borrados.' : 'Datos de demostración cargados.', 'success');
      await actualizarDemo();
      await cargarDashboardServidor();
      if (window.Mercado && typeof window.Mercado.loadComparison === 'function') {
        await window.Mercado.loadComparison();
      }
    } catch (e) {
      avisar('No se pudo ' + (borrar ? 'borrar' : 'cargar') + ' los datos demo: ' + e.message, 'error');
    } finally {
      botones.forEach(boton => { boton.disabled = false; });
    }
  }

  const demoBorrarBtn = $('#demoBorrar');
  if (demoBorrarBtn) demoBorrarBtn.addEventListener('click', () => {
    if (window.confirm('¿Borrar únicamente los datos de demostración? Tus importaciones reales se conservarán.')) {
      accionDemo('borrar');
    }
  });
  const demoRecargarBtn = $('#demoRecargar');
  if (demoRecargarBtn) demoRecargarBtn.addEventListener('click', () => {
    if (window.confirm('Esto reemplaza la demo actual por un nuevo conjunto generado. ¿Continuar?')) {
      accionDemo('cargar');
    }
  });

  function initSeguridad() {
    if (!authMe) return;
    mfaEstadoVisual(Boolean(authMe.mfa_habilitado));
    const panel = $('#adminUsersPanel');
    if (panel) panel.hidden = authRole !== 'admin';
    if (authRole === 'admin') cargarUsuarios();
  }

  const userCreateForm = $('#userCreateForm');
  if (userCreateForm) userCreateForm.addEventListener('submit', async event => {
    event.preventDefault();
    const allProps = $('#uAllProps').checked;
    const payload = {
      username: $('#uUsername').value.trim(),
      password: $('#uPassword').value,
      email: $('#uEmail').value.trim() || null,
      rol: $('#uRol').value,
      acceso_todas_propiedades: allProps,
      propiedades: allProps ? [] : [...$('#uProps').selectedOptions].map(o => Number(o.value)),
    };
    try {
      await window.API.crearUsuario(payload);
      userCreateForm.reset();
      $('#uProps').disabled = false;
      avisar('Usuario creado: ' + payload.username, 'success');
      await cargarUsuarios();
    } catch (e) { avisar('No se pudo crear el usuario: ' + e.message, 'error'); }
  });

  const uAllProps = $('#uAllProps');
  if (uAllProps) uAllProps.addEventListener('change', () => {
    const select = $('#uProps');
    if (select) select.disabled = uAllProps.checked;
  });

  async function cargarMonedas() {
    try {
      const currencyInfo = await window.API.dashboardCurrencies();
      const select = $('#moneda');
      if (!select) return;
      const anterior = select.value;
      select.replaceChildren();
      (currencyInfo.monedas || ['CLP']).forEach(code => select.add(new Option(code, code)));
      if ([...select.options].some(o => o.value === anterior)) select.value = anterior;
      else if ([...select.options].some(o => o.value === 'CLP')) select.value = 'CLP';
    } catch (err) {
      console.warn('No se pudieron cargar las monedas:', err);
    }
  }

  async function arranque() {
    try {
      const me = await window.API.me();
      applyAuth(me);
    } catch (err) {
      window.API.clearSessionHint();
      window.location.href = 'login.html';
      return;
    }

    // catálogos pequeños en paralelo: el dashboard ya está pintado desde caché.
    await Promise.all([cargarMonedas(), cargarPropiedades()]);
    //El resto de apartados se revalidan simultáneamente.
    await Promise.all([
      cargarDashboardServidor(),
      actualizarDemo(),
      Promise.resolve().then(initSeguridad)
    ]);
    // No se fuerza una vista: si el usuario ya navegó durante el arranque,
    // su elección se respeta. El HTML abre en "resumen" por defecto.
  }

  // Se ejecuta al final del módulo: renderAll() usa constantes definidas más
  // abajo (fmtOpt, fmtMoneda, …). Llamarlo antes provocaba un ReferenceError
  // de zona muerta temporal y dejaba los KPI en blanco hasta la respuesta API.
  restaurarDashboardCache();
  arranque();

  // ---------- Re-render en tiempo real con datos importados ----------
  window.onDatosImportados = async function (resumen) {
    await cargarDashboardServidor($('#moneda').value);
  };
})();
