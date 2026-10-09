(() => {
  const specs = [
    ['user','用户消息',2100,'#3b82f6'],['assistant','助手消息',7900,'#6366f1'],
    ['tool_results','工具返回',65100,'#ef8354'],['tool_calls','工具调用',8800,'#f2b84b'],
    ['skills','Skills 指令与目录',11000,'#16a085'],['memory','AGENTS.md / 记忆指令',6200,'#b799e8'],
    ['system','基础指令',5400,'#7a879a'],['developer','环境与运行指令',5300,'#8e9e78'],
    ['tools','已记录工具定义',4000,'#dc70a1'],['summary','压缩摘要',2200,'#56a9ac'],
    ['unknown','日志未覆盖的差额',30000,'#b5bac4']
  ];
  function report(scale) {
    const buckets = specs.map(([key,label,tokens,color]) => ({key,label,tokens:Math.round(tokens*scale),color,percent:Math.round(tokens*scale)/475000*100,kind:key==='unknown'?'residual':'estimate',details:key==='unknown'?[]:[{label:key==='tool_results'?'functions.exec':'日志文本',line:128,estimated_tokens:Math.round(tokens*scale*.6)}]}));
    const used = buckets.reduce((sum,b)=>sum+b.tokens,0);
    return {session:{id:'demo-session',model:'gpt-6.1-sol'},context:{used,capacity:475000,free:475000-used,percent:used/475000*100,usage_timestamp:'2026-10-08T11:48:00Z',estimate_scaled:false},latest_usage:{cached_input_tokens:used-8600,output_tokens:980},buckets,notes:[]};
  }
  const first=report(.45),second=report(1);
  window.__contextLensRequest = raw => {const request=JSON.parse(raw);setTimeout(()=>window.__codexContextLens.receive({id:request.id,report:request.turn_id==='demo-one'?first:second}),80);};
  window.__codexContextLens.update({viewToken:window.__codexContextLens.status().viewToken,sessionId:'demo-session',turns:[{turn_id:'demo-one',completed:true,used:first.context.used,capacity:475000},{turn_id:'demo-two',completed:true,used:second.context.used,capacity:475000}]});
})();
