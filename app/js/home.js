(function () {
  "use strict";
  function today() { var d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
  function days(date, now) { return Math.round((Date.parse(date + 'T00:00:00Z') - Date.parse(now + 'T00:00:00Z')) / 86400000); }
  function warrantyState(end, now) {
    if (!end) return 'unknown';
    var remaining=days(end,now);
    return remaining<0?'expired':remaining===0?'today':'active';
  }
  // A delivery or installation date only matters while it can still anchor a warranty.
  function coverEnded(r, now) {
    if (r.warrantyStatus === 'expired' || r.warrantyStatus === 'not_applicable') return true;
    if (!r.expires) return false;
    var later = r.secondaryExpiry && r.secondaryExpiry > r.expires ? r.secondaryExpiry : r.expires;
    return days(later, now || today()) < 0;
  }
  // A recorded warranty start also anchors the cover, so the delivery date is no longer needed.
  function dateNeeded(r, now) { return r.kind === 'appliance' && !r.installed && !r.delivered && !r.warrantyStart && !coverEnded(r, now); }
  function needsInformation(r, now) { return r.status !== 'Verified' || dateNeeded(r, now); }
  function nextService(r) {
    if (r.nextService) return r.nextService;
    if (!r.lastService || !r.frequencyMonths) return '';
    var d = new Date(r.lastService + 'T00:00:00Z'), day = d.getUTCDate();
    d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() + r.frequencyMonths);
    var end = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).getUTCDate();
    d.setUTCDate(Math.min(day, end)); return d.toISOString().slice(0, 10);
  }
  function actions(records, now) {
    var out = [];
    records.filter(function (r) { return r.status !== 'Archived'; }).forEach(function (r) {
      if (r.status !== 'Verified') out.push({ record: r, label: r.action || (r.status === 'Document missing' ? 'Add supporting document' : 'Check record details'), date: '', tone: 'check' });
      else if(dateNeeded(r, now))out.push({record:r,label:'Add delivery or installation date',date:'',tone:'check'});
      var dates = [];
      if (r.kind === 'insurance' && r.expires) dates.push([r.expires, r.status === 'Verified' ? 'Review policy renewal' : 'Check latest renewal']);
      if (r.kind === 'appliance' && r.secondaryExpiry && r.status === 'Verified' && days(r.secondaryExpiry, now) >= 0) dates.push([r.secondaryExpiry, (r.secondaryWarranty || 'Additional warranty') + ' ends']);
      if (r.kind === 'appliance' && r.expires && r.status === 'Verified' && days(r.expires, now) >= 0) dates.push([r.expires, 'Warranty ends']);
      if (r.kind === 'mortgage') {
        if (r.reviewDate) dates.push([r.reviewDate, 'Mortgage review']);
        else if (r.lockInEnd) {
          var d = new Date(r.lockInEnd + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() - Math.max(r.noticeDays || 0, 90));
          dates.push([d.toISOString().slice(0, 10), 'Suggested mortgage review']);
        }
      }
      if (r.kind === 'maintenance' && nextService(r)) dates.push([nextService(r), 'Service due']);
      dates.forEach(function (pair) {
        var left = days(pair[0], now);
        if (left <= 90) out.push({ record: r, label: pair[1], date: pair[0], tone: left < 0 ? 'past' : 'soon' });
      });
    });
    return out.sort(function (a, b) { return (a.date || '9999').localeCompare(b.date || '9999'); });
  }
  if (typeof module !== 'undefined' && module.exports) { module.exports = { days: days, actions: actions, nextService: nextService, warrantyState: warrantyState, needsInformation: needsInformation }; return; }

  var root = document.getElementById('pane-home'), store = { revision: 0, records: [] }, editable = false;
  var category = 'All';
  var view = 'list', sortDir = 'asc', spyHandler = null, room = 'All';
  try { view = ['list','grid','coverage'].includes(localStorage.getItem('home-view')) ? localStorage.getItem('home-view') : 'list'; } catch (e) { /* storage unavailable */ }
  var section = 'appliance', query = '', infoFilter = 'all', warrantyFilter = 'all', sortBy = 'attention', showArchived = false, txs = [], lastFocus, busy = false;
  var titles = { appliance: 'Items & fixtures', insurance: 'Home & fire insurance', mortgage: 'Mortgage', maintenance: 'Maintenance' };
  var labels = { appliance: 'appliance or furnishing', insurance: 'home policy', mortgage: 'mortgage', maintenance: 'maintenance task' };
  var fields = {
    appliance: [['category','Category','select','Appliances,Fixtures,Furniture'],['provider','Retailer / supplier'],['room','Room'],['roomDetail','Location detail'],['brand','Brand'],['model','Model'],['serial','Serial number'],['cost','Recorded cost (S$)','number'],['itemCost','Original item cost (S$)','number'],['warrantyCost','Extended warranty cost (S$)','number'],['deliveryCost','Delivery cost (S$)','number'],['costBasis','Cost source','select','Receipt / sales order,House sheet,Sheet allocation,Gift,Mixed sources'],['funding','Paid / gifted by'],['delivered','Delivery date','date'],['deliveryDetails','Delivery details'],['deliverySource','Delivery date source'],['installed','Installation date','date'],['installationType','Installation event'],['installationSource','Installation date source'],['warrantyStart','Warranty start','date'],['warrantyStartBasis','Warranty start basis'],['expires','Warranty end','date'],['warrantyTerms','Warranty terms'],['warrantyCertificate','Warranty certificate'],['warrantySourceUrl','Warranty terms URL'],['coverage','Coverage & exclusions'],['secondaryWarranty','Additional cover (e.g. compressor)'],['secondaryExpiry','Additional cover ends','date']],
    insurance: [['provider','Insurer'],['coverage','Coverage summary'],['premium','Premium per payment (S$)','number'],['cadence','Payment frequency','select','Yearly,Monthly,One-off'],['starts','Policy start','date'],['expires','Policy end','date']],
    mortgage: [['provider','Bank'],['balance','Outstanding balance (S$)','number'],['balanceDate','Balance as of','date'],['instalment','Monthly instalment (S$)','number'],['rate','Current annual rate (%)','number'],['rateSchedule','Rate schedule'],['lockInEnd','Lock-in ends','date'],['noticeDays','Notice period (days)','integer'],['reviewDate','Review date','date']],
    maintenance: [['room','Room / area'],['provider','Service provider'],['cost','Cost per service (S$)','number'],['installed','Setup / installation date','date'],['installationType','Event type'],['installationSource','Date source'],['lastService','Last serviced','date'],['frequencyMonths','Repeat every (months)','integer'],['nextService','Next service (optional override)','date']]
  };
  function esc(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function(c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  function money(v) { return v == null || v === '' ? 'Not recorded' : 'S$' + Number(v).toLocaleString('en-SG', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function date(v) { return v ? new Date(v + 'T00:00:00').toLocaleDateString('en-SG', { day:'numeric', month:'short', year:'numeric' }) : 'Not recorded'; }
  // Short, specific reasons a record still needs attention; the first one labels the card.
  function attention(r) {
    var out=[];
    if(r.status==='Document missing')out.push('Document missing');
    else if(r.status==='Needs checking')out.push(r.cost==null&&r.costBasis!=='Gift'?'Cost to confirm':['House sheet','Sheet allocation'].includes(r.costBasis)?'Receipt needed':'Needs checking');
    if(dateNeeded(r))out.push('Date needed');
    return out;
  }
  function badge(r) { var item=r.kind==='appliance', complete=item&&!needsInformation(r), reasons=attention(r);return '<span class="home-badge '+((item?complete:r.status==='Verified')?'verified':'')+'" title="'+esc(reasons.join(' · '))+'">'+esc(item?(complete?'Complete':reasons[0]||'Needs info'):r.status)+'</span>'; }
  function source(r) { return r.sourceUrl && /^https:\/\//.test(r.sourceUrl) ? '<a href="'+esc(r.sourceUrl)+'" target="_blank" rel="noopener noreferrer">'+esc(r.sourceName || 'Open document')+icon('external')+'</a>' : '<span class="home-muted">'+esc(r.sourceName || 'No document linked')+'</span>'; }
  function coverDate(r,key) {
    var state=warrantyState(r[key],today());
    if(state==='unknown')return 'End date unknown';
    if(r.status!=='Verified'&&r.warrantyStatus!=='covered')return (state==='expired'?'Recorded expiry passed · ':'Recorded end · ')+date(r[key])+' (unverified)';
    return (state==='expired'?'Expired · ':state==='today'?'Ends today · ':'Ends ')+date(r[key]);
  }
  function warranty(r) { return r.expires?coverDate(r,'expires'):esc(r.warrantyTerms || 'End date unknown'); }
  function warrantyTone(r) {
    if(r.warrantyStatus==='not_applicable')return 'not_applicable';
    if(r.warrantyStatus==='covered'&&!r.expires)return 'covered';
    var dates=['expires','secondaryExpiry'].filter(function(k){return r[k];});
    if(dates.length)return dates.some(function(k){return warrantyState(r[k],today())!=='expired';})?'covered':'expired';
    return r.warrantyStatus||(r.warrantyTerms?'unconfirmed':'missing');
  }
  function overviewHTML(assets) {
    var needs=assets.filter(needsInformation).length;
    var covered=assets.filter(function(r){return warrantyTone(r)==='covered';}).length;
    var expired=assets.filter(function(r){return warrantyTone(r)==='expired';}).length;
    var missing=assets.filter(function(r){return warrantyTone(r)==='missing';}).length;
    var notTracked=assets.filter(function(r){return warrantyTone(r)==='not_applicable';}).length;
    var active=infoFilter==='needs'?'needs':warrantyFilter!=='all'?warrantyFilter:'all';
    return '<nav class="home-overview" aria-label="Collection overview">'+[['all','All items',assets.length],['needs','Needs info',needs],['covered','Covered',covered],['expired','Expired',expired],['missing','No warranty details',missing],['not_applicable','Not tracked',notTracked]].map(function(q){
      return '<button data-quick="'+q[0]+'" aria-pressed="'+(active===q[0])+'"><i class="home-dot '+q[0]+'" aria-hidden="true"></i><span>'+q[1]+'</span><strong>'+q[2]+'</strong></button>';
    }).join('')+'</nav>';
  }
  function txLabel(t) { return t.date + ' · ' + (t.displayName || t.description || 'Transaction') + ' · ' + money(Math.abs(t.amount)) + ' · ' + t.source; }
  async function json(url) { var r = await fetch(url, {cache:'no-store'}); if (!r.ok) throw new Error('Could not load ' + url); return r.json(); }
  var ICONS={grid:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',external:'<path d="M14 4h6v6M20 4l-9 9M18 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h6"/>',chevronRight:'<path d="m9 5 7 7-7 7"/>',close:'<path d="M6 6l12 12M18 6 6 18"/>',check:'<path d="m5 12 4 4L19 7"/>',up:'<path d="m6 14 6-6 6 6"/>',down:'<path d="m6 10 6 6 6-6"/>',room:'<path d="M3 21V8l9-5 9 5v13M9 21v-6h6v6"/>'};
  function icon(name, cls) { return '<svg class="home-icon '+(cls||'')+'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+ICONS[name]+'</svg>'; }
  function homeIcon(category) {
    var paths = category === 'Furniture' ? '<path d="M5 11V7a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v4M5 16v3m14-3v3M4 11a2 2 0 0 0-2 2v3h20v-3a2 2 0 0 0-4 0H6a2 2 0 0 0-2-2Z"/>' : category === 'Fixtures' ? '<path d="M9 18h6m-5 3h4M8 13a6 6 0 1 1 8 0c-1 1-1 2-1 3H9c0-1 0-2-1-3Z"/>' : '<rect x="5" y="2" width="14" height="20" rx="2"/><path d="M5 8h14m-10-3h2"/><circle cx="12" cy="15" r="4"/>';
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+paths+'</svg>';
  }
  function roomOf(r) { return r.room||'Unassigned'; }
  // Rooms stay exact on each record; the page groups by coarser zones so no section holds one item.
  var ZONES={'Kitchen':'Kitchen & laundry','Laundry':'Kitchen & laundry','Living room':'Living & bedrooms','Entrance':'Living & bedrooms','Bedroom':'Living & bedrooms','Study':'Living & bedrooms','Bathroom':'Bathrooms','Whole home':'Whole home','Storeroom':'Whole home'};
  function zoneOf(r) { return ZONES[roomOf(r)]||roomOf(r); }
  // A category splits into zones only when it has at least eight items and every zone keeps two or more.
  function zonesFor(items) {
    if(items.length<8)return [];
    var counts={};items.forEach(function(r){counts[zoneOf(r)]=(counts[zoneOf(r)]||0)+1;});
    var names=Object.keys(counts);
    if(names.length<2||names.some(function(n){return counts[n]<2;}))return [];
    return names.sort(function(a,b){if(a==='Unassigned')return 1;if(b==='Unassigned')return -1;return counts[b]-counts[a]||a.localeCompare(b);}).map(function(n){return {name:n,count:counts[n]};});
  }
  function categoryNav(assets) {
    return '<nav class="home-category-nav" aria-label="Item categories">'+['All','Appliances','Fixtures','Furniture'].map(function(c) {
      var items=assets.filter(function(r){return c==='All'||r.category===c;});
      var zones=c==='All'?[]:zonesFor(items);
      var open=c===category;
      var roomList=!zones.length?'':'<div class="home-rail-rooms" data-for="'+esc(c)+'" '+(open?'':'hidden')+'>'+zones.map(function(z){
        return '<button type="button" data-room="'+esc(z.name)+'" data-room-category="'+esc(c)+'" aria-pressed="'+(open&&room===z.name)+'"><span>'+esc(z.name)+'</span><span>'+z.count+'</span></button>';
      }).join('')+'</div>';
      return '<div class="home-rail-group"><button data-category="'+c+'" aria-pressed="'+(c===category)+'"><span class="home-category-label">'+(c==='All'?icon('grid'):homeIcon(c))+'<strong>'+(c==='All'?'All items':c)+'</strong><span>'+items.length+'</span></span><small>'+money(items.reduce(function(n,r){return n+(r.cost||0);},0))+'</small></button>'+roomList+'</div>';
    }).join('')+'</nav>';
  }
  function syncRail() {
    root.querySelectorAll('[data-category]').forEach(function(n){n.setAttribute('aria-pressed',String(n.dataset.category===category));});
    root.querySelectorAll('.home-rail-rooms').forEach(function(list){list.hidden=list.dataset.for!==category;});
    root.querySelectorAll('[data-room]').forEach(function(n){n.setAttribute('aria-pressed',String(n.dataset.roomCategory===category&&n.dataset.room===room));});
  }
  function render() {
    var active=store.records.filter(function(r){return r.status!=='Archived';}), assets=active.filter(function(r){return r.kind==='appliance';});
    var known=assets.filter(function(r){return r.cost!=null;}), todo=actions(active,today()), due=todo.filter(function(a){return a.date&&a.record.kind===section;}), checks=active.filter(needsInformation);
    var infoRows=active.filter(function(r){return r.kind===section;}), needsInfo=infoRows.filter(needsInformation).length, completeInfo=infoRows.length-needsInfo;
    root.innerHTML='<header class="home-heading"><div><h2>Your home</h2><p>'+assets.length+' items · Purchases, warranties & care</p></div><div class="home-total"><span>Recorded home purchases</span><strong>'+money(known.reduce(function(n,r){return n+r.cost;},0))+'</strong><small>'+known.length+' priced · '+(assets.length-known.length)+' unpriced</small></div></header>'+
      '<div class="home-sections" role="group" aria-label="Home sections">'+Object.keys(titles).map(function(k){return '<button data-section="'+k+'" aria-pressed="'+(k===section)+'">'+({appliance:'Collection',insurance:'Insurance',mortgage:'Home loan',maintenance:'Care & maintenance'}[k])+'</button>';}).join('')+'</div>'+
      (due.length?'<div class="home-due-strip">'+due.map(actionHTML).join('')+'</div>':'')+(section==='appliance'?overviewHTML(assets):'')+
      '<div class="home-workspace '+(section!=='appliance'?'home-workspace-wide':'')+'">'+(section==='appliance'?categoryNav(assets):'')+'<section class="home-collection"><div class="home-panel-head"><div><h3>'+(section==='appliance'?'Your collection':titles[section])+'</h3><p class="home-muted">'+(section==='appliance'?'Purchases grouped by category.':section==='insurance'?'Policies, cover and renewal dates.':section==='mortgage'?'Your loan and next refinancing review.':'Services and upkeep, in one place.')+'</p></div>'+(section==='appliance'?viewToggle():'<button class="home-button primary" id="home-add" '+(!editable?'disabled':'')+'>+ Add '+labels[section]+'</button>')+'</div><div class="home-filters"><input id="home-search" type="search" aria-label="Search Home records" placeholder="'+({appliance:'Search items, brands or models…',insurance:'Search policies…',mortgage:'Search loan records…',maintenance:'Search services…'}[section])+'" value="'+esc(query)+'"><label class="home-info-filter"><span>Information</span><select id="home-info-filter" aria-label="Information status"><option value="all" '+(infoFilter==='all'?'selected':'')+'>All ('+infoRows.length+')</option><option value="needs" '+(infoFilter==='needs'?'selected':'')+'>Needs info ('+needsInfo+')</option><option value="complete" '+(infoFilter==='complete'?'selected':'')+'>Complete ('+completeInfo+')</option></select></label>'+(section==='appliance'?'<label><span>Warranty</span><select id="home-warranty-filter" aria-label="Warranty status"><option value="all" '+(warrantyFilter==='all'?'selected':'')+'>All</option><option value="covered" '+(warrantyFilter==='covered'?'selected':'')+'>Covered</option><option value="expired" '+(warrantyFilter==='expired'?'selected':'')+'>Expired</option><option value="unconfirmed" '+(warrantyFilter==='unconfirmed'?'selected':'')+'>Unconfirmed</option><option value="missing" '+(warrantyFilter==='missing'?'selected':'')+'>No details</option><option value="not_applicable" '+(warrantyFilter==='not_applicable'?'selected':'')+'>Not tracked</option></select></label><label><span>Sort</span><select id="home-sort" aria-label="Sort collection"><option value="attention" '+(sortBy==='attention'?'selected':'')+'>Needs attention</option><option value="name" '+(sortBy==='name'?'selected':'')+'>Name</option><option value="newest" '+(sortBy==='newest'?'selected':'')+'>Newest</option><option value="cost" '+(sortBy==='cost'?'selected':'')+'>Highest cost</option><option value="expiry" '+(sortBy==='expiry'?'selected':'')+'>Warranty end</option><option value="warranty" '+(sortBy==='warranty'?'selected':'')+'>Warranty attention</option></select></label>':'')+'<label class="home-archived-filter"><input id="home-archived" type="checkbox" '+(showArchived?'checked':'')+'> Archived</label></div><div id="home-register"></div></section></div>'+
      (checks.length?'<details class="home-checks home-panel"><summary><strong>Complete your records</strong><span>'+checks.length+' to review</span></summary><div class="home-check-list">'+todo.filter(function(a){return !a.date;}).map(actionHTML).join('')+'</div></details>':'')+referenceHTML()+
      '<footer class="home-footer"><span>'+(editable?'Saved on this computer':'Read-only')+' · Documents in Drive</span><button class="home-button" id="home-refresh">Refresh</button></footer>';
    renderRegister();
    root.querySelectorAll('[data-category]').forEach(function(b){b.onclick=function(){category=b.dataset.category;room='All';syncRail();renderRegister();};});
    root.querySelectorAll('[data-room]').forEach(function(b){b.onclick=function(){category=b.dataset.roomCategory;room=room===b.dataset.room&&category===b.dataset.roomCategory?'All':b.dataset.room;syncRail();renderRegister();};});
    root.querySelectorAll('[data-section]').forEach(function(b){b.onclick=function(){section=b.dataset.section;query='';render();};});
    root.querySelector('#home-search').oninput=function(e){query=e.target.value;renderRegister();};
    root.querySelector('#home-info-filter').onchange=function(e){infoFilter=e.target.value;renderRegister();};
    if(root.querySelector('#home-warranty-filter'))root.querySelector('#home-warranty-filter').onchange=function(e){warrantyFilter=e.target.value;renderRegister();};
    if(root.querySelector('#home-sort'))root.querySelector('#home-sort').onchange=function(e){setSort(e.target.value,false);renderRegister();};
    root.querySelectorAll('[data-view]').forEach(function(b){b.onclick=function(){view=b.dataset.view;try{localStorage.setItem('home-view',view);}catch(err){}root.querySelectorAll('[data-view]').forEach(function(n){n.setAttribute('aria-pressed',String(n===b));});renderRegister();};});
    root.querySelector('#home-archived').onchange=function(e){showArchived=e.target.checked;renderRegister();};
    root.querySelectorAll('[data-quick]').forEach(function(b){b.onclick=function(){var v=b.dataset.quick;if(v==='all'){query='';category='All';room='All';showArchived=false;}infoFilter=v==='needs'?'needs':'all';warrantyFilter=['covered','expired','missing','not_applicable'].includes(v)?v:'all';render();};});
    if(root.querySelector('#home-add'))root.querySelector('#home-add').onclick=function(){openRecord({id:'home_'+crypto.randomUUID(),kind:section,name:'',status:'Needs checking',category:section==='appliance'&&category!=='All'?category:''});};
    root.querySelector('#home-refresh').onclick=load;
    root.querySelectorAll('[data-open]').forEach(bindOpen);
  }
  var homePhotos={
    home_washerdryer:{files:['bosch-washer','bosch-dryer'],label:'Bosch WGG254A0SG washer and WQG24200SG dryer',url:'https://www.bosch-home.com.sg/en/product/washersanddryers/tumbledryers/heatpumpdryers/WQG24200SG'},
    home_dishwasher:{files:['fotile.svg'],label:'Fotile BD2B-G1',url:'https://fotile.com.bd/product/fotile-bd2b-g1-built-in-dishwasher/'},
    home_water_dispenser:{files:['happie-joy.svg'],label:'Happie Joy in silver',url:'https://happie.sg/products/joy-water-purifier/'},
    home_aircon:{files:['daikin.svg'],label:'Daikin CTKM25VVMG indoor unit',url:'https://www.daikin-bim-library.daikin.com/DKG-BIMDOWNLOAD/en/item/detail?categoryID=1400000&id=20430000&parentCategoryId=1400000&type=category'},
    home_hobhood:{files:['rinnai.svg'],label:'Rinnai RB-7032H CFB hob',url:'https://www.rinnai.sg/product-page/rb-7032h-cfb'},
    home_doorlock:{files:['yale'],label:'Yale YDR50GA gate lock',url:'https://www.yalehome.com/sg/en/products/smart-door-locks/metal-gate-smart-locks/ydr50ga'},
    home_vacuum:{files:['dreame-base','dreame-robot'],label:'Dreame X40 Master',url:'https://www.dreametech.com/products/x40-master-robot-vacuum'},
    home_fridge:{files:['hitachi'],label:'Hitachi R-VG695P9MSX',url:'https://www.hitachi-homeappliances.com/sg/promo/oneforeveryone/'},
    home_fittings:{files:['champs-sylphy'],label:'Champs Sylphy instant water heater from the bathroom fittings bundle',url:'https://champs.com.sg/product/instant-water-heater-sylphy/'},
    home_bed:{files:['woosa-mysa'],label:'Woosa Mysa mattress from the split-king bed bundle',url:'https://woosasleep.co/products/mysa'},
    home_fans:{files:['bestar-star5'],label:'Bestar Star 5 ceiling fan from the fan bundle',url:'https://intertech-hardware.com/products/bestar-star-5'},
    home_tv:{files:['sony-x90l'],label:'Sony BRAVIA XR-65X90L',url:'https://electronics.sony.com/tv-video/televisions/all-tvs/p/xr65x90l'},
    home_tv55:{files:['sony-x90l'],label:'Sony BRAVIA XR-55X90L',url:'https://electronics.sony.com/tv-video/televisions/all-tvs/p/xr55x90l'},
    home_airfryer:{files:['russell-taylors-z7'],label:'Russell Taylors Z7 6.5L air fryer',url:'https://shopee.sg/Russell-Taylors-3D-Visible-Window-Digital-Air-Fryer-Extra-Large-(6.5L)-Z7-i.234952174.24430709673'},
    home_microwave:{files:['cornell-microwave'],label:'Cornell 25L microwave',url:'https://www.harveynorman.com.sg/home-appliances/kitchen-appliances-en/microwave-ovens-en/cornell-25l-microwave-oven-black-cmos25bk.html'},
    home_switches:{files:['legrand-galion'],label:'Legrand Galion dark silver switch',url:'https://www.legrand.com/ecatalogue/en/catalog/products/galion-2-gangs-1-way-switch-16ax-dark-silver-282402-c3?category_id=43406'},
    home_study_tables:{files:['omnidesk-classic'],label:'Omnidesk Classic Wildwood desk',url:'https://theomnidesk.com/products/classic-wildwood'},
    home_switchbot_hub:{files:['switchbot-hub-mini'],label:'SwitchBot Hub Mini',url:'https://www.switch-bot.com/products/switchbot-hub-mini'},
    home_spot_cleaner:{files:['russell-taylors-sc10'],label:'Russell Taylors SC10 spot cleaner',url:'https://russelltaylors.sg/products/russell-taylors-portable-spot-cleaner-fabric-sofa-carpet-upholstery-cleaner-sc10'},
    home_monitor_arm:{files:['prism-arc-lite'],label:'PRISM+ Arc Lite dual monitor arm',url:'https://prismplus.sg/products/arc-lite'},
    home_blender:{files:['xiaomi-blender'],label:'Xiaomi Blender 600W 1.75L',url:'https://www.mi.com/global/product/xiaomi-blender/'}
  };
  // These shapes follow the item descriptions in the receipts, not just their category.
  var itemDrawings={
    home_switchbot_hub:'<rect x="4" y="6" width="16" height="14" rx="4"/><path d="M8 3a7 7 0 0 1 8 0M10 9h4m-2 8h.01"/>',
    home_monitor_arm:'<rect x="1" y="3" width="9" height="8" rx="1"/><rect x="14" y="3" width="9" height="8" rx="1"/><path d="M5 11v3h14v-3m-7 3v7m-4 0h8"/>',
    home_spot_cleaner:'<rect x="3" y="8" width="13" height="12" rx="3"/><path d="M6 8V5h7v3m-7 4h7m3 4h3V7h3v4m-16 9v1m7-1v1"/>',
    home_blender:'<path d="M6 4h11l-2 11H8ZM8 15h7l3 6H5Zm9-9h3v5h-4M9 2h5m-4 7h3m-3 3h2m-1 6h1"/>',
    home_aircon:'<rect x="2" y="4" width="20" height="9" rx="2"/><path d="M5 10h14M7 16v4m5-4v3m5-3v4"/>',
    home_fans:'<path d="M12 2v5m0 2c-3-5-8-4-9-1l7 4m2 0c4-5 9-3 9 0l-7 1m-2 1c2 5-1 8-4 7l3-7m0-2C6 12 2 16 4 18l7-5m3-2c4-1 5-5 3-7l-4 6"/><circle cx="12" cy="12" r="2"/>',
    home_tv:'<rect x="2" y="4" width="20" height="14" rx="1"/><path d="m6 18-1 3m13-3 1 3M4 16h16"/>',
    home_tv55:'<rect x="3" y="5" width="18" height="12" rx="1"/><path d="m7 17-1 3m11-3 1 3M5 15h14"/>',
    home_led:'<ellipse cx="10" cy="10" rx="7" ry="5"/><ellipse cx="10" cy="10" rx="3" ry="2"/><path d="M3 10v4c0 3 4 5 8 5h10v-4H11c-4 0-8-2-8-5m12 7h1m2 0h1M5 8l1 1m7-2 1 1"/>',
    'home_settee-light':'<ellipse cx="12" cy="11" rx="10" ry="5"/><ellipse cx="12" cy="11" rx="6" ry="3"/><path d="M5 15v3m7-2v4m7-5v3"/>',
    home_fittings:'<path d="M5 21V6a3 3 0 0 1 6 0v1m-3 2h6l-1-2H9Zm1 3v1m3-1v1m-3 3v1m3-1v1M16 17h6l-1 4h-4Z"/>',
    home_bed:'<path d="M2 20V9m20 11v-8M2 17h20M4 11l6 3h10v3M5 8l5 3m-6-1 2-3 4 2-1 3M8 14v3"/>',
    home_curtain:'<path d="M2 4h20M4 4v17l5-3V4m6 0v14l5 3V4M6 6v11m12-11v11M9 18h6"/>',
    'home_door-stopper':'<path d="M3 4h18v3H3ZM5 7v14m14-14v14M8 9h8v3H8Zm4 0 5-3m-2 12h1"/>',
    home_furniture:'<path d="M5 12V7a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v5M12 5v7M3 12h3v4h12v-4h3v7H3ZM5 19v2m14-2v2M8 14h8"/>',
    home_kinetic:'<rect x="3" y="7" width="18" height="15" rx="2"/><path d="M12 7v15M6 3a10 10 0 0 1 12 0M9 5a5 5 0 0 1 6 0M7 11v6m10-6v6"/>',
    home_lights:'<path d="M3 4h18M6 4l-2 7h7L9 4m6 0-2 7h7l-2-7M7 14v3m10-3v3M3 20h18"/>',
    home_hobhood:'<rect x="2" y="6" width="20" height="13" rx="1"/><path d="M5 10h5m-2.5-2.5v5M14 10h5m-2.5-2.5v5"/><circle cx="12" cy="16" r="1"/>',
    home_kitchen:'<path d="M2 11h20l-2 9H4Zm10 0V5a3 3 0 0 1 6 0v2m-3 0h5M6 14h12"/>',
    home_dining:'<ellipse cx="12" cy="10" rx="9" ry="4"/><path d="M6 13v8m12-8v8M2 6v9h3M22 6v9h-3M9 3v3m6-3v3"/>',
    home_washerdryer:'<rect x="1" y="4" width="10" height="17" rx="1"/><rect x="13" y="4" width="10" height="17" rx="1"/><circle cx="6" cy="14" r="3"/><circle cx="18" cy="14" r="3"/><path d="M3 8h6m6 0h6"/>',
    home_dishwasher:'<rect x="3" y="2" width="18" height="20" rx="2"/><path d="M3 7h18m-7-2h3M6 18h12M7 10v6m3-6v6m3-6v6m3-6v6"/>',
    home_water_dispenser:'<rect x="6" y="2" width="12" height="20" rx="2"/><path d="M6 8h12m-6 0v4m-3 3h6l-1 4h-4ZM10 5h4"/>',
    home_airfryer:'<path d="M6 3h12l2 6v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V9Z"/><path d="M5 11h14m-9 0v5h4v-5M9 6h6"/>',
    home_microwave:'<rect x="2" y="5" width="20" height="14" rx="2"/><rect x="5" y="8" width="10" height="8" rx="1"/><path d="M18 9h1m-1 3h1m-1 3h1"/>',
    home_storage_rack:'<path d="M4 2v20m16-20v20M4 7h16M4 14h16M4 21h16M7 3h5v4m2 3h4v4M7 17h6v4"/>',
    home_switches:'<rect x="4" y="2" width="16" height="20" rx="2"/><rect x="7" y="6" width="4" height="12" rx="1"/><rect x="13" y="6" width="4" height="12" rx="1"/>',
    home_ventilation:'<rect x="2" y="2" width="20" height="20" rx="2"/><circle cx="12" cy="12" r="7"/><path d="M12 12c-6-5-1-8 1-5v5m-1 0c7-3 8 3 4 4l-4-4m0 0c-1 8-6 6-6 2l6-2"/>',
    home_study_tables:'<path d="M2 11h20M4 11v10m16-10v10M15 12v6h5M6 3h10v6H6Zm5 6v2m6 4h1"/>',
    home_bedding:'<path d="M3 6c4 1 14 1 18 0-1 4-1 8 0 12-4-1-14-1-18 0 1-4 1-8 0-12Z"/><path d="M6 9h12M6 15h12"/>'
  };
  function itemIcon(r) {
    var path=itemDrawings[r.id];
    if(!path) {
      var n=r.name.toLowerCase();
      if(/storage rack/.test(n))path=itemDrawings.home_storage_rack;
      else if(/washer.*dryer/.test(n))path=itemDrawings.home_washerdryer;
      else if(/water dispenser/.test(n))path=itemDrawings.home_water_dispenser;
      else if(/air fryer/.test(n))path=itemDrawings.home_airfryer;
      else if(/ventilation/.test(n))path=itemDrawings.home_ventilation;
      else if(/study tables/.test(n))path=itemDrawings.home_study_tables;
      else if(/item details to confirm/.test(n))path='<path d="M3 4h10l8 8-9 9-9-9Z"/><circle cx="7" cy="8" r="1"/><path d="M11 9c2-2 5 0 3 2l-1 1m-1 3h.01"/>';
    }
    return path?'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+path+'</svg>':homeIcon(r.category);
  }
  function thumbnail(r, large) {
    var photo=homePhotos[r.id]||(r.linkedRecord&&homePhotos[r.linkedRecord]);
    return '<span class="home-thumb '+(photo&&photo.files.length>1?'home-thumb-pair':'')+(large?' home-thumb-lg':'')+'" aria-hidden="true">'+(photo?photo.files.map(function(f){return '<img src="assets/home/'+f+(f.includes('.')?'':'.webp')+'" alt="" width="48" height="48" loading="lazy" decoding="async">';}).join(''):itemIcon(r))+'</span>';
  }
  var viewIcons={list:'<path d="M4 6h16M4 12h16M4 18h16"/>',grid:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',coverage:'<path d="M3 6h9M3 12h14M3 18h6"/><circle cx="18" cy="6" r="1"/><circle cx="20" cy="12" r="1"/><circle cx="12" cy="18" r="1"/>'};
  function viewToggle() {
    return '<div class="home-view-toggle" role="group" aria-label="Collection view">'+[['list','List'],['grid','Grid'],['coverage','Coverage']].map(function(v){
      return '<button type="button" data-view="'+v[0]+'" aria-pressed="'+(view===v[0])+'" title="'+v[1]+' view"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+viewIcons[v[0]]+'</svg><span>'+v[1]+'</span></button>';
    }).join('')+'</div>';
  }
  var toneRank={expired:0,unconfirmed:1,missing:2,covered:3,not_applicable:4};
  var CATEGORIES=['Appliances','Fixtures','Furniture'];
  function coverEnd(r) { return r.expires||r.secondaryExpiry||''; }
  var defaultDir={newest:'desc',cost:'desc'};
  function setSort(key, toggle) {
    if(toggle&&sortBy===key)sortDir=sortDir==='asc'?'desc':'asc';
    else{sortBy=key;sortDir=defaultDir[key]||'asc';}
    var select=root.querySelector('#home-sort');if(select)select.value=sortBy;
  }
  function sortItems(items) {
    var dir=sortDir==='desc'?-1:1;
    function key(r){
      switch(sortBy){
        case 'name':return r.name.toLowerCase();
        case 'newest':return r.purchased||r.delivered||r.installed||'';
        case 'cost':return r.cost||0;
        case 'category':return (r.category||'zzz')+' '+r.name.toLowerCase();
        case 'expiry':return coverEnd(r)||'9999';
        case 'warranty':return toneRank[warrantyTone(r)];
        default:return Number(!needsInformation(r));
      }
    }
    return items.slice().sort(function(a,b){var ka=key(a),kb=key(b);var c=typeof ka==='number'?ka-kb:String(ka).localeCompare(String(kb));return c*dir||a.name.localeCompare(b.name);});
  }
  function groups(rows) {
    var cats=CATEGORIES.slice();
    if(rows.some(function(r){return !CATEGORIES.includes(r.category);}))cats.push('Other');
    return cats.map(function(c){
      var items=sortItems(rows.filter(function(r){return c==='Other'?!CATEGORIES.includes(r.category):r.category===c;}));
      var zones=room==='All'?zonesFor(items):[];
      var rooms=zones.length?zones.map(function(z){return {name:z.name,items:items.filter(function(r){return zoneOf(r)===z.name;})};}):[{name:'',items:items}];
      return {name:c,items:items,rooms:rooms};
    }).filter(function(g){return g.items.length;});
  }
  // One primary state per item. Everything finer lives in the detail view.
  function primaryChip(r) {
    var tone=warrantyTone(r), end=r.expires, now=today();
    var label=tone==='covered'?(end&&days(end,now)<=90?'Expiring':'Covered'):tone==='expired'?'Expired':tone==='unconfirmed'?'Unverified':tone==='not_applicable'?'Not tracked':'No warranty';
    var cls=label==='Expiring'?'expiring':tone.replace('_','-');
    var unverified=tone==='covered'&&r.status!=='Verified'&&r.warrantyStatus!=='covered';
    return '<span class="home-chip '+cls+(unverified?' dashed':'')+'" title="'+esc(r.warrantyLabel||r.warrantyTerms||'')+'">'+label+'</span>';
  }
  function coverText(r) {
    var end=coverEnd(r);
    if(end)return (warrantyState(end,today())==='expired'?'Ended ':'Ends ')+date(end);
    return r.warrantyLabel||(r.warrantyTerms?'End date unknown':'Receipt or terms needed');
  }
  function itemCell(r) {
    var sub=[r.room,r.brand,r.model].filter(Boolean).join(' · ')||r.provider||'';
    return '<span class="home-list-item">'+thumbnail(r)+'<span><strong>'+esc(r.name)+'</strong>'+(sub?'<small title="'+esc(sub)+'">'+esc(sub)+'</small>':'')+'</span></span>';
  }
  function itemButton(r) {
    var sub=[r.room,r.brand,r.model].filter(Boolean).join(' · ')||r.provider||'';
    return '<button type="button" class="home-list-item home-row-open" data-open="'+esc(r.id)+'" aria-label="Open '+esc(r.name)+'">'+thumbnail(r)+'<span><strong>'+esc(r.name)+'</strong>'+(sub?'<small title="'+esc(sub)+'">'+esc(sub)+'</small>':'')+'</span></button>';
  }
  function listHTML(rows) {
    var cols=[['name','Item'],['cost','Cost','num'],['expiry','Warranty'],['attention','To do']];
    var head=cols.map(function(c){var active=sortBy===c[0];return '<th class="'+(c[2]||'')+'" aria-sort="'+(active?(sortDir==='asc'?'ascending':'descending'):'none')+'"><button type="button" data-sort="'+c[0]+'">'+c[1]+(active?icon(sortDir==='asc'?'up':'down','home-sort-icon'):'')+'</button></th>';}).join('');
    function row(r){
      var reasons=attention(r), complete=!needsInformation(r);
      return '<tr data-open="'+esc(r.id)+'"><td>'+itemButton(r)+'</td><td class="num">'+(r.cost==null?'<span class="home-muted">'+(r.costBasis==='Gift'?'Gift':'—')+'</span>':money(r.cost))+'</td><td>'+primaryChip(r)+'<small>'+esc(coverText(r))+'</small></td><td>'+(complete?'<span class="home-todo done">'+icon('check')+'Complete</span>':'<span class="home-todo" title="'+esc(reasons.join(' · '))+'">'+esc(reasons[0])+(reasons.length>1?' +'+(reasons.length-1):'')+'</span>')+'</td></tr>';
    }
    var body=groups(rows).map(function(g){
      var total=g.items.reduce(function(n,r){return n+(r.cost||0);},0);
      var header='<tr class="home-list-group"><th colspan="4"><span class="home-group-icon">'+homeIcon(g.name)+'</span><span>'+esc(g.name)+'</span><span class="home-group-count">'+g.items.length+'</span><span class="home-list-group-total">'+money(total)+'</span></th></tr>';
      return g.rooms.map(function(sub,i){
        var roomHead=sub.name?'<tr class="home-list-room"><th colspan="4"><span>'+esc(sub.name)+'</span><span class="home-group-count">'+sub.items.length+'</span></th></tr>':'';
        return '<tbody class="home-category-group" data-group="'+esc(g.name)+'" data-room="'+esc(sub.name)+'">'+(i===0?header:'')+roomHead+sub.items.map(row).join('')+'</tbody>';
      }).join('');
    }).join('');
    var total=rows.reduce(function(n,r){return n+(r.cost||0);},0);
    return '<div class="home-list-wrap"><table class="home-list"><colgroup><col style="width:46%"><col style="width:15%"><col style="width:23%"><col style="width:16%"></colgroup><thead><tr>'+head+'</tr></thead>'+body+'<tfoot><tr><td>'+rows.length+' items</td><td class="num">'+money(total)+'</td><td colspan="2"></td></tr></tfoot></table></div>';
  }
  function shiftDays(iso, n) { var d=new Date(iso+'T00:00:00Z'); d.setUTCDate(d.getUTCDate()+n); return d.toISOString().slice(0,10); }
  function coverageHTML(rows) {
    var now=today();
    var dated=rows.filter(function(r){return r.expires||r.secondaryExpiry;}).map(function(r){
      var start=r.warrantyStart||r.installed||r.delivered||r.purchased||shiftDays(r.expires||r.secondaryExpiry,-365);
      var ends=[r.expires,r.secondaryExpiry].filter(Boolean).sort();
      return {r:r,start:start,end:ends[ends.length-1],first:r.expires||ends[0]};
    }).sort(function(a,b){return a.first.localeCompare(b.first)||a.r.name.localeCompare(b.r.name);});
    var undated=sortItems(rows.filter(function(r){return !r.expires&&!r.secondaryExpiry;}));
    if(!dated.length)return '<div class="home-empty"><h4>No dated warranty cover to chart</h4><p>Items without an end date are listed in the list and grid views.</p></div>';
    var nowYear=+now.slice(0,4);
    var minYear=Math.min.apply(null,dated.map(function(d){return +d.start.slice(0,4);}).concat(nowYear));
    var fullMax=Math.max.apply(null,dated.map(function(d){return +d.end.slice(0,4);}).concat(nowYear));
    // Cap the axis five years ahead so one- to three-year covers stay readable; longer covers run off the right edge with an arrow.
    var maxYear=Math.min(fullMax,nowYear+5);
    var axisStart=minYear+'-01-01', axisEnd=(maxYear+1)+'-01-01', span=days(axisEnd,axisStart);
    function pct(d){return Math.max(0,Math.min(100,days(d,axisStart)/span*100));}
    function term(start,end){var m=Math.round(days(end,start)/30.44);if(m<1)return '';var y=m/12,r=Math.round(y);return Math.abs(y-r)<0.1?r+(r===1?' yr':' yrs'):m<24?m+' mo':(Math.round(y*10)/10)+' yrs';}
    function bar(cls,start,end,text,title,tagged){
      var left=pct(start), over=end>=axisEnd, right=over?100:pct(end), width=Math.max(0.6,right-left), wide=width>=16;
      var label=over?text+' →':text;
      return '<span class="home-gantt-bar '+cls+(over?' overflow':'')+'" style="left:'+left+'%;width:'+width+'%" title="'+esc(title)+'">'+(wide?'<span>'+esc(label)+'</span>':'')+'</span>'+(tagged&&!wide?'<span class="home-gantt-tag '+cls+'" style="'+(right>78?'right:'+(100-left+0.8):'left:'+(right+0.8))+'%">'+esc(label)+'</span>':'');
    }
    var years=[];for(var y=minYear;y<=maxYear;y++)years.push(y);
    var grid='<span class="home-gantt-lines" aria-hidden="true">'+years.map(function(y){return '<i style="left:'+pct(y+'-01-01')+'%"></i>';}).join('')+'</span>';
    var axis='<div class="home-gantt-axis"><span></span><div>'+years.map(function(y){return '<b style="left:'+pct(y+'-01-01')+'%">'+y+'</b>';}).join('')+'<i class="home-gantt-today" style="left:'+pct(now)+'%" title="Today · '+date(now)+'"><span>Today</span></i></div></div>';
    var body=dated.map(function(d){
      var r=d.r, bars='';
      var unverified=r.status!=='Verified'&&r.warrantyStatus!=='covered';
      if(r.expires){var st=warrantyState(r.expires,now);var tone=st==='expired'?'expired':days(r.expires,now)<=90?'expiring':'covered';bars+=bar(tone+(unverified?' dashed':''),d.start,r.expires,term(d.start,r.expires)+' · '+(st==='expired'?'ended ':'ends ')+date(r.expires),'Warranty · '+date(d.start)+' to '+date(r.expires),true);}
      if(r.secondaryExpiry){var from=r.expires&&r.expires<r.secondaryExpiry?r.expires:d.start;var st2=warrantyState(r.secondaryExpiry,now);bars+=bar('secondary '+(st2==='expired'?'expired':'covered'),from,r.secondaryExpiry,(r.secondaryWarranty||'Additional cover')+' · '+date(r.secondaryExpiry),(r.secondaryWarranty||'Additional cover')+' · to '+date(r.secondaryExpiry),false);}
      return '<div class="home-gantt-row" data-open="'+esc(r.id)+'" tabindex="0" role="button" aria-label="Open '+esc(r.name)+'"><div class="home-gantt-label">'+itemCell(r)+'</div><div class="home-gantt-track">'+grid+'<i class="home-gantt-today" style="left:'+pct(now)+'%"></i>'+bars+'</div></div>';
    }).join('');
    var rest=undated.length?'<details class="home-gantt-rest"><summary><strong>Without a dated end</strong><span>'+undated.length+' items</span></summary><div class="home-gantt-rest-list">'+undated.map(function(r){return '<button type="button" class="home-gantt-rest-item" data-open="'+esc(r.id)+'">'+itemCell(r)+primaryChip(r)+'</button>';}).join('')+'</div></details>':'';
    return '<div class="home-gantt" aria-label="Warranty coverage timeline">'+axis+body+'</div><p class="home-muted home-gantt-note">Bars run from the recorded warranty start (or installation, delivery or purchase date) to the recorded end, labelled with the term length.'+(fullMax>maxYear?' Cover running past '+maxYear+' is cut at the right edge with an arrow to its end year.':'')+' Thin lower bars are additional cover such as a compressor or mattress. Dashed bars are dates from unverified documents.</p>'+rest;
  }
  function collectionHTML(rows) {
    if(view==='list')return listHTML(rows);
    if(view==='coverage')return coverageHTML(rows);
    return groups(rows).map(function(g){
      var items=g.items, c=g.name;
      return '<section class="home-category-group" data-group="'+esc(c)+'"><header><div><span class="home-group-icon">'+homeIcon(c)+'</span><h4>'+c+'</h4><span class="home-group-count">'+items.length+'</span></div><span>'+money(items.reduce(function(n,r){return n+(r.cost||0);},0))+'</span></header>'+g.rooms.map(function(sub){
        return '<div class="home-room-group" data-group="'+esc(c)+'" data-room="'+esc(sub.name)+'">'+(sub.name?'<h5><span>'+esc(sub.name)+'</span><span class="home-group-count">'+sub.items.length+'</span></h5>':'')+'<div class="home-item-grid">'+sub.items.map(function(r){
        var info=r.brand||r.provider||r.room||'Brand to add';
        var complete=!needsInformation(r), reasons=attention(r);
        var eventDate=r.installed?esc(r.installationType||'Installed')+' · '+date(r.installed):r.delivered?'Delivered · '+date(r.delivered):r.purchased?'Purchased · '+date(r.purchased):'No dates recorded';
        var model=r.model?'<span class="home-card-model" title="'+esc(r.model)+'">'+esc(r.model)+'</span>':'';
        return '<button class="home-asset-card" data-open="'+esc(r.id)+'"><span class="home-card-top">'+thumbnail(r)+'<span class="home-card-title"><span class="home-card-brand">'+esc(info)+'</span><strong class="home-card-name">'+esc(r.name)+'</strong>'+model+'</span></span><span class="home-card-price'+(r.cost==null?' unpriced':'')+'">'+(r.cost==null?(r.costBasis==='Gift'?'Housewarming gift':'Cost to add'):money(r.cost))+'</span><span class="home-card-meta"><span class="home-card-date">'+eventDate+'</span>'+(complete?'<span class="home-card-state recorded">Complete</span>':'<span class="home-card-state needs-info" title="'+esc(reasons.join(' · '))+'">'+esc(reasons[0])+'</span>')+'</span><span class="home-cover-row">'+primaryChip(r)+'<span>'+esc(coverText(r))+'</span></span></button>';
        }).join('')+'</div></div>';
      }).join('')+'</section>';
    }).join('');
  }
  // Scroll spy: while every category is shown, the rail follows the group under the sticky header.
  // Scroll spy: the rail follows the category and room under the sticky header while the view is unfiltered by room.
  function bindScrollSpy() {
    if(spyHandler){window.removeEventListener('scroll',spyHandler);spyHandler=null;}
    var nav=root.querySelector('.home-category-nav');
    if(!nav||section!=='appliance'||room!=='All'||view==='coverage')return;
    var blocks=Array.prototype.slice.call(root.querySelectorAll('#home-register [data-room]'));
    if(blocks.length<2)return;
    var ticking=false;
    function update(){
      ticking=false;
      var line=120, cat='', rm='';
      blocks.forEach(function(b){var box=b.getBoundingClientRect();if(box.top<=line&&box.bottom>line){cat=b.dataset.group;rm=b.dataset.room;}});
      if(!cat&&window.innerHeight+window.scrollY>=document.documentElement.scrollHeight-2){var last=blocks[blocks.length-1];if(last.getBoundingClientRect().top<window.innerHeight){cat=last.dataset.group;rm=last.dataset.room;}}
      var railCat=category==='All'?cat:category;
      nav.querySelectorAll('[data-category]').forEach(function(b){var on=category==='All'&&b.dataset.category===cat&&!!cat;b.classList.toggle('is-current',on);if(on)b.setAttribute('aria-current','true');else b.removeAttribute('aria-current');});
      nav.querySelectorAll('.home-rail-rooms').forEach(function(list){list.hidden=list.dataset.for!==railCat;});
      nav.querySelectorAll('[data-room]').forEach(function(b){var on=!!cat&&b.dataset.roomCategory===cat&&b.dataset.room===rm;b.classList.toggle('is-current',on);if(on)b.setAttribute('aria-current','true');else b.removeAttribute('aria-current');});
    }
    spyHandler=function(){if(!ticking){ticking=true;setTimeout(update,60);}};
    window.addEventListener('scroll',spyHandler,{passive:true});
    update();
  }
  function actionHTML(a) { return '<button class="home-action '+a.tone+'" data-open="'+esc(a.record.id)+'"><span><strong>'+esc(a.label)+'</strong><small>'+esc(a.record.name)+'</small></span><span>'+(a.date?date(a.date):esc(a.record.status))+icon('chevronRight')+'</span></button>'; }
  function bindOpen(b) {
    b.onclick=function(e){if(e.target.closest('a'))return;var inner=e.target.closest('[data-open]');if(inner&&inner!==b)return;var r=store.records.find(function(r){return r.id===b.dataset.open;});if(r)openRecord(r);};
    if(b.tagName!=='BUTTON')b.onkeydown=function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();b.click();}};
  }
  // A maintenance record may carry a fixed schedule (e.g. a prepaid three-year filter package) rendered as one card.
  function scheduleItems(r) { return Array.isArray(r.schedule)?r.schedule:[]; }
  function scheduleState(item, now) { return item.done?'done':days(item.due,now)<0?'overdue':days(item.due,now)<=90?'soon':'upcoming'; }
  function scheduleHTML(r) {
    var items=scheduleItems(r);if(!items.length)return '';var now=today();
    return '<ol class="home-schedule" aria-label="Replacement schedule">'+items.map(function(it){var st=scheduleState(it,now);return '<li class="'+st+'"><span class="home-schedule-dot" aria-hidden="true">'+(st==='done'?icon('check'):'')+'</span><strong>'+esc(it.label)+'</strong><small>'+(st==='done'?'Done '+date(it.completed||it.due):st==='overdue'?'Due '+date(it.due)+' · overdue':'Due '+date(it.due))+'</small></li>';}).join('')+'</ol>';
  }
  function scheduleFacts(r) {
    var items=scheduleItems(r), done=items.filter(function(i){return i.done;}).length, next=nextService(r);
    return [['Per replacement',money(r.cost)+(r.cost!=null&&items.length>1?'<small>'+items.length+'-visit package '+money(r.cost*items.length)+'</small>':'')],['Next replacement',date(next)+(next&&days(next,today())<0?'<small>Overdue</small>':'')],['Completed',done+' of '+items.length]];
  }
  function renderRegister() {
    var rows = store.records.filter(function(r){var incomplete=needsInformation(r),infoMatches=infoFilter==='all'||(infoFilter==='needs'&&incomplete)||(infoFilter==='complete'&&!incomplete);var warrantyMatches=section!=='appliance'||warrantyFilter==='all'||warrantyTone(r)===warrantyFilter;return r.kind===section&&(section!=='appliance'||category==='All'||r.category===category)&&(section!=='appliance'||room==='All'||zoneOf(r)===room)&&infoMatches&&warrantyMatches&&(showArchived||r.status!=='Archived')&&JSON.stringify(r).toLowerCase().includes(query.toLowerCase());});
    var el=root.querySelector('#home-register');
    if (!rows.length) { bindScrollSpy(); var filtered=query||infoFilter!=='all'||(section==='appliance'&&(warrantyFilter!=='all'||category!=='All'||room!=='All'));el.innerHTML='<div class="home-empty"><h4>'+(filtered?'No matching records':'No '+titles[section].toLowerCase()+' recorded')+'</h4><p>'+(filtered?'Change the search, category or filters.':section==='mortgage'?'Add your loan letter to record the bank, rate and review dates.':'Add a record to keep costs, dates and documents together.')+'</p></div>';return; }
    if(section==='appliance'){el.innerHTML=collectionHTML(rows);el.querySelectorAll('[data-open]').forEach(bindOpen);el.querySelectorAll('[data-sort]').forEach(function(b){b.onclick=function(){setSort(b.dataset.sort,true);renderRegister();};});bindScrollSpy();return;}
    bindScrollSpy();
    rows.sort(function(a,b){var ka=section==='maintenance'?(nextService(a)||'9999'):(a.expires||'9999'), kb=section==='maintenance'?(nextService(b)||'9999'):(b.expires||'9999');return ka.localeCompare(kb)||a.name.localeCompare(b.name);});
    el.innerHTML='<div class="home-service-grid">'+rows.map(function(r){
      var facts=section==='insurance'?[['Premium',money(r.premium)+(r.cadence?' · '+esc(r.cadence):'')],['Policy end',warranty(r)],['Coverage',esc(r.coverage||'To add')]]:section==='mortgage'?[['Balance',money(r.balance)],['Balance as of',date(r.balanceDate)],['Monthly instalment',money(r.instalment)],['Annual rate',r.rate==null?'To add':esc(r.rate)+'%'],['Lock-in ends',date(r.lockInEnd)],['Review date',date(r.reviewDate)]]:r.installed?[['Setup date',date(r.installed)],['Event',esc(r.installationType||'Installation')],['Provider',esc(r.provider||'To add')]]:scheduleItems(r).length?scheduleFacts(r):[['Service cost',money(r.cost)],['Last service',date(r.lastService)],['Next service',date(nextService(r))]];
      return '<article class="home-service-card"><header><div class="home-service-head">'+(section==='maintenance'?thumbnail(r):'')+'<div><span class="home-card-brand">'+esc(r.provider||r.room||titles[section])+'</span><h4>'+esc(r.name)+'</h4></div></div>'+badge(r)+'</header><dl>'+facts.map(function(f){return '<div><dt>'+f[0]+'</dt><dd>'+f[1]+'</dd></div>';}).join('')+'</dl>'+scheduleHTML(r)+(r.action?'<p class="home-service-note">'+esc(r.action)+'</p>':'')+'<footer>'+source(r)+'<button class="home-button" data-open="'+esc(r.id)+'">View details '+icon('chevronRight')+'</button></footer></article>';
    }).join('')+'</div>';
    el.querySelectorAll('[data-open]').forEach(bindOpen);
  }
  function referenceHTML() {
    var ref=store.costReference;if(!ref)return '';
    var comparisons=ref.comparisons||[];
    return '<details class="home-panel home-cost-reference"><summary><strong>House sheet comparison</strong><span class="home-muted"> Original categories, amounts and differences</span></summary><p class="home-muted">Read from '+esc(ref.range)+' on '+date(ref.checkedAt)+'. This is a historical source snapshot. HDB amounts are not a current mortgage balance. These amounts are not added to the item register total.</p><p><a href="'+esc(ref.sourceUrl)+'" target="_blank" rel="noopener noreferrer">Open the original House sheet '+icon('external')+'</a></p><div class="home-reference-groups">'+ref.groups.map(function(g){return '<details><summary><strong>'+esc(g.name)+'</strong><span>'+money(g.reportedTotal)+'</span></summary><table class="home-table"><thead><tr><th>Source item</th><th>Sheet amount</th></tr></thead><tbody>'+g.rows.map(function(r){return '<tr><td>'+esc(r[0])+'</td><td>'+ (r[1]==null?esc(r[2]||'Not recorded'):money(r[1]))+'</td></tr>';}).join('')+'</tbody></table></details>';}).join('')+'</div><p class="home-muted">Sheet renovation total: '+money(ref.renovationTotal)+' (Appliances + Fixtures + Furniture + Renovation). The sheet lists Storerack twice; one record is included until the second purchase is confirmed. Gift labels are preserved without assuming who owns an item.</p><h3>Reconciliation</h3><div class="home-table-wrap"><table class="home-table"><thead><tr><th>Item / group</th><th>House sheet</th><th>Current register</th><th>Notes</th></tr></thead><tbody>'+comparisons.map(function(c){var found=c.recordIds.map(function(id){return store.records.find(function(r){return r.id===id&&r.status!=='Archived';});}).filter(Boolean);var complete=found.length===c.recordIds.length&&found.every(function(r){return r.cost!=null;});var total=complete?found.reduce(function(n,r){return n+r.cost;},0):null;return '<tr><td>'+esc(c.name)+'</td><td>'+money(c.sheetAmount)+'</td><td>'+money(total)+(total!=null&&Math.abs(total-c.sheetAmount)>=0.005?'<small>Difference '+money(Math.round((total-c.sheetAmount)*100)/100)+'</small>':'')+'</td><td>'+esc(c.note)+'</td></tr>';}).join('')+'</tbody></table></div></details>';
  }
  var dialog=document.createElement('dialog');dialog.className='home-dialog';dialog.setAttribute('aria-labelledby','home-dialog-title');document.body.appendChild(dialog);
  var backdropPressed=false;
  function outsideDialog(e) {
    var bounds=dialog.getBoundingClientRect();
    return e.target===dialog&&(e.clientX<bounds.left||e.clientX>bounds.right||e.clientY<bounds.top||e.clientY>bounds.bottom);
  }
  dialog.addEventListener('pointerdown',function(e){backdropPressed=outsideDialog(e);});
  dialog.addEventListener('click',function(e){
    if(backdropPressed&&outsideDialog(e)&&!busy)dialog.close();
    backdropPressed=false;
  });
  dialog.addEventListener('close',function(){if(lastFocus&&lastFocus.isConnected)lastFocus.focus();else document.getElementById('home-add')?.focus();});
  dialog.addEventListener('cancel',function(e){if(busy)e.preventDefault();});
  function field(f,r) {
    var key=f[0], label=f[1], type=f[2]||'text', value=r[key] == null ? '' : r[key];
    var input=type==='select'?'<select name="'+key+'"><option value="">Not recorded</option>'+f[3].split(',').map(function(v){return '<option '+(v===value?'selected':'')+'>'+esc(v)+'</option>';}).join('')+'</select>':type==='textarea'?'<textarea name="'+key+'" rows="4" maxlength="1500">'+esc(value)+'</textarea>':'<input name="'+key+'" type="'+(type==='integer'?'number':type)+'" value="'+esc(value)+'" '+(type==='number'||type==='integer'?'min="0" step="'+(type==='integer'?'1':'any')+'"':'maxlength="500"')+' '+(key==='name'?'required':'')+'>';
    return '<label class="'+(type==='textarea'?'home-wide':'')+'">'+label+input+'</label>';
  }
  function groupedFields(r) {
    var groups = r.kind==='appliance' ? [
      ['Item details','category,room,brand,model,serial'],
      ['Ownership, delivery & installation','provider,cost,costBasis,funding,delivered,deliveryDetails,deliverySource,installed,installationType,installationSource'],
      ['Warranty & cover','warrantyStart,warrantyStartBasis,expires,warrantyTerms,secondaryWarranty,secondaryExpiry']
    ] : r.kind==='insurance' ? [['Policy details','provider,coverage'],['Premium & dates','premium,cadence,starts,expires']] : r.kind==='mortgage' ? [['Loan details','provider,balance,balanceDate,instalment,rate,rateSchedule'],['Refinancing & notice','lockInEnd,noticeDays,reviewDate']] : [['Service details','room,provider,cost'],['Schedule','lastService,frequencyMonths,nextService']];
    return '<section class="home-record-basics"><div class="home-form-grid">'+field(['name','Name'],r)+field(['status','Record status','select','Needs checking,Verified,Document missing,Archived'],r)+'</div></section>'+groups.map(function(g,i){
      return '<details class="home-field-group" '+(i===0?'open':'')+'><summary>'+g[0]+'</summary><div class="home-form-grid">'+g[1].split(',').map(function(key){return field(fields[r.kind].find(function(f){return f[0]===key;}),r);}).join('')+'</div></details>';
    }).join('')+'<details class="home-field-group"><summary>Documents & notes</summary><div class="home-form-grid">'+field(['action','Next action / what needs checking'],r)+field(['sourceName','Document label'],r)+field(['sourceUrl','Document or folder link (HTTPS)','url'],r)+field(['notes','Notes, source filenames & dated repair / service history','textarea'],r)+'</div></details>';
  }
  function openItem(record) {
    function facts(keys) {
      return keys.split(',').filter(function(k){return record[k]!=null&&record[k]!=='';}).map(function(k){
        var f=fields.appliance.find(function(f){return f[0]===k;});
        var value=(k==='expires'||k==='secondaryExpiry')?coverDate(record,k):f[2]==='date'?date(record[k]):f[2]==='number'?money(record[k]):record[k];
        var copy=(k==='serial'||k==='model')?'<button type="button" class="home-copy" data-copy="'+esc(record[k])+'" aria-label="Copy '+(k==='serial'?'serial number':'model')+'">Copy</button>':'';
        return '<div><dt>'+esc(f[1])+'</dt><dd>'+esc(value)+copy+'</dd></div>';
      }).join('');
    }
    function timeline() {
      var now=today();
      var steps=[['Purchased',record.purchased],['Delivered',record.delivered],[record.installationType||'Installed',record.installed],['Warranty start',record.warrantyStart],['Warranty end',record.expires,'end'],[record.secondaryWarranty||'Additional cover',record.secondaryExpiry,'end']].filter(function(s){return s[1];});
      if(steps.length<2)return '';
      return '<ol class="home-timeline" aria-label="Key dates">'+steps.map(function(s){return '<li class="'+(days(s[1],now)<=0?'done ':'')+(s[2]||'')+'"><span>'+esc(s[0])+'</span><strong>'+date(s[1])+'</strong></li>';}).join('')+'</ol>';
    }
    var photo=homePhotos[record.id];
    var subtitle=[record.brand,record.model].filter(Boolean).join(' · ');
    var warrantyFacts=facts('warrantyStart,warrantyStartBasis,expires,warrantyTerms,warrantyCertificate,secondaryWarranty,secondaryExpiry,coverage');
    var warrantySource=record.warrantySourceUrl&&/^https:\/\//.test(record.warrantySourceUrl)?'<p class="home-source"><a href="'+esc(record.warrantySourceUrl)+'" target="_blank" rel="noopener noreferrer">Open official warranty terms '+icon('external')+'</a></p>':'';
    var warrantyBasis=record.warrantyStatus==='not_applicable'?'Warranty is not tracked for this item.':record.warrantyStartBasis==='Shopee delivery'?'Warranty begins from the Shopee delivery date: '+date(record.warrantyStart)+'.':record.warrantyStartBasis==='Delivery'?'Warranty begins from the recorded delivery date: '+date(record.warrantyStart)+'.':record.warrantyStart?'Warranty begins from the recorded installation date: '+date(record.warrantyStart)+'.':record.installed?'Warranty begins from the recorded installation date: '+date(record.installed)+'.':record.delivered?'Delivered '+date(record.delivered)+'. Warranty start is not yet recorded.':'Installation or delivery date needed to establish the warranty start and calculate expiry.';
    var components=Array.isArray(record.components)?record.components:[];
    var componentHTML=components.length?'<section class="home-component-section"><h3>Included items <span>'+components.length+'</span></h3><p class="home-muted">Line amounts are document prices before any shared bundle discount. The parent record is the amount counted in your home total.</p><div class="home-component-table"><table><thead><tr><th>Fixture</th><th>Model</th><th>Area</th><th>Qty</th><th>Line amount</th></tr></thead><tbody>'+components.map(function(c){return '<tr><td><strong>'+esc(c.name)+'</strong></td><td>'+esc(c.model||'—')+'</td><td>'+esc(c.area||'—')+'</td><td>'+esc(c.quantity==null?'—':c.quantity)+'</td><td>'+(c.amount==null?'Included':money(c.amount))+'</td></tr>';}).join('')+'</tbody></table></div></section>':'';
    var costRows=[['Original item',record.itemCost],['Extended warranty',record.warrantyCost],['Delivery',record.deliveryCost]].filter(function(row){return row[1]!=null&&row[1]!=='';});
    var costBreakdown=costRows.length?'<section class="home-cost-breakdown"><h3>Purchase breakdown</h3><dl>'+costRows.map(function(row){return '<div><dt>'+row[0]+'</dt><dd>'+money(row[1])+'</dd></div>';}).join('')+'<div class="home-cost-total"><dt>Order total</dt><dd>'+money(record.cost)+'</dd></div></dl></section>':'';
    var services=store.records.filter(function(r){return r.kind==='maintenance'&&r.status!=='Archived'&&(r.linkedRecord===record.id||r.id.indexOf(record.id+'_filter_')===0);});
    var serviceHTML=services.map(function(r){var items=scheduleItems(r);return '<section class="home-linked-service"><h3>'+esc(r.name)+'</h3>'+(items.length?'<p class="home-muted">'+items.length+' replacements'+(r.cost!=null?' · '+money(r.cost*items.length)+' paid once':'')+'. Next: '+date(nextService(r))+'.</p>'+scheduleHTML(r):'<dl><div><dt>Next service</dt><dd>'+date(nextService(r))+'</dd></div></dl>')+'<p>'+source(r)+'</p></section>';}).join('');
    dialog.innerHTML='<header class="home-dialog-head"><div><h2 id="home-dialog-title">'+esc(record.name)+'</h2><p class="home-dialog-sub">'+esc([record.category||'Home collection',record.room].filter(Boolean).join(' · '))+'</p></div><button type="button" class="home-button home-close" id="home-close" aria-label="Close Home record">'+icon('close')+'</button></header><div class="home-dialog-body home-item-detail"><div class="home-detail-hero">'+thumbnail(record,true)+'<div><div class="home-detail-price"><strong>'+(record.cost==null?(record.costBasis==='Gift'?'Housewarming gift':'Cost not recorded'):money(record.cost))+'</strong>'+badge(record)+'</div>'+(subtitle?'<p class="home-detail-sub">'+esc(subtitle)+'</p>':'')+'</div></div>'+timeline()+'<p class="home-source">'+source(record)+'</p>'+(photo?'<p class="home-photo-credit"><a href="'+esc(photo.url)+'" target="_blank" rel="noopener noreferrer">Product image: '+esc(photo.label)+icon('external')+'</a></p>':'')+costBreakdown+'<section><h3>About this item</h3><dl>'+facts('brand,model,room,roomDetail,serial')+'</dl></section>'+componentHTML+'<section><h3>Ownership, delivery & installation</h3><dl>'+facts('provider,delivered,deliveryDetails,deliverySource,installed,installationType,installationSource,costBasis,funding')+'</dl></section><section><h3>Warranty & cover</h3><p class="home-warranty-basis tone-'+esc(warrantyTone(record)).replace('not_applicable','none')+'">'+esc(warrantyBasis)+'</p>'+(warrantyFacts?'<dl>'+warrantyFacts+'</dl>':record.warrantyStatus==='not_applicable'?'':'<p class="home-muted">Warranty details not yet recorded.</p>')+warrantySource+'</section>'+serviceHTML+(record.action?'<section><h3>To keep in mind</h3><p class="home-detail-note">'+esc(record.action)+'</p></section>':'')+(record.notes?'<details class="home-field-group"><summary>Documents & notes</summary><p class="home-detail-note">'+esc(record.notes)+'</p></details>':'')+'</div>';
    dialog.querySelector('#home-close').onclick=function(){dialog.close();};
    dialog.querySelectorAll('[data-copy]').forEach(function(b){b.onclick=function(){if(!navigator.clipboard)return;navigator.clipboard.writeText(b.dataset.copy).then(function(){b.textContent='Copied';setTimeout(function(){b.textContent='Copy';},1500);});};});
    dialog.showModal();
    dialog.scrollTop=0;
  }
  function openRecord(record) {
    lastFocus=document.activeElement;
    if(record.kind==='appliance'){openItem(record);return;}
    var revision=store.revision;
    var linked=txs.find(function(t){return t.id===record.transactionId&&t.source===record.transactionSource;});
    dialog.innerHTML='<form id="home-form"><header class="home-dialog-head"><div><h2 id="home-dialog-title">'+esc(record.name||'New '+labels[record.kind])+'</h2><p class="home-dialog-sub">'+esc(titles[record.kind])+'</p></div><button class="home-button home-close" type="button" id="home-close" aria-label="Close Home record">'+icon('close')+'</button></header><div class="home-dialog-body"><p class="home-source">'+source(record)+'</p><fieldset '+(!editable?'disabled':'')+'>'+groupedFields(record)+'<details class="home-field-group home-link-transaction"><summary>Linked payment</summary><p class="home-muted">Reference an existing statement payment. This does not add spending or change its ownership.</p><input id="home-tx-search" type="search" placeholder="Search payments by merchant, date or amount" aria-label="Search payments"><select id="home-tx" aria-label="Linked payment"><option value="">No linked payment</option>'+(linked?'<option value="'+esc(linked.source+':'+linked.id)+'" selected>'+esc(txLabel(linked))+'</option>':'')+'</select><small id="home-tx-help" class="home-muted">'+(record.transactionId&&!linked?'Previously linked payment is unavailable; choose a current payment.':'Search to choose a payment. Amounts remain independently recorded above.')+'</small></details></fieldset>'+(record.kind==='mortgage'?'<p class="home-muted">Without an explicit review date, attention reminders suggest the earlier of 90 days or the recorded notice period before lock-in ends. This is a planning date, not a contractual deadline.</p>':'')+'<p id="home-error" role="alert"></p></div><footer class="home-dialog-foot"><span class="home-muted">'+(editable?'Changes are saved locally with backups.':'Read-only view')+'</span><button class="home-button primary" id="home-save" '+(!editable?'disabled':'')+'>Save record</button></footer></form>';
    dialog.querySelector('#home-close').onclick=function(){if(!busy)dialog.close();};
    var select=dialog.querySelector('#home-tx');
    dialog.querySelector('#home-tx-search').oninput=function(e){
      var selected=select.value, q=e.target.value.toLowerCase(), matches=q?txs.filter(function(t){return txLabel(t).toLowerCase().includes(q);}).slice(0,60):[];
      var keep=txs.find(function(t){return t.source+':'+t.id===selected;});
      if(keep&&!matches.includes(keep))matches.unshift(keep);
      select.innerHTML='<option value="">No linked payment</option>'+matches.map(function(t){var v=t.source+':'+t.id;return '<option value="'+esc(v)+'" '+(v===selected?'selected':'')+'>'+esc(txLabel(t))+'</option>';}).join('');
    };
    dialog.querySelector('#home-form').onsubmit=async function(e){
      e.preventDefault();if(!editable||busy)return;
      var r={id:record.id,kind:record.kind}, values=new FormData(e.target);
      values.forEach(function(v,k){r[k]=v;});
      ['schedule','linkedRecord'].forEach(function(k){if(record[k]!=null&&record[k]!=='')r[k]=record[k];});
      fields[record.kind].forEach(function(f){if(f[2]==='number'||f[2]==='integer'){if(r[f[0]]==='')delete r[f[0]];else r[f[0]]=Number(r[f[0]]);}});
      if(select.value){var split=select.value.indexOf(':');r.transactionSource=select.value.slice(0,split);r.transactionId=select.value.slice(split+1);}
      busy=true;dialog.querySelector('#home-save').disabled=true;dialog.querySelector('#home-save').textContent='Saving…';
      try {
        var response=await fetch('api/home',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:revision,record:r})});
        var saved=await response.json();if(!response.ok||!saved.ok)throw new Error(saved.error||'Save failed.');
        store=saved;render();dialog.close();
      } catch(error){dialog.querySelector('#home-error').textContent=error.message;}
      finally{busy=false;dialog.querySelector('#home-save').disabled=!editable;dialog.querySelector('#home-save').textContent='Save record';}
    };
    dialog.querySelector('#home-form').addEventListener('invalid',function(e){var group=e.target.closest('details');if(group)group.open=true;},true);
    dialog.showModal();
  }
  async function load(){
    try {
      var status=await json('api/status').catch(function(){return {};});
      editable=!!(status.editable&&status.homeRecords);
      store=await json('api/home').catch(function(){return json('data/home.json');});
      if(!Array.isArray(store.records))throw new Error('Invalid Home records.');
      render();
    }catch(error){root.innerHTML='<section class="home-panel"><h2>Home records unavailable</h2><p>'+esc(error.message)+'</p><button class="home-button" id="home-retry">Retry</button></section>';root.querySelector('#home-retry').onclick=load;}
  }
  Promise.all([json('data/transactions.json'),json('data/account_transactions.json')]).then(function(all){all.forEach(function(data,i){(data.transactions||[]).forEach(function(t){txs.push(Object.assign({},t,{source:i?'bank':'card'}));});});txs.sort(function(a,b){return b.date.localeCompare(a.date);});}).catch(function(){/* Register remains usable without statement links. */});
  load();
}());
