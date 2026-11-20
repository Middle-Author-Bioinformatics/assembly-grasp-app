import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
const compile=s=>'data:text/javascript;base64,'+Buffer.from(ts.transpileModule(s,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64');
const selection=compile(await readFile(new URL('../src/lib/genome-selection.ts',import.meta.url),'utf8'));
const source=(await readFile(new URL('../src/lib/tree-handoff.ts',import.meta.url),'utf8')).replace('from "./genome-selection"',`from "${selection}"`);
const {handoffSelection,treeTarget}=await import(compile(source));
test('handoff sends only to the configured window/origin after nonce-checked readiness',()=>{
 const listeners=new Set(),sent=[],status=[];let opened;
 const child={postMessage:(payload,target)=>sent.push({payload,target})};
 const old=globalThis.window;
 globalThis.window={location:{origin:'https://grasp.example'},addEventListener:(_,fn)=>listeners.add(fn),removeEventListener:(_,fn)=>listeners.delete(fn),open:url=>{opened=new URL(url);return child;}};
 let cleanup;
 try{
  const data={version:1,source:'assembly-grasp',created_at:'now',genomes:[{assembly_accession:'GCF_000000001.1'}]};
  cleanup=handoffSelection('https://tree.example/',data,msg=>status.push(msg));
  const nonce=new URLSearchParams(opened.hash.slice(1)).get('mab-import');
  assert(!opened.href.includes('GCF_'));assert(nonce);
  const fn=[...listeners][0];
  fn({origin:'https://evil.example',source:child,data:{type:'mab:gtotree-ready',nonce}});
  fn({origin:'https://tree.example',source:{},data:{type:'mab:gtotree-ready',nonce}});
  fn({origin:'https://tree.example',source:child,data:{type:'mab:gtotree-ready',nonce:'wrong'}});
  assert.equal(sent.length,0);
  fn({origin:'https://tree.example',source:child,data:{type:'mab:gtotree-ready',nonce}});
  assert.equal(sent.length,1);assert.equal(sent[0].target,'https://tree.example');assert.equal(sent[0].payload.selection.genomes.length,1);
  fn({origin:'https://tree.example',source:child,data:{type:'mab:gtotree-imported',nonce}});
  assert.equal(listeners.size,0);assert.match(status.at(-1),/Selection transferred/);
 }finally{cleanup?.();globalThis.window=old;}
});
test('only HTTPS deployment URLs without credentials are accepted',()=>{
 assert.throws(()=>treeTarget('javascript:alert(1)'));
 assert.throws(()=>treeTarget('https://user:secret@tree.example/'));
 assert.equal(treeTarget('https://tree.example/app').pathname,'/app');
});
