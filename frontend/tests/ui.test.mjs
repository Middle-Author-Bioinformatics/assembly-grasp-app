import assert from 'node:assert/strict';
import {test} from 'node:test';
import {mkdtemp,rm} from 'node:fs/promises';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';

test('rendered selection persists across result pages, searches and remounts',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'https://grasp.example'});
 Object.assign(globalThis,{window:dom.window,document:dom.window.document,localStorage:dom.window.localStorage,HTMLElement:dom.window.HTMLElement,IS_REACT_ACT_ENVIRONMENT:true});
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
 const React=await import('react');const {act}=React;const {createRoot}=await import('react-dom/client');
 const directory=await mkdtemp(fileURLToPath(new URL('./.ui-',import.meta.url)));
 try{
  await build({entryPoints:[fileURLToPath(new URL('../src/components/SearchView.tsx',import.meta.url))],bundle:true,platform:'node',format:'esm',jsx:'automatic',packages:'external',outfile:directory+'/component.mjs',define:{'import.meta.env':JSON.stringify({VITE_AG_API_BASE:'https://api.example',VITE_GTOTREE_APP_URL:'https://tree.example'})},logLevel:'silent'});
  const {SearchView}=await import(pathToFileURL(directory+'/component.mjs').href);
  const columns=['assembly_accession','organism_name','biosample'];
  globalThis.fetch=async(_url,options)=>{const req=JSON.parse(options.body);const result=req.operation==='schema'?{columns,facets:{},dataset_id:'v1',indexed:true}:{columns,rows:[[req.page===2?'GCF_000000002.1':'GCF_000000001.1',req.query,'SAMN1']],total:2,page:req.page,page_size:1,pages:2,dataset_id:'v1',indexed:true,scanned:2};return new Response(JSON.stringify(result),{headers:{'Content-Type':'application/json'}});};
  let root=createRoot(document.getElementById('root'));
  await act(async()=>{root.render(React.createElement(SearchView));await new Promise(r=>setTimeout(r,0));});
  const input=document.querySelector('input[aria-label="Organism name"]');
  async function search(query){await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(input,query);input.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});await act(async()=>{[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Search & view').click();await new Promise(r=>setTimeout(r,0));});}
  await search('Geobacter');
  await act(async()=>document.querySelector('input[aria-label="Select GCF_000000001.1"]').click());
  assert.match(document.body.textContent,/1 \/ 100 reference genomes selected/);
  await act(async()=>{[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Next').click();await new Promise(r=>setTimeout(r,0));});
  await act(async()=>document.querySelector('input[aria-label="Select GCF_000000002.1"]').click());
  assert.match(document.body.textContent,/2 \/ 100 reference genomes selected/);
  assert.match(document.body.textContent,/share a BioSample/);
  await search('Another genus');assert.match(document.body.textContent,/2 \/ 100 reference genomes selected/);
  assert.equal(JSON.parse(localStorage.getItem('mab-assembly-selection-v1')).genomes.length,2);
  await act(async()=>root.unmount());root=createRoot(document.getElementById('root'));
  await act(async()=>{root.render(React.createElement(SearchView));await new Promise(r=>setTimeout(r,0));});
  assert.match(document.body.textContent,/2 \/ 100 reference genomes selected/);
  await act(async()=>{document.querySelector('button[aria-label="Remove GCF_000000001.1"]').click();});
  assert.equal(JSON.parse(localStorage.getItem('mab-assembly-selection-v1')).genomes.length,1);
  await act(async()=>root.unmount());
 }finally{await rm(directory,{recursive:true,force:true});dom.window.close();}
});
