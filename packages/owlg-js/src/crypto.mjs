// AES-256-GCM + PBKDF2-SHA256 via WebCrypto (available in browsers and Node 18+).
const subtle = (globalThis.crypto && globalThis.crypto.subtle);

export async function deriveKey(password, salt, iterations) {
  if (!subtle) throw new Error('WebCrypto is not available');
  const base = await subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveKey']);
  return subtle.deriveKey({ name: 'PBKDF2', salt, iterations, hash: 'SHA-256' },
    base, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
}
export async function unseal(key, prefix, idx, data) {
  const nonce = new Uint8Array(12);
  nonce.set(prefix, 0);
  new DataView(nonce.buffer).setUint32(8, idx >>> 0, false);
  return new Uint8Array(await subtle.decrypt({ name: 'AES-GCM', iv: nonce }, key, data));
}
