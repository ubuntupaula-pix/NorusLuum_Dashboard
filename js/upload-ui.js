/* ============================================================
   NORUS & LUUM · Controlador de la interfaz de importación
   Gestiona el modal, el drag & drop, el mapeo de columnas y
   el re-renderizado en tiempo real del dashboard.
   ============================================================ */
(function () {
  'use strict';

  const $ = (s) => document.querySelector(s);

  // ---------- Elementos ----------
  const overlay = $('#modalOverlay');
  const stepFile = $('#stepFile');
  const stepMap = $('#stepMap');
  const dropzone = $('#dropzone');
  const fileInput = $('#fileInput');
  const fileInfo = $('#fileInfo');
  const fileExt = $('#fileExt');
  const fileName = $('#fileName');
  const fileSize = $('#fileSize');
  const validateMsg = $('#validateMsg');
  const fileClear = $('#fileClear');
  const btnNext = $('#btnNext');
  const btnBack = $('#btnBack');
  const btnApply = $('#btnApply');
  const dataPreview = $('#dataPreview');
  const mapping = $('#mapping');
  const toast = $('#toast');

  const CAMPOS = [
    { key: 'fecha', label: 'Fecha / Período' },
    { key: 'mes', label: 'Mes' },
    { key: 'anio', label: 'Año' },
    { key: 'ocupacion', label: 'Ocupación (%)' },
    { key: 'adr', label: 'Tarifa Promedio (ADR)' },
    { key: 'revpar', label: 'RevPAR' },
    { key: 'ingresos', label: 'Ingresos' },
    { key: 'noches', label: 'Noches Vendidas' },
    { key: 'habitaciones_disp', label: 'Habitaciones Disponibles' },
    { key: 'canal', label: 'Canal de Venta' },
    { key: 'nacionalidad', label: 'Nacionalidad / Mercado' },
    { key: 'segmento', label: 'Segmento' },
    { key: 'anticipacion', label: 'Anticipación (días)' },
    { key: 'incidencias', label: 'Incidencias (cantidad)' },
    { key: 'tipo_incidencia', label: 'Tipo de incidencia' },
    { key: 'area', label: 'Área / departamento' },
    { key: 'tiempo_resol', label: 'Tiempo de Resolución (h)' },
    { key: 'satisfaccion', label: 'Satisfacción' },
    { key: 'nps', label: 'NPS' },
    { key: 'tipo_habitacion', label: 'Tipo de Habitación' },
    { key: 'habitacion', label: 'Número de Habitación' },
    { key: 'valor', label: 'Valor / Monto' },
    { key: 'moneda', label: 'Moneda ISO 4217' },
    { key: 'marca', label: 'Hotel / Propiedad' },
    { key: 'codigo', label: 'Código de Reserva' },
    { key: 'huesped', label: 'Huésped / Nombre' },
    { key: 'estado', label: 'Estado de Reserva' }
  ];

  let archivoCargado = false;

  // ---------- Toast ----------
  function notificar(msg, tipo) {
    toast.textContent = msg;
    toast.className = 'toast show ' + (tipo || 'info');
    clearTimeout(notificar._t);
    notificar._t = setTimeout(() => toast.classList.remove('show'), 4200);
  }
  window.notificar = notificar;

  // ---------- Abrir / cerrar modal ----------
  let lastFocus = null;
  function abrir() {
    if (overlay.classList.contains('show')) return;
    lastFocus = document.activeElement;
    overlay.classList.add('show');
    overlay.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
    $('#modalClose').focus();
  }
  function cerrar() {
    if (!overlay.classList.contains('show')) return;
    overlay.classList.remove('show');
    overlay.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('modal-open');
    if (lastFocus && typeof lastFocus.focus === 'function') lastFocus.focus();
  }

  $('#btnUpload').addEventListener('click', abrir);
  $('#modalClose').addEventListener('click', cerrar);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) cerrar(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') cerrar(); });

  // ---------- Drag & drop ----------
  ['dragenter','dragover'].forEach(ev => dropzone.addEventListener(ev, (e) => {
    e.preventDefault(); dropzone.classList.add('dragging');
  }));
  ['dragleave','drop'].forEach(ev => dropzone.addEventListener(ev, (e) => {
    e.preventDefault(); dropzone.classList.remove('dragging');
  }));
  dropzone.addEventListener('drop', (e) => {
    const f = e.dataTransfer.files[0];
    if (f) cargarArchivo(f);
  });
  dropzone.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', (e) => {
    if (e.target.files[0]) cargarArchivo(e.target.files[0]);
  });
  fileClear.addEventListener('click', () => limpiar());

  // ---------- Formatear tamaño ----------
  function fmtSize(n) {
    if (n < 1024) return n + ' B';
    if (n < 1048576) return (n/1024).toFixed(1) + ' KB';
    return (n/1048576).toFixed(1) + ' MB';
  }

  // ---------- Cargar archivo ----------
  async function cargarArchivo(file) {
    const okExt = /\.(csv|tsv|xlsx|json)$/i.test(file.name);
    if (!okExt) {
      notificar('Formato no soportado. Usa CSV, TSV, Excel XLSX o JSON.', 'error');
      return;
    }
    if (file.size > 512 * 1024 * 1024) {
      notificar('El archivo supera el máximo de 512 MB.', 'error');
      return;
    }
    try {
      const res = await window.Importador.procesar(file);
      archivoCargado = true;
      fileExt.textContent = '.' + file.name.split('.').pop();
      fileName.textContent = file.name;
      fileSize.textContent = fmtSize(file.size) + ' · ' + res.total + ' filas · ' + res.columnas.length + ' columnas';
      validateMsg.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12.5l5 5L20 6.5"/></svg><span>' + res.columnas.length + ' columnas detectadas. Revisa el mapeo y continúa.</span>';
      dropzone.style.display = 'none';
      fileInfo.style.display = 'block';
      guardarEstadoMap(res.mapeo);
      btnNext.disabled = false;
      notificar('Archivo cargado: ' + file.name, 'success');
    } catch (err) {
      notificar('Error al leer el archivo: ' + err.message, 'error');
    }
  }

  function limpiar() {
    archivoCargado = false;
    dropzone.style.display = '';
    fileInfo.style.display = 'none';
    fileInput.value = '';
    window.Importador.reset();
  }

  // ---------- Guardar estado importante ----------
  let ultimoMapeo = {};
  function guardarEstadoMap(m) { ultimoMapeo = Object.assign({}, m); }

  // ---------- Vista previa ----------
  function renderPreview() {
    const cols = window.Importador.columnas;
    let html = '<div class="pv-head">Vista previa (' + cols.length + ' columnas)</div><div class="pv-table-wrap"><table class="pv-table"><thead><tr>';
    cols.forEach(c => html += '<th>' + esc(c) + '</th>');
    html += '</tr></thead><tbody>';
    const filas = window.Importador.filas;
    filas.forEach(r => {
      html += '<tr>' + cols.map(c => '<td>' + esc(r[c] !== undefined ? r[c] : '') + '</td>').join('') + '</tr>';
    });
    html += '</tbody></table></div>';
    dataPreview.innerHTML = html;
  }

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  // ---------- Renderizar mapeo ----------
  function renderMapping() {
    // reconstruir opciones de columnas
    const cols = window.Importador.columnas;
    let html = CAMPOS.map(campo => {
      const sel = ultimoMapeo[campo.key] || '';
      return `
        <div class="map-row">
          <span class="map-label" title="${esc(campo.label)}">${esc(campo.label)}</span>
          <select data-kpi="${campo.key}">
            <option value="">— Sin asignar —</option>
            ${cols.map(c => `<option value="${esc(c)}" ${sel===c?'selected':''}>${esc(c)}</option>`).join('')}
          </select>
        </div>`;
    }).join('');
    mapping.innerHTML = html;
  }

  // ---------- Flujos ----------
  btnNext.addEventListener('click', () => {
    guardarEstadoMap(window.Importador.mapeo);
    renderPreview();
    renderMapping();
    stepFile.style.display = 'none';
    stepMap.style.display = 'block';
  });

  btnBack.addEventListener('click', () => {
    stepMap.style.display = 'none';
    stepFile.style.display = 'block';
  });

  // Escuchar cambios de mapeo
  mapping.addEventListener('change', (e) => {
    if (e.target.matches('select[data-kpi]')) {
      ultimoMapeo[e.target.dataset.kpi] = e.target.value;
      window.Importador.setMapeo(ultimoMapeo);
    }
  });

  btnApply.addEventListener('click', async () => {
    try {
      const archivo = window.Importador.archivo;
      if (!archivo || !window.API || !window.API.getToken()) throw new Error('Inicia sesión para importar al servidor.');
      btnApply.disabled = true;
      btnApply.textContent = 'Procesando…';
      const resultado = await window.API.importar(archivo, window.Importador.mapeo);
      if (!resultado.filas_exitosas) throw new Error(resultado.mensaje || 'El servidor no pudo procesar ninguna fila.');
      if (window.onDatosImportados) await window.onDatosImportados(resultado);
      cerrar();
      const resumen = resultado.filas_fallidas
        ? `${resultado.filas_exitosas} filas cargadas; ${resultado.filas_fallidas} rechazadas. Revisa el historial de carga.`
        : `${resultado.filas_exitosas} filas cargadas correctamente.`;
      notificar(resumen, resultado.filas_fallidas ? 'info' : 'success');
      const est = $('#estadoTexto');
      est.textContent = 'Datos del servidor · ' + resultado.nombre_archivo;
      document.querySelector('#estadoPMS .dot').style.background = '#27ae60';
      if (window.onCrudRefresh) window.onCrudRefresh();
    } catch (err) {
      notificar('Error al aplicar: ' + err.message, 'error');
    } finally {
      btnApply.disabled = false;
      btnApply.textContent = 'Procesar e importar';
    }
  });

  // ---------- Plantilla vacía para evitar introducir datos ficticios ----------
  $('#btnTemplate').addEventListener('click', () => {
    const cabecera = ['codigo','huesped','habitacion','fecha','marca','moneda','ocupacion','adr','revpar','ingresos','noches','habitaciones_disp','canal','nacionalidad','segmento','anticipacion','tipo_habitacion','estado'];
    const csv = cabecera.join(',') + '\n';
    const blob = new Blob(['\uFEFF'+csv], { type: 'text/csv;charset=utf-8;' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'plantilla_vacia_norus_luum.csv';
    a.click();
  });
})();
