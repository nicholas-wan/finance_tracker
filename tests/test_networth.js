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

// Net contributions per destination, cumulative, one point per month.
const contrib = N.contributionSeries({transactions:[
  {date:'2025-04-21',flow:'Investment',direction:'withdrawal',amount:1000,description:'INTERACTIVE BROKERS'},
  {date:'2025-04-28',flow:'Investment',direction:'withdrawal',amount:500,description:'IBKR LLC'},
  {date:'2025-06-02',flow:'Investment',direction:'deposit',amount:200,description:'INTERACTIVE BROKERS'},
  {date:'2025-06-05',flow:'Investment',direction:'withdrawal',amount:300,description:'TIGER BROKERS'},
  {date:'2025-07-01',flow:'Other',direction:'withdrawal',amount:99,description:'RENT'}
]});
assert.deepEqual(Object.keys(contrib).sort(),['Interactive Brokers','Tiger Brokers']);
assert.deepEqual(contrib['Interactive Brokers'].points,[{date:'2025-04-30',value:1500,source:'net contributions from UOB ONE statements'},{date:'2025-06-30',value:1300,source:'net contributions from UOB ONE statements'}]);
assert.equal(contrib['Interactive Brokers'].total,1300);
console.log('Contribution series accumulate net transfers by month.');

// The change is the movement in total net worth; accounts first recorded
// inside the window are named rather than dropped from the figure.
{
  const all = [{account:{id:'a',name:'Bank'}},{account:{id:'b',name:'Broker'}},{account:{id:'c',name:'Old loan'}}];
  const then = {net:100, values:{a:100, c:-20}}, now = {net:400, values:{a:150, b:250}};
  const change = N.changeBetween(all, then, now);
  assert.equal(change.delta, 300);
  assert.equal(change.base, 100);
  assert.deepEqual(change.added, ['Broker']);
  assert.deepEqual(change.dropped, ['Old loan']);
  console.log('Net worth: change is the movement in the total, with added and dropped accounts named.');
}

// An annual top-up schedule carries balances forward: each top-up adds to
// the last known value once its month begins, a recorded balance takes over
// and absorbs a top-up in its own month, and a future month is not counted.
{
  const srs = {id:'srs', topUp:{month:12, amount:15300, since:2023}};
  assert.deepEqual(N.scheduledPoints(srs, [], '2026-09-12').map(p=>[p.date,p.value]), [['2023-12-01',15300],['2024-12-01',30600],['2025-12-01',45900]]);
  assert.deepEqual(N.scheduledPoints(srs, [{date:'2025-12-23',value:15300}], '2026-09-12').map(p=>[p.date,p.value]), [['2023-12-01',15300],['2024-12-01',30600],['2025-12-23',15300]]);
  assert.deepEqual(N.scheduledPoints(srs, [{date:'2025-12-23',value:15300}], '2026-12-05').map(p=>[p.date,p.value]).pop(), ['2026-12-01',30600]);
  assert.deepEqual(N.scheduledPoints({id:'plain'}, [{date:'2025-01-01',value:1}], '2026-09-12'), [{date:'2025-01-01',value:1}]);
  assert.equal(N.nextTopUp(srs.topUp, '2026-09-12'), '2026-12');
  assert.equal(N.nextTopUp(srs.topUp, '2026-12-05'), '2027-12');
  console.log('Net worth: annual top-up schedule carries balances forward.');
}

// A top-up reminder runs from the month before the top-up to the month
// after, names the year and due date, and clears once the statements show
// that year's transfer.
{
  const srs = {month:12, amount:15300, since:2025, flow:'Retirement (SRS)'};
  const paid = [{flow:'Retirement (SRS)', direction:'withdrawal', date:'2025-12-23', amount:15300}];
  assert.equal(N.topUpDue('SRS', srs, '2026-10-31', paid), null);
  const nov = N.topUpDue('SRS', srs, '2026-11-01', paid);
  assert.deepEqual([nov.name, nov.year, nov.dueBy, nov.overdue, nov.seen, nov.tracked], ['SRS', 2026, '2026-12-31', false, null, true]);
  const jan = N.topUpDue('SRS', srs, '2027-01-15', paid);
  assert.deepEqual([jan.year, jan.overdue, jan.seen], [2026, true, null]);
  assert.equal(N.topUpDue('SRS', srs, '2027-02-01', paid), null);
  const done = N.topUpDue('SRS', srs, '2026-12-05', paid.concat([{flow:'Retirement (SRS)', direction:'withdrawal', date:'2026-12-02', amount:15300}]));
  assert.equal(done.seen, '2026-12-02');
  assert.equal(N.topUpDue('x', {month:12, amount:1, since:2027}, '2026-11-15', []), null, 'not before the first year');
  assert.equal(N.topUpDue('x', undefined, '2026-11-15', []), null);
  // A standalone reminder clears on a description match, case-insensitively.
  const mum = {month:12, amount:2000, since:2025, match:'CENTRAL PROVIDENT'};
  const cpf = [{flow:'Transfer', direction:'withdrawal', date:'2026-12-04', amount:2000, description:'PAYNOW-FAST PIB Central Provident Fu OTHR'}];
  assert.equal(N.topUpDue('Mum', mum, '2026-11-20', []).seen, null);
  assert.equal(N.topUpDue('Mum', mum, '2026-12-20', cpf).seen, '2026-12-04');
  assert.equal(N.topUpDue('Mum', mum, '2026-12-20', [{flow:'Transfer', direction:'deposit', date:'2026-12-04', amount:2000, description:'CENTRAL PROVIDENT'}]).seen, null, 'a deposit is not a top-up');
  console.log('Net worth: top-up reminder window and clearing.');
}

// The Coming up list wants the next occurrence: this year's while unpaid
// (overdue included), otherwise the first later year.
{
  const srs = {month:12, amount:15300, since:2025, flow:'Retirement (SRS)'};
  const paid2025 = [{flow:'Retirement (SRS)', direction:'withdrawal', date:'2025-12-23', amount:15300}];
  assert.deepEqual([N.topUpNext('SRS', srs, '2026-09-12', paid2025).dueBy, N.topUpNext('SRS', srs, '2026-09-12', paid2025).overdue], ['2026-12-31', false]);
  assert.equal(N.topUpNext('SRS', srs, '2027-01-10', paid2025).overdue, true, 'January still shows last year overdue');
  const paid2026 = paid2025.concat([{flow:'Retirement (SRS)', direction:'withdrawal', date:'2026-12-02', amount:15300}]);
  assert.equal(N.topUpNext('SRS', srs, '2026-12-20', paid2026).dueBy, '2027-12-31', 'paid this year, so next year');
  assert.equal(N.topUpNext('SRS', srs, '2027-01-10', paid2026).dueBy, '2027-12-31');
  assert.equal(N.topUpNext('x', {month:12, amount:1, since:2029}, '2026-09-12', []), null, 'nothing within the next two years');
  console.log('Net worth: next top-up occurrence for the Coming up list.');
}
