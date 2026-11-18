/* ============================================================
   NORUS & LUUM · Cliente API
   Comunicación con el backend FastAPI (sesión HttpOnly + CSRF).
   ============================================================ */
(function () {
  'use strict';

  const API_BASE = '/api';
  // The credential itself is an HttpOnly SameSite cookie. sessionStorage only
  // records a non-secret UI hint and is never sent to the server.
  let sessionHint = sessionStorage.getItem('norus_session_active') === '1';

  function setToken(t) {
    sessionHint = Boolean(t);
    if (sessionHint) sessionStorage.setItem('norus_session_active', '1');
    else sessionStorage.removeItem('norus_session_active');
  }

  function cookie(name) {
    const pair = document.cookie.split('; ').find(row => row.startsWith(name + '='));
    return pair ? decodeURIComponent(pair.slice(name.length + 1)) : '';
  }

  function clearSessionHint() {
    setToken(null);
    sessionStorage.removeItem('norus_user');
    sessionStorage.removeItem('norus_role');
    sessionStorage.removeItem('norus_props');
  }

  function redirectToLogin() {
    clearSessionHint();
    if (!/login\.html$/.test(window.location.pathname)) window.location.href = 'login.html';
  }

  async function request(method, path, body, allowAnonymous) {
    const headers = { 'Content-Type': 'application/json' };
    // Double-submit CSRF: the readable cookie must match the request header.
    if (method !== 'GET' && method !== 'HEAD') {
      const token = cookie('norus_csrf');
      if (token) headers['X-CSRF-Token'] = token;
    }

    const opts = { method, headers, credentials: 'same-origin' };
    if (body !== undefined) opts.body = JSON.stringify(body);

    let res;
    try {
      res = await fetch(API_BASE + path, opts);
    } catch (err) {
      throw new Error('Error de conexión con el servidor. Verifique su red.');
    }

    if (res.status === 401 && !allowAnonymous) redirectToLogin();

    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const error = new Error(data.detail || 'Error ' + res.status);
      error.status = res.status;
      throw error;
    }
    return data;
  }

  async function upload(path, file, fields = {}) {
    const formData = new FormData();
    formData.append('file', file);
    Object.entries(fields).forEach(([key, value]) => formData.append(key, typeof value === 'string' ? value : JSON.stringify(value)));
    const headers = {};
    const token = cookie('norus_csrf');
    if (token) headers['X-CSRF-Token'] = token;

    const res = await fetch(API_BASE + path, {
      method: 'POST',
      headers,
      credentials: 'same-origin',
      body: formData,
    });

    if (res.status === 401) redirectToLogin();
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const error = new Error(data.detail || 'Error ' + res.status);
      error.status = res.status;
      throw error;
    }
    return data;
  }

  function query(params) {
    const search = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') search.set(key, value);
    });
    const text = search.toString();
    return text ? '?' + text : '';
  }

  window.API = {
    setToken,
    clearSessionHint,
    getToken: () => sessionHint ? 'cookie-session' : null,

    // Auth
    login: (username, password) => request('POST', '/auth/login', { username, password }, true),
    verifyMfa: (mfaToken, code) => request('POST', '/auth/mfa/verify', { mfa_token: mfaToken, code }, true),
    me: () => request('GET', '/auth/me'),
    logout: () => request('POST', '/auth/logout'),
    changePassword: (passwordActual, passwordNueva) =>
      request('POST', '/auth/password', { password_actual: passwordActual, password_nueva: passwordNueva }),

    // MFA management
    mfaSetup: () => request('POST', '/auth/mfa/setup'),
    mfaEnable: (code) => request('POST', '/auth/mfa/enable', { code }),
    mfaDisable: (password, code) => request('POST', '/auth/mfa/disable', { password, code }),

    // SSO
    ssoStatus: () => request('GET', '/auth/sso/status', undefined, true),

    // Dashboard
    dashboard: (currency, granularidad, desde, hasta, propiedadId) =>
      request('GET', '/dashboard' + query({ moneda: currency, granularidad, desde, hasta, propiedad_id: propiedadId })),
    dashboardCurrencies: () => request('GET', '/dashboard/monedas'),
    dashboardProperties: () => request('GET', '/dashboard/propiedades'),

    // Operación hotelera
    opsProperties: () => request('GET', '/operaciones/propiedades'),
    opsSummary: (propertyId) => request('GET', '/operaciones/resumen' + query({ propiedad_id: propertyId })),
    opsList: (module, propertyId, habitacion) => request('GET', '/operaciones/' + module + query({ propiedad_id: propertyId, habitacion })),
    opsCreate: (module, body) => request('POST', '/operaciones/' + module, body),
    opsState: (module, id, estado) => request('PATCH', '/operaciones/' + module + '/' + id, { estado }),
    opsStock: (id, cantidad) => request('PATCH', '/operaciones/inventario/' + id, { cantidad }),
    opsIncidentContext: (id) => request('GET', '/operaciones/incidencias/' + id + '/contexto'),
    opsLinkIncident: (id, reservaId) =>
      request('PATCH', '/operaciones/incidencias/' + id + '/reserva', { reserva_id: reservaId }),

    // Habitaciones · capacidad declarada y desempeño por tipo
    roomSummary: (propertyId, currency, desde, hasta) =>
      request('GET', '/habitaciones/resumen' + query({ propiedad_id: propertyId, moneda: currency, desde, hasta })),
    roomTypes: () => request('GET', '/habitaciones/tipos'),
    roomCapacities: (propertyId) => request('GET', '/habitaciones/capacidades' + query({ propiedad_id: propertyId })),
    roomCapacitySave: (payload) => request('POST', '/habitaciones/capacidades', payload),
    roomCapacityDelete: (id) => request('DELETE', '/habitaciones/capacidades/' + id),

    // Mercado · referencias públicas y comparación de largo plazo
    marketReferences: (params) => request('GET', '/mercado/referencias' + query(params || {})),
    marketReferenceCreate: (payload) => request('POST', '/mercado/referencias', payload),
    marketReferenceUpdate: (id, payload) => request('PUT', '/mercado/referencias/' + id, payload),
    marketReferenceDelete: (id) => request('DELETE', '/mercado/referencias/' + id),
    marketImport: (file, propertyId) =>
      upload('/mercado/importar' + query({ propiedad_id: propertyId }), file),
    marketPublicSources: () => request('GET', '/mercado/publico/fuentes'),
    marketPublicRefresh: (params) =>
      request('POST', '/mercado/publico/actualizar' + query(params || {})),
    marketComparison: (params) => request('GET', '/mercado/comparacion' + query(params || {})),

    // Reservas CRUD
    listarReservas: (skip, limit, busqueda, canal, propiedadId) =>
      request('GET', '/reservas' + query({ skip, limit, busqueda, canal, propiedad_id: propiedadId })),
    crearReserva: (data) => request('POST', '/reservas', data),
    actualizarReserva: (id, data) => request('PUT', '/reservas/' + id, data),
    eliminarReserva: (id) => request('DELETE', '/reservas/' + id),

    // Importación ETL
    importar: (file, mapping) => upload('/importar', file, { mapeo: mapping || {} }),
    listarCargas: (skip, limit) => request('GET', '/cargas' + query({ skip, limit })),

    // Auditoría
    listarAuditoria: (skip, limit) => request('GET', '/auditoria' + query({ skip, limit })),
    registrarEvento: (accion, detalle) =>
      request('POST', '/auditoria/registrar' + query({ accion, detalle })),

    // Usuarios (admin)
    listarUsuarios: () => request('GET', '/usuarios'),
    crearUsuario: (payload) => request('POST', '/usuarios', payload),
    actualizarUsuario: (id, payload) => request('PATCH', '/usuarios/' + id, payload),
    resetPassword: (id, password) => request('POST', '/usuarios/' + id + '/reset-password', { password }),
  };
})();
