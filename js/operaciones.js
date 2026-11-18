(function () {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const property = $('#opsProperty');
  const roomFilter = $('#opsRoomFilter');
  const table = $('#opsTable');
  const form = $('#opsForm');
  // El rol se resuelve en cada render: la sesión se confirma de forma asíncrona
  // y también puede cambiar sin recargar la página (p. ej. tras iniciar sesión).
  const canEdit = () => ['admin', 'editor'].includes(sessionStorage.getItem('norus_role'));
  const modules = {
    tareas: { title: 'Tareas', fields: [['titulo','Título','text',true],['area','Área','text',true],['prioridad','Prioridad','select',['baja','media','alta','critica']],['asignado_a','Asignado a','text'],['vence_en','Vence','datetime-local'],['descripcion','Descripción','textarea']], columns: [['titulo','Tarea'],['area','Área'],['prioridad','Prioridad'],['asignado_a','Asignado'],['estado','Estado']], states: ['pendiente','en_progreso','completada','cancelada'] },
    incidencias: { title: 'Incidencias', fields: [['titulo','Título','text',true],['categoria','Categoría','text',true],['severidad','Severidad','select',['baja','media','alta','critica']],['area','Área','text'],['habitacion','Habitación','text'],['reserva_id','Reserva vinculada (ID, opcional)','number'],['responsable','Responsable','text'],['detalle','Detalle','textarea']], columns: [['titulo','Incidencia'],['categoria','Categoría'],['severidad','Severidad'],['area','Área'],['habitacion','Hab.'],['estado','Estado']], states: ['abierta','en_progreso','resuelta','cancelada'], context: true },
    mantenimiento: { title: 'Mantenimiento', fields: [['titulo','Trabajo requerido','text',true],['tipo','Tipo','select',['Preventivo','Correctivo','Inspección']],['prioridad','Prioridad','select',['baja','media','alta','critica']],['ubicacion','Ubicación','text'],['responsable','Responsable','text'],['detalle','Detalle','textarea']], columns: [['titulo','Orden'],['tipo','Tipo'],['prioridad','Prioridad'],['ubicacion','Ubicación'],['responsable','Responsable'],['estado','Estado']], states: ['pendiente','en_progreso','completada','cancelada'] },
    inventario: { title: 'Inventario', fields: [['nombre','Artículo','text',true],['categoria','Categoría','text'],['unidad','Unidad','text'],['cantidad','Cantidad','number'],['stock_minimo','Stock mínimo','number'],['proveedor','Proveedor','text']], columns: [['nombre','Artículo'],['categoria','Categoría'],['cantidad','Stock'],['stock_minimo','Mínimo'],['proveedor','Proveedor']], stock: true },
    'objetos-perdidos': { title: 'Objetos perdidos', fields: [['descripcion','Descripción','text',true],['categoria','Categoría','text'],['ubicacion','Lugar encontrado','text'],['responsable','Responsable','text']], columns: [['descripcion','Objeto'],['categoria','Categoría'],['ubicacion','Ubicación'],['fecha_encontrado','Encontrado'],['estado','Estado']], states: ['resguardado','reclamado','entregado','desechado'] },
    checklists: { title: 'Checklists', fields: [['nombre','Nombre del checklist','text',true],['area','Área','text'],['elementos','Puntos de control (uno por línea)','textarea',true]], columns: [['nombre','Checklist'],['area','Área'],['elementos','Puntos'],['ejecutado_por','Ejecutó'],['estado','Estado']], states: ['en_progreso','completada','cancelada'] }
  };
  let active = 'tareas';
  let pendingFocus = null;

  function cellValue(row, key) {
    const v = row[key];
    if (v === null || v === undefined || v === '') return '—';
    if (key === 'elementos') return Array.isArray(v) ? `${v.length} puntos` : String(v);
    if (key.endsWith('_en') || key === 'fecha_encontrado') {
      const d = new Date(v); return Number.isNaN(d.valueOf()) ? String(v) : d.toLocaleString('es-CL');
    }
    return String(v);
  }
  function appendCell(row, value, tag = 'td') {
    const cell = document.createElement(tag);
    if (tag === 'th') cell.scope = 'col';
    cell.textContent = value;
    row.appendChild(cell);
  }
  function makeField(field) {
    const [key, label, type, extra] = field;
    const wrapper = document.createElement('label'); wrapper.className = 'ops-field'; wrapper.textContent = label;
    let input;
    if (type === 'textarea') { input = document.createElement('textarea'); input.rows = 3; }
    else if (type === 'select') {
      input = document.createElement('select');
      (Array.isArray(extra) ? extra : []).forEach(v => { const op = document.createElement('option'); op.value = v; op.textContent = v; input.appendChild(op); });
    } else { input = document.createElement('input'); input.type = type; if (type === 'number') { input.min = '0'; input.step = '0.01'; input.value = '0'; } }
    input.name = key; input.required = extra === true;
    if (key === 'elementos') input.placeholder = 'Revisar habitación\nConfirmar limpieza\nRegistrar minibar';
    wrapper.appendChild(input); return wrapper;
  }
  function renderForm() {
    const spec = modules[active], editable = canEdit();
    $('#opsFormTitle').textContent = 'Nuevo registro · ' + spec.title;
    form.replaceChildren(); form.classList.toggle('ops-readonly', !editable);
    spec.fields.forEach(field => form.appendChild(makeField(field)));
    const button = document.createElement('button'); button.type = 'submit'; button.className = 'btn btn-primary';
    button.textContent = editable ? 'Guardar registro' : 'Solo lectura'; button.disabled = !editable; form.appendChild(button);
  }
  function contextLine(label, value) {
    const div = document.createElement('div');
    div.className = 'ops-context-line';
    const l = document.createElement('span'); l.className = 'ops-context-label'; l.textContent = label;
    const v = document.createElement('span'); v.textContent = (value === null || value === undefined || value === '') ? '—' : String(value);
    div.append(l, v);
    return div;
  }

  async function showGuestContext(item) {
    const panel = $('#opsGuestContext');
    if (!panel) return;
    panel.replaceChildren();
    panel.hidden = false;
    panel.textContent = 'Resolviendo contexto del huésped…';
    try {
      const data = await window.API.opsIncidentContext(item.id);
      panel.replaceChildren();
      const head = document.createElement('div');
      head.className = 'ops-context-head';
      const title = document.createElement('h4');
      title.textContent = 'Contexto huésped · ' + (data.incidencia.titulo || 'Incidencia');
      const close = document.createElement('button');
      close.type = 'button'; close.className = 'btn btn-ghost'; close.textContent = 'Cerrar';
      close.addEventListener('click', () => { panel.hidden = true; panel.replaceChildren(); });
      head.append(title, close);
      panel.appendChild(head);

      const reserva = data.reserva;
      if (!reserva) {
        const note = document.createElement('p');
        note.className = 'ops-note';
        note.textContent = data.nota || 'No se encontró una estancia asociada a esta habitación.';
        panel.appendChild(note);
      } else {
        const grid = document.createElement('div');
        grid.className = 'ops-context-grid';
        grid.appendChild(contextLine('Huésped', data.pii_visible ? reserva.huesped : 'Oculto para su rol'));
        grid.appendChild(contextLine('Código', data.pii_visible ? reserva.codigo : '—'));
        grid.appendChild(contextLine('Habitación', reserva.habitacion));
        grid.appendChild(contextLine('Tipo', reserva.tipo_habitacion));
        grid.appendChild(contextLine('Entrada', reserva.fecha));
        grid.appendChild(contextLine('Noches', reserva.noches));
        grid.appendChild(contextLine('Valor', data.pii_visible ? reserva.valor + ' ' + reserva.moneda : 'Oculto'));
        grid.appendChild(contextLine('Canal', reserva.canal));
        grid.appendChild(contextLine('Estado', reserva.estado));
        grid.appendChild(contextLine('Propiedad', reserva.propiedad));
        panel.appendChild(grid);
        const origen = document.createElement('p');
        origen.className = 'ops-note';
        origen.textContent = 'Vínculo: ' + (data.vinculo === 'explicito'
          ? 'reserva vinculada explícitamente.'
          : 'coincidencia de propiedad, habitación y fecha de estancia.');
        panel.appendChild(origen);
      }

      const resumenHab = data.resumen_habitacion || {};
      const otrasIncidencias = Array.isArray(data.incidencias_habitacion) ? data.incidencias_habitacion : [];
      const estancias = Array.isArray(data.estancias_habitacion) ? data.estancias_habitacion : [];
      if (resumenHab.habitacion) {
        const box = document.createElement('div');
        box.className = 'ops-room-context';
        const head = document.createElement('h5');
        head.textContent = 'Habitación ' + resumenHab.habitacion +
          ' · ' + (resumenHab.incidencias_abiertas || 0) + ' incidencia(s) abierta(s)' +
          ' · ' + (resumenHab.estancias_recientes || 0) + ' estancia(s) reciente(s)';
        box.appendChild(head);
        if (otrasIncidencias.length) {
          const list = document.createElement('ul');
          list.className = 'ops-context-list';
          otrasIncidencias.forEach(inc => {
            const li = document.createElement('li');
            const strong = document.createElement('strong');
            strong.textContent = inc.titulo;
            const meta = document.createElement('span');
            meta.textContent = ' · ' + (inc.estado || '—') + ' · ' + (inc.severidad || '—') + (inc.actual ? ' · actual' : '');
            li.append(strong, meta);
            list.appendChild(li);
          });
          box.appendChild(list);
        }
        if (estancias.length) {
          const list = document.createElement('ul');
          list.className = 'ops-context-list';
          estancias.slice(0, 5).forEach(st => {
            const li = document.createElement('li');
            li.textContent = (st.huesped || 'Huésped oculto') + ' · ' +
              (st.fecha || '—') + ' · ' + (st.noches ?? 0) + ' noches · ' + (st.estado || '—');
            list.appendChild(li);
          });
          box.appendChild(list);
        }
        panel.appendChild(box);
      }

      if (canEdit()) {
        const form = document.createElement('form');
        form.className = 'ops-context-link';
        const label = document.createElement('label');
        label.className = 'ops-field';
        label.textContent = 'Vincular reserva (ID)';
        const input = document.createElement('input');
        input.type = 'number'; input.min = '1'; input.step = '1';
        input.value = reserva ? reserva.id : '';
        input.setAttribute('aria-label', 'ID de reserva para vincular');
        label.appendChild(input);
        const save = document.createElement('button');
        save.type = 'submit'; save.className = 'btn btn-primary'; save.textContent = 'Vincular';
        const unlink = document.createElement('button');
        unlink.type = 'button'; unlink.className = 'btn btn-ghost'; unlink.textContent = 'Desvincular';
        unlink.addEventListener('click', async () => {
          try {
            await window.API.opsLinkIncident(item.id, null);
            await showGuestContext(item);
            await load();
          } catch (e) { alert(e.message); }
        });
        form.append(label, save, unlink);
        form.addEventListener('submit', async event => {
          event.preventDefault();
          const value = Number(input.value);
          if (!Number.isFinite(value) || value < 1) { alert('Indica un ID de reserva válido.'); return; }
          try {
            await window.API.opsLinkIncident(item.id, value);
            await showGuestContext(item);
            await load();
          } catch (e) { alert(e.message); }
        });
        panel.appendChild(form);
      }
      panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (e) {
      panel.textContent = 'No se pudo resolver el contexto del huésped: ' + e.message;
    }
  }

  function renderTable(rows) {
    const spec = modules[active], thead = table.querySelector('thead'), tbody = table.querySelector('tbody');
    thead.replaceChildren(); tbody.replaceChildren();
    const header = document.createElement('tr'); spec.columns.forEach(([, label]) => appendCell(header, label, 'th'));
    if (spec.states || spec.stock) appendCell(header, 'Acción', 'th');
    if (spec.context) appendCell(header, 'Huésped', 'th');
    thead.appendChild(header);
    document.querySelectorAll('#opsTabs .ops-tab').forEach(tab => {
      tab.classList.toggle('active', tab.dataset.module === active);
      tab.setAttribute('aria-selected', tab.dataset.module === active ? 'true' : 'false');
    });
    rows.forEach(item => {
      const tr = document.createElement('tr'); spec.columns.forEach(([key]) => appendCell(tr, cellValue(item, key)));
      if (spec.states) {
        const td = document.createElement('td');
        const select = document.createElement('select'); select.className = 'ops-state'; select.setAttribute('aria-label', 'Cambiar estado');
        spec.states.forEach(state => { const option = document.createElement('option'); option.value = state; option.textContent = state.replaceAll('_',' '); option.selected = item.estado === state; select.appendChild(option); });
        select.disabled = !canEdit();
        select.addEventListener('change', async () => { try { await window.API.opsState(active, item.id, select.value); await load(); } catch (e) { alert(e.message); } });
        td.appendChild(select); tr.appendChild(td);
      } else if (spec.stock) {
        const td = document.createElement('td'), wrap = document.createElement('div'), input = document.createElement('input'), btn = document.createElement('button');
        wrap.className = 'ops-stock'; input.type='number'; input.min='0'; input.step='0.01'; input.value=item.cantidad; input.setAttribute('aria-label','Nuevo stock'); input.disabled=!canEdit();
        btn.type='button'; btn.className='btn btn-ghost'; btn.textContent='Actualizar'; btn.disabled=!canEdit();
        btn.addEventListener('click', async () => { try { await window.API.opsStock(item.id, Number(input.value)); await load(); } catch (e) { alert(e.message); } });
        wrap.append(input,btn); td.appendChild(wrap); tr.appendChild(td);
      }
      if (spec.context) {
        const td = document.createElement('td');
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-ghost';
        btn.textContent = 'Ver contexto';
        btn.addEventListener('click', () => showGuestContext(item));
        td.appendChild(btn);
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    });
    $('#opsEmpty').hidden = rows.length > 0;
  }
  async function loadProperties() {
    try {
      const current = property.value, data = await window.API.opsProperties();
      property.replaceChildren(new Option('Todas las propiedades',''));
      data.forEach(p => property.add(new Option(p.nombre, p.id)));
      if (current) {
        property.value = current;
        // Si el filtro guardado ya no está permitido, vuelve a "Todas".
        if (property.value !== current) property.value = '';
      } else if (data.length === 1) {
        // Con una sola propiedad asignada se evita crear registros sin dueño.
        property.value = String(data[0].id);
      }
    } catch (e) { console.warn('No fue posible cargar propiedades:', e.message); }
  }
  async function load() {
    try {
      if (pendingFocus) {
        const focus = pendingFocus;
        pendingFocus = null;
        await loadProperties();
        if (focus.propertyId) property.value = String(focus.propertyId);
        if (roomFilter) roomFilter.value = focus.room || '';
      }
      const filter = property.value;
      const room = active === 'incidencias' && roomFilter ? roomFilter.value.trim() : '';
      const [summary, rows] = await Promise.all([
        window.API.opsSummary(filter),
        window.API.opsList(active, filter, room)
      ]);      const cards = [
        ['Tareas activas', summary.tareas_pendientes, 'teal'],
        ['Incidencias abiertas', summary.incidencias_abiertas, 'accent'],
        ['Incidencias críticas', summary.incidencias_criticas, 'red'],
        ['Mantenimiento pendiente', summary.mantenimientos_pendientes, 'accent'],
        ['Stock bajo mínimo', summary.inventario_bajo, 'yellow'],
        ['Objetos resguardados', summary.objetos_resguardados, 'teal'],
        ['Checklists en curso', summary.checklists_en_progreso, 'green']
      ];
      $('#opsKpis').replaceChildren();
      cards.forEach(([label, value, cls]) => { const div=document.createElement('div'); div.className='kpi '+(cls||''); const l=document.createElement('div'),v=document.createElement('div'); l.className='kpi-label';l.textContent=label;v.className='kpi-value';v.textContent=value;div.append(l,v);$('#opsKpis').appendChild(div); });
      renderTable(rows);
      if (!rows.length) {
        $('#opsEmpty').textContent = 'Aún no hay registros de ' + modules[active].title.toLowerCase() +
          '. Crea el primero con el formulario de la izquierda.';
      }
    } catch (e) {
      $('#opsEmpty').hidden = false;
      $('#opsEmpty').textContent = 'No se pudo cargar la operación: ' + e.message;
    }
  }
  function init() {
    const tabs = $('#opsTabs');
    Object.entries(modules).forEach(([key, spec]) => {
      const btn=document.createElement('button');btn.type='button';btn.className='ops-tab';btn.textContent=spec.title;btn.setAttribute('role','tab');
      btn.dataset.module=key;btn.setAttribute('aria-selected',key===active?'true':'false');
      btn.addEventListener('click',()=>{active=key;const ctx=$('#opsGuestContext');if(ctx){ctx.hidden=true;ctx.replaceChildren();}renderForm();load();});
      if (key===active) btn.classList.add('active'); tabs.appendChild(btn);
    });
    renderForm();
    property.addEventListener('change', load);
    if (roomFilter) roomFilter.addEventListener('change', () => { if (active === 'incidencias') load(); });
    $('#opsRefresh').addEventListener('click', load);
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (!canEdit()) return;
      const body=Object.fromEntries(new FormData(form).entries());
      if (body.elementos !== undefined) body.elementos=body.elementos.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
      if (body.propiedad_id !== undefined) delete body.propiedad_id;
      if (property.value) body.propiedad_id=Number(property.value);
      else if ([...property.options].filter(o => o.value).length > 1) {
        alert('Selecciona la propiedad del registro antes de guardar.');
        property.focus();
        return;
      }
      for (const key of ['cantidad','stock_minimo']) if (body[key] !== undefined) body[key]=Number(body[key]);
      if (body.reserva_id !== undefined) {
        if (body.reserva_id === '') delete body.reserva_id;
        else body.reserva_id = Number(body.reserva_id);
      }
      if (body.vence_en) body.vence_en=new Date(body.vence_en).toISOString(); else delete body.vence_en;
      try {
        await window.API.opsCreate(active, body);
        form.reset();
        if (typeof window.notificar === 'function') window.notificar('Registro guardado en ' + modules[active].title + '.', 'success');
        await load();
      }
      catch (e) { alert('No se pudo guardar: '+e.message); }
    });
  }
  let lastCanEdit = canEdit();
  init();
  window.Operaciones = {
    load: async () => {
      // Re-renderiza el formulario si el rol se conoció después del arranque.
      if (lastCanEdit !== canEdit()) { lastCanEdit = canEdit(); renderForm(); }
      await loadProperties();
      await load();
    },
    focusRoom: (propertyId, room) => {
      // Puente desde el desglose por habitación: abre Incidencias filtradas.
      pendingFocus = { propertyId: propertyId ? Number(propertyId) : null, room: room || '' };
      active = 'incidencias';
      renderForm();
      const nav = document.querySelector('.nav-item[data-view="operaciones"]');
      if (nav) nav.click();
      else load();
    }
  };
})();
