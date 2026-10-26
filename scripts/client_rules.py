"""Narrow client-side bypasses for the user's UU Remote application."""
UU_PROCESSES = ['UURemote','UURemoteDaemon','UURemoteService','UURemoteServer','UURemoteHelper','UURemoteUpdater','uuyc-cli']
UU_PROCESSES += [name+'.exe' for name in UU_PROCESSES if name!='uuyc-cli']
UU_DOMAINS = ['uuyc.163.com']
def clash_rules():
 return ['PROCESS-NAME,'+name+',DIRECT' for name in UU_PROCESSES]+['DOMAIN-SUFFIX,'+domain+',DIRECT' for domain in UU_DOMAINS]
def sing_rules():
 return [{'process_name':UU_PROCESSES,'action':'route','outbound':'direct'},{'domain_suffix':UU_DOMAINS,'action':'route','outbound':'direct'}]

# Foreign domain classification precedes CN IP matching so overseas services
# using a China CDN do not accidentally bypass the proxy.
CN_CLASH_RULES = ['GEOSITE,geolocation-!cn,PROXY','GEOSITE,cn,DIRECT','GEOIP,CN,DIRECT']
CN_SING_RULES = [
 {'rule_set':'geosite-geolocation-!cn','action':'route','outbound':'proxy'},
 {'rule_set':'geosite-cn','action':'route','outbound':'direct'},
 {'action':'resolve','server':'local'},
 {'rule_set':'geoip-cn','action':'route','outbound':'direct'}]
def china_split(config,kind):
 if kind=='clash':
  existing=[r for r in config.get('rules',[]) if r not in CN_CLASH_RULES and not r.startswith(('MATCH,','FINAL,'))]
  config.update(mode='rule',rules=existing+CN_CLASH_RULES+['MATCH,PROXY'])
 else:
  route=config['route'];route['rules']=[r for r in route['rules'] if r not in CN_SING_RULES]+CN_SING_RULES
  route['final']='proxy'
  rule_sets=[{'type':'remote','tag':tag,'format':'binary','url':'https://raw.githubusercontent.com/SagerNet/'+repo+'/rule-set/'+tag+'.srs','download_detour':'proxy','update_interval':'1d'} for tag,repo in [('geosite-geolocation-!cn','sing-geosite'),('geosite-cn','sing-geosite'),('geoip-cn','sing-geoip')]]
  route['rule_set']=[r for r in route.get('rule_set',[]) if r['tag'] not in {x['tag'] for x in rule_sets}]+rule_sets
  config['dns']={'servers':[{'type':'local','tag':'local'}],'final':'local'}
 return config
