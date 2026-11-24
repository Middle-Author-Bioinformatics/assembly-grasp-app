import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {build} from 'esbuild';
test('enabled account mode routes app data through authenticated API and preserves worker upload contracts',async()=>{
 const directory=await mkdtemp(fileURLToPath(new URL('./.app-',import.meta.url))),requests=[],puts=[];
 globalThis.window={dispatchEvent(){}};
 globalThis.fetch=async(url,options)=>{
  requests.push({url:String(url),options});assert(String(url).startsWith('https://accounts.example/apps/'));
  assert.equal(new Headers(options.headers).get('Authorization'),'Bearer valid');
  let data={state:'succeeded'};
  if(String(url).endsWith('/upload'))data={entries:JSON.parse(options.body).files.map(f=>({file_name:f.name,url:'https://signed.example/'+f.name,headers:{'Content-Type':f.content_type}}))};
  if(String(url).includes('/results/'))data={files:[{key:'Example123/FeGenie-heatmap-data.csv',size:100,last_modified:'',url:'https://signed.example/result'}],expires_at:Date.now()+900000};
  if(String(url).endsWith('samples.json'))data={samples:[{key:'sample',label:'Sample'}]};
  if(String(url).endsWith('mutation_predictions.json'))data=[{mutation:'SNP'}];
  if(String(url).endsWith('/search'))data=JSON.parse(options.body).operation==='schema'?{columns:['organism'],facets:{},dataset_id:'v1',indexed:true}:{columns:['organism'],rows:[['Pelobacter']],total:1,page:1,page_size:50,pages:1,dataset_id:'v1',indexed:true,scanned:1};
  return new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
 };
 globalThis.XMLHttpRequest=class {upload={};headers={};status=200;open(method,url){this.url=url;this.method=method;}setRequestHeader(k,v){this.headers[k]=v;}send(body){puts.push({body,url:this.url,headers:this.headers});assert(!this.headers.Authorization);queueMicrotask(()=>this.onload());}};
 try{
  await build({entryPoints:[fileURLToPath(new URL('../src/lib/assembly-config.ts',import.meta.url))],bundle:true,format:'esm',platform:'node',packages:'external',outfile:directory+'/app.mjs',define:{'import.meta.env':JSON.stringify({VITE_MAB_AUTH_ENABLED:'true',VITE_MAB_API_BASE:'https://accounts.example',VITE_MAB_CLIENT_ID:'test-client'})},plugins:[{name:'session',setup(b){b.onResolve({filter:/account-session$/},()=>({path:'session',namespace:'fixture'}));b.onLoad({filter:/.*/,namespace:'fixture'},()=>({contents:"export async function accessToken(){return 'valid'}",loader:'js'}));}}],logLevel:'silent'});
  const app=await import(pathToFileURL(directory+'/app.mjs'));
  const schema=await app.loadSchema();assert.deepEqual(schema.columns,['organism']);
await app.queryAssemblies({query:'Pelobacter',search_mode:'name',filters:[],page:1,page_size:50});
assert(requests.every(r=>r.url==='https://accounts.example/apps/taxonsleuth/search'));
assert.equal(JSON.parse(requests[1].options.body).query,'Pelobacter');
 }finally{await rm(directory,{recursive:true,force:true});}
});
