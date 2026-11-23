/* Importación segura: vista previa y mapeo antes del ETL autenticado del servidor. */
(function () {
  'use strict';

  const CAMPOS = [
    ['fecha', /fecha|date|periodo|period|check.?in|arrival/i],
    ['mes', /mes|month/i],
    ['anio', /año|anio|year|yr/i],
    ['ocupacion', /ocupacion|occup|occ%/i],
    ['adr', /\badr\b|tarifa.*prom|avg.*rate/i],
    ['revpar', /revpar/i],
    ['ingresos', /ingreso|revenue|venta|sales|income/i],
    ['noches', /noches|rooms.*sold|roomnights|nights/i],
    ['habitaciones_disp', /habitaciones?.*(disponib|available)|room.*available/i],
    ['canal', /canal|channel|ota|booking.*source/i],
    ['nacionalidad', /nacionalid|country|nationality|mercado|market/i],
    ['segmento', /segmento|segment/i],
    ['anticipacion', /anticipac|lead.*time/i],
    ['incidencias', /incidencia|complaint|issue/i],
    ['tipo_incidencia', /tipo.*incid|issue.*type|complaint.*type/i],
    ['area', /area|department|service.*area/i],
    ['tiempo_resol', /resoluc|resolut/i],
    ['satisfaccion', /satisfac|csat/i],
    ['nps', /nps/i],
    ['tipo_habitacion', /tipo.*habitacion|room.*type/i],
    ['habitacion', /num(ero)?.*habitacion|habitacion.*(num|nro|no\b|#)|^habitacion$|^room\s*(number|num|no|#)?$/i],
    ['valor', /valor|monto|amount|importe|precio|price/i],
    ['moneda', /moneda|currency|divisa/i],
    ['marca', /marca|hotel|propiedad|property/i],
    ['codigo', /codigo|booking.*id|reservation.*id|folio|reference/i],
    ['huesped', /huesped|guest|nombre|customer/i],
    ['estado', /estado|status|state/i]
  ];

  let archivo = null;
  let filas = [];
  let columnas = [];
  let mapeo = {};
  const MAX_BYTES = 1024 * 1024 * 1024;
  const MAX_ROWS = 1000000;
  const MAX_COLUMNS = 200;

  function detectarMapeo(cols) {
    const result = {};
    cols.forEach(col => {
      const nombre = String(col).normalize('NFD').replace(/[\u0300-\u036f]/g, '');
      for (const [key, pattern] of CAMPOS) {
        if (!result[key] && pattern.test(nombre)) { result[key] = col; break; }
      }
    });
    return result;
  }

  function parsearJson(texto) {
    const limpio = texto.replace(/^\uFEFF/, '').trim();
    if (!limpio) return [];
    let datos;
    try {
      datos = JSON.parse(limpio);
    } catch (err) {
      // NDJSON: un objeto por línea.
      const lineas = limpio.split(/\r?\n/).filter(linea => linea.trim());
      if (!lineas.length) throw err;
      datos = lineas.map(linea => JSON.parse(linea));
    }
    if (Array.isArray(datos)) return datos;
    if (datos && typeof datos === 'object') {
      for (const clave of ['data', 'rows', 'registros', 'filas', 'items']) {
        if (Array.isArray(datos[clave])) return datos[clave];
      }
      return [datos];
    }
    throw new Error('El JSON no contiene registros.');
  }

  async function leer(file) {
    if (file.size > MAX_BYTES) throw new Error('El archivo supera el máximo de 1 GB.');
    const ext = file.name.split('.').pop().toLowerCase();
    if (!['csv', 'tsv', 'txt', 'xlsx', 'json'].includes(ext)) throw new Error('Usa CSV, TSV, XLSX o JSON.');
    if (ext === 'csv' || ext === 'tsv' || ext === 'txt') {
      const text = (await file.text()).replace(/^\uFEFF/, '');
      const parsed = Papa.parse(text, {
        header: true,
        skipEmptyLines: 'greedy',
        transformHeader: h => String(h || '').trim(),
        delimiter: ext === 'tsv' ? '\t' : '',
        delimitersToGuess: [',', ';', '\t', '|'],
      });
      if (parsed.errors.some(e => e.type === 'Delimiter')) throw new Error('No se pudo detectar el separador del archivo.');
      filas = (parsed.data || []).filter(row => Object.values(row).some(v => String(v ?? '').trim() !== ''));
      columnas = (parsed.meta.fields || []).filter(Boolean);
    } else if (ext === 'xlsx') {
      const workbook = XLSX.read(await file.arrayBuffer(), { type: 'array', cellDates: true });
      const sheet = workbook.Sheets[workbook.SheetNames[0]];
      if (!sheet) throw new Error('El Excel no contiene hojas.');
      filas = XLSX.utils.sheet_to_json(sheet, { defval: '' });
      columnas = filas.length ? Object.keys(filas[0]) : [];
    } else {
      const datos = parsearJson(await file.text());
      filas = datos.filter(row => row && typeof row === 'object' && !Array.isArray(row));
      columnas = [...new Set(filas.flatMap(row => Object.keys(row)))];
    }
    if (!filas.length || !columnas.length) throw new Error('El archivo no contiene filas tabulares con encabezados.');
    if (columnas.length > MAX_COLUMNS || filas.length > MAX_ROWS) {
      throw new Error('Máximo permitido: ' + MAX_COLUMNS + ' columnas y ' + MAX_ROWS.toLocaleString('es-CL') + ' filas.');
    }
    archivo = file;
    mapeo = detectarMapeo(columnas);
    return { total: filas.length, columnas: columnas.slice(), mapeo: { ...mapeo } };
  }

  window.Importador = {
    get archivo() { return archivo; },
    get columnas() { return columnas.slice(); },
    get mapeo() { return { ...mapeo }; },
    get filas() { return filas.slice(0, 8); },
    procesar: leer,
    setMapeo(value) { mapeo = { ...value }; },
    reset() { archivo = null; filas = []; columnas = []; mapeo = {}; }
  };
})();
