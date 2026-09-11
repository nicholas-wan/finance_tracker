(function(root) {
  'use strict';
  var stores=['HoYoverse','Kuro Games','Steam','G2G marketplace','ZeusX marketplace','PlayStation','Nintendo','Xbox','Epic Games','Riot Games','Garena','Top-up sites','Other games'];
  function identity(t) {
    var d=t.gameDetails||{}, inferred=t.game||'Other games';
    var title=d.title||(stores.indexOf(inferred)<0?inferred:'');
    return {key:title?'title:'+title:'store:'+inferred,title:title||'Game not assigned',store:d.platform||(title?'':inferred),label:title||inferred,assigned:!!title};
  }
  // Published launch dates; Steam-specific and early-access dates are labelled.
  var releases={
    'Zenless Zone Zero':{date:'2024-07-04',label:'Global launch',source:'https://www.hoyolab.com/article/29127111'},
    'Wuthering Waves':{date:'2024-05-23',label:'Global launch (Singapore date)',source:'https://www.mobygames.com/game/224327/wuthering-waves/releases/'},
    'Chaos Zero Nightmare':{date:'2025-10-22',label:'Global launch',source:'https://newsroom.smilegate.com/eng/1761563064'},
    'Neverness to Everness':{date:'2026-04-29',label:'Global launch',source:'https://nte.perfectworld.com/de/article/news/gamenews/20260428/261949.html'},
    'Etheria Restart':{date:'2025-06-05',label:'Global launch',source:'https://store.steampowered.com/news/posts/?enddate=1745824729&feed=steam_community_announcements'},
    'Morimens':{date:'2024-08-01',label:'Steam release',source:'https://store.steampowered.com/app/3052450/Morimens/'},
    'Slay the Spire 2':{date:'2026-03-05',label:'Early access launch',source:'https://www.megacrit.com/press-kits/slay-the-spire-2/'}
  };
  function releaseTiming(title,date) {
    var release=releases[title];
    if(!release||!/^\d{4}-\d{2}-\d{2}$/.test(date||''))return null;
    var start=new Date(release.date+'T00:00:00Z'),end=new Date(date+'T00:00:00Z');
    if(!Number.isFinite(end.getTime())||end.toISOString().slice(0,10)!==date)return null;
    var days=Math.round((end-start)/86400000), age;
    if(days<0)age='Transaction predates this launch by '+(-days)+' day'+(days===-1?'':'s');
    else if(days===0)age='On launch day';
    else {
      var months=(end.getUTCFullYear()-start.getUTCFullYear())*12+end.getUTCMonth()-start.getUTCMonth();
      if(end.getUTCDate()<start.getUTCDate())months--;
      var anchor=new Date(Date.UTC(start.getUTCFullYear(),start.getUTCMonth()+months,start.getUTCDate()));
      var remainder=Math.round((end-anchor)/86400000),parts=[];
      function part(n,label){if(n)parts.push(n+' '+label+(n===1?'':'s'));}
      part(Math.floor(months/12),'year');part(months%12,'month');part(remainder,'day');
      age=parts.join(', ')+' after launch';
    }
    return {date:release.date,label:release.label,source:release.source,age:age,days:days};
  }
  // A store implies how money usually reaches a game; the guess is shown as a
  // guess until a purchase type is saved on the transaction.
  var storeTypes={'HoYoverse':'Top-up','Kuro Games':'Top-up','Steam':'Base game','G2G marketplace':'Account purchase','ZeusX marketplace':'Account purchase','PlayStation':'Base game','Nintendo':'Base game','Xbox':'Base game','Epic Games':'Base game','Top-up sites':'Top-up'};
  function purchaseType(t) {
    var saved=(t.gameDetails||{}).purchaseType;
    if(saved)return {type:saved,guessed:false};
    var guess=storeTypes[(t.gameDetails||{}).platform]||storeTypes[t.game];
    return guess?{type:guess,guessed:true}:null;
  }
  function month(t){return (t.date||t.month).slice(0,7);}
  function cents(t){return Math.round(t.amount*100)*(t.type==='debit'?1:-1);}
  function saleKey(s){return 'title:'+s.game;}
  function summary(transactions,sales,year,key,through) {
    var groups={}, allMonths=transactions.map(month).concat(sales.map(function(s){return s.month;}));
    function group(k,info){if(!groups[k])groups[k]={key:k,info:info,total:0,lifetime:0,rows:[],sales:[]};return groups[k];}
    transactions.forEach(function(t){var info=identity(t),g=group(info.key,info);g.lifetime+=cents(t);if(year==='All'||month(t).slice(0,4)===year){g.total+=cents(t);g.rows.push(t);}});
    sales.forEach(function(s){if(year==='All'||s.month.slice(0,4)===year)group(saleKey(s),{title:s.game,label:s.game,store:s.publisher||'',assigned:true}).sales.push(s);});
    var visible=Object.values(groups).filter(function(g){return g.rows.length||g.sales.length;}).sort(function(a,b){return b.total-a.total||a.info.label.localeCompare(b.info.label);});
    var positive=visible.reduce(function(n,g){return n+Math.max(g.total,0);},0);
    visible.forEach(function(g){g.purchaseCount=g.rows.filter(function(t){return t.type==='debit';}).length;g.share=positive&&g.total>0?g.total/positive:0;});
    if(!visible.some(function(g){return g.key===key;}))key='All';
    var rows=transactions.filter(function(t){return (year==='All'||month(t).slice(0,4)===year)&&(key==='All'||identity(t).key===key);});
    var selectedSales=sales.filter(function(s){return (year==='All'||s.month.slice(0,4)===year)&&(key==='All'||saleKey(s)===key);});
    var first=allMonths.slice().sort()[0]||through;
    var start=year==='All'?first:year+'-01', end=year==='All'?through:(year===through.slice(0,4)?through:year+'-12');
    var months=[],cursor=start;
    while(cursor<=end&&months.length<1200){months.push({month:cursor,cents:0});var y=+cursor.slice(0,4),m=+cursor.slice(5,7);cursor=m===12?(y+1)+'-01':y+'-'+String(m+1).padStart(2,'0');}
    var lookup={};months.forEach(function(m){lookup[m.month]=m;});
    rows.forEach(function(t){if(lookup[month(t)])lookup[month(t)].cents+=cents(t);});
    var purchaseRows=rows.filter(function(t){return t.type==='debit';}),refundRows=rows.filter(function(t){return t.type!=='debit';});
    var purchases=purchaseRows.reduce(function(n,t){return n+cents(t);},0),refunds=-refundRows.reduce(function(n,t){return n+cents(t);},0);
    var proceeds=selectedSales.reduce(function(n,s){return n+Math.round(s.amount*100);},0);
    return {key:key,groups:visible,rows:rows,sales:selectedSales,months:months,purchases:purchases,refunds:refunds,purchaseCount:purchaseRows.length,refundCount:refundRows.length,spend:purchases-refunds,proceeds:proceeds,net:purchases-refunds-proceeds,average:months.length?(purchases-refunds)/months.length:0};
  }
  function activity(groups,months) {
    return groups.filter(function(g){return g.info.assigned;}).map(function(g){
      var cells=months.map(function(m){return {month:m.month,cents:0,count:0};}),lookup={};
      cells.forEach(function(c){lookup[c.month]=c;});
      g.rows.forEach(function(t){var cell=lookup[month(t)];if(cell&&t.type==='debit'){cell.cents+=cents(t);cell.count++;}});
      return {key:g.key,title:g.info.label,cells:cells};
    }).filter(function(g){return g.cells.some(function(c){return c.count;});}).sort(function(a,b){return a.title.localeCompare(b.title);});
  }
  // One bar per month, split by game so the chart carries what the activity
  // grid shows. Refund-heavy months keep their net below the baseline.
  function stacked(groups,months,rows) {
    var order=groups.map(function(g){return g.key;}),lookup={};
    var out=months.map(function(m){var cell={month:m.month,cents:0,refund:0,segments:[]};lookup[m.month]=cell;return cell;});
    var perGame={};
    rows.forEach(function(t){var cell=lookup[month(t)];if(!cell)return;var k=identity(t).key;perGame[cell.month]=perGame[cell.month]||{};perGame[cell.month][k]=(perGame[cell.month][k]||0)+cents(t);});
    out.forEach(function(cell){var byGame=perGame[cell.month]||{};
      Object.keys(byGame).sort(function(a,b){return order.indexOf(a)-order.indexOf(b);}).forEach(function(k){var c=byGame[k];cell.cents+=c;if(c>0)cell.segments.push({key:k,cents:c});else cell.refund+=c;});
    });
    return out;
  }
  var api={activity:activity,releaseTiming:releaseTiming,identity:identity,month:month,cents:cents,summary:summary,stacked:stacked,purchaseType:purchaseType};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.Gaming=api;
})(typeof window==='undefined'?this:window);
