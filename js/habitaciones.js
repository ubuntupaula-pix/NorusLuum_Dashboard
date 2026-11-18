/* ============================================================
   NORUS & LUUM · Habitaciones
   Ocupación, ADR y RevPAR por tipo de habitación usando la
   capacidad declarada por propiedad. Si la capacidad no existe,
   los indicadores se muestran como «—» (nunca se estiman).
   ============================================================ */
(function () {
  'use strict';

  const $ = (s) => document.querySelector(s);
  const canEdit = () => ['admin', 'editor'].includes(sessionStorage.getItem('norus_role'));
  const currency = () => ($('#moneda') ? $('#moneda').value : 'CLP') || 'CLP';
  const dateFrom = () => ($('#desde') ? $('#desde').value : '');
  const dateTo = () => ($('#hasta') ? $('#hasta').value : '');
  const selectedProperty = () => ($('#roomProperty') ? $('#roomProperty').value : '');

  let chart = null;
  let capacityRows = [];

  function notify(msg, tipo) {
    if (typeof window.notificar === 'function') window.notificar(msg, tipo || 'info');
    else console.info(msg);
  }

  function dash(value) {
    return value === null || value === undefined || value === '' ? '—' : value;
  }

  function fmtMoney(value, code) {
    if (value === null || value === undefined || !isFinite(value)) return '—';
    return (code || currency()) + ' ' + Number(value).toLocaleString('es-CL', { maximumFractionDigits: 0 });
  }

  function fmtPct(value) {
    if (value === null || value === undefined || !isFinite(value)) return '—';
    return Number(value).toFixed(1) + '%';
  }

  function cell(row, text) {
    const td = document.createElement('td');
    td.textContent = text;
    row.appendChild(td);
    return td;
  }

  function renderKpis(summary) {
    const container = $('#roomKpis');
    if (!container) return;
    const cards = [
      ['Habitaciones configuradas', dash(summary.habitaciones_configuradas), 'teal'],
      ['Tipos con capacidad', dash(summary.tipos_configurados), 'accent'],
      ['Noches vendidas', dash(summary.noches_vendidas), 'green'],
      ['Ingresos del período', fmtMoney(summary.ingresos), ''],
      ['Ocupación global', fmtPct(summary.ocupacion), 'teal'],
      ['RevPAR global', fmtMoney(summary.revpar), 'accent'],
      ['Habitaciones con actividad', dash(summary.habitaciones_con_actividad), ''],
      ['Incidencias abiertas', dash(summary.incidencias_abiertas), (summary.incidencias_abiertas || 0) > 0 ? 'red' : 'green']
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
  }

  function renderTypes(tipos) {
    const tbody = $('#roomTypesTable tbody');
    tbody.replaceChildren();
    tipos.forEach(item => {
      const tr = document.createElement('tr');
      cell(tr, item.propiedad || '—');
      cell(tr, item.tipo_habitacion || '—');
      cell(tr, dash(item.habitaciones));
      cell(tr, dash(item.reservas));
      cell(tr, dash(item.noches_vendidas));
      cell(tr, fmtPct(item.ocupacion));
      cell(tr, fmtMoney(item.adr));
      cell(tr, fmtMoney(item.revpar));
      tbody.appendChild(tr);
    });
    const empty = $('#roomTypesEmpty');
    if (empty) {
      empty.hidden = tipos.length > 0;
      if (!tipos.length) {
        empty.textContent = 'Sin capacidad declarada para el filtro actual. Configura la capacidad para calcular ocupación por tipo.';
      }
    }
    renderMix(tipos);
  }

  function renderMix(tipos) {
    const canvas = $('#chartRoomMix');
    if (!canvas || typeof Chart === 'undefined') return;
    const valid = tipos.filter(t => t.noches_vendidas !== null);
    if (chart) { chart.destroy(); chart = null; }
    chart = new Chart(canvas.getContext('2d'), {
      type: 'doughnut',
      data: {
        labels: valid.map(t => (t.tipo_habitacion || 'Sin tipo') + (t.propiedad ? ' · ' + t.propiedad : '')),
        datasets: [{
          data: valid.map(t => Number(t.noches_vendidas) || 0),
          backgroundColor: ['#1e3a4a', '#4f7466', '#a98b5f', '#6d8ea3', '#7c6a8a', '#b79a72'],
          borderWidth: 2,
          borderColor: '#fff'
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '58%',
        plugins: {
          legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } }
        }
      }
    });
  }

  function numeric(value) {
    return value === null || value === undefined || !isFinite(value) ? 0 : Number(value);
  }

  function focusOperations(item) {
    if (!item || !item.habitacion) return;
    if (window.Operaciones && typeof window.Operaciones.focusRoom === 'function') {
      window.Operaciones.focusRoom(item.propiedad_id, item.habitacion);
      return;
    }
    const nav = document.querySelector('.nav-item[data-view="operaciones"]');
    if (nav) nav.click();
  }

  function renderRoomNumbers(rooms) {
    const tbody = $('#roomNumberTable tbody');
    if (!tbody) return;
    tbody.replaceChildren();
    rooms.forEach(item => {
      const tr = document.createElement('tr');
      cell(tr, item.habitacion || '—');
      cell(tr, item.propiedad || '—');
      cell(tr, item.tipo_habitacion || '—');
      cell(tr, item.huesped || '—');
      cell(tr, dash(item.reservas));
      cell(tr, dash(item.noches_vendidas));
      cell(tr, fmtPct(item.ocupacion));
      cell(tr, fmtMoney(item.adr));
      cell(tr, fmtMoney(item.ingresos));
      const td = document.createElement('td');
      const open = numeric(item.incidencias_abiertas);
      if (open > 0) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-ghost btn-incident';
        btn.textContent = open + ' abierta' + (open === 1 ? '' : 's');
        btn.title = 'Abrir las incidencias de esta habitación en Operación Hotelera';
        btn.addEventListener('click', () => focusOperations(item));
        td.appendChild(btn);
      } else {
        td.textContent = 'Sin incidencias';
        td.className = 'cell-muted';
      }
      tr.appendChild(td);
      tbody.appendChild(tr);
    });
    const empty = $('#roomNumberEmpty');
    if (empty) {
      empty.hidden = rooms.length > 0;
      if (!rooms.length) empty.textContent = 'Sin estancias con número de habitación para el período seleccionado.';
    }
    const count = $('#roomNumberCount');
    if (count) count.textContent = String(rooms.length);
  }

  function renderDetail(detalle, total, limitado) {
    const tbody = $('#roomDetailTable tbody');
    tbody.replaceChildren();
    detalle.forEach(item => {
      const tr = document.createElement('tr');
      cell(tr, item.habitacion || '—');
      cell(tr, item.propiedad || '—');
      cell(tr, item.tipo_habitacion || '—');
      cell(tr, item.huesped || '—');
      cell(tr, item.fecha || '—');
      cell(tr, dash(item.noches));
      cell(tr, item.valor === null || item.valor === undefined ? '—' : fmtMoney(item.valor, item.moneda));
      cell(tr, numeric(item.incidencias_abiertas) > 0 ? String(item.incidencias_abiertas) : '—');
      tbody.appendChild(tr);
    });
    const empty = $('#roomDetailEmpty');
    if (empty) empty.hidden = detalle.length > 0;
    const count = $('#roomDetailCount');
    if (count) {
      const totalValue = typeof total === 'number' ? total : detalle.length;
      count.textContent = limitado ? detalle.length + ' de ' + totalValue : String(detalle.length);
    }
  }

  function renderCapacities() {
    const tbody = $('#capacityTable tbody');
    if (!tbody) return;
    tbody.replaceChildren();
    capacityRows.forEach(item => {
      const tr = document.createElement('tr');
      cell(tr, item.propiedad || '—');
      cell(tr, item.tipo_habitacion || '—');
      cell(tr, item.habitaciones);
      cell(tr, item.actualizado_por || '—');
      const td = document.createElement('td');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn btn-ghost';
      btn.textContent = 'Eliminar';
      btn.disabled = !canEdit();
      btn.addEventListener('click', async () => {
        if (!window.confirm('¿Eliminar la capacidad declarada de este tipo?')) return;
        try {
          await window.API.roomCapacityDelete(item.id);
          notify('Capacidad eliminada.', 'success');
          await loadCapacities();
          await loadSummary();
        } catch (e) { notify('No se pudo eliminar: ' + e.message, 'error'); }
      });
      td.appendChild(btn);
      tr.appendChild(td);
      tbody.appendChild(tr);
    });
    const empty = $('#capacityEmpty');
    if (empty) empty.hidden = capacityRows.length > 0;
  }

  async function loadSummary() {
    try {
      const data = await window.API.roomSummary(
        selectedProperty(), currency(), dateFrom(), dateTo()
      );
      renderKpis(data.resumen || {});
      renderTypes(data.tipos || []);
      renderRoomNumbers(data.por_habitacion || []);
      renderDetail(data.detalle || [], data.detalle_total, data.detalle_limitado);
      const select = $('#roomProperty');
      if (select && data.propiedades) {
        const current = select.value;
        select.replaceChildren(new Option('Todas las propiedades', ''));
        data.propiedades.forEach(p => select.add(new Option(p.nombre, p.id)));
        if (current) select.value = current;
      }
      const formSelect = $('#capProperty');
      if (formSelect && data.propiedades) {
        const current = formSelect.value;
        formSelect.replaceChildren(new Option('Selecciona una propiedad', ''));
        data.propiedades.forEach(p => formSelect.add(new Option(p.nombre, p.id)));
        if (current) formSelect.value = current;
        else if (data.propiedades.length === 1) formSelect.value = String(data.propiedades[0].id);
      }
    } catch (e) {
      notify('No se pudo cargar habitaciones: ' + e.message, 'error');
      const empty = $('#roomTypesEmpty');
      if (empty) { empty.hidden = false; empty.textContent = 'No se pudo cargar el análisis por habitación: ' + e.message; }
    }
  }

  async function loadCapacities() {
    try {
      capacityRows = await window.API.roomCapacities(selectedProperty());
      renderCapacities();
    } catch (e) {
      capacityRows = [];
      renderCapacities();
      notify('No se pudieron cargar las capacidades: ' + e.message, 'error');
    }
  }

  async function loadTypes() {
    const select = $('#capType');
    if (!select) return;
    try {
      const tipos = await window.API.roomTypes();
      const current = select.value;
      select.replaceChildren(new Option('Selecciona un tipo', ''));
      tipos.forEach(t => select.add(new Option(t.nombre, t.nombre)));
      if (current) select.value = current;
    } catch (e) {
      console.warn('No se pudieron cargar los tipos de habitación:', e.message);
    }
  }

  async function load() {
    await loadTypes();
    await Promise.all([loadSummary(), loadCapacities()]);
  }

  function init() {
    const form = $('#capacityForm');
    if (form) {
      form.addEventListener('submit', async event => {
        event.preventDefault();
        if (!canEdit()) return;
        const marcaId = Number($('#capProperty').value);
        const tipo = $('#capType').value.trim();
        const habitaciones = Number($('#capRooms').value);
        if (!marcaId || !tipo) {
          notify('Selecciona la propiedad y el tipo de habitación.', 'error');
          return;
        }
        if (!Number.isFinite(habitaciones) || habitaciones < 0) {
          notify('La capacidad debe ser un número igual o mayor que cero.', 'error');
          return;
        }
        try {
          await window.API.roomCapacitySave({
            marca_id: marcaId,
            tipo_habitacion: tipo,
            habitaciones: Math.round(habitaciones)
          });
          notify('Capacidad guardada.', 'success');
          await Promise.all([loadCapacities(), loadSummary()]);
        } catch (e) { notify('No se pudo guardar la capacidad: ' + e.message, 'error'); }
      });
    }

    const property = $('#roomProperty');
    if (property) property.addEventListener('change', async () => { await Promise.all([loadSummary(), loadCapacities()]); });
    const refresh = $('#roomRefresh');
    if (refresh) refresh.addEventListener('click', load);

    ['moneda', 'desde', 'hasta'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('change', () => {
        const view = document.getElementById('view-habitaciones');
        if (view && view.classList.contains('active')) loadSummary();
      });
    });
  }

  init();
  window.Habitaciones = { load };
})();
