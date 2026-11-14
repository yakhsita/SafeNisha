/* SafeNight app: views, state and interactions. Data comes only through SN.data (js/data.js). */
(function () {
  'use strict';
  const { data, util: U } = SN;
  const { esc, riskClass, riskColor, reduced } = U;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const store = {
    get(k, d) { try { const v = localStorage.getItem('sn.' + k); return v == null ? d : JSON.parse(v); } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem('sn.' + k, JSON.stringify(v)); } catch (_) { /* storage unavailable */ } },
  };
  const BETA_MAX = 5;
  const WALK_M_PER_MIN = (data.WALK_KMH * 1000) / 60;

  const IC = {
    shield: '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="du" d="M12 3 4.5 6v6c0 4.4 3.2 7.9 7.5 9 4.3-1.1 7.5-4.6 7.5-9V6z"/><path d="M12 3 4.5 6v6c0 4.4 3.2 7.9 7.5 9 4.3-1.1 7.5-4.6 7.5-9V6L12 3z"/><path d="m9 12 2 2 4-4"/></svg>',
    bolt: '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="du" d="M13 2.5 4.5 13.5H12l-1 8 8.5-11H12z"/><path d="M13 2.5 4.5 13.5H12l-1 8 8.5-11H12l1-8z"/></svg>',
    info: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle class="du" cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>',
    alert: '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="du" d="M12 3.5 2.8 19.5h18.4z"/><path d="M12 3.5 2.8 19.5h18.4L12 3.5z"/><path d="M12 10v4.2M12 17h.01"/></svg>',
    route: '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="du" d="M17.5 2.8a3.6 3.6 0 0 0-3.6 3.6c0 2.7 3.6 6.1 3.6 6.1s3.6-3.4 3.6-6.1a3.6 3.6 0 0 0-3.6-3.6z"/><path d="M17.5 2.8a3.6 3.6 0 0 0-3.6 3.6c0 2.7 3.6 6.1 3.6 6.1s3.6-3.4 3.6-6.1a3.6 3.6 0 0 0-3.6-3.6z"/><circle cx="17.5" cy="6.4" r="1.1"/><circle class="du" cx="5.8" cy="18.2" r="2.6"/><circle cx="5.8" cy="18.2" r="2.6"/><path d="M8.6 17.6c3.4-.6 3.1-4.2 6.2-4.9" stroke-dasharray="1.6 2.4"/></svg>',
    pin: '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="du" d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z"/><path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/></svg>',
  };

  /* ================= helpers ================= */
  function toast(msg, type = 'ok') {
    const t = document.createElement('div');
    t.className = 'toast ' + type;
    t.innerHTML = `<span class="toast-dot"></span><span>${esc(msg)}</span>`;
    $('#toasts').appendChild(t);
    setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 320); }, 4600);
  }
  function showError(el, msg) {
    delete el.dataset.kind;
    el.textContent = msg;
    el.hidden = false;
    el.style.animation = 'none'; void el.offsetWidth; el.style.animation = '';
  }
  function segCtl(el, value, onChange) {
    const btns = $$('button', el);
    el.style.setProperty('--n', btns.length);
    const set = (v, fire) => {
      btns.forEach((b, i) => {
        const on = b.dataset.v === String(v);
        b.setAttribute('aria-pressed', on);
        if (on) el.style.setProperty('--i', i);
      });
      if (fire && onChange) onChange(v);
    };
    btns.forEach((b) => b.addEventListener('click', () => set(b.dataset.v, true)));
    set(value, false);
    return { set };
  }
  /** Ease scene.t toward scene.target, calling onFrame each frame. */
  function morph(sc, onFrame) {
    if (sc._raf) return;
    let last = performance.now();
    const step = (now) => {
      const dt = now - last; last = now;
      const k = reduced() ? 1 : 1 - Math.exp(-dt / 75);
      sc.t += (sc.target - sc.t) * k;
      if (Math.abs(sc.target - sc.t) < 0.0015) sc.t = sc.target;
      onFrame();
      sc._raf = sc.t === sc.target ? 0 : requestAnimationFrame(step);
    };
    sc._raf = requestAnimationFrame(step);
  }
  const mins = (m) => Math.max(1, Math.round(m / WALK_M_PER_MIN));

  /* ================= route comparison panel ================= */
  class RoutePanel {
    constructor(el, sc, opts = {}) {
      this.el = el; this.sc = sc; this.opts = opts; this.key = '';
      el.addEventListener('click', (e) => { const c = e.target.closest('.route-card'); if (c) opts.onSelect(c.dataset.k); });
      el.addEventListener('keydown', (e) => {
        const c = e.target.closest('.route-card');
        if (c && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); opts.onSelect(c.dataset.k); }
      });
    }
    _kind() {
      const sc = this.sc;
      if (sc.loading) return 'loading';
      if (sc.searched && sc.state === 'no_route') return 'none';
      if (!sc.searched || !sc.routes) return 'empty';
      if (sc.showRoutes === false) return 'ready';
      return 'routes';
    }
    sync(force) {
      const kind = this._kind();
      const R = kind === 'routes' ? SN.computeRoutes(this.sc) : null;
      const key = kind + (R ? '|' + (R.short.r == null) + '|' + (R.short.seg ? R.short.seg.length : 0) : '');
      if (force || key !== this.key) { this.key = key; this._build(kind, R); }
      if (R) this.update();
    }
    _card(k, R) {
      const low = k === 'low', seg = R[k].seg;
      const strip = seg
        ? `<div class="seg" role="img" aria-label="Estimated risk along the route, start to end">${'<i class="seg-bar"></i>'.repeat(seg.length)}</div>
           <div class="seg-axis"><span>Start</span><span>Risk along route · synthetic</span><span>End</span></div>`
        : '<div class="seg-none">Segment risk: data limited</div>';
      return `<article class="route-card ${k}" data-k="${k}" role="button" tabindex="0" aria-pressed="false">
        <div class="rc-head">
          <span class="rc-icon">${low ? IC.shield : IC.bolt}</span>
          <div><h3 class="rc-title">${low ? 'Lower estimated risk' : 'Shortest'}</h3><p class="rc-sub" data-b="sub"></p></div>
          <span class="sel-pill" data-b="pill"></span>
        </div>
        <div class="stats">
          <div class="stat"><span class="stat-l">Distance</span><span class="stat-v" data-b="dist"></span></div>
          <div class="stat"><span class="stat-l">Walk</span><span class="stat-v" data-b="time"></span></div>
          <div class="stat"><span class="stat-l">Est. risk</span><span class="stat-v" data-b="risk"></span></div>
        </div>${strip}</article>`;
    }
    _build(kind, R) {
      let h = '';
      if (kind === 'loading') h = '<div class="skel"></div><div class="skel"></div><div class="skel sm"></div>';
      else if (kind === 'none') {
        h = `<div class="note bad">${IC.alert}<div><b>No walking route found.</b> Try a nearby start or destination. A blocked or disconnected path can cause this.</div></div>
             <div class="empty">${IC.route}<h3>Nothing to compare</h3><p>Once a walkable route exists, both options appear here side by side.</p></div>`;
      } else if (kind === 'empty') {
        h = `<div class="empty">${IC.route}<h3>No routes yet</h3><p>Select your starting point and destination.</p></div>`;
      } else if (kind === 'ready') {
        h = `<div class="empty">${IC.pin}<h3>Ready to compare</h3><p>Routes appear once you search.</p></div>`;
      } else {
        const limited = R.short.r == null;
        h = this._card('low', R) + this._card('short', R) +
          '<div class="tradeoff" data-b="trade"></div>' +
          (this.opts.showBeta ? '<div class="beta-meter"><span>Faster</span><div class="track"><div class="fill" data-b="bfill"></div></div><span>Lower risk</span><b data-b="bval"></b></div>' : '') +
          `<div class="note${limited ? ' warn' : ''}">${limited ? IC.alert : IC.info}<div data-b="explain"></div></div>` +
          `<p class="fine">Compared ${this.sc.routes?.candidate_count||1} walking route${this.sc.routes?.candidate_count===1?'':'s'} - colours show local model risk</p>`;
      }
      this.el.innerHTML = h;
    }
    update() {
      if (!this.key.startsWith('routes')) return;
      const sc = this.sc, R = SN.computeRoutes(sc), el = this.el;
      const set = (root, b, html) => { const n = root.querySelector(`[data-b="${b}"]`); if (n && n._h !== html) { n.innerHTML = html; n._h = html; } };
      ['low', 'short'].forEach((k) => {
        const m = R[k], c = el.querySelector(`.route-card[data-k="${k}"]`);
        if (!c) return;
        const sel = sc.sel === k;
        c.classList.toggle('is-sel', sel);
        c.setAttribute('aria-pressed', sel);
        set(c, 'pill', sel ? 'Selected' : 'Select');
        set(c, 'sub', k === 'low' ? (JSON.stringify(R.low.path)===JSON.stringify(R.short.path) ? 'Same route at this preference' : 'Preferred at this safety setting') : 'Quickest walk');
        set(c, 'dist', `${Math.round(m.d)}<small> m</small>`);
        set(c, 'time', `${mins(m.d)}<small> min</small>`);
        set(c, 'risk', m.r == null ? '<span class="chip-warn">Data limited</span>'
          : `<span style="color:${riskColor(m.r)}">${m.r.toFixed(2)}</span><span class="risk-cls">${riskClass(m.r)}</span>`);
        if (m.seg) {
          $$('.seg-bar', c).forEach((b, i) => {
            const v = m.seg[i];
            if (v == null) return;
            b.style.height = (10 + v * 90).toFixed(1) + '%';
            b.style.backgroundColor = riskColor(v);
            b.title = `Segment ${i + 1}: ${riskClass(v)} (${v.toFixed(2)})`;
          });
        }
      });
      const ex = R.low.d - R.short.d;
      let trade;
      if (JSON.stringify(R.low.path)===JSON.stringify(R.short.path)) trade = '<span class="delta">Same route at this preference</span>';
      else {
        trade = `<span class="delta">+${Math.round(ex)} m</span><span class="delta">+${mins(R.low.d) - mins(R.short.d)} min</span>`;
        if (R.short.r != null && R.low.r != null) trade += `<span class="delta good">−${(R.short.r - R.low.r).toFixed(2)} est. risk</span>`;
      }
      set(el, 'trade', trade);
      set(el, 'explain', explain(R));
      const fill = el.querySelector('[data-b="bfill"]');
      if (fill) { fill.style.width = (sc.t * 100).toFixed(1) + '%'; set(el, 'bval', 'β ' + (sc.t * BETA_MAX).toFixed(1)); }
    }
  }
  function explain(R) {
    const ex = R.low.d - R.short.d, pct = Math.round((ex / R.short.d) * 100), dm = mins(R.low.d) - mins(R.short.d);
    if (JSON.stringify(R.low.path)===JSON.stringify(R.short.path)) return 'Both choices use the same available walking route.';
    if (R.short.r == null || R.low.r == null) return `The lower-risk option is ${Math.round(ex)} m longer (+${pct}%). Risk information is unavailable, so the routes can’t be compared on risk yet.`;
    return `The lower-risk route is ${Math.round(ex)} m longer (+${pct}%, about ${dm} min) and its estimated risk indicator is ${(R.short.r - R.low.r).toFixed(2)} lower (${R.short.r.toFixed(2)} to ${R.low.r.toFixed(2)}). These are estimates, not a guarantee of safety.`;
  }

  /* ================= settings + shared state ================= */
  const settings = { state: 'normal', showHazards: true, offline: false };
  const maps = [];
  const visibleHazards = () => (settings.showHazards ? data.getHazards().filter(h=>h.status!=='resolved') : []);

  /* ================= Integrated route planner ================= */
  const plan={routes:null,state:'normal',searched:false,loading:false,showRoutes:true,
    t:0.4,target:0.4,sel:'low',hazards:visibleHazards(),start:null,end:null,offline:false,
    previewHazard:null,synthetic_zones:[],currentLocation:null};
  let activeEndpoint='start',hazardPick=false,fetchId=0,routeTimer=0,watchId=null;
  let journeyActive=false,lastJourneyFix=null,lastJourneyAt=0,travelHeading=null;
  const resolved={start:null,end:null},searchVersions={start:0,end:0};
  const suggestionTimers={start:0,end:0};
  let suggestionBusy=false,pendingSuggestion=null;
  const avoidReportIds=new Set();
  const srcEl=$('#src'),dstEl=$('#dst'),findBtn=$('#find'),searchErr=$('#search-error');
  const beta=$('#beta'),betaOut=$('#beta-out');
  const planMap=new (SN.Map3D||SN.Map)($('#plan-map'),{mode:'planner',label:'Route map',
    onSelect:k=>selectPlan(k),onPick:(lat,lon)=>pickPlanPoint(lat,lon)});
  planMap.scene=plan;maps.push(planMap);
  const planPanel=new RoutePanel($('#plan-panel'),plan,{onSelect:k=>selectPlan(k)});
  function selectPlan(k){plan.sel=k;planMap.draw();planPanel.sync();}
  function parsePoint(value){
    const values=value.split(',').map(v=>Number(v.trim()));
    if(values.length!==2||!value.trim()||!value.split(',').every(v=>v.trim())||!values.every(Number.isFinite)||Math.abs(values[0])>90||Math.abs(values[1])>180)return null;
    return {lat:values[0],lon:values[1]};
  }
  function formatPoint(p){return p.lat.toFixed(6)+', '+p.lon.toFixed(6);}
  function endpoint(which){
    const field=which==='start'?srcEl:dstEl;
    return parsePoint(field.value)||(resolved[which]?.text===field.value?resolved[which].point:null);
  }
  function selectEndpoint(which){
    setHazardPick(false);
    activeEndpoint=which;
    srcEl.closest('.field').classList.toggle('is-active',which==='start');
    dstEl.closest('.field').classList.toggle('is-active',which==='end');
  }
  function updateEndpoints(){plan.start=endpoint('start');plan.end=endpoint('end');}
  function chooseAddress(which,place){
    const field=which==='start'?srcEl:dstEl;
    field.value=place.label;field.title=place.label;
    resolved[which]={text:place.label,point:{lat:place.lat,lon:place.lon}};
    clearTimeout(suggestionTimers[which]);++searchVersions[which];
    $('[data-search="'+which+'"]').hidden=true;
    $('#'+(which==='start'?'src':'dst')+'-results').hidden=true;
    planMap.centerOn(place.lat,place.lon);selectEndpoint(which==='start'?'end':endpoint('start')?'end':'start');scheduleRoute();
  }
  async function searchEndpoint(which,automatic=false,fromTyping=false){
    const field=which==='start'?srcEl:dstEl;
    const point=parsePoint(field.value);
    if(point){if(!automatic)scheduleRoute();return true;}
    const query=field.value.trim(),version=++searchVersions[which];
    const results=$('#'+(which==='start'?'src':'dst')+'-results');
    const button=$('[data-search="'+which+'"]');
    button.disabled=true;results.hidden=true;searchErr.hidden=true;
    try {
      const places=await data.searchAddress(query,plan.currentLocation||plan.start);
      if(version!==searchVersions[which]||field.value.trim()!==query)return false;
      if(fromTyping&&!field.closest('.endpoint-search').contains(document.activeElement))return false;
      if(!places.length){if(!automatic)showError(searchErr,'No matching address found. Try a fuller address or choose on the map.');return false;}
      if(places.length===1&&!automatic){chooseAddress(which,places[0]);return true;}
      results.replaceChildren();
      places.forEach(place=>{
        const option=document.createElement('button');option.type='button';option.className='address-option';
        option.textContent=place.label+(Number.isFinite(place.distance_m)?' - '+(place.distance_m/1000).toFixed(1)+' km':'');option.addEventListener('click',()=>chooseAddress(which,place));results.appendChild(option);
      });
      results.hidden=false;return false;
    }catch(error){if(!automatic&&version===searchVersions[which])showError(searchErr,error.message);return false;}
    finally{button.disabled=false;}
  }
  async function runSuggestions(which){
    pendingSuggestion=which;
    if(suggestionBusy)return;
    suggestionBusy=true;
    try{
      while(pendingSuggestion){
        const next=pendingSuggestion;pendingSuggestion=null;
        const field=next==='start'?srcEl:dstEl;
        if(document.activeElement===field&&field.value.trim().length>=2&&!endpoint(next))await searchEndpoint(next,true,true);
      }
    }finally{suggestionBusy=false;}
  }
  function scheduleSuggestions(which){
    clearTimeout(suggestionTimers[which]);
    const field=which==='start'?srcEl:dstEl,button=$('[data-search="'+which+'"]');
    button.hidden=field.value.trim().length<2||!!endpoint(which);
    if(!button.hidden)suggestionTimers[which]=setTimeout(()=>runSuggestions(which),600);
  }
  function scheduleRoute(){
    ++fetchId;findBtn.disabled=false;clearTimeout(routeTimer);updateEndpoints();planMap.draw();
    if(plan.start&&plan.end)routeTimer=setTimeout(()=>findRoutes(),300);
    else{plan.routes=null;plan.searched=false;plan.loading=false;planPanel.sync(true);}
  }
  function headingAlongPath(point){
    const path=plan.routes?.lower_risk.path||[];
    let best=Infinity,heading=null;
    for(let i=0;i<path.length-1;i++){
      const [la,lo]=path[i],[lb,lob]=path[i+1],scale=Math.cos(point.lat*Math.PI/180);
      const dx=(lob-lo)*scale,dy=lb-la,len=dx*dx+dy*dy;if(!len)continue;
      const t=Math.max(0,Math.min(1,((point.lon-lo)*scale*dx+(point.lat-la)*dy)/len));
      const distance=((point.lon-lo)*scale-t*dx)**2+(point.lat-la-t*dy)**2;
      if(distance<best){best=distance;heading=(Math.atan2(dx,dy)*180/Math.PI+360)%360;}
    }
    return heading;
  }
  async function findRoutes(options={}){
    updateEndpoints();
    if(!plan.start||!plan.end){if(srcEl.value&&dstEl.value)showError(searchErr,'Search each address or choose the locations on the map.');return;}
    const id=++fetchId;searchErr.hidden=true;findBtn.classList.remove('has-error');
    plan.loading=true;planPanel.sync();findBtn.disabled=true;
    const force=!!options.force;
    const currentPoint=options.currentLocation||plan.currentLocation;
    const heading=journeyActive&&force?(travelHeading??headingAlongPath(currentPoint)):null;
    try{
      const res=await data.getRouteResult('normal',plan.start,plan.end,+beta.value,null,{
        avoid_report_ids:[...avoidReportIds],force_reroute:force,journey_active:journeyActive,
        current_location:currentPoint,heading});
      if(id!==fetchId)return;
      Object.assign(plan,{loading:false,searched:true,routes:res,state:'normal',start:res.start,end:res.end,synthetic_zones:res.synthetic_zones,risk_field:res.risk_field});
      Object.assign(atlasMap.scene,{atlas:null,synthetic_zones:res.synthetic_zones,risk_field:res.risk_field,
        routes:res,searched:true,state:'normal',showRoutes:true,sel:plan.sel,
        start:res.start,end:res.end,currentLocation:plan.currentLocation});
      atlasMap.fit();atlasMap.draw();
      planMap.fit(true);planMap.draw();planPanel.sync(true);
      if(res.hazard_blocked)showError(searchErr,'No walking route avoiding these hazards was found.');
    }catch(error){if(id!==fetchId)return;plan.loading=false;planPanel.sync(true);showError(searchErr,error.message);findBtn.classList.add('has-error');}
    finally{if(id===fetchId)findBtn.disabled=false;}
  }
  function pickPlanPoint(lat,lon){
    if(hazardPick){
      setHazardPick(false);
      latEl.value=lat.toFixed(6);lonEl.value=lon.toFixed(6);
      reportMap.scene.pin={lat,lon};
      clearTimeout(routeTimer);++fetchId;plan.loading=false;planPanel.sync();
      showTab('report');reportMap.draw({dropPin:true});
      return;
    }
    const field=activeEndpoint==='start'?srcEl:dstEl;
    field.value=formatPoint({lat,lon});resolved[activeEndpoint]=null;plan.previewHazard=null;clearTimeout(suggestionTimers[activeEndpoint]);$('[data-search="'+activeEndpoint+'"]').hidden=true;
    $('#'+(activeEndpoint==='start'?'src':'dst')+'-results').hidden=true;
    if(activeEndpoint==='start')selectEndpoint('end');scheduleRoute();
  }
  srcEl.addEventListener('focus',()=>selectEndpoint('start'));dstEl.addEventListener('focus',()=>selectEndpoint('end'));
  [[srcEl,'start'],[dstEl,'end']].forEach(([field,which])=>{
    field.addEventListener('input',()=>{resolved[which]=null;++searchVersions[which];$('#'+field.id+'-results').hidden=true;scheduleRoute();scheduleSuggestions(which);});
    field.addEventListener('blur',e=>{if(!field.closest('.endpoint-search').contains(e.relatedTarget))setTimeout(()=>{$('#'+field.id+'-results').hidden=true;},150);});
    field.addEventListener('keydown',e=>{const list=$('#'+field.id+'-results');if(e.key==='Enter'){e.preventDefault();searchEndpoint(which);}else if(e.key==='ArrowDown'&&!list.hidden){e.preventDefault();list.querySelector('button')?.focus();}else if(e.key==='Escape'){list.hidden=true;clearTimeout(suggestionTimers[which]);++searchVersions[which];if(pendingSuggestion===which)pendingSuggestion=null;}});
  });
  $$('[data-search]').forEach(button=>button.addEventListener('click',()=>{const list=$('#'+(button.dataset.search==='start'?'src':'dst')+'-results');if(!list.hidden)list.hidden=true;else searchEndpoint(button.dataset.search,true);}));
  $('#search-form').addEventListener('submit',async e=>{e.preventDefault();clearTimeout(routeTimer);if(!endpoint('start'))return searchEndpoint('start');if(!endpoint('end'))return searchEndpoint('end');findRoutes();});
  $('#swap').addEventListener('click',()=>{[srcEl.value,dstEl.value]=[dstEl.value,srcEl.value];[resolved.start,resolved.end]=[resolved.end,resolved.start];scheduleRoute();});
  beta.addEventListener('input',()=>{plan.t=plan.target=+beta.value/BETA_MAX;betaOut.textContent=(+beta.value).toFixed(1);scheduleRoute();});
  function clearGpsError(){if(searchErr.dataset.kind==='gps'){searchErr.hidden=true;delete searchErr.dataset.kind;}}
  function freshLocation(){
    return new Promise((resolve,reject)=>{
      if(!navigator.geolocation)return reject(new Error('Location access is unavailable.'));
      let retried=false;
      function request(){navigator.geolocation.getCurrentPosition(position=>{
        const point={lat:position.coords.latitude,lon:position.coords.longitude};plan.currentLocation=point;
        clearGpsError();if(Number.isFinite(position.coords.heading))travelHeading=position.coords.heading;
        resolve(point);
      },error=>{
        if(error.code!==1&&!retried){retried=true;setTimeout(request,500);return;}
        reject(new Error('Location unavailable: '+(error.message||'Could not get a fresh GPS fix.')));
      },{enableHighAccuracy:true,timeout:15000,maximumAge:3000});}
      request();
    });
  }
  async function requestLocation(useAsStart=false){
    try{const point=await freshLocation();planMap.centerOn(point.lat,point.lon);
      if(useAsStart){srcEl.value=formatPoint(point);resolved.start=null;selectEndpoint('end');scheduleRoute();}}
    catch(error){showError(searchErr,error.message);}
  }
  $('#map-location').addEventListener('click',()=>requestLocation(false));$('#use-location').addEventListener('click',()=>requestLocation(true));
  async function rerouteFromHere(includeAll=false){
    clearTimeout(routeTimer);++fetchId;
    if(!endpoint('end')||(!journeyActive&&!endpoint('start')))return showError(searchErr,'Choose both endpoints before rerouting.');
    const button=$('#map-reroute');button.disabled=true;
    try{
      await data.refreshHazards();redrawHazards();
      if(includeAll)data.getHazards().filter(h=>h.status!=='resolved').forEach(h=>avoidReportIds.add(h.id));
      const activeIds=new Set(data.getHazards().filter(h=>h.status!=='resolved').map(h=>h.id));
      [...avoidReportIds].forEach(id=>{if(!activeIds.has(id))avoidReportIds.delete(id);});
      let point=null;
      if(journeyActive){point=await freshLocation();srcEl.value=formatPoint(point);resolved.start=null;}
      await findRoutes({force:true,currentLocation:point});
    }catch(error){showError(searchErr,error.message);}finally{button.disabled=false;}
  }
  $('#map-reroute').addEventListener('click',()=>rerouteFromHere(true));
  function setHazardPick(enabled){
    hazardPick=enabled;
    $('#map-hazard').classList.toggle('is-active',enabled);
    $('#map-hazard').setAttribute('aria-pressed',String(enabled));
    planMap.host.classList.toggle('is-pick',enabled);
    planMap._chip(enabled?'Choose the hazard location':'');
  }
  $('#map-hazard').addEventListener('click',()=>{
    setHazardPick(!hazardPick);
    if(hazardPick){
      clearTimeout(routeTimer);++fetchId;plan.loading=false;planPanel.sync();
      srcEl.closest('.field').classList.remove('is-active');
      dstEl.closest('.field').classList.remove('is-active');
    }
  });
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&hazardPick)setHazardPick(false);});
  function stopJourney(){journeyActive=false;if(watchId!==null)navigator.geolocation.clearWatch(watchId);watchId=null;$('#journey-toggle').textContent='Start journey';}
  $('#journey-toggle').addEventListener('click',()=>{
    if(journeyActive)return stopJourney();
    if(!endpoint('end'))return showError(searchErr,'Choose a destination first.');
    if(!navigator.geolocation)return showError(searchErr,'Location access is unavailable.');
    journeyActive=true;lastJourneyFix=null;lastJourneyAt=0;$('#journey-toggle').textContent='Stop journey';
    watchId=navigator.geolocation.watchPosition(position=>{
      if(!journeyActive)return;
      clearGpsError();
      const point={lat:position.coords.latitude,lon:position.coords.longitude};plan.currentLocation=point;planMap.draw();
      const dx=lastJourneyFix?(point.lon-lastJourneyFix.lon)*111320*Math.cos(point.lat*Math.PI/180):0;
      const dy=lastJourneyFix?(point.lat-lastJourneyFix.lat)*110574:0;
      const moved=lastJourneyFix?Math.hypot(dx,dy):Infinity;
      if(Number.isFinite(position.coords.heading))travelHeading=position.coords.heading;
      else if(lastJourneyFix&&moved>=5)travelHeading=(Math.atan2(dx,dy)*180/Math.PI+360)%360;
      if(moved>=50||Date.now()-lastJourneyAt>=60000){lastJourneyFix=point;lastJourneyAt=Date.now();srcEl.value=formatPoint(point);resolved.start=null;if(current!=='report'&&!hazardPick)scheduleRoute();}
    },error=>{showError(searchErr,error.code===1?'Location permission denied.':'GPS temporarily unavailable. Waiting for a fresh fix.');searchErr.dataset.kind='gps';if(error.code===1)stopJourney();},{enableHighAccuracy:true,maximumAge:5000,timeout:20000});
  });
  addEventListener('pagehide',stopJourney);selectEndpoint('start');
  SN.planner={scene:plan,map:planMap,refresh:findRoutes};

  /* ================= Report hazard ================= */
  const latEl = $('#h-lat'), lonEl = $('#h-lon'), descEl = $('#h-desc'), hErr = $('#h-error'), hBtn = $('#h-submit');
  const startPin = { lat: 12.9352, lon: 77.6245 };
  const reportMap = new SN.Map($('#report-map'), {
    mode: 'picker', label: 'Hazard location picker. Click to set the location',
    onPick:(lat,lon)=>{latEl.value=lat.toFixed(6);lonEl.value=lon.toFixed(6);},
  });
  reportMap.scene = { hazards: visibleHazards(), start: data.START, end: data.END, pin: Object.assign({}, startPin), offline: false };
  maps.push(reportMap);
  latEl.value = startPin.lat.toFixed(5);
  lonEl.value = startPin.lon.toFixed(5);
  [latEl, lonEl].forEach((el) => el.addEventListener('input', () => {
    const la = parseFloat(latEl.value), lo = parseFloat(lonEl.value);
    if (Number.isFinite(la) && Number.isFinite(lo) && Math.abs(la) <= 90 && Math.abs(lo) <= 180) {
      reportMap.scene.pin = { lat: la, lon: lo };
      reportMap.draw();
    }
  }));
  descEl.addEventListener('input', () => { $('#h-count').textContent = `${descEl.value.length} / 200`; });
  $('#hazard-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const typeEl = $('input[name="htype"]:checked');
    hBtn.classList.add('is-busy'); hBtn.disabled = true;
    const r = await data.submitHazard(parseFloat(latEl.value), parseFloat(lonEl.value), typeEl ? typeEl.value : '', descEl.value.trim());
    hBtn.classList.remove('is-busy'); hBtn.disabled = false;
    if (!r.ok) { showError(hErr, r.message); return; }
    hErr.hidden = true;
    plan.previewHazard={lat:r.hazard.lat,lon:r.hazard.lon};avoidReportIds.add(r.hazard.id);
    r.hazard._drop = true;
    setTimeout(() => { delete r.hazard._drop; }, 1000);
    descEl.value = ''; $('#h-count').textContent = '0 / 200';
    renderReports(true);
    redrawHazards();
    toast(r.message);
    showTab('plan');
    if(endpoint('end')&&(journeyActive||endpoint('start')))await rerouteFromHere(false);
  });
  function renderReports(flash) {
    const list = data.getHazards();
    $('#reports').innerHTML = list.map((h, i) => `
      <li class="report${flash && i === 0 ? ' is-new' : ''}">
        <span class="rep-icon">${IC.alert}</span>
        <div class="rep-main"><b>${esc(h.label)}</b><span>${h.lat.toFixed(4)}, ${h.lon.toFixed(4)}${h.description ? ' · ' + esc(h.description) : ''}</span></div>
        <span class="status ${h.status === 'new' ? 'new' : 'confirmed'}">${h.status === 'new' ? 'Unverified' : h.status==='resolved'?'Resolved':'Confirmed'}</span>
        <div class="report-review">${h.status==='resolved'?'':h.status!=='confirmed'?`<button class="btn ghost" data-review="${h.id}" data-status="confirmed">Confirm</button>`:''}${h.status!=='resolved'?`<button class="btn ghost" data-review="${h.id}" data-status="resolved">Resolve</button>`:''}</div>
      </li>`).join('');
    $('#rep-count').textContent = list.length + (list.length === 1 ? ' report' : ' reports');
  }

  $('#reports').addEventListener('click',async e=>{
    const button=e.target.closest('[data-review]');if(!button)return;
    button.disabled=true;
    try {await data.setHazardStatus(+button.dataset.review,button.dataset.status);
      renderReports(false);redrawHazards();if(plan.start&&plan.end)await findRoutes();}
    catch(error){toast(error.message,'bad');button.disabled=false;}
  });

  /* ================= Risk Atlas ================= */
  const atlasMap = new SN.Map($('#atlas-map'), { mode: 'atlas', label: 'Synthetic risk atlas and routes',onSelect:k=>{atlasMap.scene.sel=k;atlasMap.draw();} });
  atlasMap.scene = { hazards: visibleHazards(), start: data.START, end: data.END, atlas: data.ATLAS_BOUNDS, offline: false };
  maps.push(atlasMap);
  $('#atlas-location').addEventListener('click',async()=>{
    const button=$('#atlas-location');button.disabled=true;
    try{
      const point=await freshLocation();
      atlasMap.scene.currentLocation=point;
      atlasMap.centerOn(point.lat,point.lon);
    }catch(error){toast(error.message,'bad');}
    finally{button.disabled=false;}
  });

  function redrawHazards() {
    const hz = visibleHazards();
    [planMap,atlasMap].forEach(m=>{m.scene.hazards=hz;m.draw();});
    reportMap.scene.hazards=data.getHazards();reportMap.draw();
  }

  /* ================= Guardian Mode ================= */
  const G = { status: 'idle', end: 0, left: 0, total: 0, contact: '', minutes: 15, expired: false, iv: 0 };
  const CIRC = 2 * Math.PI * 52, ring = $('#g-ring');
  ring.style.strokeDasharray = CIRC.toFixed(2);
  ring.style.strokeDashoffset = CIRC.toFixed(2);
  const gContact = $('#g-contact'), gErr = $('#g-error');
  segCtl($('#g-min'), 15, (v) => { G.minutes = +v; });
  const fmt = (s) => { s = Math.max(0, Math.ceil(s)); return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0'); };
  function gPaint(rem) {
    $('#g-time').textContent = fmt(rem);
    ring.style.strokeDashoffset = (CIRC * (1 - (G.total ? rem / G.total : 0))).toFixed(2);
  }
  function gTick() {
    if (G.status !== 'active') return;
    const rem = (G.end - Date.now()) / 1000;
    if (rem <= 0) { G.status = 'ended'; G.expired = true; gRender(); return; }
    gPaint(rem);
  }
  function gRender() {
    clearInterval(G.iv);
    const card = $('#gcard'), s = G.status;
    card.dataset.status = s;
    const label = { idle: 'Not sharing', active: 'Sharing (preview)', paused: 'Paused', ended: G.expired ? 'Time limit reached' : 'Stopped' }[s];
    $('#g-label').textContent = label;
    $('#g-sub').textContent = { idle: 'not sharing', active: 'remaining', paused: 'paused', ended: 'ended' }[s];
    $('#g-meta').textContent = s === 'idle'
      ? 'Choose a contact and a time limit to preview.'
      : `Trusted contact: ${G.contact || 'not set'} · nothing is actually shared in this demo`;
    if (s === 'idle') { $('#g-time').textContent = '--:--'; ring.style.strokeDashoffset = CIRC.toFixed(2); }
    else if (s === 'active') { gTick(); G.iv = setInterval(gTick, 1000); }
    else if (s === 'paused') gPaint(G.left);
    else gPaint(0);
    const A = $('#g-actions');
    if (s === 'idle' || s === 'ended') A.innerHTML = '<button class="btn success" type="button" data-g="start">Start sharing (preview)</button>';
    else A.innerHTML = (s === 'active'
      ? '<button class="btn" type="button" data-g="pause">Pause</button>'
      : '<button class="btn primary" type="button" data-g="resume">Resume</button>') +
      '<button class="btn danger" type="button" data-g="stop">Stop sharing</button>';
  }
  $('#g-actions').addEventListener('click', (e) => {
    const b = e.target.closest('[data-g]');
    if (!b) return;
    const act = b.dataset.g;
    if (act === 'start') {
      const c = gContact.value.trim();
      if (!c) { showError(gErr, 'Add a trusted contact first.'); gContact.focus(); return; }
      gErr.hidden = true;
      Object.assign(G, { status: 'active', contact: c, total: G.minutes * 60, end: Date.now() + G.minutes * 60000, expired: false });
    } else if (act === 'pause') {
      Object.assign(G, { status: 'paused', left: Math.max(0, (G.end - Date.now()) / 1000) });
    } else if (act === 'resume') {
      Object.assign(G, { status: 'active', end: Date.now() + G.left * 1000 });
    } else if (act === 'stop') {
      Object.assign(G, { status: 'ended', left: 0, expired: false });
    }
    gRender();
    const next = $('#g-actions .btn');
    if (next) next.focus();
  });

  /* ================= Profile / sign in ================= */
  const auth = data.auth;
  let profile = auth.current();
  let authMode = 'in';
  const initials = (n) => (n || '').trim().split(/\s+/).slice(0, 2).map((w) => w[0] || '').join('').toUpperCase() || '?';
  const X_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>';

  segCtl($('#auth-mode'), 'in', (v) => setAuthMode(v));
  function setAuthMode(v) {
    authMode = v;
    const up = v === 'up';
    $('#auth-name-fld').hidden = !up;
    $('#auth-pass-hint').hidden = !up;
    $('#auth-up-extra').hidden = !up;
    $('#auth-title').textContent = up ? 'Create your account' : 'Welcome back';
    $('#auth-lead').textContent = up
      ? 'Save your profile, trusted contacts and default route preference to your account.'
      : 'Sign in to keep your trusted contacts and route preference ready for every walk.';
    $('#auth-submit-txt').textContent = up ? 'Create account' : 'Sign in';
    $('#auth-pass').setAttribute('autocomplete', up ? 'new-password' : 'current-password');
    $('#auth-error').hidden = true;
  }
  $('#auth-peek').addEventListener('click', (e) => {
    const i = $('#auth-pass'), show = i.type === 'password', b = e.currentTarget;
    i.type = show ? 'text' : 'password';
    b.setAttribute('aria-pressed', show);
    b.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
  });
  $('#auth-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = $('#auth-submit');
    btn.classList.add('is-busy'); btn.disabled = true;
    const r = authMode === 'up'
      ? await auth.signUp($('#auth-name').value, $('#auth-email').value, $('#auth-pass').value,
        { phone: $('#auth-phone').value, gender: $('#auth-gender').value, dob: $('#auth-dob').value })
      : await auth.signIn($('#auth-email').value, $('#auth-pass').value);
    btn.classList.remove('is-busy'); btn.disabled = false;
    if (!r.ok) { showError($('#auth-error'), r.message); return; }
    $('#auth-pass').value = '';
    signedIn(r.profile, authMode === 'up' ? `Account created. Welcome, ${r.profile.name}!` : `Signed in as ${r.profile.name}.`);
  });
  function signedIn(p, msg) { profile = p; applyProfile(true); renderProfile(); toast(msg); }
  $('#p-signout').addEventListener('click', async () => {
    clearTimeout(betaSave);
    if (profile) {
      const saved = await auth.update({beta:+$('#p-beta').value});
      if (!saved.ok) toast(saved.message, 'bad');
    }
    const r = await auth.signOut();
    if (!r.ok) return toast(r.message, 'bad');
    profile = null;
    renderProfile();
    gContact.value = '';
    toast('Signed out. Your profile is saved for your next sign-in.');
  });

  function renderProfile() {
    const p = profile, ini = $('#avatar-ini'), btn = $('#profile-btn');
    $('#auth-out').hidden = !!p;
    $('#auth-in').hidden = !p;
    ini.hidden = !p;
    btn.classList.toggle('signed', !!p);
    btn.setAttribute('aria-label', p ? 'Profile: ' + p.name : 'Profile and sign in');    if (!p) { $('#contact-list').innerHTML = ''; return; }
    ini.textContent = initials(p.name);
    $('#p-avatar').textContent = ini.textContent;
    $('#p-name').textContent = p.name;
    $('#p-email').textContent = p.email;
    $('#p-kind').textContent = 'Account · saved on this server';
    const b = p.beta ?? 2;
    $('#p-beta').value = b;
    $('#p-beta-out').textContent = 'β ' + (+b).toFixed(1);
    renderContacts();
    renderDetails();
  }

  /* ----- personal details list (tap a row to edit) ----- */
  // Two-tone icons: a soft fill (.du) under a crisp outline.
  const DI = {
    name: '<circle class="du" cx="12" cy="8" r="3.8"/><path class="du" d="M5 20a7 7 0 0 1 14 0z"/><circle cx="12" cy="8" r="3.8"/><path d="M5 20a7 7 0 0 1 14 0"/>',
    phone: '<path class="du" d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/><path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/><path d="M15 3.5a6 6 0 0 1 5.5 5.5M15 7a2.5 2.5 0 0 1 2 2"/>',
    email: '<rect class="du" x="3" y="5" width="18" height="14" rx="2.5"/><rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="m4 7 8 6 8-6"/>',
    gender: '<circle class="du" cx="9.5" cy="8" r="3.5"/><path class="du" d="M3 20a6.5 6.5 0 0 1 13 0z"/><circle cx="9.5" cy="8" r="3.5"/><path d="M3 20a6.5 6.5 0 0 1 13 0"/><path d="m15.5 9 2 2 4-4.5"/>',
    dob: '<path class="du" d="M3.5 7.5A2.5 2.5 0 0 1 6 5h12a2.5 2.5 0 0 1 2.5 2.5V10h-17z"/><rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/><path d="M8 13.5h.01M12 13.5h.01M16 13.5h.01M8 17h.01M12 17h.01M16 17h.01"/>',
    memberSince: '<circle class="du" cx="12" cy="9" r="5.5"/><circle cx="12" cy="9" r="5.5"/><path d="m12 6.6.8 1.6 1.7.3-1.2 1.2.3 1.7-1.6-.8-1.6.8.3-1.7-1.2-1.2 1.7-.3z"/><path d="m8.5 13.5-1.5 7 5-2.5 5 2.5-1.5-7"/>',
    emergency: '<path class="du" d="M7 18v-5a5 5 0 0 1 10 0v5z"/><path d="M7 18v-5a5 5 0 0 1 10 0v5"/><path d="M5 18h14v2.5H5zM12 2.5v2M4.2 5.7l1.4 1.4M19.8 5.7l-1.4 1.4M2.5 12h2M19.5 12h2"/>',
  };
  const FIELDS = [
    { k: 'name', label: 'Name', type: 'text', ac: 'name', req: true, c: 'accent' },
    { k: 'phone', label: 'Phone Number', type: 'tel', ac: 'tel', req: true, c: 'safe' },
    { k: 'email', label: 'Email', type: 'email', ac: 'email', req: true, c: 'info' },
    { k: 'gender', label: 'Gender', type: 'select', c: 'accent-2' },
    { k: 'dob', label: 'Date of Birth', type: 'date', ac: 'bday', req: true, c: 'warn' },
    { k: 'memberSince', label: 'Member Since', readonly: true, c: 'warn' },
    { k: 'emergency', label: 'Emergency contact', link: true, c: 'risk' },
  ];
  const CHEV = '<svg class="chev" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 5.5 6.5 6.5L9 18.5"/></svg>';
  const fmtDob = (v) => { const [y, m, d] = v.split('-').map(Number); return new Date(y, m - 1, d).toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' }); };
  const fmtMonth = (v) => { const [y, m] = v.split('-').map(Number); return new Date(y, m - 1, 1).toLocaleDateString('en-GB', { month: 'long', year: 'numeric' }); };
  function detailValue(f) {
    const p = profile;
    if (f.k === 'emergency') {
      const names = (p.contacts || []).map((c) => c.name);
      return names.length ? { text: names.join(', ') } : { text: 'Add a trusted contact', req: true };
    }
    const v = p[f.k] || '';
    if (!v) return f.req ? { text: 'Required', req: true } : { text: 'Not set', muted: true };
    if (f.k === 'dob') return { text: fmtDob(v) };
    if (f.k === 'memberSince') return { text: fmtMonth(v) };
    return { text: v };
  }
  function renderDetails() {
    $('#p-details').innerHTML = FIELDS.map((f) => {
      const val = detailValue(f);
      const ic = `<span class="d-ic" style="--ic:var(--${f.c})"><svg viewBox="0 0 24 24" aria-hidden="true">${DI[f.k]}</svg></span>`;
      const main = `<span class="d-main"><b>${f.label}</b><span class="d-val${val.req ? ' req' : ''}${val.muted ? ' muted' : ''}">${esc(val.text)}</span></span>`;
      if (f.readonly) return `<li class="drow"><div class="d-btn static">${ic}${main}</div></li>`;
      return `<li class="drow" data-k="${f.k}"><button type="button" class="d-btn" aria-expanded="false"${f.link ? '' : ` aria-controls="ed-${f.k}"`}>${ic}${main}${CHEV}</button></li>`;
    }).join('');
  }
  function openEditor(li) {
    const k = li.dataset.k, f = FIELDS.find((x) => x.k === k);
    $$('.d-edit', $('#p-details')).forEach((e) => closeEditor(e.closest('.drow')));
    const cur = profile[k] || '';
    const input = f.type === 'select'
      ? `<select class="input select" name="v" id="ed-in-${k}">${['', ...auth.GENDERS].map((g) => `<option value="${esc(g)}"${g === cur ? ' selected' : ''}>${g || 'Select'}</option>`).join('')}</select>`
      : `<input class="input" name="v" id="ed-in-${k}" type="${f.type}" autocomplete="${f.ac || 'off'}" value="${esc(cur)}"${f.type === 'date' ? ` max="${new Date().toISOString().slice(0, 10)}"` : ''}>`;
    const form = document.createElement('form');
    form.className = 'd-edit'; form.id = 'ed-' + k; form.noValidate = true;
    form.innerHTML = `<label class="sr" for="ed-in-${k}">${f.label}</label>${input}
      <div class="d-actions"><button class="btn ghost" type="button" data-cancel>Cancel</button><button class="btn primary" type="submit"><span class="spinner" aria-hidden="true"></span>Save</button></div>
      <p class="form-error" role="alert" hidden></p>`;
    li.appendChild(form);
    li.classList.add('open');
    li.querySelector('.d-btn').setAttribute('aria-expanded', 'true');
    const inp = form.querySelector('[name="v"]');
    inp.focus();
    if (inp.select && f.type !== 'date') inp.select();
    form.querySelector('[data-cancel]').addEventListener('click', () => { closeEditor(li); li.querySelector('.d-btn').focus(); });
    form.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.stopPropagation(); closeEditor(li); li.querySelector('.d-btn').focus(); } });
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = form.querySelector('[type="submit"]');
      btn.classList.add('is-busy'); btn.disabled = true;
      const r = await auth.update({ [k]: inp.value });
      btn.classList.remove('is-busy'); btn.disabled = false;
      if (!r.ok) { showError(form.querySelector('.form-error'), r.message); inp.focus(); return; }
      profile = r.profile;
      renderProfile();
      toast(`${f.label} saved.`);
      const row = $(`#p-details .drow[data-k="${k}"] .d-btn`);
      if (row) row.focus();
    });
  }
  function closeEditor(li) {
    if (!li) return;
    const e = li.querySelector('.d-edit');
    if (e) e.remove();
    li.classList.remove('open');
    li.querySelector('.d-btn').setAttribute('aria-expanded', 'false');
  }
  $('#p-details').addEventListener('click', (e) => {
    const btn = e.target.closest('button.d-btn');
    if (!btn) return;
    const li = btn.closest('.drow');
    if (li.dataset.k === 'emergency') {
      $('#contacts-card').scrollIntoView({ behavior: reduced() ? 'auto' : 'smooth', block: 'center' });
      setTimeout(() => $('#c-name').focus({ preventScroll: true }), reduced() ? 0 : 400);
      return;
    }
    if (li.classList.contains('open')) closeEditor(li); else openEditor(li);
  });

  /* ----- help dialog ----- */
  const helpDlg = $('#help-dlg');
  $('#help-btn').addEventListener('click', () => { if (helpDlg.showModal) helpDlg.showModal(); else helpDlg.setAttribute('open', ''); });
  $('#help-close').addEventListener('click', () => helpDlg.close ? helpDlg.close() : helpDlg.removeAttribute('open'));
  helpDlg.addEventListener('click', (e) => { if (e.target === helpDlg) helpDlg.close(); });
  function renderContacts() {
    const list = profile.contacts || [];
    $('#c-list').innerHTML = list.length
      ? list.map((c, i) => `<li class="contact"><span class="c-ava">${esc(initials(c.name))}</span>
          <div class="c-main"><b>${esc(c.name)}</b><span>${esc(c.phone || 'No number saved')}</span></div>
          ${c.phone ? `<a class="icon-btn sm" href="tel:${esc(c.phone.replace(/[^\d+]/g, ''))}" aria-label="Call ${esc(c.name)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/></svg></a>` : ''}
          <button type="button" class="icon-btn sm" data-del="${i}" aria-label="Remove ${esc(c.name)}">${X_ICON}</button></li>`).join('')
      : '<li class="c-empty">No trusted contacts yet. Add someone you would call on a late walk.</li>';
    $('#c-count').textContent = list.length + (list.length === 1 ? ' contact' : ' contacts');
    $('#contact-list').innerHTML = list.map((c) => `<option value="${esc(c.name)}">${esc(c.phone || '')}</option>`).join('');
    const em = $('#p-details .drow[data-k="emergency"] .d-val');
    if (em) { const v = detailValue(FIELDS[6]); em.textContent = v.text; em.classList.toggle('req', !!v.req); }
  }
  $('#c-list').addEventListener('click', async (e) => {
    const b = e.target.closest('[data-del]');
    if (!b) return;
    const gone = profile.contacts[+b.dataset.del];
    b.disabled = true;
    const r = await auth.update({contacts:profile.contacts.filter((_, i) => i !== +b.dataset.del)});
    if (!r.ok) { b.disabled = false; return toast(r.message, 'bad'); }
    profile = r.profile;
    renderContacts();
    toast(`${gone.name} removed from trusted contacts.`);
  });
  $('#c-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const n = $('#c-name').value.trim(), ph = $('#c-phone').value.trim(), err = $('#c-error');
    if (!n) { showError(err, 'Add a name for the contact.'); $('#c-name').focus(); return; }
    if (ph && !/^\+?[\d\s()-]{6,}$/.test(ph)) { showError(err, 'Enter a valid phone number, or leave it empty.'); $('#c-phone').focus(); return; }
    err.hidden = true;
    const button = $('#c-form [type="submit"]');
    button.disabled = true;
    const r = await auth.update({contacts:(profile.contacts || []).concat({ name: n, phone: ph })});
    button.disabled = false;
    if (!r.ok) return showError(err, r.message);
    profile = r.profile;
    $('#c-name').value = ''; $('#c-phone').value = '';
    renderContacts();
    applyProfile(false);
    toast(`${n} added to trusted contacts.`);
  });
  let betaSave = 0;
  $('#p-beta').addEventListener('input', (e) => {
    const v = +e.target.value;
    $('#p-beta-out').textContent = 'β ' + v.toFixed(1);
    profile.beta = v;
    clearTimeout(betaSave);
    betaSave = setTimeout(async () => {
      const r = await auth.update({beta:v});
      if (!r.ok) toast(r.message, 'bad');
    }, 250);
    applyBeta(v);
  });
  const pThemeCtl = segCtl($('#p-theme'), document.documentElement.dataset.theme, (v) => setTheme(v, true));
  function applyBeta(v) {
    beta.value = v;
    beta.dispatchEvent(new Event('input'));
  }
  /** Push saved profile defaults into the planner and Guardian Mode. */
  function applyProfile(withBeta) {
    if (!profile) return;
    if (withBeta && profile.beta != null) applyBeta(profile.beta);
    const first = (profile.contacts || [])[0];
    if (first && !gContact.value.trim() && G.status !== 'active' && G.status !== 'paused') gContact.value = first.name;
  }

  /* ================= tabs ================= */
  const TABS = ['plan', 'report', 'atlas', 'guardian', 'profile'];
  const tabs = $$('.tab'), nav = $('.tabs'), ink = $('.tab-ink');
  let current = null;
  function moveInk() {
    const b = tabs.find((t) => t.dataset.tab === current);
    $('#profile-btn').classList.toggle('is-on', current === 'profile');    if (current === 'profile') $('#profile-btn').setAttribute('aria-current', 'page'); else $('#profile-btn').removeAttribute('aria-current');
    ink.style.opacity = b ? '1' : '0';
    if (!b) return;
    ink.style.width = b.offsetWidth + 'px';
    ink.style.transform = `translateX(${b.offsetLeft}px)`;
    if (b.offsetLeft < nav.scrollLeft || b.offsetLeft + b.offsetWidth > nav.scrollLeft + nav.clientWidth) {
      nav.scrollTo({ left: b.offsetLeft - 24, behavior: reduced() ? 'auto' : 'smooth' });
    }
  }
  function showTab(id) {
    if (!TABS.includes(id)) id = 'plan';
    if (id === current) return;
    current = id;
    if(id!=='plan')setHazardPick(false);
    tabs.forEach((t) => { const on = t.dataset.tab === id; t.setAttribute('aria-selected', on); t.tabIndex = on ? 0 : -1; });
    $$('.view').forEach((v) => { const on = v.id === 'view-' + id; v.hidden = !on; v.classList.toggle('enter', on); });
    moveInk();
    if(id==='atlas'&&!atlasMap.scene.synthetic_zones?.length){
      data.getAtlas(plan.currentLocation||plan.start||{lat:12.9352,lon:77.6245})
        .then(result=>{if(!atlasMap.scene.synthetic_zones?.length){atlasMap.scene.synthetic_zones=result.synthetic_zones;atlasMap.scene.risk_field=result.risk_field;atlasMap.scene.atlas=result.bounds;atlasMap.fit();atlasMap.draw();}})
        .catch(error=>toast(error.message,'bad'));
    }
    if(id==='report'){data.refreshHazards().then(()=>{redrawHazards();renderReports(false);reportMap.fit();}).catch(error=>showError(hErr,error.message));}
    if (location.hash !== '#' + id) history.replaceState(null, '', '#' + id);
    SN.tip.hide();
    const p = $('#settings');
    if (p && !p.hidden && !p.contains(document.activeElement)) { p.hidden = true; $('#settings-btn').setAttribute('aria-expanded', 'false'); }
  }
  tabs.forEach((t, i) => {
    t.addEventListener('click', () => showTab(t.dataset.tab));
    t.addEventListener('keydown', (e) => {
      let j = null;
      if (e.key === 'ArrowRight') j = (i + 1) % tabs.length;
      else if (e.key === 'ArrowLeft') j = (i - 1 + tabs.length) % tabs.length;
      else if (e.key === 'Home') j = 0;
      else if (e.key === 'End') j = tabs.length - 1;
      if (j == null) return;
      e.preventDefault();
      showTab(tabs[j].dataset.tab);
      tabs[j].focus();
    });
  });
  addEventListener('hashchange', () => showTab(location.hash.slice(1)));
  addEventListener('resize', moveInk);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(moveInk);

  /* ================= theme ================= */
  const themeBtn = $('#theme-btn');
  function setTheme(t, save) {
    document.documentElement.dataset.theme = t;
    if (save) store.set('theme', t);
    SN.refreshColors();
    themeBtn.setAttribute('aria-label', t === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
    $('meta[name="theme-color"]').setAttribute('content', t === 'dark' ? '#070B18' : '#F3F5FC');
    $$('svg', themeBtn).forEach((s) => { s.style.animation = 'none'; void s.getBoundingClientRect(); s.style.animation = ''; });
    maps.forEach((m) => m.themeChanged());
    if (typeof pThemeCtl !== 'undefined') pThemeCtl.set(t);
    planPanel.sync(true);
  }
  themeBtn.addEventListener('click', () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark', true));

  /* ================= settings popover ================= */
  const pop = $('#settings'), sBtn = $('#settings-btn');
  function openPop(open) {
    pop.hidden = !open;
    sBtn.setAttribute('aria-expanded', open);
    if (open) {
      pop.style.top = Math.round($('#topbar').getBoundingClientRect().bottom + 8) + 'px';
      const f = pop.querySelector('[aria-pressed="true"]');
      if (f) f.focus();
    }
  }
  sBtn.addEventListener('click', (e) => { e.stopPropagation(); openPop(pop.hidden); });
  document.addEventListener('click', (e) => { if (!pop.hidden && !pop.contains(e.target) && !sBtn.contains(e.target)) openPop(false); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !pop.hidden) { openPop(false); sBtn.focus(); } });
  $('#tg-hazards').addEventListener('change', (e) => { settings.showHazards = e.target.checked; redrawHazards(); });
  $('#tg-offline').addEventListener('change', (e) => {
    settings.offline = e.target.checked;
    maps.forEach((m) => { m.scene.offline = settings.offline; m._tiles(); });
  });

  /* ================= boot ================= */
  document.addEventListener('visibilitychange', () => {
    document.body.classList.toggle('hidden-tab', document.hidden);
  });
  setTheme(document.documentElement.dataset.theme === 'light' ? 'light' : 'dark', false);
  renderReports(false);
  gRender();
  renderProfile();
  applyProfile(true);
  auth.restore().then(p => { profile = p; renderProfile(); applyProfile(true); });
  planPanel.sync();
  showTab(location.hash.slice(1) || 'plan');
  data.refreshHazards().then(()=>{renderReports(false);redrawHazards();})
    .catch(error=>toast(error.message,'bad'));
  requestLocation(false);
})();
