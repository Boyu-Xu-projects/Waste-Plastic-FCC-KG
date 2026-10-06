/*
  GitHub Pages static API adapter — sharded/lazy v2.
  Generated for the existing 05_14 Waste Plastic–FCC KG interface.
*/
(() => {
  const nativeFetch = window.fetch.bind(window);
  const CACHE = new Map();

  async function loadJSON(path) {
    if (!CACHE.has(path)) {
      CACHE.set(path, nativeFetch(path).then(r => {
        if (!r.ok) throw new Error(`Failed to load ${path}: ${r.status}`);
        return r.json();
      }));
    }
    return CACHE.get(path);
  }

  function jresp(obj) {
    return Promise.resolve(new Response(JSON.stringify(obj), {
      status: 200,
      headers: {"Content-Type":"application/json; charset=utf-8"}
    }));
  }

  function hash32(text, n) {
    let h = 0 >>> 0;
    text = String(text ?? "");
    for (let i=0; i<text.length; i++) {
      h = (Math.imul(h,31) + text.charCodeAt(i)) >>> 0;
    }
    return h % n;
  }

  function stem(mid, route) {
    if (mid === "M01") return "m1";
    if (mid === "M02") return `m2_${route || "ALL"}`;
    if (mid === "M03") return `m3_${(!route || route === "ALL") ? "TARGET" : route}`;
    return "m1";
  }

  async function manifest(mid, route) {
    const s = stem(mid, route);
    return loadJSON(`data/bundles/${s}/manifest.json`);
  }

  async function loadEntities(mid, route, etype) {
    const m = await manifest(mid, route);
    const p = m.entity_files?.[etype];
    return p ? loadJSON(p) : [];
  }

  async function loadPreview(mid, route) {
    const m = await manifest(mid, route);
    const [edges,nodes] = await Promise.all([
      loadJSON(m.preview_edges),
      loadJSON(m.preview_nodes)
    ]);
    return {m,edges,nodes};
  }

  async function loadNodeMapForIds(mid, route, ids) {
    const m = await manifest(mid, route);
    const groups = new Map();
    for (const id of ids) {
      if (!id) continue;
      const k = hash32(id, m.node_files.length);
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(id);
    }
    const out = new Map();
    await Promise.all([...groups.entries()].map(async ([k,wanted]) => {
      const obj = await loadJSON(m.node_files[k]);
      for (const id of wanted) {
        if (obj[id]) out.set(id, obj[id]);
      }
    }));
    return out;
  }

  async function loadAdjacency(mid, route, seeds) {
    const m = await manifest(mid, route);
    const groups = new Map();
    for (const id of seeds) {
      if (!id) continue;
      const k = hash32(id, m.adj_files.length);
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(id);
    }

    const byId = new Map();
    await Promise.all([...groups.entries()].map(async ([k,wanted]) => {
      const obj = await loadJSON(m.adj_files[k]);
      for (const id of wanted) {
        for (const e of (obj[id] || [])) {
          const eid = e.edge_id || `${e.source_canonical_id}|${e.relation}|${e.target_canonical_id}`;
          if (!byId.has(eid)) byId.set(eid,e);
        }
      }
    }));
    return [...byId.values()];
  }

  async function loadEdgeById(mid, route, eid) {
    const m = await manifest(mid, route);
    const k = hash32(eid, m.edge_files.length);
    const obj = await loadJSON(m.edge_files[k]);
    return obj[eid] || null;
  }

  async function loadEvidenceByEdge(mid, route, eid) {
    const m = await manifest(mid, route);
    const k = hash32(eid, m.evidence_files.length);
    const obj = await loadJSON(m.evidence_files[k]);
    return obj[eid] || [];
  }

  function uniqueEdges(rows) {
    const m = new Map();
    for (const e of rows || []) {
      const eid = e.edge_id || `${e.source_canonical_id}|${e.relation}|${e.target_canonical_id}`;
      if (!m.has(eid)) m.set(eid,e);
    }
    return [...m.values()];
  }

  async function graphForContexts(contexts, seeds, maxEdges, entityStory) {
    const seedSet = new Set(seeds || []);
    let edges = [];
    let seedNodeMaps = [];

    if (!seedSet.size) {
      const previews = await Promise.all(contexts.map(c => loadPreview(c.mid,c.route)));
      edges = uniqueEdges(previews.flatMap(x => x.edges || [])).slice(0,maxEdges);

      const nmap = new Map();
      for (const p of previews) {
        for (const n of p.nodes || []) {
          const id = n.canonical_id || n.id;
          if (id && !nmap.has(id)) nmap.set(id,n);
        }
      }

      const ids = new Set();
      for (const e of edges) {
        e.source = e.source_canonical_id;
        e.target = e.target_canonical_id;
        ids.add(e.source_canonical_id);
        ids.add(e.target_canonical_id);
      }

      const missing = [...ids].filter(id => !nmap.has(id));
      if (missing.length) {
        const maps = await Promise.all(contexts.map(c => loadNodeMapForIds(c.mid,c.route,missing)));
        for (const mm of maps) for (const [id,n] of mm) if (!nmap.has(id)) nmap.set(id,n);
      }

      const nodes = [...ids].map(id => {
        const n = {...(nmap.get(id) || {canonical_id:id,canonical_name_en:id,type:""})};
        n.id=id;
        n.name=n.display_name||n.canonical_name_en||id;
        n.is_seed=false;
        return n;
      });

      return {nodes,links:edges,entity_story:false,story_counts:{M01:0,M02:0,M03:0}};
    }

    // Selected entity: load only adjacency shards for selected seeds.
    const adj = await Promise.all(contexts.map(c => loadAdjacency(c.mid,c.route,[...seedSet])));
    edges = uniqueEdges(adj.flatMap(x => x));

    const ids = new Set(seedSet);
    for (const e of edges) {
      e.source=e.source_canonical_id;
      e.target=e.target_canonical_id;
      ids.add(e.source_canonical_id);
      ids.add(e.target_canonical_id);
    }

    const maps = await Promise.all(contexts.map(c => loadNodeMapForIds(c.mid,c.route,[...ids])));
    const nmap = new Map();
    for (const mm of maps) {
      for (const [id,n] of mm) {
        if (!nmap.has(id)) nmap.set(id,n);
        else nmap.set(id,{...nmap.get(id),...n});
      }
    }

    const counts={M01:0,M02:0,M03:0};
    if (entityStory) {
      for (const e of edges) {
        if (counts[e.story_module] !== undefined) counts[e.story_module]+=1;
      }
    }

    const nodes=[...ids].map(id=>{
      const n={...(nmap.get(id)||{canonical_id:id,canonical_name_en:id,type:""})};
      n.id=id;
      n.name=n.display_name||n.canonical_name_en||id;
      n.is_seed=seedSet.has(id);

      if (entityStory) {
        if (n.is_seed) n.story_module="SEED";
        else {
          const mods=[];
          for (const e of edges) {
            if (e.source_canonical_id===id||e.target_canonical_id===id) {
              if (e.story_module) mods.push(e.story_module);
            }
          }
          n.story_module=["M01","M02","M03"].find(x=>mods.includes(x))||"";
        }
      }
      return n;
    });

    return {nodes,links:edges.slice(0,maxEdges),entity_story:!!entityStory,story_counts:counts};
  }

  function contextsFor(mods, route, storyAll=false) {
    if (storyAll || mods.length>1) {
      return [
        {mid:"M01",route:"ALL"},
        {mid:"M02",route:"ALL"},
        {mid:"M03",route:"TARGET"}
      ];
    }
    const mid=mods[0]||"M01";
    return [{mid,route: route || (mid==="M03"?"TARGET":"ALL")}];
  }

  async function nodeEvidence(cid, contexts, maxN=80) {
    const edgeLists = await Promise.all(
      contexts.map(c => loadAdjacency(c.mid,c.route,[cid]))
    );
    const edges=uniqueEdges(edgeLists.flatMap(x=>x));

    // Load only the evidence shards actually touched by this node's incident edges.
    const rows=[];
    const seen=new Set();

    for (const c of contexts) {
      const m=await manifest(c.mid,c.route);
      const groups=new Map();
      for (const e of edges) {
        const eid=e.edge_id;
        if (!eid) continue;
        const k=hash32(eid,m.evidence_files.length);
        if (!groups.has(k)) groups.set(k,[]);
        groups.get(k).push(eid);
      }

      for (const [k,eids] of groups) {
        const obj=await loadJSON(m.evidence_files[k]);
        for (const eid of eids) {
          for (const ev of (obj[eid]||[])) {
            const key=[
              ev.doi||"",ev.title||ev.filename||"",ev.year||"",
              ev.page||"",ev.evidence||""
            ].join("|");
            if (seen.has(key)) continue;
            seen.add(key);rows.push(ev);
          }
        }
      }
    }

    const yr=x=>{
      const m=String(x?.year??"").match(/(?:19|20)\d{2}/);
      return m?Number(m[0]):0;
    };
    rows.sort((a,b)=>yr(b)-yr(a));
    return rows.slice(0,maxN);
  }

  async function findNode(cid, contexts) {
    const maps=await Promise.all(
      contexts.map(c=>loadNodeMapForIds(c.mid,c.route,[cid]))
    );
    let n=null;
    for (const mm of maps) {
      const x=mm.get(cid);
      if (x) n=n?{...n,...x}:x;
    }
    return n||{canonical_id:cid,canonical_name_en:cid,type:""};
  }

  async function findEdge(eid, contexts) {
    for (const c of contexts) {
      const e=await loadEdgeById(c.mid,c.route,eid);
      if (e) return {edge:e,context:c};
    }
    return {
      edge:{edge_id:eid,relation:"",source_canonical_name_en:"",target_canonical_name_en:""},
      context:contexts[0]
    };
  }

  async function handleApi(url) {
    const u=new URL(url,window.location.href);
    const path=u.pathname;
    const p=u.searchParams;

    if (path.endsWith("/api/config")) {
      return loadJSON("data/config.json");
    }

    if (path.endsWith("/api/categories")) {
      const all=await loadJSON("data/categories.json");
      const etype=p.get("type")||"";
      const q=(p.get("q")||"").trim().toLowerCase();
      let rows=[...(all[etype]||[])];
      if(q){
        rows=rows.filter(c=>
          [c.code,c.name_zh,c.name_en].some(v=>String(v||"").toLowerCase().includes(q))
        );
      }
      return rows;
    }

    if (path.endsWith("/api/entities")) {
      const mods=(p.get("modules")||p.get("module")||"M01").split(",").filter(Boolean);
      const mid=mods.length===1?mods[0]:(mods[0]||"M01");
      const route=p.get("route")||(mid==="M03"?"TARGET":"ALL");
      const etype=p.get("type")||"";
      const category=p.get("category")||"";
      const q=(p.get("q")||"").trim().toLowerCase();

      let rows=[...(await loadEntities(mid,route,etype))];
      if(category){
        rows=rows.filter(r=>
          r.controlled_category_code===category || r.canonical_id===category
        );
      }
      if(q){
        rows=rows.filter(r=>
          [r.display_name,r.canonical_name_en,r.controlled_category_zh,
           r.controlled_category_en,r.canonical_id]
          .some(v=>String(v||"").toLowerCase().includes(q))
        );
      }
      return rows.slice(0,250);
    }

    if (path.endsWith("/api/graph")) {
      const mods=(p.get("modules")||"M01").split(",").filter(Boolean);
      const route=p.get("route")||(mods[0]==="M03"?"TARGET":"ALL");
      const seeds=(p.get("seeds")||"").split(",").filter(Boolean);
      const maxEdges=Math.min(Math.max(Number(p.get("max_edges")||1000),50),8000);
      const entityStory=p.get("entity_story")==="1";

      const contexts=contextsFor(mods,route,entityStory&&seeds.length);
      return graphForContexts(contexts,seeds,maxEdges,entityStory&&seeds.length);
    }

    const nm=path.match(/\/api\/node\/(.+)$/);
    if(nm){
      const cid=decodeURIComponent(nm[1]);
      const mods=(p.get("modules")||"M01").split(",").filter(Boolean);
      const route=p.get("route")||(mods[0]==="M03"?"TARGET":"ALL");
      const storyAll=p.get("story_all")==="1";
      const contexts=contextsFor(mods,route,storyAll);

      const [node,evidence]=await Promise.all([
        findNode(cid,contexts),
        nodeEvidence(cid,contexts,80)
      ]);
      return {node,evidence,data_source:"GitHub Pages static export"};
    }

    const em=path.match(/\/api\/edge\/(.+)$/);
    if(em){
      const eid=decodeURIComponent(em[1]);
      const mods=(p.get("modules")||"M01").split(",").filter(Boolean);
      const route=p.get("route")||(mods[0]==="M03"?"TARGET":"ALL");
      const storyAll=p.get("story_all")==="1";
      const contexts=contextsFor(mods,route,storyAll);

      const found=await findEdge(eid,contexts);
      let evidence=[];
      // Prefer the same context in which the edge was found.
      if(found.context){
        evidence=await loadEvidenceByEdge(found.context.mid,found.context.route,eid);
      }
      if(!evidence.length){
        for(const c of contexts){
          evidence=await loadEvidenceByEdge(c.mid,c.route,eid);
          if(evidence.length) break;
        }
      }
      return {edge:found.edge,evidence,data_source:"GitHub Pages static export"};
    }

    throw new Error(`Unsupported static API path: ${path}`);
  }

  window.fetch=function(input,init){
    const url=typeof input==="string"?input:input?.url;
    try{
      const u=new URL(url,window.location.href);
      if(u.pathname.includes("/api/")){
        return handleApi(url).then(jresp);
      }
    }catch(_){}
    return nativeFetch(input,init);
  };
})();
