/* Standalone frontend adapter: route and hazard requests use the local Python API. Profiles remain local browser previews. */
(function () {
  'use strict';
  const SN = (window.SN = window.SN || {});

  const HAZARD_TYPES = ['broken_streetlight', 'dark_path', 'blocked_footpath', 'other_infrastructure'];
  const HAZARD_LABELS = {
    broken_streetlight: 'Broken streetlight',
    dark_path: 'Dark path',
    blocked_footpath: 'Blocked footpath',
    other_infrastructure: 'Other infrastructure issue',
  };
  const WALK_KMH = 5; // shown to the user; used for the walking-time estimate

  const START = null, END = null;
  const BASE_HAZARDS = [];
  const ATLAS_BOUNDS = null;
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  let hazards = [];
  async function request(url, payload) {
    const response = await fetch(url, payload === undefined ? {} : {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Request failed. Please retry.');
    return result;
  }
  async function getRouteResult(state, source, dest, beta=2, preview=null, options={}) {
    if (!source || !dest) return null;
    return request('/api/routes',{start:source,destination:dest,beta,preview_hazard:preview,...options});
  }
  async function getAtlas(center){return request('/api/atlas',{center});}
  async function searchAddress(query,bias=null){return (await request('/api/geocode',{query,bias})).places;}
  function getHazards() { return hazards; }
  async function refreshHazards() {
    hazards = (await request('/api/hazards')).hazards;
    return hazards;
  }
  async function submitHazard(lat,lon,hazardType,description='') {
    try {
      const result=await request('/api/hazards',{lat,lon,hazard_type:hazardType,description});
      await refreshHazards();
      return result;
    } catch(error) { return {ok:false,message:error.message}; }
  }
  async function setHazardStatus(id,status) {
    const result=await request('/api/hazards/status',{id,status});
    hazards=result.hazards;
    return result;
  }

  /* ---------- Accounts: SWAP POINT (auth service) ----------
   * Placeholder: the profile lives in this browser's localStorage. Passwords are checked for
   * shape only and never stored. A real backend replaces these bodies, keeping the shapes:
   *   profile = { name, email, phone, gender, dob, memberSince, contacts: [{ name, phone }], beta }
   */
  const PKEY = 'sn.profile';
  const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  function readProfile() {
    try { const p = JSON.parse(localStorage.getItem(PKEY)); return p && typeof p === 'object' ? p : null; } catch (_) { return null; }
  }
  function writeProfile(p) {
    try { if (p) localStorage.setItem(PKEY, JSON.stringify(p)); else localStorage.removeItem(PKEY); } catch (_) { /* storage unavailable */ }
    return p;
  }
  const nameFromEmail = (e) => e.split('@')[0].replace(/[._-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  const GENDERS = ['Female', 'Male', 'Non-binary', 'Prefer not to say'];
  const PHONE_RE = /^\+?[\d\s()-]{7,}$/;
  const thisMonth = () => new Date().toISOString().slice(0, 7); // "YYYY-MM"
  const blank = (extra) => Object.assign({ name: '', email: '', phone: '', gender: '', dob: '', contacts: [], beta: 2, memberSince: thisMonth() }, extra);

  /** Field rules shared by sign-up and profile edits. Returns an error message or ''. */
  function checkField(k, v) {
    v = (v || '').trim();
    if (k === 'name') return v ? (v.length > 60 ? 'Name is too long (60 characters max).' : '') : 'Enter your name.';
    if (k === 'email') return EMAIL_RE.test(v) ? '' : 'Enter a valid email address.';
    if (k === 'phone') return PHONE_RE.test(v) && v.replace(/\D/g, '').length >= 7 ? '' : 'Enter a valid phone number, e.g. +91 98765 43210.';
    if (k === 'gender') return !v || GENDERS.includes(v) ? '' : 'Pick an option from the list.';
    if (k === 'dob') {
      if (!v) return '';
      const d = new Date(v + 'T00:00:00');
      if (isNaN(d)) return 'Enter a valid date.';
      if (d > new Date()) return 'Date of birth can’t be in the future.';
      if (d.getFullYear() < 1900) return 'Enter a year after 1900.';
      return '';
    }
    return '';
  }

  const auth = {
    GENDERS,
    checkField,
    current() {
      const p = readProfile();
      if (p && p.guest) { writeProfile(null); return null; } // guest sessions are no longer supported
      if (p && !p.memberSince) { p.memberSince = thisMonth(); writeProfile(p); }
      return p;
    },
    async signIn(email, password) {
      await wait(500);
      email = (email || '').trim().toLowerCase();
      if (!EMAIL_RE.test(email)) return { ok: false, message: 'Enter a valid email address.' };
      if (!password) return { ok: false, message: 'Enter your password.' };
      const prev = readProfile();
      const p = prev && prev.email === email ? prev : blank({ name: nameFromEmail(email), email });
      return { ok: true, profile: writeProfile(p) };
    },
    /** extra = { phone, gender, dob } */
    async signUp(name, email, password, extra = {}) {
      await wait(600);
      name = (name || '').trim(); email = (email || '').trim().toLowerCase();
      const phone = (extra.phone || '').trim(), gender = extra.gender || '', dob = extra.dob || '';
      const err = checkField('name', name) || checkField('email', email) ||
        ((password || '').length < 8 ? 'Use a password with at least 8 characters.' : '') ||
        checkField('phone', phone) || checkField('gender', gender) || checkField('dob', dob);
      if (err) return { ok: false, message: err };
      return { ok: true, profile: writeProfile(blank({ name, email, phone, gender, dob })) };
    },
    /** Update one or more personal fields. Resolves to { ok, message?, profile? }. */
    async update(fields) {
      await wait(250);
      const p = readProfile();
      if (!p) return { ok: false, message: 'You are signed out.' };
      for (const k of Object.keys(fields)) {
        const v = (fields[k] || '').trim();
        const err = checkField(k, v);
        if (err) return { ok: false, message: err };
        p[k] = k === 'email' ? v.toLowerCase() : v;
      }
      return { ok: true, profile: writeProfile(p) };
    },
    async save(p) { return writeProfile(p); },
    async signOut() { writeProfile(null); return { ok: true }; },
  };

  SN.data = {
    HAZARD_TYPES, HAZARD_LABELS, WALK_KMH, START, END, BASE_HAZARDS, ATLAS_BOUNDS,
    getRouteResult, getAtlas, searchAddress, getHazards, refreshHazards, submitHazard, setHazardStatus, clone, auth,
  };
})();
