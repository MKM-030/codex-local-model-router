"""Opt-in read-only probe: real Codex/apps/search, deterministic mock inference."""
import argparse,importlib.util,json,os,socket,subprocess,sys,tempfile,threading,time,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
sys.stdout.reconfigure(errors="backslashreplace")
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'router'))
from tool_bridge import dumps
spec=importlib.util.spec_from_file_location('router',ROOT/'router/hybrid-model-router.py');router=importlib.util.module_from_spec(spec);spec.loader.exec_module(router)
def port():
 with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('--codex',required=True);p.add_argument('--codex-home',required=True);p.add_argument('--catalog',required=True);p.add_argument('--search-model',required=True);p.add_argument('--allow-cloud-tools',action='store_true');args=p.parse_args()
 if not args.allow_cloud_tools:raise SystemExit('This opt-in probe reads the public router repo through your GitHub app and opens example.com through cloud search. Pass --allow-cloud-tools to authorize it.')
 home=Path(args.codex_home);model=json.loads(Path(args.catalog).read_text(encoding='utf-8-sig'))['models'][0]['slug'];profile_name='bridge-read-probe-'+uuid.uuid4().hex[:8];profile=home/(profile_name+'.config.toml');requests=[];failures=[]
 with tempfile.TemporaryDirectory(prefix='bridge-connected-') as d:
  base=Path(d);mockport=port();bridgeport=port()
  class Mock(BaseHTTPRequestHandler):
   def log_message(self,*a):pass
   def do_POST(self):
    payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(payload);n=len(requests)-1
    try:
     tools=payload.get('tools',[])
     if n==0:
      tool=next(t for t in tools if '__github' in t.get('description','').lower() and '._get_repo.' in t.get('description',''))
      name=tool['name'];arguments={'repository_full_name':'MKM-030/codex-local-model-router'}
     elif n==1:
      tool=next(t for t in tools if 'Codex tool web.run.' in t.get('description',''));name=tool['name'];arguments={'open':[{'ref_id':'https://example.com'}],'response_length':'short'}
     else:name=None;arguments=None
     response={'id':'resp'+str(n),'object':'response','created_at':int(time.time()),'model':model,'status':'in_progress','output':[]}
     if name:item={'id':'item'+str(n),'type':'function_call','call_id':'call'+str(n),'name':name,'arguments':dumps(arguments),'status':'completed'}
     else:item={'id':'msg'+str(n),'type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':'CONNECTED_BRIDGE_READ_OK','annotations':[]}]}
     self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Connection','close');self.end_headers()
     events=[{'type':'response.created','response':response},{'type':'response.output_item.added','output_index':0,'item':dict(item,arguments='',status='in_progress') if name else dict(item,status='in_progress',content=[])}]
     if name:events += [{'type':'response.function_call_arguments.delta','item_id':item['id'],'output_index':0,'delta':item['arguments']},{'type':'response.function_call_arguments.done','item_id':item['id'],'output_index':0,'arguments':item['arguments']}]
     else:events += [{'type':'response.output_text.delta','output_index':0,'content_index':0,'item_id':item['id'],'delta':'CONNECTED_BRIDGE_READ_OK'}]
     events += [{'type':'response.output_item.done','output_index':0,'item':item},{'type':'response.completed','response':dict(response,status='completed',output=[item],usage={'input_tokens':100,'output_tokens':20,'total_tokens':120})}]
     for seq,event in enumerate(events):event['sequence_number']=seq;self.wfile.write(('event: '+event['type']+'\ndata: '+dumps(event)+'\n\n').encode());self.wfile.flush()
    except Exception as exc:
     failures.append(type(exc).__name__+': '+str(exc));print('MOCK_ERROR',failures[-1],flush=True)
  mock=ThreadingHTTPServer(('127.0.0.1',mockport),Mock);threading.Thread(target=mock.serve_forever,daemon=True).start()
  config={'host':'127.0.0.1','port':bridgeport,'logMetadata':False,'allowCloudSearch':True,'searchModel':args.search_model,'models':[{'id':model,'baseUrl':f'http://127.0.0.1:{mockport}/v1','catalogPath':str(Path(args.catalog).resolve())}]}
  bridge=router.make_server(router.Router(config,base));threading.Thread(target=bridge.serve_forever,daemon=True).start()
  profile.write_text('\n'.join(['model='+json.dumps(model),'model_provider="bridge_read_probe"','model_reasoning_effort="low"','model_catalog_json='+json.dumps(str(Path(args.catalog).resolve())),'[features]','standalone_web_search=true','[model_providers.bridge_read_probe]','name="Read-only connected bridge validation"',f'base_url="http://127.0.0.1:{bridgeport}/v1"','wire_api="responses"','requires_openai_auth=true','supports_websockets=false','supports_standalone_web_search=true','request_max_retries=0','stream_max_retries=0']),encoding='utf-8')
  try:
   env=os.environ.copy();env['CODEX_HOME']=str(home)
   result=subprocess.run([args.codex,'exec','--profile',profile_name,'--ephemeral','--skip-git-repo-check','--color','never','-C',str(ROOT),'Read public repository MKM-030/codex-local-model-router with the connected GitHub get_repo tool, then open https://example.com using web.run. Do not modify files, repository data, or account settings.'],input='',capture_output=True,text=True,encoding='utf-8',errors='replace',env=env,timeout=90)
   print(result.stdout[-6000:]);print(result.stderr[-6000:]);print('REQUESTS',len(requests),'MOCK_FAILURES',failures)
   assert result.returncode==0 and len(requests)>=3 and not failures,'Codex connected probe did not finish'
   results={i.get('call_id'):i.get('output') for i in requests[-1].get('input',[]) if isinstance(i,dict) and i.get('type')=='function_call_output'}
   github=dumps(results.get('call0'));web=dumps(results.get('call1'))
   print('GITHUB_RESULT_PREVIEW',github[:900]);print('WEB_RESULT_PREVIEW',web[:500])
   assert 'codex-local-model-router' in github and 'error' not in github.lower(),'Connected GitHub read failed'
   assert 'Example Domain' in web,'Standalone search did not return the expected page'
   print('PASS: REAL_CONNECTED_GITHUB_READ_AND_WEB_RUN_UNDER_LOCAL_MODEL_ID; INFERENCE WAS MOCKED')
  finally:
   profile.unlink(missing_ok=True);bridge.shutdown();bridge.server_close();mock.shutdown();mock.server_close()
if __name__=='__main__':main()
