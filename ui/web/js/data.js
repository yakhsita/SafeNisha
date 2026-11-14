/* Standalone frontend adapter: route and hazard requests use the local Python API. Profiles use authenticated server sessions. */
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

  /* Accounts are persisted by the API; session cookies are HttpOnly. */
  const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  const GENDERS = ['Female', 'Male', 'Non-binary', 'Prefer not to say'];
  const PHONE_RE = /^\+?[\d\s()-]{7,}$/;
  let currentProfile = null, authRevision = 0, profileWrites = Promise.resolve();
  try { localStorage.removeItem('sn.profile'); } catch (_) { /* old preview only */ }
  async function accountRequest(url, payload) {
    try { return await request(url, payload); }
    catch (error) { return {ok:false, message:error.message}; }
  }

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
    GENDERS, checkField,
    current() { return currentProfile; },
    async restore() {
      const revision = authRevision;
      const r = await accountRequest('/api/auth/me');
      if (revision === authRevision && r.ok) currentProfile = r.profile;
      return currentProfile;
    },
    async signIn(email, password) {
      ++authRevision;
      const r = await accountRequest('/api/auth/login', {email, password});
      if (r.ok) currentProfile = r.profile;
      return r;
    },
    async signUp(name, email, password, extra = {}) {
      ++authRevision;
      const r = await accountRequest('/api/auth/register', {name, email, password, ...extra});
      if (r.ok) currentProfile = r.profile;
      return r;
    },
    update(fields) {
      const revision = authRevision;
      profileWrites = profileWrites.then(async () => {
        if (revision !== authRevision) return {ok:false,message:'Your session changed. Please try again.'};
        const r = await accountRequest('/api/profile', fields);
        if (r.ok && revision === authRevision) currentProfile = r.profile;
        return r;
      });
      return profileWrites;
    },
    async signOut() {
      await profileWrites;
      const r = await accountRequest('/api/auth/logout', {});
      if (r.ok) { ++authRevision; currentProfile = null; }
      return r;
    },
  };

  SN.data = {
    HAZARD_TYPES, HAZARD_LABELS, WALK_KMH, START, END, BASE_HAZARDS, ATLAS_BOUNDS,
    getRouteResult, getAtlas, searchAddress, getHazards, refreshHazards, submitHazard, setHazardStatus, clone, auth,
  };
})();
