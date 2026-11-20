// Unlock's second factor in the browser: a passkey (fingerprint or Face ID) through WebAuthn.
// The server keeps the public keys; this phone only remembers which credential is its own.
// navigator.credentials.get/create are called before any await, so Safari still sees the tap.
import * as api from './api.js';
import { t } from './strings.js';

const KEY = 'sportage.passkey';

export function savedId() {
  try { return localStorage.getItem(KEY); } catch { return null; }
}

export function remember(id) {
  try { localStorage.setItem(KEY, id); } catch { /* private mode: PIN next time */ }
}

export function forget() {
  try { localStorage.removeItem(KEY); } catch { /* ignore */ }
}

let available = null;

/** True when this browser can use the phone's own fingerprint / face check. */
export async function isAvailable() {
  if (available !== null) return available;
  // A passkey needs a name in the address (localhost, car.example.com), never an IP like 127.0.0.1.
  const ipHost = /^[\d.]+$/.test(location.hostname) || location.hostname.includes(':');
  try {
    available = !ipHost && !!(window.isSecureContext && window.PublicKeyCredential
      && await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable());
  } catch { available = false; }
  return available;
}

export function deviceLabel() {
  const ua = navigator.userAgent || '';
  if (/iPhone/.test(ua)) return t('passkey.label.iphone');
  if (/iPad/.test(ua)) return t('passkey.label.ipad');
  if (/Android/.test(ua)) return t('passkey.label.android');
  return t('passkey.label.computer');
}

// ---------------------------------------------------------------- base64url <-> bytes

function toBytes(b64url) {
  const b64 = b64url.replace(/-/g, '+').replace(/_/g, '/');
  const bin = atob(b64 + '='.repeat((4 - (b64.length % 4)) % 4));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) out[i] = bin.charCodeAt(i);
  return out;
}

function toB64(buf) {
  const bytes = new Uint8Array(buf);
  let bin = '';
  for (let i = 0; i < bytes.length; i += 1) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

// ---------------------------------------------------------------- unlock

/** Ask the service for a challenge ahead of the tap. null when there's nothing to use. */
export async function prefetchUnlock() {
  if (!savedId() || !(await isAvailable())) return null;
  try {
    return await api.unlockChallenge();
  } catch (err) {
    if (err.status === 404) forget();   // the service no longer knows any passkey here
    return null;
  }
}

/** Sign the challenge with this phone's passkey. Call straight from a tap handler. */
export function assertion(options) {
  const mine = savedId();
  const publicKey = {
    challenge: toBytes(options.challenge),
    rpId: options.rpId,
    timeout: options.timeout,
    userVerification: 'required',
    allowCredentials: (options.allowCredentials || [])
      .filter((c) => !mine || c.id === mine)
      .map((c) => ({ type: 'public-key', id: toBytes(c.id), transports: c.transports })),
  };
  return navigator.credentials.get({ publicKey }).then((cred) => {
    if (!cred) throw new DOMException('No credential', 'NotAllowedError');
    const r = cred.response;
    return {
      id: cred.id,
      rawId: toB64(cred.rawId),
      type: cred.type,
      authenticatorAttachment: cred.authenticatorAttachment || null,
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
      response: {
        clientDataJSON: toB64(r.clientDataJSON),
        authenticatorData: toB64(r.authenticatorData),
        signature: toB64(r.signature),
        userHandle: r.userHandle ? toB64(r.userHandle) : null,
      },
    };
  });
}

// ---------------------------------------------------------------- setting one up

/** Registration options ahead of the tap (no PIN needed for these; the PIN goes with the result). */
export async function prefetchRegister() {
  if (!(await isAvailable())) return null;
  try { return await api.passkeyOptions(deviceLabel()); } catch { return null; }
}

/** Make a new passkey on this phone. Call straight from a tap handler. */
export function create(options) {
  const publicKey = {
    challenge: toBytes(options.challenge),
    rp: options.rp,
    user: { ...options.user, id: toBytes(options.user.id) },
    pubKeyCredParams: options.pubKeyCredParams,
    timeout: options.timeout,
    attestation: options.attestation || 'none',
    authenticatorSelection: options.authenticatorSelection,
    excludeCredentials: (options.excludeCredentials || [])
      .map((c) => ({ type: 'public-key', id: toBytes(c.id), transports: c.transports })),
  };
  return navigator.credentials.create({ publicKey }).then((cred) => {
    if (!cred) throw new DOMException('No credential', 'NotAllowedError');
    const r = cred.response;
    return {
      id: cred.id,
      rawId: toB64(cred.rawId),
      type: cred.type,
      authenticatorAttachment: cred.authenticatorAttachment || null,
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
      response: {
        clientDataJSON: toB64(r.clientDataJSON),
        attestationObject: toB64(r.attestationObject),
        transports: r.getTransports ? r.getTransports() : [],
      },
    };
  });
}

/** Send the new passkey with the PIN; remember it on success. Returns the server's record. */
export async function register(credential, pin) {
  const item = await api.registerPasskey(credential, pin);
  remember(item.id);
  return item;
}

/** Remove this phone's passkey from the service and forget it here. */
export async function turnOff() {
  const id = savedId();
  if (!id) return;
  try {
    await api.deletePasskey(id);
  } catch (err) {
    if (err.status !== 404) throw err;
  }
  forget();
  // Newer phones can also drop it from their own passkey list.
  try {
    PublicKeyCredential.signalUnknownCredential?.({ rpId: location.hostname, credentialId: id })?.catch(() => {});
  } catch { /* not supported */ }
}

/** Check the service still has this phone's passkey (it may have been removed on the PC). */
export async function sync() {
  const id = savedId();
  if (!id) return false;
  try {
    const res = await api.listPasskeys();
    const known = Array.isArray(res?.passkeys) && res.passkeys.some((p) => p.id === id);
    if (!known) forget();
    return known;
  } catch { return true; }   // offline: keep it
}
