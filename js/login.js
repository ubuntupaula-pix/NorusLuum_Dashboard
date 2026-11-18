(function () {
  'use strict';

  const loginForm = document.getElementById('loginForm');
  const mfaForm = document.getElementById('mfaForm');
  const ssoBlock = document.getElementById('ssoBlock');
  const ssoButton = document.getElementById('ssoButton');
  const errorEl = document.getElementById('errorMsg');
  const mfaErrorEl = document.getElementById('mfaError');
  const loginSubmit = document.getElementById('loginSubmit');
  const mfaSubmit = document.getElementById('mfaSubmit');

  // El token de segundo factor vive solo en memoria durante esta pestaña.
  let mfaToken = null;
  let ssoAvailable = false;

  function finishSession(result) {
    // The JWT stays in the HttpOnly cookie; only keep a non-secret UI hint.
    window.API.setToken(true);
    sessionStorage.setItem('norus_user', result.username || '');
    sessionStorage.setItem('norus_role', result.rol || 'lector');
    window.location.href = 'index.html';
  }

  function setBusy(button, busy, label) {
    button.disabled = busy;
    button.textContent = busy ? label : button.dataset.label;
  }

  loginSubmit.dataset.label = loginSubmit.textContent;
  mfaSubmit.dataset.label = mfaSubmit.textContent;

  // Si ya existe una sesión válida, el servidor redirige igualmente; esta
  // comprobación mejora la experiencia sin ser la barrera de seguridad.
  if (sessionStorage.getItem('norus_session_active') === '1') {
    window.API.me()
      .then(() => { window.location.href = 'index.html'; })
      .catch(() => window.API.clearSessionHint());
  }

  // Errores devueltos por el callback SSO.
  const ssoError = new URLSearchParams(window.location.search).get('sso_error');
  if (ssoError) {
    errorEl.textContent = ssoError === '2'
      ? 'Tu cuenta de SSO no está autorizada en Norus. Contacta al administrador.'
      : 'No fue posible completar el inicio de sesión con SSO. Intenta nuevamente.';
    if (window.history.replaceState) window.history.replaceState({}, '', 'login.html');
  }

  // Mostrar SSO solo si el proveedor está configurado.
  window.API.ssoStatus()
    .then(status => {
      ssoAvailable = Boolean(status && status.enabled);
      ssoBlock.hidden = !ssoAvailable;
    })
    .catch(() => { /* SSO opcional: si falla, se usa usuario y contraseña */ });

  ssoButton.addEventListener('click', () => { window.location.href = '/api/auth/sso/login'; });

  loginForm.addEventListener('submit', async event => {
    event.preventDefault();
    errorEl.textContent = '';
    const username = document.getElementById('user').value.trim();
    const password = document.getElementById('pass').value;
    if (!username || !password) {
      errorEl.textContent = 'Ingresa tu usuario y contraseña.';
      return;
    }
    setBusy(loginSubmit, true, 'Verificando…');
    try {
      const result = await window.API.login(username, password);
      if (result.mfa_required) {
        mfaToken = result.mfa_token;
        loginForm.hidden = true;
        ssoBlock.hidden = true;
        mfaForm.hidden = false;
        mfaErrorEl.textContent = '';
        document.getElementById('mfaCode').value = '';
        document.getElementById('mfaCode').focus();
        document.getElementById('user').value = username;
        return;
      }
      finishSession(result);
    } catch (error) {
      errorEl.textContent = error.message || 'No fue posible iniciar sesión.';
      document.getElementById('pass').value = '';
      document.getElementById('pass').focus();
    } finally {
      setBusy(loginSubmit, false);
    }
  });

  mfaForm.addEventListener('submit', async event => {
    event.preventDefault();
    mfaErrorEl.textContent = '';
    const code = document.getElementById('mfaCode').value.trim();
    if (!mfaToken) {
      mfaErrorEl.textContent = 'La verificación expiró. Vuelve a iniciar sesión.';
      return;
    }
    setBusy(mfaSubmit, true, 'Validando…');
    try {
      const result = await window.API.verifyMfa(mfaToken, code);
      finishSession(result);
    } catch (error) {
      mfaErrorEl.textContent = error.message || 'Código incorrecto.';
      document.getElementById('mfaCode').value = '';
      document.getElementById('mfaCode').focus();
    } finally {
      setBusy(mfaSubmit, false);
    }
  });

  document.getElementById('mfaCancel').addEventListener('click', () => {
    mfaToken = null;
    mfaForm.hidden = true;
    loginForm.hidden = false;
    ssoBlock.hidden = !ssoAvailable;
    errorEl.textContent = '';
    document.getElementById('pass').value = '';
    document.getElementById('pass').focus();
  });
})();
