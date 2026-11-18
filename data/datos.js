/* Empty presentation state. Production metrics are loaded from the authenticated API. */
const Datos = {
  moneda: 'CLP',
  meses: [], ocupacionMensual: [], adrMensual: [], revparMensual: [], ingresosMensuales: [],
  canales: { labels: [], valores: [], ingresos: [], anticipacion: [] },
  nacionalidad: { labels: [], valores: [] },
  segmentos: { labels: [], adr: [], ocupacion: [] },
  anticipacion: { labels: [], valores: [] },
  incidencias: { tipos: [], valores: [], meses: [], serie: [], resolucion: { labels: [], horas: [] } },
  satisfaccion: { areas: [], nota: [], encuestas: { labels: [], satisfecho: [], neutral: [], insatisfecho: [] } },
  habitaciones: { labels: [], ingresos: [] },
  kpi: {
    anio: {
      ocupacion: { valor: null, delta: null, sub: 'Sin capacidad reportada' },
      adr: { valor: null, delta: null, sub: 'Sin datos' },
      revpar: { valor: null, delta: null, sub: 'Sin datos' },
      ingresos: { valor: null, delta: null, sub: 'Sin datos' },
      noches: { valor: null, delta: null, sub: 'Sin datos' },
      nps: { valor: null, delta: null, sub: 'Sin encuestas' },
      incidencias: { valor: null, delta: null, sub: 'Sin datos' },
      satisfaccion: { valor: null, delta: null, sub: 'Sin encuestas' }
    }, impor: {} },
  reservasTotal: 0, kpiReservas: { anticipacion_media: null, mercados: 0, reservas_anticipadas_pct: null }, reservas: [], marcas: [], ocupacionMarca: [], adrMarca: [], ingresosMarca: [],
  marcaColores: [], kpiMarca: [], temporadas: { meses: [], series: {} },
  revenue: { labels: [], adr: [], ocupacion: [] }, nacionalidadMarca: { labels: [], series: {} },
  nps: { score: null, promotores: null, neutros: null, detractores: null, fill: 0 },
  diaSemana: { labels: [], valores: [] }, aportacionCanal: { directo: null, ota: null, gds: null, otros: null }
};
