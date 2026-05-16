// polyfill.js
if (typeof globalThis.Blob === 'undefined') {
  globalThis.Blob = class Blob {
    constructor(parts, options) {
      this._parts = parts;
      this._options = options;
      this.type = options?.type || '';
      this.size = parts.reduce((acc, part) => acc + (part.length || 0), 0);
    }
    text() { return Promise.resolve(this._parts.join('')); }
    arrayBuffer() { return Promise.resolve(Buffer.from(this._parts.join(''))); }
    slice() { return this; }
  };
}