const assert = require('node:assert/strict');
const N = require('../app/js/networth.js');
assert.equal(N.ageDays('2026-06-08', '2026-09-07'), 91);
assert.equal(N.ageDays('2026-06-09', '2026-09-07'), 90);
assert.equal(N.ageDays('2026-08-31', '2026-09-07'), 7);
const accounts = [{id:'cpf',source:'CPF portal'}, {id:'broker',source:'Broker portal'}];
const snapshots = [{accountId:'cpf',date:'2026-01-01',value:1,source:'Old source'}, {accountId:'cpf',date:'2026-09-01',value:10,source:'Current CPF source'}];
assert.deepEqual(N.snapshotDefaults(accounts,snapshots,'cpf'),{source:'Current CPF source',placeholder:'10'});
assert.deepEqual(N.snapshotDefaults(accounts,snapshots,'broker'),{source:'Broker portal',placeholder:'0.00'});
const bank = {account:{id:'bank',group:'cash'}};
const property = {account:{id:'flat',group:'property'}};
const car = {account:{id:'car',group:'liabilities'}};
const loan = {id:'dbs',kind:'mortgage',name:'DBS',status:'Verified',balance:200,balanceDate:'2026-09-01'};
let series = N.replaceHomeSeries([bank,property,car],{records:[loan]});
assert.equal(series.length,4);
const values={bank:100,flat:500,car:-50,home_dbs:-200};
const net=350;
assert.equal(net-N.homeEquity(series,values),50,'Non-property net worth must retain car debt');
series=N.replaceHomeSeries(series,{records:[{...loan,balance:150}]});
assert.equal(series.length,4,'Updating the home loan must not duplicate it');
assert.equal(series.find(s=>s.sourceType==='home').points[0].value,150);
series=N.replaceHomeSeries(series,{records:[{...loan,status:'Archived'}]});
assert.equal(series.length,3,'Archiving the home loan removes the derived liability');
assert.ok(series.includes(car));
console.log('Net worth: 90-day age, account source defaults, unrelated debt and live mortgage replacement passed.');

// A loan with a start date, instalment and rate is amortised back month by
// month from its recorded balance; each earlier balance is larger by that
// month's principal, and the series begins on the start date.
const amortised = N.replaceHomeSeries([bank], {records:[{...loan, starts:'2026-05-01', balance:251726.63, balanceDate:'2026-09-06', instalment:990, rate:1.55}]}).find(s=>s.sourceType==='home');
assert.equal(amortised.points[0].date,'2026-05-01');
assert.equal(amortised.points[amortised.points.length-1].value,251726.63);
for (let i=1;i<amortised.points.length;i++) assert.ok(amortised.points[i-1].value>amortised.points[i].value, 'balance falls month by month');
const principal = amortised.points[amortised.points.length-2].value - 251726.63;
assert.ok(principal > 600 && principal < 700, 'one month of principal at S$990 and 1.55% is about S$665, got '+principal);
console.log('Mortgage amortises back to its start date.');
