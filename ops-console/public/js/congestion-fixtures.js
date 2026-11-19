/* Fictional, browser-only evidence. Never stored or sent to a collector. */
(function () {
  const OC = window.OpsConsole = window.OpsConsole || {};
  OC.buildCongestionDemo = function ({env, scenario, window: period, since, until, anchor}) {
    const end = period === 'custom' ? Date.parse(until) : anchor;
    const begin = period === 'custom' ? Date.parse(since) : end - ({'1h':1,'6h':6,'24h':24,'7d':168}[period] || 24)*3600000;
    if (!Number.isFinite(begin) || !Number.isFinite(end) || end <= begin || end-begin > 7*86400000 || end>Date.now()+5000) throw new Error('Informe um intervalo passado de até sete dias');
    const span=Math.min(180000,(end-begin)*0.8), origin=end-span-1;
    const time = fraction => new Date(origin+span*fraction).toISOString();
    const details = new Map();
    function snapshot(pid, index, fraction, pending, running, state, partial=false) {
      const routes=[['GET','/api/v1/relatorios/resumo/',8,42000],['GET','/api/v1/dashboard/',4,18000],['POST','/api/v1/escala-flex/status-events/',4,6500]];
      const slow=routes.flatMap(([method,route],group)=>Array.from({length:group===0?4:1},(_,n)=>({id:`${pid}-${group*10+n+1}`,method,route,
        duration_ms:(scenario==='short'?4300:42000)-group*(scenario==='short'?400:5000)-n*(scenario==='short'?120:700),wait_ms:group*800,client_disconnected:partial&&group===2})));
      const id=`demo-${env}-${pid}-${index}`;
      return {id,environment:env,pid,event_at:time(fraction),ingested_at:new Date(Date.parse(time(fraction))+100).toISOString(),state,
        threads:16,pending_requests:pending,running_requests:running,dispatcher_queued_channels:pending,waitress_queue_depth:pending,
        pending_routes:pending?[{method:'GET',route:'/api/v1/dashboard/',count:pending,max_ms:scenario==='short'?0:18000}]:[],
        running_routes:routes.map(([method,route,count,max_ms],group)=>({method,route,
          count:group===0?8:group===1?Math.min(4,running-9):running-8-Math.min(4,running-9),
          max_ms:scenario==='short'?4300-group*400:max_ms})),slow_executions:slow,
        pending_routes_partial:partial,running_routes_partial:partial||running!==16,slow_executions_partial:partial,
        request_totals_partial:false,unlisted_requests:partial?3:0,diagnostic_failures:partial?1:0};
    }
    function episode(id,pid,rows,startKnown=true,sessionKnown=true) {
      const last=rows[rows.length-1], closed=last.state==='recovered';
      const ep={id,pid,environment:env,session:`demo-session-${pid}`,start_at:rows[0].event_at,last_at:last.event_at,
        end_at:closed?last.event_at:null,start_known:startKnown,partial:rows.some(row=>row.slow_executions_partial),
        snapshot_count:rows.length,max_pending:Math.max(...rows.map(row=>row.pending_requests)),max_running:Math.max(...rows.map(row=>row.running_requests)),
        duration_ms:closed&&startKnown?Date.parse(last.event_at)-Date.parse(rows[0].event_at):null};
      rows.forEach(row=>{row.episode_id=id;});
      details.set(id,{environment:env,episode:ep,snapshots:rows,selected:null,latest_snapshot:last,next_offset:null,
        series:rows.map(row=>({at:row.event_at,last_at:row.event_at,samples:1,snapshot_id:row.id,state:row.state,
          running:row.running_requests,pending:row.pending_requests,threads:row.threads})),series_aggregated:false,expected_interval_seconds:scenario==='sustained'?30:5,session_known:sessionKnown});
      return ep;
    }
    let episodes;
    if(scenario==='short'){
      const first=snapshot(9001,1,0.8,1,16,'congestion_start');
      const last=snapshot(9001,2,0.8,0,13,'recovered');
      last.event_at=new Date(Date.parse(first.event_at)+Math.min(71,span*0.1)).toISOString();
      last.ingested_at=new Date(Date.parse(last.event_at)+100).toISOString();
      episodes=[episode('demo-short',9001,[first,last])];
    }else if(scenario==='partial'){
      episodes=[episode('demo-restarted',9002,[snapshot(9002,1,0.7,12,16,'congestion_ongoing',true),snapshot(9002,2,0.9,0,10,'recovered',true)],false,false),
        episode('demo-unfinished',9001,[snapshot(9001,1,0.05,4,16,'congestion_start'),snapshot(9001,2,0.15,35,16,'congestion_ongoing',true)],true,true)];
    }else{
      const levels=[3,12,40,80,24,0];
      episodes=[episode('demo-sustained',9001,levels.map((pending,index)=>snapshot(9001,index+1,0.05+index*0.16,pending,index===5?13:16,index===0?'congestion_start':index===5?'recovered':'congestion_ongoing')))];
    }
    const all=[...details.values()].flatMap(detail=>detail.snapshots).sort((a,b)=>Date.parse(a.event_at)-Date.parse(b.event_at));
    return {overview:{environment:env,since:new Date(begin).toISOString(),until:new Date(end).toISOString(),enabled_environments:['MAIN','HOM','DEV'],
      collection:{enabled:true,collector_stale:false,polled_at:new Date(anchor).toISOString(),pending_import:false,
        invalid_lines:scenario==='partial'?3:0,dropped_records:scenario==='partial'?25:0,sources:[{file:`PPLID_${env}.waitress.queue.log (simulado)`,status:scenario==='partial'?'missing':'available'}]},
      latest:all[all.length-1],latest_interval_seconds:5,coverage:{oldest_event_at:all[0].event_at,latest_event_at:all[all.length-1].event_at,snapshots:all.length,retention_days:7},
      episodes,next_offset:null},details};
  };
})();
