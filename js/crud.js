/* ============================================================
   NORUS & LUUM · CRUD de la base de datos (reservas)
   Create · Read · Update · Delete con persistencia en el
   servidor (API FastAPI + PostgreSQL/SQLite).
   ============================================================ */
(function () {
  'use strict';

  const $ = (s) => document.querySelector(s);

  function auditoria(accion, detalle) {
    try {
      if (window.API && window.API.getToken()) {
        window.API.registrarEvento(accion, detalle).catch(function () {});
      }
    } catch (e) { /* nunca romper el CRUD por auditoría */ }
  }

  // ---------- Estado ----------
  const PAG = 8;
  let pagina = 1;
  let reservasPagina = [];
  let totalReservas = 0;
  let canalesDisponibles = [];
  let modoLocal = false;
  let secuenciaCarga = 0;

  // ---------- Cargar reservas del servidor (paginación real) ----------
  async function cargarReservas() {
    const id = ++secuenciaCarga;
    try {
      const f = getFiltros();
      const propiedad = $('#propiedad') ? $('#propiedad').value : '';
      const skip = (pagina - 1) * PAG;
      const datos = await window.API.listarReservas(skip, PAG, f.q, f.canal, propiedad, f.habitacion, true);
      if (id !== secuenciaCarga) return; //descarta respuestas obsoletas
      reservasPagina = Array.isArray(datos.items) ? datos.items : [];
      totalReservas = Number(datos.total) || 0;
      canalesDisponibles = Array.isArray(datos.canales) ? datos.canales : [];
      modoLocal = false;
      const totalPag = Math.max(1, Math.ceil(totalReservas / PAG));
      if (pagina > totalPag) { pagina = totalPag; await cargarReservas(); return; }
      refillCanales();
      render();
    } catch (err) {
      if (id !== secuenciaCarga) return;
      console.warn('Error cargando reservas:', err);
      // Fallback solo si el servidor no responde: se pagina el dataset local.
      if (typeof Datos !== 'undefined' && Datos.reservas) {
        modoLocal = true;
        canalesDisponibles = [...new Set(Datos.reservas.map(r => r.canal).filter(Boolean))].sort();
        refillCanales();
        render();
      }
    }
  }

  // ---------- Filtros ----------
  function getFiltros() {
    const q = ($('#crudBuscar').value || '').toLowerCase();
    const canal = $('#crudFiltroCanal').value;
    const habitacion = ($('#crudBuscarHabitacion').value || '').trim().toLowerCase();
    return { q, canal, habitacion };
  }

  function registrosFiltrados() {
    if (!modoLocal) return reservasPagina; //el servidor ya aplicó filtros y paginación
    const { q, canal, habitacion } = getFiltros();
    return (Datos.reservas || []).filter(r => {
      const texto = [r.codigo, r.huesped, r.habitacion, r.nacionalidad, r.canal, r.estado, r.fecha].join(' ').toLowerCase();
      const okQ = !q || texto.includes(q);
      const okC = !canal || (r.canal || '') === canal;
      const okH = !habitacion || String(r.habitacion || '').toLowerCase().includes(habitacion);
      return okQ && okC && okH;
    });
  }

  // ---------- Rellenar filtro de canal ----------
  function refillCanales() {
    const sel = $('#crudFiltroCanal');
    const actual = sel.value;
    sel.innerHTML = '<option value="">Todos los canales</option>' +
      canalesDisponibles.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
    if (actual && canalesDisponibles.includes(actual)) sel.value = actual;
  }

  // ---------- Formateo ----------
  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s === undefined || s === null ? '' : String(s);
    return d.innerHTML;
  }
  function fmtMoney(n, currency) {
    const v = Number(n);
    if (isNaN(v)) return (currency || Datos.moneda || 'CLP') + ' 0';
    return (currency || Datos.moneda || 'CLP') + ' ' + v.toLocaleString('es-CL', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  }
  function fmtFecha(f) {
    if (!f) return '—';
    const s = String(f);
    if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10);
    return s;
  }

  // ---------- Clases de estado ----------
  function claseEstado(estado) {
    const e = String(estado || '').toLowerCase();
    if (e.includes('confirm')) return 'ok';
    if (e.includes('check-in')) return 'in';
    if (e.includes('check-out')) return 'out';
    if (e.includes('cancel')) return 'cancel';
    if (e.includes('wait')) return 'wait';
    return 'ok';
  }

  // ---------- Render tabla ----------
  function render() {
    const lista = registrosFiltrados();
    const total = modoLocal ? lista.length : totalReservas;
    const totalPag = Math.max(1, Math.ceil(total / PAG));
    if (pagina > totalPag) pagina = totalPag;
    const slice = modoLocal ? lista.slice((pagina - 1) * PAG, pagina * PAG) : lista;

    $('#crudCountText').textContent =
      Datos._importado
        ? 'Base importada: ' + (Datos._formatoOrigen || '') + ' · ' + total + ' registros'
        : total + ' reservas en la base de datos';

    const body = $('#crudBody');
    if (!slice.length) {
      body.innerHTML = '';
      $('#crudEmpty').style.display = 'block';
      $('#crudPagination').innerHTML = '';
      return;
    }
    $('#crudEmpty').style.display = 'none';

    body.innerHTML = slice.map(r => `
      <tr>
        <td class="mono">${esc(r.codigo)}</td>
        <td>${esc(r.huesped)}</td>
        <td class="mono">${esc(r.habitacion || '—')}</td>
        <td>${esc(r.nacionalidad)}</td>
        <td><span class="chip-chip">${esc(r.canal)}</span></td>
        <td>${fmtFecha(r.fecha)}</td>
        <td class="mono">${fmtMoney(r.valor, r.moneda)}</td>
        <td><span class="stato stato-${claseEstado(r.estado)}">${esc(r.estado)}</span></td>
        <td class="acciones">
          <button class="mini-btn edit" data-id="${r.id}" title="Editar" aria-label="Editar reserva"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20h4l10-10-4-4L4 16v4z"/><path d="M13.5 6.5l4 4"/></svg></button>
          <button class="mini-btn del" data-id="${r.id}" title="Eliminar" aria-label="Eliminar reserva"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16"/><path d="M9 7V5h6v2"/><path d="M6 7l1 13h10l1-13"/><path d="M10 11v6M14 11v6"/></svg></button>
        </td>
      </tr>
    `).join('');

    // Paginación
    let p = '';
    if (totalPag > 1) {
      p += `<button class="pg" data-pg="${Math.max(1, pagina-1)}" ${pagina===1?'disabled':''}>‹</button>`;
      for (let i = 1; i <= totalPag; i++) {
        p += `<button class="pg ${i===pagina?'act':''}" data-pg="${i}">${i}</button>`;
      }
      p += `<button class="pg" data-pg="${Math.min(totalPag, pagina+1)}" ${pagina===totalPag?'disabled':''}>›</button>`;
    }
    $('#crudPagination').innerHTML = p;
  }

  // ---------- Abrir formulario ----------
  let lastFormFocus = null;
  function abrirForm(r) {
    $('#crudModalTitle').textContent = r ? 'Editar Reserva' : 'Nueva Reserva';
    $('#crudId').value = r ? r.id : '';
    $('#fCodigo').value = r ? (r.codigo || '') : ('RES-' + Date.now().toString().slice(-5));
    $('#fHuesped').value = r ? (r.huesped || '') : '';
    $('#fHabitacion').value = r ? (r.habitacion || '') : '';
    if (r && r.nacionalidad) {
      const sel = $('#fNacionalidad');
      if ([...sel.options].some(o => o.value === r.nacionalidad)) sel.value = r.nacionalidad;
    }
    if (r && r.canal) {
      const sel = $('#fCanal');
      if ([...sel.options].some(o => o.value === r.canal)) sel.value = r.canal;
    }
    let fecha = '';
    if (r && r.fecha) {
      const s = String(r.fecha).slice(0, 10);
      if (/^\d{4}-\d{2}-\d{2}/.test(s)) fecha = s;
    }
    $('#fFecha').value = fecha;
    $('#fValor').value = r ? (r.valor || '') : '';
    $('#fMoneda').value = r && r.moneda ? r.moneda : ($('#moneda').value || 'CLP');
    if (r && r.estado) {
      const sel = $('#fEstado');
      if ([...sel.options].some(o => o.value === r.estado)) sel.value = r.estado;
    }
    lastFormFocus = document.activeElement;
    $('#crudModalOverlay').classList.add('show');
    $('#crudModalOverlay').setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
    $('#fCodigo').focus();
  }

  function cerrarForm() {
    const modal = $('#crudModalOverlay');
    if (!modal.classList.contains('show')) return;
    modal.classList.remove('show');
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('modal-open');
    if (lastFormFocus && typeof lastFormFocus.focus === 'function') lastFormFocus.focus();
  }

  // ---------- Guardar (create/update) ----------
  async function guardar(e) {
    e.preventDefault();
    const id = $('#crudId').value;
    const huesped = $('#fHuesped').value.trim();
    if (!huesped) { notificar('El nombre del huésped es obligatorio', 'error'); return; }
    const registro = {
      codigo: $('#fCodigo').value.trim() || ('RES-' + Date.now().toString().slice(-5)),
      huesped,
      habitacion: ($('#fHabitacion') ? $('#fHabitacion').value.trim() : ''),
      nacionalidad: $('#fNacionalidad').value,
      canal: $('#fCanal').value,
      fecha: $('#fFecha').value,
      valor: Number($('#fValor').value) || 0,
      moneda: $('#fMoneda').value.trim().toUpperCase(),
      estado: $('#fEstado').value
    };
    try {
      if (id) {
        await window.API.actualizarReserva(Number(id), registro);
        notificar('Reserva actualizada: ' + registro.codigo, 'success');
        auditoria('reserva_editar', 'Reserva ' + registro.codigo + ' actualizada');
      } else {
        const globalProperty = $('#propiedad') ? $('#propiedad').value : '';
        if (globalProperty) registro.propiedad_id = Number(globalProperty);
        await window.API.crearReserva(registro);
        notificar('Reserva creada: ' + registro.codigo, 'success');
        auditoria('reserva_crear', 'Nueva reserva ' + registro.codigo + ' · ' + registro.huesped);
        pagina = 1; //la reserva nueva se ordena primero en el servidor
      }
      cerrarForm();
      await cargarReservas();
    } catch (err) {
      notificar('Error: ' + err.message, 'error');
    }
  }

  // ---------- Eliminar ----------
  async function eliminar(id) {
    const r = reservasPagina.find(x => x.id === id);
    const nombre = r ? (r.codigo + ' · ' + r.huesped) : ('#' + id);
    if (!confirm('¿Eliminar la reserva ' + nombre + '?')) return;
    try {
      await window.API.eliminarReserva(id);
      notificar('Reserva eliminada: ' + nombre, 'success');
      auditoria('reserva_eliminar', 'Reserva eliminada ' + nombre);
      await cargarReservas();
    } catch (err) {
      notificar('Error: ' + err.message, 'error');
    }
  }

  // ---------- Event listeners ----------
  function setup() {
    $('#btnNuevo').addEventListener('click', () => abrirForm(null));
    $('#crudModalClose').addEventListener('click', cerrarForm);
    $('#crudCancel').addEventListener('click', cerrarForm);
    $('#crudForm').addEventListener('submit', guardar);
    $('#crudModalOverlay').addEventListener('click', (e) => { if (e.target === $('#crudModalOverlay')) cerrarForm(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') cerrarForm(); });

    let temporizadorBusqueda;
    function recargarDesdeInicio() {
      pagina = 1;
      if (modoLocal) render();
      else cargarReservas();
    }
    ['#crudBuscar', '#crudBuscarHabitacion'].forEach(selector => {
      const input = $(selector);
      if (input) input.addEventListener('input', () => {
        clearTimeout(temporizadorBusqueda);
        temporizadorBusqueda = setTimeout(recargarDesdeInicio, 250);
      });
    });
    $('#crudFiltroCanal').addEventListener('change', recargarDesdeInicio);

    $('#crudBody').addEventListener('click', (e) => {
      const ed = e.target.closest('.edit');
      const del = e.target.closest('.del');
      if (ed) {
        const r = reservasPagina.find(x => x.id === Number(ed.dataset.id));
        if (r) abrirForm(r);
      } else if (del) {
        eliminar(Number(del.dataset.id));
      }
    });
    $('#crudPagination').addEventListener('click', (e) => {
      const pg = e.target.closest('.pg');
      if (pg && !pg.disabled) {
        pagina = Number(pg.dataset.pg);
        if (modoLocal) render();
        else cargarReservas();
      }
    });

    cargarReservas();
  }

  // Re-render al importar nuevos datos desde otro módulo
  window.onCrudRefresh = function () {
    pagina = 1;
    cargarReservas();
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setup);
  else setup();
})();
