const assert=require('node:assert/strict');
const G=require('../app/js/games.js');
const tx=(id,date,amount,type,game,gameDetails)=>({id,date,month:date.slice(0,7),amount,type,game,gameDetails});
const rows=[tx('a','2026-01-30',10,'debit','Steam'),tx('b','2026-02-02',10,'refund','Steam'),tx('c','2026-03-04',30,'debit','HoYoverse',{title:'Example game',platform:'PC'})];
let m=G.summary(rows,[],'2026','All','2026-04');
assert.equal(m.groups.length,2);assert.equal(m.purchaseCount,2);assert.equal(m.refundCount,1);
assert.equal(m.net,3000);assert.equal(m.average,750);assert.equal(m.months.length,4);assert.equal(m.months[3].cents,0);
m=G.summary(rows,[],'2026','store:Steam','2026-04');
assert.equal(m.key,'store:Steam');assert.equal(m.rows.length,2);assert.equal(m.net,0);assert.equal(m.months[1].cents,-1000);
m=G.summary(rows,[{game:'Sold game',publisher:'HoYoverse',month:'2026-04',amount:20}],'2026','title:Sold game','2026-04');
assert.equal(m.rows.length,0);assert.equal(m.net,-2000);assert.equal(m.sales.length,1);
m=G.summary(rows,[{game:'Sold game',publisher:'HoYoverse',month:'2026-04',amount:20}],'2026','title:Example game','2026-04');
assert.equal(m.proceeds,0);assert.equal(m.net,3000);
const old=tx('old','2025-12-31',5,'debit','Steam');old.month='2026-01';
assert.equal(G.summary([old],[],'2025','All','2026-04').purchases,500);
assert.equal(G.identity(rows[0]).assigned,false);assert.equal(G.identity(rows[2]).title,'Example game');
console.log('Gaming refunds, calendar months, game assignment and sale isolation passed.');

// Year-specific library entries, totals and sales must exclude other years.
const mixed=rows.concat([tx('prior','2025-06-01',45,'debit','Kuro Games'),tx('earlier','2025-05-01',7,'debit','HoYoverse',{title:'Example game'})]);
const priorSales=[{game:'Old sold game',month:'2024-03',amount:25}];
let current=G.summary(mixed,priorSales,'2026','All','2026-08');
assert.deepEqual(current.groups.map(g=>g.info.label).sort(),['Example game','Steam']);
assert.equal(current.groups.find(g=>g.info.label==='Example game').total,3000);
assert.equal(current.purchases,4000);assert.equal(current.proceeds,0);
assert.ok(current.rows.every(t=>G.month(t).startsWith('2026')));
const entire=G.summary(mixed,priorSales,'All','All','2026-08');
assert.ok(entire.groups.some(g=>g.info.label==='Kuro Games'));
assert.ok(entire.groups.some(g=>g.info.label==='Old sold game'));
assert.equal(entire.purchases,9200);
console.log('Year filters exclude historical games and sales; All years restores them.');

// Historical age uses the transaction date, with explicit prelaunch handling.
assert.equal(G.releaseTiming('Etheria Restart','2025-06-13').age,'8 days after launch');
assert.equal(G.releaseTiming('Zenless Zone Zero','2026-06-21').age,'1 year, 11 months, 17 days after launch');
assert.equal(G.releaseTiming('Neverness to Everness','2026-04-29').age,'On launch day');
assert.equal(G.releaseTiming('Neverness to Everness','2026-04-28').days,-1);
assert.match(G.releaseTiming('Neverness to Everness','2026-04-28').age,/predates/);
assert.equal(G.releaseTiming('Slay the Spire 2','2026-03-08').label,'Early access launch');
assert.equal(G.releaseTiming('Steam','2026-03-08'),null);
assert.equal(G.releaseTiming('Etheria Restart','2025-02-30'),null);
assert.equal(G.releaseTiming('Etheria Restart',undefined),null);
assert.equal(G.releaseTiming('Zenless Zone Zero','2025-07-04').age,'1 year after launch');
console.log('Game release timing and historical date boundaries passed.');

const activityRows=[tx('one','2026-01-03',10,'debit','Steam',{title:'Game A'}),tx('two','2026-01-04',5,'debit','Steam',{title:'Game B'}),tx('refund','2026-02-01',10,'credit','Steam',{title:'Game A'}),tx('unknown','2026-01-05',4,'debit','Steam'),tx('older','2025-01-01',20,'debit','Steam',{title:'Old game'})];
const activityModel=G.summary(activityRows,[],'2026','title:Game A','2026-03');
const timeline=G.activity(activityModel.groups,activityModel.months);
assert.deepEqual(timeline.map(g=>g.title),['Game A','Game B']);
assert.equal(timeline[0].cells[0].cents,1000);
assert.equal(timeline[1].cells[0].count,1);
assert.equal(timeline[0].cells[1].count,0);
assert.equal(timeline[0].cells[2].count,0);
console.log('Concurrent purchase timeline preserves other games, excludes refunds and unknown titles, and keeps gaps empty.');

// Library rows carry a purchase count and a share of the period's positive spend.
assert.equal(current.groups.find(g=>g.info.label==='Example game').purchaseCount,1);
assert.equal(current.groups.find(g=>g.info.label==='Example game').share,1);
assert.equal(current.groups.find(g=>g.info.label==='Steam').share,0);
// Stacked months split each month by game, keep refunds below the baseline, and follow the library order.
const stack=G.stacked(activityModel.groups,activityModel.months,activityRows.filter(t=>t.date.startsWith('2026')));
assert.deepEqual(stack[0].segments.map(s=>s.key),activityModel.groups.map(g=>g.key));
assert.equal(stack[0].segments.find(s=>s.key==='title:Game A').cents,1000);assert.equal(stack[1].refund,-1000);assert.equal(stack[1].segments.length,0);
// Purchase types come from the saved value first, then the store.
assert.deepEqual(G.purchaseType(tx('p','2026-01-01',1,'debit','HoYoverse')),{type:'Top-up',guessed:true});
assert.deepEqual(G.purchaseType(tx('p','2026-01-01',1,'debit','HoYoverse',{purchaseType:'DLC'})),{type:'DLC',guessed:false});
assert.equal(G.purchaseType(tx('p','2026-01-01',1,'debit','Other games')),null);
console.log('Library shares, stacked months and purchase-type guesses passed.');
